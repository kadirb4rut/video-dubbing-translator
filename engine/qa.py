"""Automatic TTS quality assurance with hard/soft failure semantics."""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

import numpy as np


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "").casefold()
    # Unicode casefold expands capital Turkish İ to i + COMBINING DOT.
    # Removing punctuation next would split one word (İlginç -> i lginç)
    # and falsely reject correctly recovered Turkish speech. Keep dotless ı
    # and other accents distinct; this only canonicalizes the dotted i.
    text = text.replace("i\u0307", "i")
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _edit_distance(left: list[str], right: list[str]) -> int:
    row = list(range(len(right) + 1))
    for i, token in enumerate(left, 1):
        current = [i]
        for j, other in enumerate(right, 1):
            current.append(min(current[-1] + 1, row[j] + 1, row[j - 1] + (token != other)))
        row = current
    return row[-1]


def word_error_rate(expected: str, recovered: str) -> float:
    reference, hypothesis = normalize_text(expected).split(), normalize_text(recovered).split()
    return float(_edit_distance(reference, hypothesis) / max(1, len(reference)))


def character_error_rate(expected: str, recovered: str) -> float:
    reference, hypothesis = list(normalize_text(expected)), list(normalize_text(recovered))
    return float(_edit_distance(reference, hypothesis) / max(1, len(reference)))


def normalized_similarity(expected: str, recovered: str) -> float:
    return float(max(0.0, 1.0 - character_error_rate(expected, recovered)))


@dataclass(frozen=True)
class SignalHealth:
    sample_rate: int
    channels: int
    duration_seconds: float
    peak: float
    rms: float
    clipping_fraction: float
    silence_fraction: float
    truncated: bool
    dc_offset: float
    nan_fraction: float

    @property
    def hard_failures(self) -> tuple[str, ...]:
        failures = []
        if self.duration_seconds <= 0:
            failures.append("empty_audio")
        if self.nan_fraction > 0:
            failures.append("nan_audio")
        if self.truncated:
            failures.append("truncated_audio")
        if self.clipping_fraction > 0.02:
            failures.append("excessive_clipping")
        return tuple(failures)

    @property
    def warnings(self) -> tuple[str, ...]:
        warnings = []
        if self.silence_fraction > 0.65:
            warnings.append("mostly_silent")
        if abs(self.dc_offset) > 0.02:
            warnings.append("dc_offset")
        if self.clipping_fraction > 0:
            warnings.append("clipping")
        return tuple(warnings)


@dataclass(frozen=True)
class SpeakerSimilarity:
    cosine: Optional[float]
    backend: str
    available: bool


@dataclass(frozen=True)
class TimingQA:
    expected_seconds: Optional[float]
    recovered_seconds: Optional[float]
    absolute_error_seconds: Optional[float]
    ratio: Optional[float]


@dataclass(frozen=True)
class TTSQuality:
    expected_text: str
    recovered_text: Optional[str]
    wer: Optional[float]
    cer: Optional[float]
    normalized_similarity: Optional[float]
    speaker_similarity: SpeakerSimilarity
    signal: SignalHealth
    timing: TimingQA
    hard_failures: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    retry_count: int = 0
    metadata: Mapping[str, object] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.hard_failures


def inspect_signal(path: str | Path, *, expected_duration: Optional[float] = None) -> SignalHealth:
    import soundfile as sf

    audio, sample_rate = sf.read(Path(path), always_2d=True, dtype="float32")
    values = np.asarray(audio, dtype=np.float32)
    flat = values.reshape(-1)
    nan_fraction = float(np.mean(~np.isfinite(flat))) if flat.size else 0.0
    clean = np.nan_to_num(flat)
    peak = float(np.max(np.abs(clean))) if clean.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(clean)))) if clean.size else 0.0
    clipping = float(np.mean(np.abs(clean) >= 0.995)) if clean.size else 0.0
    silence = float(np.mean(np.abs(clean) < 10 ** (-45 / 20))) if clean.size else 1.0
    duration = float(values.shape[0] / sample_rate) if sample_rate else 0.0
    truncated = bool(expected_duration is not None and duration < max(0.02, expected_duration * 0.50))
    return SignalHealth(
        sample_rate=int(sample_rate), channels=int(values.shape[1] if values.ndim == 2 else 1),
        duration_seconds=duration, peak=peak, rms=rms, clipping_fraction=clipping,
        silence_fraction=silence, truncated=truncated,
        dc_offset=float(np.mean(clean)) if clean.size else 0.0, nan_fraction=nan_fraction,
    )


