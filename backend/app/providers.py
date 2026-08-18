from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Sequence


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


class VoiceProvider(Protocol):
    name: str

    def synthesize(self, text: str, *, reference_voice: Path, language: str, output_path: Path) -> Path: ...


class StemSeparator(Protocol):
    name: str

    def separate(self, audio_path: Path, *, stems: int, output_dir: Path) -> dict[str, Path]: ...


class NoiseRemovalProvider(Protocol):
    name: str

    def enhance(self, audio_path: Path, *, output_path: Path) -> Path: ...


class LipSyncProvider(Protocol):
    name: str

    def sync(self, video_path: Path, audio_path: Path, *, output_path: Path) -> Path: ...


def provider_registry() -> dict[str, str]:
    """Production provider names are configuration, so model swaps do not change job orchestration."""
    return {
        "transcription": "whisper (cached model adapter)",
        "translation": "configured-api",
        "voice": "chatterbox-multilingual-v3",
        "stem_separation": "demucs",
        "noise_removal": "deepfilternet",
        "lip_sync": "latentsync-optional",
    }
