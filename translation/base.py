from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Mapping, Optional
import unicodedata


def glossary_preservation(source: str, target: str, glossary=None) -> dict[str, bool]:
    """Check explicit term mappings without rewriting or appending speech.

    This is a literal glossary contract, not a semantic translation score.
    Localized units/names may be meaningful but violate an explicit mapping.
    """
    def canonical(text):
        return unicodedata.normalize('NFC', text).casefold()
    source, target = canonical(source), canonical(target)
    return {term: canonical(value) in target for term, value in (glossary or {}).items()
            if canonical(term) in source}


@dataclass(frozen=True)
class TranslationContext:
    previous_text: Optional[str] = None
    next_text: Optional[str] = None
    speaker: Optional[str] = None
    available_duration_seconds: Optional[float] = None
    target_spoken_units: Optional[float] = None
    source_pause_seconds: Optional[float] = None
    candidate_style: Optional[str] = None
    protected_tokens: tuple[str, ...] = ()
    preferred_duration_seconds: Optional[float] = None
    safe_available_duration_seconds: Optional[float] = None
    refinement_direction: Optional[str] = None
    previous_translation: Optional[str] = None


class Translator(ABC):
    @abstractmethod
    def translate(
        self,
        source_text: str,
        source_language: Optional[str],
        target_language: str,
        *,
        context: Optional[TranslationContext] = None,
        glossary: Optional[Mapping[str, str]] = None,
    ) -> str:
        raise NotImplementedError

    @abstractmethod
    def rewrite_for_duration(
        self,
        source_text: str,
        current_translation: str,
        source_language: Optional[str],
        target_language: str,
        target_duration_seconds: float,
        actual_duration_seconds: float,
        *,
        context: Optional[TranslationContext] = None,
        glossary: Optional[Mapping[str, str]] = None,
    ) -> str:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def __enter__(self) -> "Translator":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()
