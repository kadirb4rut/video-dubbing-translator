from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class AudioTrack:
    path: Path
    language: str | None = None
    start_seconds: float = 0
    duration_seconds: float | None = None


class TranscriptionProvider(Protocol):
    name: str

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> Sequence[dict]: ...


class TranslationProvider(Protocol):
    name: str

    def translate(self, segments: Sequence[dict], *, source: str, target: str) -> Sequence[dict]: ...

    # Optional richer operation used by dubbing. Legacy providers only need
    # to implement translate(); the worker feature-detects this method.
    def translate_segments(
        self,
        segments: Sequence[dict],
        *,
        source: str,
        target: str,
        context: str | None = None,
        glossary: Sequence[dict] | None = None,
        style: str | None = None,
        duration_aware: bool = False,
    ) -> Sequence[dict]: ...


class VoiceProvider(Protocol):
    name: str

    def synthesize(self, text: str, *, reference_voice: Path | None, language: str, output_path: Path) -> Path: ...


class StemSeparationProvider(Protocol):
    name: str

    def separate(self, audio_path: Path, *, stems: int, output_dir: Path) -> dict[str, Path]: ...


# Compatibility alias for callers that used the shorter pre-production name.
StemSeparator = StemSeparationProvider


class NoiseRemovalProvider(Protocol):
    name: str

    def enhance(self, audio_path: Path, *, output_path: Path) -> Path: ...


class LipSyncProvider(Protocol):
    name: str

    def sync(self, video_path: Path, audio_path: Path, *, output_path: Path) -> Path: ...


class SpeakerDiarizationProvider(Protocol):
    name: str

    def diarize(self, audio_path: Path) -> Sequence[dict]: ...


def provider_registry() -> dict[str, str]:
    """Production provider names are configuration, so model swaps do not change job orchestration."""
    return {
        "transcription": "whisper (cached model adapter)",
        "translation": "google-deep-translator:GoogleTranslator",
        "translation_refinement": "hymt2:tencent/Hy-MT2-1.8B (duration-triggered, max one pass)",
        "voice": "chatterbox-multilingual-v3",
        "stem_separation": "demucs",
        "noise_removal": "deepfilternet",
        "speaker_diarization": "single-speaker-default; external diarization adapter not enabled",
        "lip_sync": "disabled-until-commercial-provider-is-configured",
    }
