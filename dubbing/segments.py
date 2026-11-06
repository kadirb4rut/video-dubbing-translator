from __future__ import annotations

from dataclasses import dataclass, replace
import math
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
    sentences: Sequence[str],
    starts: Sequence[float],
    ends: Sequence[float],
    *,
    media_duration: Optional[float] = None,
) -> list[SpeechSegment]:
    if not (len(sentences) == len(starts) == len(ends)):
        raise ValueError(
            "Sentence and timestamp counts differ: "
            f"sentences={len(sentences)}, starts={len(starts)}, ends={len(ends)}."
        )
    if media_duration is not None and (
        not math.isfinite(media_duration) or media_duration <= 0
    ):
        raise ValueError("Media duration must be finite and positive.")
    segments: list[SpeechSegment] = []
    for index, (text, start_value, end_value) in enumerate(
        zip(sentences, starts, ends)
    ):
        content = str(text).strip()
        if not content:
            continue
        try:
            start, end = float(start_value), float(end_value)
        except (TypeError, ValueError):
            continue
        if not math.isfinite(start) or not math.isfinite(end):
            continue
        if media_duration is not None:
            if end <= 0 or start >= media_duration:
                continue
            start = max(0.0, start)
            end = min(media_duration, end)
        if end <= start:
            continue
        segments.append(SpeechSegment(index, start, end, content))
    return segments


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
