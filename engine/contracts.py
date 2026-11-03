from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence


class BackendUnavailable(RuntimeError):
    """Raised when a configured backend cannot serve a request."""


@dataclass(frozen=True)
class InferenceMetrics:
    """Timing and backend metadata attached to every inference result."""

    backend: str
    wall_seconds: float
    audio_seconds: Optional[float] = None
    queue_seconds: float = 0.0
    cold_start: bool = False
    request_id: Optional[str] = None
    extra: Mapping[str, object] = field(default_factory=dict)

    @property
    def real_time_factor(self) -> Optional[float]:
        if not self.audio_seconds or self.audio_seconds <= 0:
            return None
        return self.wall_seconds / self.audio_seconds


@dataclass(frozen=True)
class AudioResult:
    """A normalized audio response from any TTS backend."""

    wav_path: Optional[Path] = None
    pcm: bytes = b""
    sample_rate: int = 48_000
    channels: int = 1
    sample_width: int = 2
    metrics: Optional[InferenceMetrics] = None
    backend: str = "unknown"

    def __post_init__(self) -> None:
        if self.wav_path is None and not self.pcm:
            raise ValueError("AudioResult needs wav_path or pcm bytes")
        if self.sample_rate <= 0 or self.channels <= 0 or self.sample_width <= 0:
            raise ValueError("Audio format values must be positive")

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.pcm:
            return len(self.pcm) / (self.sample_rate * self.channels * self.sample_width)
        return self.metrics.audio_seconds if self.metrics else None


@dataclass(frozen=True)
class SpeakerProfile:
    """Stable speaker identity and reference material used by a synthesis call."""

    speaker_id: str
    reference_wav: Path
    transcript: str = ""
    embedding: tuple[float, ...] = ()
    normalized_rate: float = 1.0
    metadata: Mapping[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.speaker_id.strip():
            raise ValueError("speaker_id must not be empty")
        if not str(self.reference_wav):
            raise ValueError("reference_wav must be provided")


@dataclass(frozen=True)
class ProsodyProfile:
    """Speaker-normalized acoustic prosody features."""

    duration_seconds: float
    median_f0_hz: float
    f0_contour_hz: tuple[float, ...] = ()
    pitch_range_hz: float = 0.0
    rms_db: float = -60.0
    energy_contour_db: tuple[float, ...] = ()
    speaking_rate: float = 1.0
    pause_durations: tuple[float, ...] = ()
    voiced_ratio: float = 0.0
    intensity_trend_db: float = 0.0
    normalized: bool = True
    metadata: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ModalConfig:
    """Safe, serializable Modal deployment configuration.

    The preferred RTX identifier is verified against Modal's public GPU API.
    ``fallback_gpus`` is intentionally ordered and only used for allocation
    failures, never as a silent model change.
    """

    gpu: str = "RTX-PRO-6000"
    fallback_gpus: tuple[str, ...] = ("H200", "H100", "B200")
    concurrency: int = 4
    timeout_seconds: int = 900
    app_name: str = "video-dubbing-translator"
    model_id: str = "openbmb/VoxCPM2"

    def __post_init__(self) -> None:
        if self.gpu != "RTX-PRO-6000":
            raise ValueError("The primary Modal GPU must be RTX-PRO-6000")
        if self.concurrency < 1:
            raise ValueError("concurrency must be >= 1")
        if self.timeout_seconds < 1:
            raise ValueError("timeout_seconds must be >= 1")


class TTSBackend(ABC):
    """Backend-neutral text-to-speech interface."""

    name = "tts"
    sample_rate = 48_000

    @abstractmethod
    def synthesize(
        self,
        text: str,
        speaker: SpeakerProfile,
        *,
        prosody: Optional[ProsodyProfile] = None,
        style: Optional[str] = None,
        output_wav: Optional[Path] = None,
        seed: int = 42,
    ) -> AudioResult:
        raise NotImplementedError

    def stream(
        self,
        text: str,
        speaker: SpeakerProfile,
        *,
        prosody: Optional[ProsodyProfile] = None,
        style: Optional[str] = None,
    ) -> Iterator[bytes]:
        """Yield PCM16 chunks.  Backends may override for native streaming."""
        result = self.synthesize(text, speaker, prosody=prosody, style=style)
        if result.pcm:
            yield result.pcm
        elif result.wav_path:
            import soundfile as sf

            data, rate = sf.read(result.wav_path, dtype="int16")
            if rate != self.sample_rate:
                raise RuntimeError(f"Expected {self.sample_rate}Hz stream, got {rate}Hz")
            yield data.tobytes()

    def close(self) -> None:
        return None


class TranslationBackend(ABC):
    """Backend-neutral translation interface used by batch and live paths."""

    name = "translation"

    @abstractmethod
    def translate(
        self,
        text: str,
        source_language: Optional[str],
        target_language: str,
        *,
        context: Optional[object] = None,
        glossary: Optional[Mapping[str, str]] = None,
    ) -> str:
        raise NotImplementedError

    def translate_many(
        self,
        texts: Sequence[str],
        source_language: Optional[str],
        target_language: str,
        *,
        context: Optional[object] = None,
        glossary: Optional[Mapping[str, str]] = None,
    ) -> list[str]:
        return [
            self.translate(
                text,
                source_language,
                target_language,
                context=context,
                glossary=glossary,
            )
            for text in texts
        ]

    def close(self) -> None:
        return None
