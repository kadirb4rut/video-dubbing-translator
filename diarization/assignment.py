from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

from dubbing.segments import SpeechSegment


@dataclass(frozen=True)
class DiarizationTurn:
    start: float
    end: float
    speaker: str

    def __post_init__(self) -> None:
        if self.start < 0 or self.end <= self.start:
            raise ValueError(f"Invalid diarization turn: {self.start}-{self.end}.")
        if not self.speaker.strip():
            raise ValueError("Diarization speaker label must not be empty.")


@dataclass(frozen=True)
class DiarizationResult:
    turns: tuple[DiarizationTurn, ...]
    exclusive_turns: tuple[DiarizationTurn, ...] = ()

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> "DiarizationResult":
        def convert(items: object) -> tuple[DiarizationTurn, ...]:
            if not isinstance(items, list):
                return ()
            return tuple(
                DiarizationTurn(
                    start=float(item["start"]),
                    end=float(item["end"]),
                    speaker=str(item["speaker"]),
                )
                for item in items
                if isinstance(item, dict)
            )

        return cls(
            turns=convert(payload.get("turns")),
            exclusive_turns=convert(payload.get("exclusive_turns")),
        )


def interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def validate_speaker_bounds(
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> None:
    values = {
        "num_speakers": num_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }
    for name, value in values.items():
        if value is not None and value < 1:
            raise ValueError(f"{name} must be at least 1.")
    if num_speakers is not None and (min_speakers is not None or max_speakers is not None):
        raise ValueError("Use exact num_speakers or min/max bounds, not both.")
    if min_speakers is not None and max_speakers is not None and min_speakers > max_speakers:
        raise ValueError("min_speakers cannot exceed max_speakers.")


def _overlap_totals(
    segment: SpeechSegment, turns: Iterable[DiarizationTurn]
) -> dict[str, float]:
    totals: dict[str, float] = {}
    for turn in turns:
        seconds = interval_overlap(segment.start, segment.end, turn.start, turn.end)
        if seconds > 0:
            totals[turn.speaker] = totals.get(turn.speaker, 0.0) + seconds
    return totals


def _has_cross_speaker_overlap(
    segment: SpeechSegment, turns: Sequence[DiarizationTurn]
) -> bool:
    relevant = [
        turn
        for turn in turns
        if interval_overlap(segment.start, segment.end, turn.start, turn.end) > 0
    ]
    for index, first in enumerate(relevant):
        for second in relevant[index + 1 :]:
            if first.speaker == second.speaker:
                continue
            overlap_start = max(segment.start, first.start, second.start)
            overlap_end = min(segment.end, first.end, second.end)
            if overlap_end > overlap_start:
                return True
    return False


def _canonical_speaker_map(turns: Sequence[DiarizationTurn]) -> dict[str, str]:
    first_seen: dict[str, float] = {}
    for turn in turns:
        first_seen[turn.speaker] = min(first_seen.get(turn.speaker, turn.start), turn.start)
    ordered = sorted(first_seen, key=lambda label: (first_seen[label], label))
    return {label: f"SPEAKER_{index:02d}" for index, label in enumerate(ordered)}


def assign_speakers(
    segments: Sequence[SpeechSegment], result: DiarizationResult
) -> list[SpeechSegment]:
    """Assign one stable identity per transcript segment.

    Community-1's exclusive diarization is preferred for identity assignment. The
    regular diarization remains authoritative for overlap detection.
    """

    identity_turns = result.exclusive_turns or result.turns
    speaker_map = _canonical_speaker_map(identity_turns or result.turns)
    assigned: list[SpeechSegment] = []
    for segment in segments:
        totals = _overlap_totals(segment, identity_turns)
        overlap = _has_cross_speaker_overlap(segment, result.turns)
        if not totals:
            assigned.append(segment.with_speaker(None, overlap=overlap, ambiguous=True))
            continue

        ranked = sorted(totals.items(), key=lambda item: (-item[1], item[0]))
        best_label, best_seconds = ranked[0]
        second_seconds = ranked[1][1] if len(ranked) > 1 else 0.0
        ambiguous = best_seconds <= 0 or abs(best_seconds - second_seconds) < 1e-6
        speaker = None if ambiguous else speaker_map.get(best_label, best_label)
        assigned.append(
            segment.with_speaker(speaker, overlap=overlap, ambiguous=ambiguous)
        )
    return assigned


def reconcile_or_fallback(
    segments: Sequence[SpeechSegment], result: Optional[DiarizationResult]
) -> list[SpeechSegment]:
    """Keep the original segment-reference behavior when diarization is unavailable."""

    return list(segments) if result is None else assign_speakers(segments, result)
