"""Bounded READ/WAIT/WRITE coordination for live source chunks.

ASR and the source endpoint policy own semantic boundaries. Translation runs
ahead only for one bounded, confirmed source candidate, with stable following
context supplied as background. A draft can be written only if its complete
source word/timestamp interval exactly matches the source interval the endpoint
controller commits; no target substring is split or assigned by token counts.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .adaptive import Word, normalized, source_text


def _source_prefix(left: Sequence[Word], right: Sequence[Word]) -> bool:
    """Return whether left is the same time-aligned prefix of right."""
    if not left or len(left) >= len(right):
        return False
    for a, b in zip(left, right):
        if normalized(a.text) != normalized(b.text):
            return False
        if abs(a.end - b.end) > .35 or abs(a.start - b.start) > .5:
            return False
    return True

@dataclass(frozen=True)
class TargetHypothesis:
    source_words: tuple[Word, ...]
    source_text: str
    target_text: str
    draft: dict
    observed_at: float
    source_asr_completed_at: float | None


@dataclass(frozen=True)
class SimultaneousCommit:
    source_words: tuple[Word, ...]
    source_text: str
    target_text: str
    draft: dict
    source_start: float
    source_end: float
    evidence: dict
    source_asr_completed_at: float | None


class AdaptiveSimultaneousTranslationController:
    """READ growing confirmed source, WAIT for a safe source chunk, then WRITE.

    Translation hypotheses for arbitrary growing prefixes can revise earlier
    wording, so they are never sliced or spoken on target-string agreement
    alone. The live session supplies an independently confirmed source clause,
    sentence, or pause; this controller writes only the complete translation
    of that exact source interval. The endpoint controller supplies each
    mode's acoustic lookahead and hold; target-side local agreement is not
    treated as proof that a partial translation has completed a thought.
    """

    POLICIES = {
        'quality': {'query_interval': 1.8, 'right_context': 2.0},
        'balanced': {'query_interval': 1.25, 'right_context': 1.2},
        'low_latency': {'query_interval': .85, 'right_context': .75},
    }

    def __init__(self, mode='balanced', *, history_limit=12):
        if mode not in self.POLICIES:
            raise ValueError('Mode must be quality, balanced or low_latency')
        if type(history_limit) is not int or history_limit < 4:
            raise ValueError('Target hypothesis history must be bounded and at least four')
        self.mode = mode
        self.policy = self.POLICIES[mode]
        self.history_limit = history_limit
        self.hypotheses: list[TargetHypothesis] = []
        self.last_query_at = float('-inf')
        self.last_query_words: tuple[Word, ...] = ()
        self.committed_source_end = -1.0
        self.committed_count = 0
        self.rejected_revision_count = 0

    def refresh_source(self, stable_words: Sequence[Word]) -> None:
        """Drop target evidence whose source prefix was revised by ASR."""
        stable = tuple(stable_words)
        retained = [item for item in self.hypotheses
                    if len(item.source_words) <= len(stable)
                    and all(normalized(a.text) == normalized(b.text)
                            and abs(a.end-b.end) <= .35
                            and abs(a.start-b.start) <= .5
                            for a, b in zip(item.source_words, stable))]
        if len(retained) != len(self.hypotheses):
            self.rejected_revision_count += len(self.hypotheses)-len(retained)
            self.hypotheses = retained

    def should_query(self, stable_words: Sequence[Word], now: float) -> bool:
        """Throttle redundant model calls while ensuring a growing read stream."""
        words = tuple(stable_words)
        if len(words) < 2 or words[-1].end-words[0].start < .65:
            return False
        if self.last_query_words:
            same_snapshot = (len(words) == len(self.last_query_words) and all(
                normalized(a.text) == normalized(b.text)
                and abs(a.end-b.end) <= .35 and abs(a.start-b.start) <= .5
                for a, b in zip(self.last_query_words, words)))
            if same_snapshot:
                return False
            if not _source_prefix(self.last_query_words, words):
                # A stable-prefix revision invalidates both cadence and evidence.
                self.refresh_source(words)
                self.last_query_words = ()
                self.last_query_at = float('-inf')
        if now-self.last_query_at < self.policy['query_interval']:
            return False
        return True

    def mark_query(self, stable_words: Sequence[Word], now: float) -> None:
        words = tuple(stable_words)
        if not words or len(words) > 256 or len(source_text(words)) > 8000:
            raise ValueError('Invalid bounded stable source prefix')
        self.last_query_words = words
        self.last_query_at = float(now)

    def observe(self, stable_words: Sequence[Word], draft: dict, now: float,
                *, source_asr_completed_at: float | None = None) -> None:
        """Record one real translation result for its exact source snapshot."""
        words = tuple(stable_words)
        text = source_text(words)
        target = draft.get('text') if isinstance(draft, dict) else None
        if (not words or len(words) > 256 or len(text) > 8000 or
                not isinstance(target, str) or not target.strip() or len(target) > 8000):
            raise ValueError('Invalid bounded simultaneous translation hypothesis')
        if words[0].start <= self.committed_source_end + .025:
            raise ValueError('Target hypothesis overlaps already committed source')
        self.refresh_source(words)
        signature = tuple((normalized(w.text), round(w.start, 1), round(w.end, 1)) for w in words)
        self.hypotheses = [h for h in self.hypotheses if tuple(
            (normalized(w.text), round(w.start, 1), round(w.end, 1))
            for w in h.source_words) != signature]
        self.hypotheses.append(TargetHypothesis(words, text, target.strip(), draft,
                                                float(now), source_asr_completed_at))
        if len(self.hypotheses) > self.history_limit:
            self.hypotheses = self.hypotheses[-self.history_limit:]

    def source_boundary_proposal(self, current_stable_words: Sequence[Word], *,
                                 boundary_kind: str | None,
                                 right_context_seconds: float) -> SimultaneousCommit | None:
        """Return a prefetched translation only for the exact committed chunk.

        The source endpoint controller owns boundary semantics and ASR
        confirmation. Its normal hold/pause policy decides when ownership
        transfers; this method only verifies that the prefetched translation
        belongs to the exact candidate which that policy committed.
        """
        current = tuple(current_stable_words)
        if not current or boundary_kind not in {'clause', 'sentence', 'pause', 'eos'}:
            return None
        right_context_seconds = max(0.0, float(right_context_seconds))
        current_text = source_text(current)
        match = None
        for item in reversed(self.hypotheses):
            if (len(item.source_words) == len(current)
                    and item.source_text == current_text
                    and all(normalized(a.text) == normalized(b.text)
                            and abs(a.end-b.end) <= .35
                            and abs(a.start-b.start) <= .5
                            for a, b in zip(item.source_words, current))):
                match = item
                break
        if match is None or current[0].start <= self.committed_source_end + .025:
            return None
        evidence = {
            'policy': 'confirmed_source_boundary_prefetch',
            'mode': self.mode,
            'source_boundary_kind': boundary_kind,
            'right_context_seconds': round(right_context_seconds, 3),
            'mode_right_context_target_seconds': self.policy['right_context'],
            'right_context_policy_target_observed_met': (
                right_context_seconds >= self.policy['right_context']),
            'source_interval_exact': True,
            'target_text_split_or_aligned': False,
            'semantic_correctness_certified': False,
        }
        return SimultaneousCommit(current, current_text, match.target_text,
            match.draft, current[0].start, current[-1].end, evidence,
            match.source_asr_completed_at)

    def accept(self, commit: SimultaneousCommit) -> None:
        """Advance source ownership exactly once after downstream admission."""
        if commit.source_end <= self.committed_source_end + .025:
            raise ValueError('Simultaneous source ownership is not monotonic')
        self.committed_source_end = commit.source_end
        self.committed_count += 1
        # No later target text is split or guessed; re-read the remaining
        # confirmed source with the newly committed source as left context.
        self.hypotheses.clear()
        self.last_query_words = ()
        self.last_query_at = float('-inf')

    def advance_source_boundary(self, source_end: float) -> None:
        """Discard speculative target reads after the ordinary endpoint commits."""
        if source_end <= self.committed_source_end + .025:
            raise ValueError('Source ownership is not monotonic')
        self.committed_source_end = float(source_end)
        self.hypotheses.clear()
        self.last_query_words = ()
        self.last_query_at = float('-inf')

    def reset_uncommitted(self) -> None:
        """Clear speculative evidence after language/source context changes."""
        self.hypotheses.clear()
        self.last_query_words = ()
        self.last_query_at = float('-inf')
