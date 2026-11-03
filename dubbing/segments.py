from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable, Optional, Sequence


@dataclass(frozen=True)
class SpeechSegment:
    """A transcript unit that is synthesized into one timed video segment."""

    index: int
    start: float
    end: float
    text: str
    speaker: Optional[str] = None
    overlap: bool = False
    speaker_ambiguous: bool = False

    def __post_init__(self) -> None:
        if self.index < 0:
            raise ValueError("Segment index must be non-negative.")
        if self.start < 0 or self.end <= self.start:
            raise ValueError(
                f"Invalid segment interval: start={self.start}, end={self.end}."
            )
        if not self.text.strip():
            raise ValueError("Segment text must not be empty.")

    @property
    def duration(self) -> float:
        return self.end - self.start

    def with_speaker(
        self, speaker: Optional[str], *, overlap: bool, ambiguous: bool = False
    ) -> "SpeechSegment":
        return replace(
            self,
            speaker=speaker,
            overlap=overlap,
            speaker_ambiguous=ambiguous,
        )


def from_legacy_lists(
    sentences: Sequence[str], starts: Sequence[float], ends: Sequence[float]
) -> list[SpeechSegment]:
    if not (len(sentences) == len(starts) == len(ends)):
        raise ValueError(
            "Sentence and timestamp counts differ: "
            f"sentences={len(sentences)}, starts={len(starts)}, ends={len(ends)}."
        )
    return [
        SpeechSegment(
            index=index,
            start=float(start),
            end=float(end),
            text=str(text).strip(),
        )
        for index, (text, start, end) in enumerate(zip(sentences, starts, ends))
        if str(text).strip()
    ]


def surrounding_text(
    segments: Sequence[SpeechSegment], index: int
) -> tuple[Optional[str], Optional[str]]:
    previous = segments[index - 1].text if index > 0 else None
    following = segments[index + 1].text if index + 1 < len(segments) else None
    return previous, following


def segment_dicts(segments: Iterable[SpeechSegment]) -> list[dict[str, object]]:
    return [
        {
            "index": segment.index,
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "speaker": segment.speaker,
            "overlap": segment.overlap,
            "speaker_ambiguous": segment.speaker_ambiguous,
        }
        for segment in segments
    ]
