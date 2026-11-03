"""Bounded, priority-aware admission for inference sharing one local GPU.

CUDA kernels are not preemptible through stream priorities. This gate therefore
serializes expensive model stages at application boundaries and admits short
ASR/TTS work ahead of contextual boundary calls. VoxCPM generation keeps its
existing persistent owner thread; the gate is released between native audio
chunks so higher-priority work can run without moving CUDA Graph state.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading
import time


@dataclass(frozen=True)
class InferenceLease:
    wait_seconds: float


@dataclass(eq=False)
class _Ticket:
    sequence: int
    stage: str
    enqueued_at: float


class PriorityInferenceScheduler:
    """A single-GPU inference gate with bounded waiters and starvation relief."""

    PRIORITY = {'asr': 0, 'tts': 0, 'qa': 1, 'translation': 2, 'boundary': 3}

    def __init__(self, *, enabled=True, max_waiters=8,
                 starvation_after_seconds=4.0, high_priority_burst=8):
        if type(enabled) is not bool or type(max_waiters) is not int or max_waiters < 1:
            raise ValueError('Invalid inference scheduler bounds')
        if starvation_after_seconds <= 0 or high_priority_burst < 1:
            raise ValueError('Invalid inference scheduler fairness policy')
        self.enabled = enabled
        self.max_waiters = max_waiters
        self.starvation_after_seconds = float(starvation_after_seconds)
        self.high_priority_burst = int(high_priority_burst)
        self._condition = threading.Condition()
        self._waiters: list[_Ticket] = []
        self._next_sequence = 0
        self._active = False
        self._high_priority_streak = 0
        self._local = threading.local()

    def reset_thread_metrics(self):
        self._local.waits = {}

    def thread_metrics(self):
        return {stage: round(seconds, 6)
                for stage, seconds in getattr(self._local, 'waits', {}).items()}

    def _next_ticket(self, now):
        if not self._waiters:
            return None
        high = [ticket for ticket in self._waiters
                if self.PRIORITY[ticket.stage] == 0]
        aged = [ticket for ticket in self._waiters
                if now-ticket.enqueued_at >= self.starvation_after_seconds]
        if aged and self._high_priority_streak >= self.high_priority_burst:
            # After a short burst, service the oldest aged request. This keeps
            # boundary evaluation moving while bounding how long ASR/TTS wait.
            return min(aged, key=lambda ticket: ticket.sequence)
        pool = high or self._waiters
        return min(pool, key=lambda ticket: (self.PRIORITY[ticket.stage], ticket.sequence))

    @contextmanager
    def lease(self, stage):
        if stage not in self.PRIORITY:
            raise ValueError(f'Unknown inference stage: {stage}')
        if not self.enabled:
            yield InferenceLease(0.0)
            return

        with self._condition:
            while len(self._waiters) >= self.max_waiters:
                self._condition.wait()
            ticket = _Ticket(self._next_sequence, stage, time.perf_counter())
            self._next_sequence += 1
            self._waiters.append(ticket)
            self._condition.notify_all()
            while self._active or self._next_ticket(time.perf_counter()) is not ticket:
                self._condition.wait()
            self._waiters.remove(ticket)
            self._active = True
            acquired_at = time.perf_counter()
            wait_seconds = max(0.0, acquired_at-ticket.enqueued_at)
            if self.PRIORITY[stage] == 0:
                self._high_priority_streak += 1
            else:
                self._high_priority_streak = 0
            waits = getattr(self._local, 'waits', None)
            if waits is None:
                waits = self._local.waits = {}
            waits[stage] = waits.get(stage, 0.0) + wait_seconds

        try:
            yield InferenceLease(wait_seconds)
        finally:
            with self._condition:
                self._active = False
                self._condition.notify_all()
