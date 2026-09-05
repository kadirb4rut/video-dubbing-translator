from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from pathlib import Path

VOXCPM_MODEL_ID = "openbmb/VoxCPM2"
VOXCPM_MODEL_REVISION = "32279effe8c19989596f05d353d1447f51d9e915"
VOXCPM_OUTPUT_SAMPLE_RATE = 48_000
VOXCPM_REFERENCE_SAMPLE_RATE = 16_000
VOXCPM_REQUIRED_FILES = (
    "audiovae.pth",
    "config.json",
    "model.safetensors",
    "special_tokens_map.json",
    "tokenization_voxcpm2.py",
    "tokenizer.json",
    "tokenizer_config.json",
)
VOXCPM_EXPECTED_SIZES = {
    "audiovae.pth": 376_951_122,
    "model.safetensors": 4_580_080_592,
}
VOXCPM_SUPPORTED_LANGUAGES = {
    "ar", "bn", "da", "de", "el", "en", "es", "fi", "fr", "he", "hi", "id",
    "it", "ja", "km", "ko", "lo", "ms", "nl", "no", "pl", "pt", "ru", "sv",
    "sw", "th", "tl", "tr", "vi", "zh",
}


def resolve_device(requested: str | None = None) -> str:
    value = (requested or os.getenv("VOXCPM_DEVICE", "auto")).strip().lower()
    if value and value != "auto":
        return value
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return "mps"
    return "cpu"


def resolve_dtype(device: str, requested: str | None = None) -> str:
    value = (requested or os.getenv("VOXCPM_DTYPE", "auto")).strip().lower()
    aliases = {"bf16": "bfloat16", "fp16": "float16", "fp32": "float32"}
    if value in aliases:
        value = aliases[value]
    if value and value != "auto":
        if value not in {"bfloat16", "float16", "float32"}:
            raise ValueError(f"Unsupported VOXCPM_DTYPE: {value}")
        return value
    if device.startswith("cuda"):
        try:
            import torch
            major, _minor = torch.cuda.get_device_capability(device)
            return "float16" if major < 8 else "bfloat16"
        except (ImportError, RuntimeError, ValueError):
            return "float16"
    if device == "mps":
        return "float32"
    # The pinned reference runtime uses BF16 on CPU. It keeps this 2B model
    # within the 16 GiB validation task's memory envelope; operators can set
    # VOXCPM_DTYPE=float32 when their CPU lacks BF16 support.
    return "bfloat16"


def resolve_model_path(
    *,
    model_id: str = VOXCPM_MODEL_ID,
    revision: str = VOXCPM_MODEL_REVISION,
    local_files_only: bool = True,
) -> Path:
    if not local_files_only and os.getenv("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=model_id,
            revision=revision,
            allow_patterns=list(VOXCPM_REQUIRED_FILES),
            local_files_only=local_files_only,
        )
    )


def allow_model_download() -> bool:
    value = os.getenv("VOXCPM_ALLOW_DOWNLOAD", "false").strip().lower()
    return value in {"1", "true", "yes", "on"}


def model_ready(
    *,
    model_id: str = VOXCPM_MODEL_ID,
    revision: str = VOXCPM_MODEL_REVISION,
) -> tuple[bool, Path | None]:
    try:
        model_path = resolve_model_path(model_id=model_id, revision=revision, local_files_only=True)
    except (ImportError, OSError, RuntimeError, ValueError):
        return False, None
    for filename in VOXCPM_REQUIRED_FILES:
        path = model_path / filename
        if not path.is_file():
            return False, None
        expected = VOXCPM_EXPECTED_SIZES.get(filename)
        if expected is not None and path.stat().st_size != expected:
            return False, None
    return True, model_path


