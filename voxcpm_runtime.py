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


def continuation_prompt(text, prompt_text):
    """Keep a long prompt transcript from dominating a short utterance.

    The real 3-word Turkish fixture was garbled with its 20-word English
    continuation, while reference-only cloning recovered every word at the
    correct duration. A 7-word streaming target had the same failure; removing
    its 19-word continuation recovered every word with the same seed. Prefer
    reference-only conditioning when the prompt is at least twice as long.
    Both modes retain the identical speaker WAV. Comparable-length utterances
    keep Ultimate Cloning's exact-transcript continuation.
    """
    if not prompt_text or not prompt_text.strip():
        return None
    target_words = len(text.split())
    if len(prompt_text.split()) >= 2 * max(1, target_words):
        return None
    return prompt_text.strip()


def synthesize_cloned_speech(
    model,
    text,
    reference_wav_path,
    output_wav_path,
    prompt_text=None,
    seed=42,
    style=None,
    prosody=None,
):
    import soundfile as sf
    import torch

    reference_wav_path = Path(reference_wav_path).expanduser().resolve()
    output_wav_path = Path(output_wav_path).expanduser().resolve()
    if not reference_wav_path.is_file():
        raise FileNotFoundError(f"Speaker reference audio was not found: {reference_wav_path}")
    if not text or not text.strip():
        raise ValueError("VoxCPM2 target text must not be empty.")

    # VoxCPM2 exposes no separate style/prosody control argument.  Never turn
    # a control description into target text: the model will speak it.  Keep
    # prosody extraction/reporting intact, but leave style application opt-in
    # and inert until the installed model exposes a supported control API.
    target_text = text.strip()

    generation_options = {
        "text": target_text,
        "reference_wav_path": str(reference_wav_path),
        "cfg_value": float(os.environ.get("VOXCPM_CFG_VALUE", "2.0")),
        "inference_timesteps": int(os.environ.get("VOXCPM_INFERENCE_STEPS", "10")),
        "normalize": False,
        "denoise": False,
    }
    prompt_text = continuation_prompt(target_text, prompt_text)
    if prompt_text:
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


def stream_cloned_speech(
    model,
    text,
    reference_wav_path,
    *,
    prompt_text=None,
    seed=42,
    style=None,
):
    """Yield native VoxCPM2 PCM chunks when ``generate_streaming`` is available.

    This is intentionally a thin adapter over the official native API.  A
    backend can fall back to one complete chunk when an older VoxCPM build does
    not expose streaming, but it must never fabricate incremental audio.
    """
    reference_wav_path = Path(reference_wav_path).expanduser().resolve()
    if not reference_wav_path.is_file():
        raise FileNotFoundError(f"Speaker reference audio was not found: {reference_wav_path}")
    # See synthesize_cloned_speech: style prose is metadata, not speech text.
    target_text = text.strip()
    options = {
        "text": target_text,
        "reference_wav_path": str(reference_wav_path),
        "cfg_value": float(os.environ.get("VOXCPM_CFG_VALUE", "2.0")),
        "inference_timesteps": int(os.environ.get("VOXCPM_INFERENCE_STEPS", "10")),
        "normalize": False,
        "denoise": False,
    }
    prompt_text = continuation_prompt(target_text, prompt_text)
    if prompt_text:
        options.update(prompt_wav_path=str(reference_wav_path), prompt_text=prompt_text.strip())
    import torch

    torch.manual_seed(seed)
    generator = getattr(model, "generate_streaming", None)
    if generator is None:
        import numpy as np

        waveform = model.generate(**options)
        if waveform is None or len(waveform) == 0:
            raise RuntimeError("VoxCPM2 returned an empty waveform.")
        yield np.asarray(waveform)
        return
    for chunk in generator(**options):
        if chunk is not None and len(chunk):
            yield chunk
