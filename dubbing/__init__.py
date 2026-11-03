"""Core, model-independent dubbing data structures and orchestration helpers."""

from .segments import SpeechSegment
from .timing import DurationBudget, TimingGroup, TimingGroupConfig

__all__ = ["SpeechSegment", "TimingGroup", "TimingGroupConfig", "DurationBudget"]
