from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping


class JobState(StrEnum):
    QUEUED = "queued"
    PROVISIONING = "provisioning"
    DOWNLOADING = "downloading"
    SEPARATING_AUDIO = "separating_audio"
    TRANSCRIBING = "transcribing"
    TRANSLATING = "translating"
    SYNTHESIZING = "synthesizing"
    MIXING = "mixing"
    LIP_SYNCING = "lip_syncing"
    UPLOADING = "uploading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_STATES = {JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED}
ALLOWED_TRANSITIONS: Mapping[JobState, set[JobState]] = {
    JobState.QUEUED: {JobState.PROVISIONING, JobState.CANCELLED, JobState.FAILED},
    JobState.PROVISIONING: {JobState.DOWNLOADING, JobState.FAILED, JobState.CANCELLED},
    JobState.DOWNLOADING: {JobState.SEPARATING_AUDIO, JobState.FAILED, JobState.CANCELLED},
    JobState.SEPARATING_AUDIO: {JobState.TRANSCRIBING, JobState.FAILED, JobState.CANCELLED},
    JobState.TRANSCRIBING: {JobState.TRANSLATING, JobState.FAILED, JobState.CANCELLED},
    JobState.TRANSLATING: {JobState.SYNTHESIZING, JobState.FAILED, JobState.CANCELLED},
    JobState.SYNTHESIZING: {JobState.MIXING, JobState.LIP_SYNCING, JobState.FAILED, JobState.CANCELLED},
    JobState.MIXING: {JobState.LIP_SYNCING, JobState.UPLOADING, JobState.FAILED, JobState.CANCELLED},
    JobState.LIP_SYNCING: {JobState.UPLOADING, JobState.FAILED, JobState.CANCELLED},
    JobState.UPLOADING: {JobState.COMPLETED, JobState.FAILED},
    JobState.COMPLETED: set(), JobState.FAILED: set(), JobState.CANCELLED: set(),
}


@dataclass(frozen=True)
class CostProfile:
    """Measured provider economics; values are populated by benchmark jobs, not guessed in UI code."""

    provider: str
    gpu_profile: str
    startup_seconds: float
    processing_minutes_per_gpu_hour: float
    hourly_price_usd: float
    safety_multiplier: float = 1.35

    @property
    def cost_per_media_minute(self) -> float:
        if self.processing_minutes_per_gpu_hour <= 0:
            raise ValueError("processing_minutes_per_gpu_hour must be positive")
        return (self.hourly_price_usd / self.processing_minutes_per_gpu_hour) * self.safety_multiplier


def estimate_credits(
    duration_minutes: float,
    operation: str,
    profiles: Mapping[str, CostProfile],
    *,
    lip_sync: bool = False,
    quality_multiplier: float = 1.0,
) -> int:
    if duration_minutes <= 0:
        raise ValueError("duration_minutes must be positive")
    if operation not in profiles:
        raise KeyError(f"No measured cost profile for {operation!r}")
    base = duration_minutes * profiles[operation].cost_per_media_minute
    if lip_sync:
        base += duration_minutes * profiles.get("lip_sync", profiles[operation]).cost_per_media_minute
    return max(1, round(base * quality_multiplier))


def transition(current: JobState, target: JobState) -> JobState:
    if target not in ALLOWED_TRANSITIONS[current]:
        raise ValueError(f"Invalid job transition: {current.value} -> {target.value}")
    return target


def should_recredit(*, infrastructure_failure: bool, user_cancelled: bool = False) -> bool:
    """Credits are only finalized on success; infra failures release reservations."""
    return infrastructure_failure and not user_cancelled
