"""Lightweight local spoken-length estimation and speaker-rate calibration."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Optional


_WORD_RE = re.compile(r"[\w]+(?:['’/-][\w]+)*", re.UNICODE)
_VOWELS = set("aeiouyáàâäãåæeéèêëiíìîïoóòôöõuúùûüyÿıöüâîô")


@dataclass(frozen=True)
class SpeechLengthEstimate:
    words: int
    characters: int
    syllables: int
    phonemes: int
    method: str = "vowel-group-fallback"

    @property
    def spoken_units(self) -> int:
        return self.syllables


def _word_syllables(word: str) -> int:
    letters = [char.lower() for char in word if char.isalpha()]
    if not letters:
        return 0
    groups = 0
    previous_vowel = False
    for char in letters:
        vowel = char in _VOWELS
        if vowel and not previous_vowel:
            groups += 1
        previous_vowel = vowel
    return max(1, groups)


def estimate_spoken_length(text: str, language: Optional[str] = None) -> SpeechLengthEstimate:
    """Estimate target speech length without a network or heavyweight dependency.

    The estimator deliberately exposes both syllable-like units and a coarse
    phoneme proxy.  Language-specific phonemizers can replace this implementation
    later without changing timing-controller interfaces.
    """

    words = _WORD_RE.findall(text or "")
    characters = len(re.sub(r"\s+", "", text or ""))
    syllables = sum(_word_syllables(word) for word in words)
    # For scripts without Latin vowel groups, each non-space grapheme is a useful
    # conservative proxy for a spoken unit.
    if not syllables and characters:
        syllables = characters
    phonemes = max(syllables, round(syllables * 2.1))
    return SpeechLengthEstimate(
        words=len(words),
        characters=characters,
        syllables=max(0, syllables),
        phonemes=phonemes,
    )


class SpeakerRateEstimator:
    """Robust EWMA of target spoken units per second per speaker."""

    def __init__(
        self,
        *,
        fallback_units_per_second: float = 4.8,
        alpha: float = 0.25,
        min_sample_units: int = 5,
        min_sample_seconds: float = 0.7,
    ) -> None:
        if fallback_units_per_second <= 0 or not 0 < alpha <= 1:
            raise ValueError("Invalid speaker-rate estimator settings.")
        self.fallback_units_per_second = fallback_units_per_second
        self.alpha = alpha
        self.min_sample_units = min_sample_units
        self.min_sample_seconds = min_sample_seconds
        self._rates: dict[str, float] = {}

    def rate_for(self, speaker: Optional[str]) -> float:
        return self._rates.get(speaker or "__fallback__", self.fallback_units_per_second)

    def predict_duration(self, estimate: SpeechLengthEstimate, speaker: Optional[str]) -> float:
        return estimate.spoken_units / self.rate_for(speaker) if estimate.spoken_units else 0.0

    def update(
        self,
        speaker: Optional[str],
        estimate: SpeechLengthEstimate,
        actual_seconds: float,
        *,
        available_seconds: Optional[float] = None,
        min_tempo: float = 0.85,
        max_tempo: float = 1.25,
    ) -> bool:
        """Update only from normal-quality, non-micro, non-stretched samples."""

        if (
            estimate.spoken_units < self.min_sample_units
            or actual_seconds < self.min_sample_seconds
            or actual_seconds <= 0
        ):
            return False
        if available_seconds and available_seconds > 0:
            required_tempo = actual_seconds / available_seconds
            if required_tempo < min_tempo or required_tempo > max_tempo:
                return False
        observed = estimate.spoken_units / actual_seconds
        if not 1.5 <= observed <= 10.0:
            return False
        key = speaker or "__fallback__"
        current = self._rates.get(key, self.fallback_units_per_second)
        self._rates[key] = current + self.alpha * (observed - current)
        return True
