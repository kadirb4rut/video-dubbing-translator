"""Production-facing interfaces for local and Modal dubbing.

The original command-line pipeline remains the compatibility entry point.  The
modules in :mod:`engine` provide small, dependency-light contracts around it so
that quality, context, streaming, and remote inference can be composed without
coupling the UI to a particular model server.
"""

from .contracts import (
    AudioResult,
    BackendUnavailable,
    InferenceMetrics,
    ModalConfig,
    ProsodyProfile,
    SpeakerProfile,
    TTSBackend,
    TranslationBackend,
)

__all__ = [
    "AudioResult",
    "BackendUnavailable",
    "InferenceMetrics",
    "ModalConfig",
    "ProsodyProfile",
    "SpeakerProfile",
    "TTSBackend",
    "TranslationBackend",
]
