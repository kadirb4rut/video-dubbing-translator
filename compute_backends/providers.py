"""User-visible compute targets and their currently supported scope."""

from __future__ import annotations


PROVIDERS = {
    "modal": {
        "label": "Modal",
        "batch": "full",
        "live": "unsupported",
        "description": "Run the complete offline job on Modal GPU with a persistent model Volume.",
    },
    "local": {
        "label": "This computer",
        "batch": "full",
        "live": "full",
        "description": "Run the existing pipeline and use this computer's CPU/GPU.",
    },
    "colab": {
        "label": "Colab G4",
        "batch": "full",
        "live": "full",
        "description": (
            "Run inference on the selected Colab runtime. Input media is sent "
            "through the authenticated connection. The selected runtime stays connected "
            "until the user presses Disconnect GPU."
        ),
    },
    "modal-tts": {
        "label": "Modal GPU (VoxCPM2 speech only)",
        "batch": "tts-only",
        "live": "unsupported",
        "description": (
            "Use the authenticated Modal VoxCPM2 function for speech generation. "
            "Transcription, translation, separation and other batch stages "
            "remain on this computer. Idle GPU containers are released after the "
            "batch job; Live Dubbing is not available on this option."
        ),
    },
}


def validate_provider(value: str) -> str:
    provider = str(value or "local").strip().lower()
    if provider not in PROVIDERS:
        raise ValueError(f"Unsupported compute provider: {value!r}")
    return provider


def provider_description(value: str) -> dict[str, str]:
    return dict(PROVIDERS[validate_provider(value)])
