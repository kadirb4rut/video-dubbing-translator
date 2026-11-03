"""Pre-TTS candidate diagnostics and protected-token scoring."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping, Optional, Sequence

from .speech_length import SpeechLengthEstimate, estimate_spoken_length


_PROTECTED_RE = re.compile(
    r"(?:\b\d+(?:[.,:/-]\d+)*\b|\b[A-Z]{2,}[A-Za-z0-9-]*\b|\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b)"
)


@dataclass(frozen=True)
class TranslationCandidate:
    text: str
    estimate: SpeechLengthEstimate
    predicted_duration: float
    protected_tokens: tuple[str, ...]
    score: float = 0.0


@dataclass(frozen=True)
class TimingCandidateAssessment:
    """Measured candidate quality after bounded tempo correction."""

    version: str
    text: str
    duration: float
    required_tempo: float
    applied_tempo: float
    adjusted_duration: float
    synchronization_error: float
    borrowed_silence: float
    safe_fit: bool
    ideal_band: bool
    hard_band: bool
    protected_fraction: float


def protected_tokens(
    source_text: str,
    glossary: Optional[Mapping[str, str]] = None,
) -> tuple[str, ...]:
    values = list(_PROTECTED_RE.findall(source_text or ""))
    if glossary:
        for term in glossary:
            if term in source_text and term not in values:
                values.append(term)
    return tuple(dict.fromkeys(values))


def _protected_hits(tokens: Sequence[str], candidate_text: str) -> int:
    lowered = candidate_text.casefold()
    return sum(1 for token in tokens if token.casefold() in lowered)


def score_candidate(
    source_text: str,
    candidate_text: str,
    *,
    target_language: Optional[str],
    speaker_rate: float,
    available_duration: float,
    preferred_duration: Optional[float] = None,
    glossary: Optional[Mapping[str, str]] = None,
) -> TranslationCandidate:
    estimate = estimate_spoken_length(candidate_text, target_language)
    predicted = estimate.spoken_units / speaker_rate if speaker_rate > 0 else float("inf")
    tokens = protected_tokens(source_text, glossary)
    hits = _protected_hits(tokens, candidate_text)
    protection = (hits / len(tokens)) if tokens else 1.0
    target_duration = preferred_duration or available_duration
    timing_error = abs(predicted / target_duration - 1.0) if target_duration > 0 else 10.0
    # Prefer complete, punctuated sentences when timing is otherwise comparable.
    naturalness = 0.1 if candidate_text.rstrip().endswith(('.', '!', '?')) else 0.0
    score = protection * 5.0 + naturalness - timing_error
    return TranslationCandidate(
        text=candidate_text.strip(),
        estimate=estimate,
        predicted_duration=predicted,
        protected_tokens=tokens,
        score=score,
    )


def choose_best_candidate(candidates: Sequence[TranslationCandidate]) -> TranslationCandidate:
    if not candidates:
        raise ValueError("At least one translation candidate is required.")
    # Stable max preserves the first candidate on exact score ties.
    return max(candidates, key=lambda candidate: candidate.score)


def assess_timing_candidate(
    *,
    version: str,
    text: str,
    duration: float,
    preferred_duration: float,
    safe_available_duration: float,
    protected: Sequence[str] = (),
    min_tempo: float = 0.85,
    max_tempo: float = 1.25,
    ideal_min_tempo: float = 0.90,
    ideal_max_tempo: float = 1.10,
) -> TimingCandidateAssessment:
    """Evaluate the result of applying bounded tempo against the preferred target."""

    if duration <= 0 or preferred_duration <= 0 or safe_available_duration < preferred_duration:
        raise ValueError("Invalid candidate timing durations.")
    if min_tempo <= 0 or max_tempo < min_tempo:
        raise ValueError("Invalid hard tempo band.")
    if ideal_min_tempo <= 0 or ideal_max_tempo < ideal_min_tempo:
        raise ValueError("Invalid ideal tempo band.")
    required = duration / preferred_duration
    applied = min(max(required, min_tempo), max_tempo)
    adjusted = duration / applied
    error = abs(adjusted - preferred_duration) / preferred_duration
    borrowed = max(0.0, min(adjusted - preferred_duration, safe_available_duration - preferred_duration))
    safe_fit = adjusted <= safe_available_duration + 1e-6
    hard_band = min_tempo <= required <= max_tempo
    ideal_band = ideal_min_tempo <= required <= ideal_max_tempo
    lowered = text.casefold()
    protected_fraction = (
        sum(token.casefold() in lowered for token in protected) / len(protected)
        if protected else 1.0
    )
    return TimingCandidateAssessment(
        version=version,
        text=text,
        duration=duration,
        required_tempo=required,
        applied_tempo=applied,
        adjusted_duration=adjusted,
        synchronization_error=error,
        borrowed_silence=borrowed,
        safe_fit=safe_fit,
        ideal_band=ideal_band,
        hard_band=hard_band,
        protected_fraction=protected_fraction,
    )


def choose_best_timing_candidate(
    candidates: Sequence[TimingCandidateAssessment],
) -> TimingCandidateAssessment:
    """Choose by semantic safety, bounded fit, naturalness, then sync error."""

    if not candidates:
        raise ValueError("At least one timing candidate is required.")

    def key(candidate: TimingCandidateAssessment):
        return (
            candidate.protected_fraction >= 1.0,
            candidate.safe_fit,
            candidate.ideal_band,
            candidate.hard_band,
            -candidate.synchronization_error,
            -abs(candidate.required_tempo - 1.0),
            -candidate.borrowed_silence,
            candidate.version == "initial",
        )

    return max(candidates, key=key)
