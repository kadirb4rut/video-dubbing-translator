"""Bounded learned READ/WAIT/WRITE proposals for unsegmented source speech.

The translation model proposes both an owned source frontier and its complete
target chunk. A separate gate accepts it only after the same source interval
and complete target text recur in two strictly growing stable-ASR snapshots.
No target substring or common prefix is sliced or assigned to source words.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from typing import Sequence

from .adaptive import Word, normalized, source_text
from .commitment import CommitmentGate, SourceUnit, VerifiedUnit


@dataclass(frozen=True)
class ReadWaitWritePolicy:
    query_interval_seconds: float
    minimum_chunk_seconds: float
    preferred_chunk_seconds: float
    source_lookahead_seconds: float
    minimum_right_context_words: int = 2
    observations: int = 2


@dataclass(frozen=True)
class ReadWaitWriteCommit:
    verified: VerifiedUnit
    source_words: tuple[Word, ...]
    observation_id: int
    evidence: dict


class AdaptiveReadWaitWriteController:
    """Select model queries and verify target stability independently.

    Chunk duration is a preference/minimum for eligible model proposals, never
    a timer that cuts source at a fixed point. The model may choose a later
    eligible timestamp boundary or wait for more speech.
    """

    POLICIES = {
        'quality': ReadWaitWritePolicy(1.8, 2.0, 5.5, 1.2, 3),
        'balanced': ReadWaitWritePolicy(1.2, 1.4, 4.0, .8, 2),
        'low_latency': ReadWaitWritePolicy(.8, 1.0, 2.8, .5, 2),
    }
    MAX_SOURCE_UNITS = 256
    MAX_SOURCE_CHARACTERS = 8000

    def __init__(self, mode='balanced'):
        if mode not in self.POLICIES:
            raise ValueError('Mode must be quality, balanced or low_latency')
        self.mode = mode
        self.policy = self.POLICIES[mode]
        self.gate = CommitmentGate(observations=self.policy.observations,
            lookahead_seconds=self.policy.source_lookahead_seconds,
            sentence_format_agreement=True)
        self.last_query_at = float('-inf')
        self.last_query_words: tuple[Word, ...] = ()
        self.next_observation_id = 0
        self.invalidated_snapshots = 0
        self.query_snapshots: dict[int, tuple[Word, ...]] = {}

    @staticmethod
    def source_units(words: Sequence[Word]) -> tuple[SourceUnit, ...]:
        result = []
        for index, word in enumerate(words):
            surface = word.surface if word.surface is not None else (
                ('' if index == 0 else ' ') + word.text)
            result.append(SourceUnit(surface, float(word.start), float(word.end)))
        return tuple(result)

    def unowned_words(self, words: Sequence[Word]) -> tuple[Word, ...]:
        """Drop only words already covered by an accepted source interval."""
        frontier = self.gate.accepted_end
        return tuple(word for word in words if word.end > frontier + .025)

    @property
    def target_observations(self) -> int:
        """Count exact growing-snapshot target observations toward stability."""
        return self.gate._count

    @staticmethod
    def _same_prefix(prefix: Sequence[Word], current: Sequence[Word]) -> bool:
        return bool(prefix) and len(prefix) <= len(current) and all(
            normalized(a.text) == normalized(b.text)
            and abs(a.start-b.start) <= .5 and abs(a.end-b.end) <= .35
            for a, b in zip(prefix, current))

    def eligible_ends(self, words: Sequence[Word], media_now: float) -> tuple[int, ...]:
        words = self.unowned_words(words)
        if (not words or len(words) > self.MAX_SOURCE_UNITS
                or any(not math.isfinite(w.start) or not math.isfinite(w.end) for w in words)):
            return ()
        if sum(len(w.surface if w.surface is not None else w.text) for w in words) > self.MAX_SOURCE_CHARACTERS:
            return ()
        policy = self.policy
        latest_end = words[0].start
        eligible = []
        # Counts are 1-based end-unit indices. Keep the configured amount of
        # independent, timestamped right context after every eligible cut.
        final_candidate = len(words) - policy.minimum_right_context_words
        for end_unit in range(1, final_candidate + 1):
            word = words[end_unit-1]
            if end_unit > 1 and word.end <= latest_end + .025:
                continue
            latest_end = word.end
            if (word.end-words[0].start >= policy.minimum_chunk_seconds
                    and media_now-word.end >= policy.source_lookahead_seconds):
                eligible.append(end_unit)
        return tuple(eligible)

    def preferred_end(self, words: Sequence[Word], eligible_ends: Sequence[int]) -> int:
        if not eligible_ends:
            raise ValueError('A read/write proposal requires at least one eligible source boundary')
        words = self.unowned_words(words)
        target = self.policy.preferred_chunk_seconds
        return min(eligible_ends, key=lambda count: abs(
            words[count-1].end-words[0].start-target))

    def should_query(self, words: Sequence[Word], now: float, *, media_now: float) -> bool:
        current = self.unowned_words(words)
        if (not math.isfinite(now) or not math.isfinite(media_now)
                or len(current) > self.MAX_SOURCE_UNITS):
            return False
        if self.last_query_words:
            if not self._same_prefix(self.last_query_words, current):
                self.gate.reset_candidate()
                self.last_query_words = ()
                self.last_query_at = float('-inf')
                self.query_snapshots.clear()
                self.invalidated_snapshots += 1
            elif len(current) <= len(self.last_query_words):
                return False
        if now-self.last_query_at < self.policy.query_interval_seconds:
            return False
        return bool(self.eligible_ends(current, media_now))

    def mark_query(self, words: Sequence[Word], now: float) -> int:
        current = self.unowned_words(words)
        if not current or len(current) > self.MAX_SOURCE_UNITS:
            raise ValueError('Invalid bounded READ/WAIT/WRITE source snapshot')
        if self.last_query_words and not self._same_prefix(self.last_query_words, current):
            self.gate.reset_candidate()
            self.invalidated_snapshots += 1
        if self.last_query_words and len(current) <= len(self.last_query_words):
            raise ValueError('A new READ/WAIT/WRITE query must observe a growing source prefix')
        self.last_query_words = current
        self.last_query_at = float(now)
        self.next_observation_id += 1
        self.query_snapshots.clear()
        self.query_snapshots[self.next_observation_id] = current
        return self.next_observation_id

    def observe_proposal(self, raw: str, words: Sequence[Word], *, media_now: float,
                         observation_id: int,
                         eligible_ends: Sequence[int]) -> VerifiedUnit | None:
        """Record one exact proposal and return only repeated, eligible text.

        This first phase deliberately performs no model calls. The session can
        therefore avoid spending a boundary and semantic check on every
        speculative proposal; it runs those checks only after the complete
        target and owned source prefix have independently repeated.
        """
        snapshot = self.unowned_words(words)
        if not snapshot:
            return None
        query_snapshot = self.query_snapshots.pop(observation_id, None)
        if (query_snapshot is None or len(query_snapshot) != len(snapshot)
                or not self._same_prefix(query_snapshot, snapshot)):
            self.reset_candidate()
            self.invalidated_snapshots += 1
            return None
        units = self.source_units(snapshot)
        # LocalAgreement owns exact current Word objects, while ASR surfaces
        # may revise capitalization or punctuation as context grows. Compare
        # lexical source units for RWW stability; quantities, negation and
        # internal punctuation remain material under adaptive.normalized().
        comparison_units = tuple(SourceUnit(normalized(unit.text),
            unit.start, unit.end) for unit in units)
        verified = self.gate.observe(raw, comparison_units, len(units),
            observation=observation_id, media_now=media_now,
            eligible_ends=eligible_ends,
            minimum_right_context_units=self.policy.minimum_right_context_words)
        if verified is None:
            return None
        return verified

    def verify_candidate(self, verified: VerifiedUnit, words: Sequence[Word], *,
                         observation_id: int, boundary_check: dict | None = None,
                         translation_check: dict | None = None) -> ReadWaitWriteCommit | None:
        """Bind fresh dependency and meaning checks to the current proposal."""
        snapshot = self.unowned_words(words)
        if verified is not self.gate._verified or not snapshot:
            return None
        if len(verified.source) > len(snapshot):
            self.gate.reset_candidate()
            return None
        if not self._same_prefix(verified.source, snapshot):
            self.gate.reset_candidate()
            self.invalidated_snapshots += 1
            return None
        # Agreement alone previously accepted a relative clause before its
        # main predicate. Require a fresh check for this exact observation and
        # cut; a model check is evidence, never a semantic correctness proof.
        if (not isinstance(boundary_check, dict)
                or type(boundary_check.get('observation_id')) is not int
                or type(boundary_check.get('end_unit')) is not int
                or boundary_check.get('observation_id') != observation_id
                or boundary_check.get('end_unit') != len(verified.source)
                or boundary_check.get('crosses_dependency') is not False
                or not isinstance(boundary_check.get('provider'), str)
                or not boundary_check['provider'].strip()):
            self.gate.reset_candidate()
            return None
        source_words = snapshot[:len(verified.source)]
        if not source_words:
            raise ValueError('Verified READ/WAIT/WRITE output has no timestamped source owner')
        source_digest = hashlib.sha256(source_text(source_words).encode('utf-8')).hexdigest()
        target_digest = hashlib.sha256(verified.translation.encode('utf-8')).hexdigest()
        # Boundary safety does not prove the target's meaning: a real ASR
        # fragment was independently observed to acquire a fabricated action.
        # Bind a positive source/target check to this exact observation and
        # pair; on missing or negative evidence the source remains uncommitted.
        if (not isinstance(translation_check, dict)
                or type(translation_check.get('observation_id')) is not int
                or type(translation_check.get('end_unit')) is not int
                or translation_check.get('observation_id') != observation_id
                or translation_check.get('end_unit') != len(source_words)
                or translation_check.get('source_sha256') != source_digest
                or translation_check.get('target_sha256') != target_digest
                or translation_check.get('supported') is not True
                or not isinstance(translation_check.get('provider'), str)
                or not translation_check['provider'].strip()):
            self.gate.reset_candidate()
            return None
        evidence = {
            'policy': 'incremental_target_stable_read_wait_write',
            'mode': self.mode,
            'target_stability_observations': verified.observations,
            'source_snapshot_word_count': len(snapshot),
            'owned_source_word_count': len(source_words),
            'stable_source_right_context_words': len(snapshot)-len(source_words),
            'source_lookahead_seconds': round(max(0.0,
                snapshot[-1].end-verified.source[-1].end), 3),
            'source_start': source_words[0].start,
            'source_end': source_words[-1].end,
            'translation_correctness_certified': False,
            'target_text_split_or_aligned': False,
            'boundary_check':dict(boundary_check),
            'translation_check':dict(translation_check),
        }
        # Rebind the accepted proposal to the exact current twice-observed
        # LocalAgreement words. Earlier RWW snapshots may differ by tolerated
        # timestamp or punctuation jitter; carrying those stale Word objects
        # into LiveSession.commit violates its exact-confirmed-prefix contract.
        current_source = self.source_units(source_words)
        rebound = VerifiedUnit(current_source, verified.translation, verified.observations)
        self.gate._verified = rebound
        self.gate._candidate = (self.gate._signature(current_source, verified.translation),
                                current_source[-1].end)
        return ReadWaitWriteCommit(rebound, tuple(source_words), observation_id, evidence)

    def observe(self, raw: str, words: Sequence[Word], *, media_now: float,
                observation_id: int, eligible_ends: Sequence[int],
                boundary_check: dict | None = None,
                translation_check: dict | None = None) -> ReadWaitWriteCommit | None:
        """Compatibility wrapper for callers with precomputed model checks."""
        verified = self.observe_proposal(raw, words, media_now=media_now,
            observation_id=observation_id, eligible_ends=eligible_ends)
        if verified is None:
            return None
        return self.verify_candidate(verified, words, observation_id=observation_id,
            boundary_check=boundary_check, translation_check=translation_check)

    def accept(self, commit: ReadWaitWriteCommit) -> None:
        self.gate.accept(commit.verified)
        self.query_snapshots.clear()
        self.last_query_words = ()
        self.last_query_at = float('-inf')

    def advance_committed_frontier(self, source_end: float) -> None:
        """Synchronize ownership after the normal endpoint commits a phrase."""
        if not math.isfinite(source_end) or source_end < self.gate.accepted_end:
            raise ValueError('The READ/WAIT/WRITE source frontier must be finite and monotonic')
        self.gate.accepted_end = source_end
        self.gate.reset_candidate()
        self.query_snapshots.clear()
        self.last_query_words = ()
        self.last_query_at = float('-inf')

    def reset_candidate(self) -> None:
        self.gate.reset_candidate()
