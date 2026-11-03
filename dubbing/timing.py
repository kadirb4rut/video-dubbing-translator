"""Timing groups and safe duration budgets for speaker-aware dubbing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from .segments import SpeechSegment


@dataclass(frozen=True)
class TimingGroupConfig:
    """Conservative, configurable grouping defaults."""

    max_gap_seconds: float = 0.75
    micro_segment_seconds: float = 0.85
    tight_gap_seconds: float = 0.35
    max_group_duration_seconds: float = 14.0

    def __post_init__(self) -> None:
        if self.max_gap_seconds < 0 or self.micro_segment_seconds <= 0:
            raise ValueError("Timing grouping thresholds must be positive.")
        if self.tight_gap_seconds < 0 or self.max_group_duration_seconds <= 0:
            raise ValueError("Timing grouping thresholds must be valid.")
        if self.tight_gap_seconds > self.max_gap_seconds:
            raise ValueError("tight_gap_seconds cannot exceed max_gap_seconds.")


@dataclass(frozen=True)
class DurationBudget:
    """The usable timing window for one group.

    ``available_duration`` includes only safe trailing silence.  Leading silence
    remains an onset anchor and is recorded for diagnostics rather than borrowed.
    """

    speech_start: float
    speech_end: float
    speech_duration: float
    internal_pauses: tuple[float, ...]
    previous_speech_end: float
    next_speech_start: float
    safe_leading_silence: float
    safe_trailing_silence: float
    available_duration: float
    max_safe_expanded_timing_window: float

    @property
    def preferred_duration(self) -> float:
        """Original active speech duration, the primary sync target."""

        return self.speech_duration

    @property
    def safe_available_duration(self) -> float:
        """Hard maximum window including only safe trailing silence."""

        return self.available_duration

    @property
    def render_end(self) -> float:
        return self.speech_start + self.max_safe_expanded_timing_window


@dataclass(frozen=True)
class TimingGroup:
    """One natural dubbing unit retaining all original segment metadata."""

    index: int
    segments: tuple[SpeechSegment, ...]
    start: float
    end: float
    text: str
    internal_pauses: tuple[float, ...]
    budget: Optional[DurationBudget] = None

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def source_duration(self) -> float:
        return self.duration

    @property
    def speaker(self) -> Optional[str]:
        speakers = {segment.speaker for segment in self.segments}
        return next(iter(speakers)) if len(speakers) == 1 else None

    @property
    def overlap(self) -> bool:
        return any(segment.overlap for segment in self.segments)

    @property
    def speaker_ambiguous(self) -> bool:
        return any(segment.speaker_ambiguous for segment in self.segments)

    @property
    def original_segment_indices(self) -> tuple[int, ...]:
        return tuple(segment.index for segment in self.segments)

    @property
    def source_words(self) -> int:
        return sum(len(segment.text.split()) for segment in self.segments)

    def as_reference_segment(self) -> SpeechSegment:
        """Return a traceable proxy for the existing reference manager."""

        first = self.segments[0]
        return SpeechSegment(
            index=first.index,
            start=self.start,
            end=self.end,
            text=self.text,
            speaker=self.speaker,
            overlap=self.overlap,
            speaker_ambiguous=self.speaker_ambiguous,
        )


def _join_text(segments: Sequence[SpeechSegment]) -> str:
    # Existing Whisper text already contains sentence punctuation.  Keeping it
    # and inserting a space preserves clause boundaries for VoxCPM2 prosody.
    return " ".join(segment.text.strip() for segment in segments).strip()


def _can_merge(previous: SpeechSegment, current: SpeechSegment, config: TimingGroupConfig) -> bool:
    if previous.speaker is None or current.speaker is None:
        return False
    if previous.speaker != current.speaker:
        return False
    if previous.overlap or current.overlap:
        return False
    if previous.speaker_ambiguous or current.speaker_ambiguous:
        return False
    gap = current.start - previous.end
    if gap < 0 or gap > config.max_gap_seconds:
        return False
    combined_duration = current.end - previous.start
    if combined_duration > config.max_group_duration_seconds:
        return False
    return (
        gap <= config.tight_gap_seconds
        or previous.duration <= config.micro_segment_seconds
        or current.duration <= config.micro_segment_seconds
    )


def build_timing_groups(
    segments: Sequence[SpeechSegment],
    *,
    config: TimingGroupConfig = TimingGroupConfig(),
) -> list[TimingGroup]:
    """Merge only safe adjacent same-speaker segments.

    The result is deterministic and never crosses a speaker change, overlap, or
    ambiguous boundary.  All source ``SpeechSegment`` objects are retained.
    """

    if not segments:
        return []
    groups: list[list[SpeechSegment]] = []
    for segment in segments:
        if groups and _can_merge(groups[-1][-1], segment, config):
            groups[-1].append(segment)
        else:
            groups.append([segment])
    return [
        TimingGroup(
            index=index,
            segments=tuple(items),
            start=items[0].start,
            end=items[-1].end,
            text=_join_text(items),
            internal_pauses=tuple(
                max(0.0, right.start - left.end)
                for left, right in zip(items, items[1:])
            ),
        )
        for index, items in enumerate(groups)
    ]


def calculate_duration_budgets(
    groups: Sequence[TimingGroup],
    media_duration: Optional[float] = None,
) -> list[TimingGroup]:
    """Attach a non-overlapping safe timing window to every group."""

    if not groups:
        return []
    if media_duration is None:
        media_duration = groups[-1].end
    if media_duration < groups[-1].end:
        raise ValueError("Media duration cannot precede the final timing group.")
    budgeted: list[TimingGroup] = []
    for index, group in enumerate(groups):
        previous_end = groups[index - 1].end if index else 0.0
        next_start = groups[index + 1].start if index + 1 < len(groups) else media_duration
        safe_leading = max(0.0, group.start - previous_end)
        safe_trailing = max(0.0, next_start - group.end)
        available = group.duration + safe_trailing
        budget = DurationBudget(
            speech_start=group.start,
            speech_end=group.end,
            speech_duration=group.duration,
            internal_pauses=group.internal_pauses,
            previous_speech_end=previous_end,
            next_speech_start=next_start,
            safe_leading_silence=safe_leading,
            safe_trailing_silence=safe_trailing,
            available_duration=available,
            max_safe_expanded_timing_window=max(group.duration, available),
        )
        budgeted.append(
            TimingGroup(
                index=group.index,
                segments=group.segments,
                start=group.start,
                end=group.end,
                text=group.text,
                internal_pauses=group.internal_pauses,
                budget=budget,
            )
        )
    return budgeted