def _embedding_from_samples(audio: np.ndarray, sr: int) -> tuple[float, ...]:
    """Build the deterministic MFCC-statistics vector used by QA and live mode."""
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    try:
        import librosa

        mfcc = librosa.feature.mfcc(y=audio, sr=sr, n_mfcc=20, hop_length=max(128, sr // 50))
        values = np.concatenate([mfcc.mean(axis=1), mfcc.std(axis=1)])
    except Exception:
        values = np.array([np.mean(audio), np.std(audio), np.max(np.abs(audio))], dtype=np.float32)
    norm = float(np.linalg.norm(values))
    return tuple(float(x / norm) for x in values) if norm > 0 else tuple(float(x) for x in values)


def speaker_embedding(path: str | Path) -> tuple[float, ...]:
    """Return a compact local speaker embedding (MFCC statistics).

    SpeechBrain ECAPA is supported by callers that provide it, but this
    deterministic MFCC-statistics embedding keeps the default QA self-hostable
    and avoids downloading another model.
    """
    import soundfile as sf

    audio, sr = sf.read(Path(path), dtype="float32")
    return _embedding_from_samples(np.asarray(audio, dtype=np.float32), int(sr))


def speaker_embedding_from_pcm(pcm16: bytes, sample_rate: int = 16_000) -> tuple[float, ...]:
    """Return the same local embedding for a raw PCM16 live phrase.

    Live mode must not write every phrase to disk merely to identify a speaker;
    this helper keeps the feature extraction identical to offline QA while
    accepting the microphone/session PCM representation directly.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")
    values = np.frombuffer(pcm16 or b"", dtype=np.int16).astype(np.float32) / 32768.0
    return _embedding_from_samples(values, int(sample_rate))


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> Optional[float]:
    if not left or not right or len(left) != len(right):
        return None
    a, b = np.asarray(left), np.asarray(right)
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    return float(np.dot(a, b) / denominator) if denominator else None


def evaluate_tts(
    wav_path: str | Path,
    expected_text: str,
    *,
    recovered_text: Optional[str] = None,
    expected_duration: Optional[float] = None,
    reference_wav: Optional[str | Path] = None,
    retry_count: int = 0,
) -> TTSQuality:
    signal = inspect_signal(wav_path, expected_duration=expected_duration)
    wer = word_error_rate(expected_text, recovered_text) if recovered_text is not None else None
    cer = character_error_rate(expected_text, recovered_text) if recovered_text is not None else None
    similarity = normalized_similarity(expected_text, recovered_text) if recovered_text is not None else None
    speaker = SpeakerSimilarity(None, "unavailable", False)
    if reference_wav is not None:
        try:
            speaker = SpeakerSimilarity(cosine_similarity(speaker_embedding(reference_wav), speaker_embedding(wav_path)), "mfcc-statistics", True)
        except Exception:
            pass
    recovered_duration = signal.duration_seconds
    timing = TimingQA(expected_duration, recovered_duration, abs(recovered_duration - expected_duration) if expected_duration else None, recovered_duration / expected_duration if expected_duration and expected_duration > 0 else None)
    hard = list(signal.hard_failures)
    warnings = list(signal.warnings)
    if wer is not None and wer > 0.35:
        hard.append("high_wer")
    elif wer is not None and wer > 0.18:
        warnings.append("soft_wer_warning")
    if expected_duration and timing.ratio is not None and not 0.50 <= timing.ratio <= 1.75:
        hard.append("timing_outlier")
    elif expected_duration and timing.ratio is not None and not 0.75 <= timing.ratio <= 1.25:
        warnings.append("timing_warning")
    if speaker.cosine is not None and speaker.cosine < 0.35:
        hard.append("speaker_mismatch")
    elif speaker.cosine is not None and speaker.cosine < 0.55:
        warnings.append("speaker_similarity_warning")
    return TTSQuality(expected_text, recovered_text, wer, cer, similarity, speaker, signal, timing, tuple(dict.fromkeys(hard)), tuple(dict.fromkeys(warnings)), retry_count)


def synthesize_with_qa_retry(
    synthesize: Callable[[int], str | Path],
    expected_text: str,
    *,
    evaluator: Callable[[str | Path, int], TTSQuality],
    max_extra_retries: int = 1,
) -> tuple[str | Path, TTSQuality]:
    """Retry at most once for a hard failure; soft warnings are retained."""
    attempts = 1 + min(1, max(0, int(max_extra_retries)))
    last_path = None
    last_quality = None
    for attempt in range(attempts):
        last_path = synthesize(attempt)
        last_quality = evaluator(last_path, attempt)
        if not last_quality.hard_failures:
            return last_path, last_quality
    assert last_path is not None and last_quality is not None
    return last_path, last_quality
