"""Bounded admission policy for expensive contextual boundary inference."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class BoundaryAdmission:
    admitted: bool
    reason: str
    urgent: bool
    source_age_seconds: float
    source_advance_seconds: float
    changed_units: int
    minimum_source_gap_seconds: float
    urgent_source_gap_seconds: float
    requests_per_source_minute: int


class QwenBoundaryAdmission:
    """Admit Qwen only for new evidence, and yield to ASR/TTS under pressure.

    Source words and media timestamps are the revision identity. The controller
    keeps only the last admitted revision and a one-minute source-time window;
    repeated/deferred requests cannot create an unbounded queue.
    """

    POLICIES = {
        'quality': {'minimum_gap': 5.0, 'minimum_units': 3, 'urgent_fraction': .72,
                    'minimum_context_advance': 1.5, 'nonurgent_pairs_per_source_minute': 3,
                    'urgent_pairs_per_source_minute': 18},
        'balanced': {'minimum_gap': 4.0, 'minimum_units': 2, 'urgent_fraction': .60,
                     'minimum_context_advance': 1.0, 'nonurgent_pairs_per_source_minute': 4,
                     'urgent_pairs_per_source_minute': 24},
        'low_latency': {'minimum_gap': 2.5, 'minimum_units': 1, 'urgent_fraction': .45,
                        'minimum_context_advance': .6, 'nonurgent_pairs_per_source_minute': 6,
                        'urgent_pairs_per_source_minute': 36},
    }

    def __init__(self, mode='balanced'):
        if mode not in self.POLICIES:
            raise ValueError('Mode must be quality, balanced or low_latency')
        self.mode = mode
        self.last_signature = None
        self.last_units = ()
        self.last_candidate_id = None
        self.last_source_time = None
        self.last_source_age = None
        self.last_urgent_age = None
        self.last_pair_seconds = 0.0
        self._admitted_source_times = deque()

    def record_pair_seconds(self, seconds):
        if isinstance(seconds, (int, float)) and math.isfinite(seconds) and seconds >= 0:
            self.last_pair_seconds = float(seconds)

    def _clear_source(self):
        self.last_signature = None
        self.last_units = ()
        self.last_candidate_id = None
        self.last_source_time = None
        self.last_source_age = None
        self.last_urgent_age = None
        self.last_pair_seconds = 0.0
        self._admitted_source_times.clear()

    def admit(self, *, signature, source_units, candidate_id, source_time,
              source_age_seconds, source_budget_seconds, asr_queue_depth=0,
              asr_queue_capacity=100, tts_inflight=False, phrase_queue_depth=0,
              ready_speech_seconds=0.0, previous_pair_seconds=0.0,
              rate_window_time_seconds=None):
        policy = self.POLICIES[self.mode]
        rate_window_time = (source_time if rate_window_time_seconds is None
                            else rate_window_time_seconds)
        values = (source_time, source_age_seconds, source_budget_seconds,
                  rate_window_time, ready_speech_seconds, previous_pair_seconds)
        if (not all(isinstance(v, (int, float)) and math.isfinite(v) for v in values)
                or source_age_seconds < 0 or source_budget_seconds <= 0
                or ready_speech_seconds < 0 or previous_pair_seconds < 0
                or type(asr_queue_depth) is not int or asr_queue_depth < 0
                or type(asr_queue_capacity) is not int or asr_queue_capacity < 1
                or type(phrase_queue_depth) is not int or phrase_queue_depth < 0
                or type(tts_inflight) is not bool):
            raise ValueError('Invalid boundary admission telemetry')

        source_time = float(source_time)
        rate_window_time = float(rate_window_time)
        source_age_seconds = float(source_age_seconds)
        if (self.last_source_time is not None
                and source_time < self.last_source_time - .35):
            # A source seek/restart starts a distinct evidence timeline.
            self._clear_source()

        urgent_threshold = source_budget_seconds * policy['urgent_fraction']
        urgent = source_age_seconds >= urgent_threshold
        min_gap = max(policy['minimum_gap'], min(8.0, 2.5 * previous_pair_seconds))
        # Urgent cuts need the measured model-pair cooldown, not the slower
        # nonurgent mode cadence. Stable source advancement, ASR priority and
        # a mode-specific source-minute cap keep this responsive without
        # allowing provisional revisions to build an unbounded Qwen queue.
        urgent_min_gap = max(.6, min(8.0, 1.5 * previous_pair_seconds))

        def decision(admitted, reason, advance=0.0, changed=0):
            return BoundaryAdmission(admitted, reason, urgent, source_age_seconds,
                advance, changed, min_gap, urgent_min_gap,
                self._requests_per_minute(rate_window_time))

        if signature == self.last_signature:
            return decision(False, 'unchanged_source_revision')

        changed_units = len(source_units)
        source_advance = 0.0
        if self.last_signature is not None:
            common = 0
            for before, after in zip(self.last_units, source_units):
                if before != after:
                    break
                common += 1
            changed_units = max(len(source_units) - common, len(self.last_units) - common)
            previous_source_time = (self.last_source_time if self.last_source_time is not None
                                    else source_time)
            source_advance = max(0.0, source_time - previous_source_time)
            candidate_changed = candidate_id != self.last_candidate_id
            meaningful = (changed_units >= policy['minimum_units']
                          or source_advance >= policy['minimum_context_advance']
                          or candidate_changed)
            if not meaningful and not urgent:
                return decision(False, 'insufficient_new_source_evidence', source_advance, changed_units)

            gap = max(0.0, source_time - previous_source_time)
            urgent_gap_due = (urgent and (self.last_urgent_age is None
                              or source_age_seconds - self.last_urgent_age >= urgent_min_gap - 1e-6))
            revised_evidence = (changed_units >= policy['minimum_units']
                                or (candidate_changed and changed_units > 0))
            if (gap < min_gap and not urgent_gap_due
                    and (urgent or not revised_evidence)):
                return decision(False, 'measured_boundary_rate_budget', source_advance, changed_units)

        # A stalled stable-ASR timestamp must not freeze the rate window while
        # VAD confirms that live source audio continues to advance.
        self._trim_rate_window(rate_window_time)
        # Keep the ordinary Qwen budget bounded, but never let a soft per-minute
        # cap strand an old source prefix. Urgent calls still obey the measured
        # pair cooldown above, so one pending decode can issue at most one pair
        # at a time and cannot create an inference queue.
        request_budget = (policy['urgent_pairs_per_source_minute'] if urgent else
                          policy['nonurgent_pairs_per_source_minute'])
        if len(self._admitted_source_times) >= request_budget:
            reason = ('urgent_source_minute_inference_budget' if urgent else
                      'source_minute_inference_budget')
            return decision(False, reason, source_advance, changed_units)

        audio_backlog = ready_speech_seconds >= 3.0
        asr_backlog = asr_queue_depth >= min(25, max(10, asr_queue_capacity // 4))
        if asr_backlog:
            return decision(False, 'asr_queue_has_priority', source_advance, changed_units)
        if not urgent and (tts_inflight or phrase_queue_depth > 0 or audio_backlog):
            return decision(False, 'approved_tts_or_audio_backlog_has_priority', source_advance, changed_units)

        self.last_signature = signature
        self.last_units = tuple(source_units)
        self.last_candidate_id = candidate_id
        self.last_source_time = source_time
        self.last_source_age = source_age_seconds
        if urgent:
            self.last_urgent_age = source_age_seconds
        self._admitted_source_times.append(rate_window_time)
        self._trim_rate_window(rate_window_time)
        return decision(True, 'urgent_uncertainty' if urgent else 'new_contextual_evidence',
                        source_advance, changed_units)

    def _trim_rate_window(self, source_time):
        while self._admitted_source_times and source_time - self._admitted_source_times[0] > 60.0:
            self._admitted_source_times.popleft()

    def _requests_per_minute(self, source_time):
        self._trim_rate_window(float(source_time))
        return len(self._admitted_source_times)