@contextlib.contextmanager
def _force_runtime_dtype(dtype: str) -> Iterator[None]:
    """Override VoxCPM's checkpoint dtype without changing cached model files."""
    try:
        import voxcpm.model.voxcpm as voxcpm_v1
        import voxcpm.model.voxcpm2 as voxcpm_v2
        from voxcpm.model import utils
    except ImportError:
        yield
        return
    original = (utils.pick_runtime_dtype, voxcpm_v1.pick_runtime_dtype, voxcpm_v2.pick_runtime_dtype)
    def forced(_device, _configured):
        return dtype

    utils.pick_runtime_dtype = forced
    voxcpm_v1.pick_runtime_dtype = forced
    voxcpm_v2.pick_runtime_dtype = forced
    try:
        yield
    finally:
        utils.pick_runtime_dtype, voxcpm_v1.pick_runtime_dtype, voxcpm_v2.pick_runtime_dtype = original


def load_model(
    *,
    device: str | None = None,
    dtype: str | None = None,
    model_id: str = VOXCPM_MODEL_ID,
    revision: str = VOXCPM_MODEL_REVISION,
):
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from voxcpm import VoxCPM

    runtime_device = resolve_device(device)
    runtime_dtype = resolve_dtype(runtime_device, dtype)
    model_path = resolve_model_path(
        model_id=model_id,
        revision=revision,
        local_files_only=not allow_model_download(),
    )
    with _force_runtime_dtype(runtime_dtype):
        model = VoxCPM.from_pretrained(
            str(model_path),
            load_denoiser=False,
            optimize=False,
            device=runtime_device,
        )
    model._lingowave_device = runtime_device
    model._lingowave_dtype = runtime_dtype
    model._lingowave_model_id = model_id
    model._lingowave_revision = revision
    return model


def validate_reference_audio(reference_wav_path: Path) -> None:
    if not reference_wav_path.is_file():
        raise FileNotFoundError(f"Speaker reference audio was not found: {reference_wav_path}")
    import soundfile as sf

    info = sf.info(str(reference_wav_path))
    if info.frames <= 0 or info.samplerate <= 0 or info.duration <= 0:
        raise ValueError("Speaker reference audio must contain non-empty PCM audio")


def synthesize_cloned_speech(
    model,
    text: str,
    reference_wav_path: Path | None,
    output_wav_path: Path,
    *,
    prompt_text: str | None = None,
    seed: int = 42,
    cfg_value: float = 2.0,
    inference_timesteps: int = 10,
    expected_sample_rate: int = VOXCPM_OUTPUT_SAMPLE_RATE,
) -> Path:
    if not text or not text.strip():
        raise ValueError("VoxCPM2 target text must not be empty.")
    if reference_wav_path is not None:
        reference_wav_path = Path(reference_wav_path).expanduser().resolve()
        validate_reference_audio(reference_wav_path)
    output_wav_path = Path(output_wav_path).expanduser().resolve()

    import soundfile as sf
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    options = {
        "text": text.strip(),
        "cfg_value": cfg_value,
        "inference_timesteps": inference_timesteps,
        "normalize": False,
        "denoise": False,
    }
    if reference_wav_path is not None:
        options["reference_wav_path"] = str(reference_wav_path)
    if prompt_text and prompt_text.strip() and reference_wav_path is not None:
        options["prompt_wav_path"] = str(reference_wav_path)
        options["prompt_text"] = prompt_text.strip()

    waveform = model.generate(**options)
    sample_rate = int(model.tts_model.sample_rate)
    if sample_rate != expected_sample_rate:
        raise RuntimeError(
            f"Unexpected VoxCPM2 output sample rate: {sample_rate}; "
            f"expected {expected_sample_rate}."
        )
    if waveform is None or len(waveform) == 0:
        raise RuntimeError("VoxCPM2 returned an empty waveform.")
    if hasattr(waveform, "detach"):
        waveform = waveform.detach().float().cpu().numpy()
    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output_wav_path), waveform, sample_rate, subtype="PCM_24")
    return output_wav_path
