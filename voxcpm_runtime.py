import os
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


def default_runtime_device():
    requested = os.environ.get("VOXCPM_DEVICE", "").strip()
    if requested:
        return requested

    import torch

    if torch.cuda.is_available():
        return "auto"
    if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
        try:
            total_memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
        except (AttributeError, OSError, ValueError):
            total_memory = 0
        if total_memory and total_memory < 16 * 1024**3:
            return "cpu"
    return "auto"


def resolve_model_path(local_files_only=True):
    if not local_files_only and os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        os.environ["HF_HUB_DISABLE_XET"] = "1"
    from huggingface_hub import snapshot_download

    return Path(
        snapshot_download(
            repo_id=VOXCPM_MODEL_ID,
            revision=VOXCPM_MODEL_REVISION,
            allow_patterns=list(VOXCPM_REQUIRED_FILES),
            local_files_only=local_files_only,
        )
    )


def model_ready():
    try:
        model_path = resolve_model_path(local_files_only=True)
    except Exception:
        return False, None
    ready = all(
        (model_path / filename).is_file()
        and (
            filename not in VOXCPM_EXPECTED_SIZES
            or (model_path / filename).stat().st_size == VOXCPM_EXPECTED_SIZES[filename]
        )
        for filename in VOXCPM_REQUIRED_FILES
    )
    return ready, model_path if ready else None


def load_model(device=None):
    os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
    from voxcpm import VoxCPM

    model_path = resolve_model_path(local_files_only=True)
    runtime_device = device or default_runtime_device()
    return VoxCPM.from_pretrained(
        str(model_path),
        load_denoiser=False,
        optimize=False,
        device=runtime_device,
    )


def synthesize_cloned_speech(
    model,
    text,
    reference_wav_path,
    output_wav_path,
    prompt_text=None,
    seed=42,
):
    import soundfile as sf
    import torch

    reference_wav_path = Path(reference_wav_path).expanduser().resolve()
    output_wav_path = Path(output_wav_path).expanduser().resolve()
    if not reference_wav_path.is_file():
        raise FileNotFoundError(f"Speaker reference audio was not found: {reference_wav_path}")
    if not text or not text.strip():
        raise ValueError("VoxCPM2 target text must not be empty.")

    generation_options = {
        "text": text.strip(),
        "reference_wav_path": str(reference_wav_path),
        "cfg_value": float(os.environ.get("VOXCPM_CFG_VALUE", "2.0")),
        "inference_timesteps": int(os.environ.get("VOXCPM_INFERENCE_STEPS", "10")),
        "normalize": False,
        "denoise": False,
    }
    if prompt_text and prompt_text.strip():
        generation_options.update(
            prompt_wav_path=str(reference_wav_path),
            prompt_text=prompt_text.strip(),
        )

    torch.manual_seed(seed)
    waveform = model.generate(**generation_options)
    sample_rate = int(model.tts_model.sample_rate)
    if sample_rate != VOXCPM_OUTPUT_SAMPLE_RATE:
        raise RuntimeError(
            f"Unexpected VoxCPM2 output sample rate: {sample_rate}; "
            f"expected {VOXCPM_OUTPUT_SAMPLE_RATE}."
        )
    if waveform is None or len(waveform) == 0:
        raise RuntimeError("VoxCPM2 returned an empty waveform.")

    output_wav_path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(output_wav_path, waveform, sample_rate, subtype="PCM_24")
    return output_wav_path
