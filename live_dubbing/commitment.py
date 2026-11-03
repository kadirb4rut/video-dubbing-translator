"""Language-independent contracts for learned streaming interpretation.

The model proposes a meaningful source prefix; this module verifies source
ownership and repeated observations. It contains no grammatical word lists,
sentence parser, script-specific segmentation or translation-quality scores.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
import unicodedata
from typing import Sequence


class InvalidProposal(ValueError):
    pass


@dataclass(frozen=True)
class SourceUnit:
    """An ASR surface unit, retaining its original spaces and timestamps."""
    text: str
    start: float
    end: float

    def __post_init__(self):
        if (not isinstance(self.text, str) or not self.text
                or not math.isfinite(self.start) or not math.isfinite(self.end)
                or self.start < 0 or self.end < self.start):
            raise ValueError('Invalid timestamped source unit')


@dataclass(frozen=True)
class Proposal:
    status: str
    end_unit: int
    translation: str

    @classmethod
    def parse(cls, raw: str, stable_count: int) -> 'Proposal':
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise InvalidProposal('Duplicate proposal field')
                result[key] = value
            return result
        try:
            value = json.loads(raw, object_pairs_hook=unique_object)
        except (json.JSONDecodeError, TypeError) as exc:
            raise InvalidProposal('Model response must be one JSON object') from exc
        if not isinstance(value, dict) or set(value) != {'status', 'end_unit', 'translation'}:
            raise InvalidProposal('Unexpected proposal fields')
        status, end, text = value['status'], value['end_unit'], value['translation']
        if (status not in ('wait', 'ready') or type(end) is not int
                or not isinstance(text, str) or len(text) > 8000
                or not 0 <= end <= stable_count):
            raise InvalidProposal('Invalid proposal values or unstable source boundary')
        if status == 'wait' and (end != 0 or text != ''):
            raise InvalidProposal('Wait must consume no source and produce no speech')
        if status == 'ready' and (end == 0 or not text.strip()):
            raise InvalidProposal('Ready requires source and target text')
        return cls(status, end, text)


@dataclass(frozen=True)
class VerifiedUnit:
    source: tuple[SourceUnit, ...]
    translation: str
    observations: int

    @property
    def source_text(self) -> str:
        return ''.join(unit.text for unit in self.source)


class CommitmentGate:
    """Require agreement before accepting a learned semantic boundary.

    Agreement is an observable consistency check, not proof of translation
    correctness. A later decoder observation must independently reproduce the
    complete proposed target. Already accepted source cannot be consumed twice.
    """
    def __init__(self, *, observations=2, lookahead_seconds=.6, timestamp_tolerance=.35,
                 sentence_format_agreement=False):
        if (observations < 2 or lookahead_seconds < 0 or timestamp_tolerance < 0
                or type(sentence_format_agreement) is not bool):
            raise ValueError('Invalid commitment configuration')
        self.required_observations = observations
        self.lookahead_seconds = lookahead_seconds
        self.timestamp_tolerance = timestamp_tolerance
        self.sentence_format_agreement = sentence_format_agreement
        self.accepted_end = -1.0
        self.last_observation = -1
        self._candidate = None
        self._count = 0
        self._verified = None

    def _signature(self, source, translation):
        surfaces = [unicodedata.normalize('NFC', unit.text) for unit in source]
        text = unicodedata.normalize('NFC', translation).strip()
        if self.sentence_format_agreement:
            # Ignore initial-letter case and a comma after a final lexical
            # word: ASR can add that comma as later context arrives. A comma
            # after a number may be a decimal separator and remains material.
            # Internal punctuation, digits, negation and acronyms stay intact.
            def initial_case(value):
                index = next((i for i, ch in enumerate(value) if not ch.isspace()), None)
                if index is None:
                    return value
                if index+1 < len(value) and value[index+1].isupper():
                    return value
                return value[:index] + value[index].casefold() + value[index+1:]
            if surfaces:
                surfaces[0] = initial_case(surfaces[0])
                if (len(surfaces[-1]) > 1 and surfaces[-1].endswith(',')
                        and surfaces[-1][-2].isalpha()):
                    surfaces[-1] = surfaces[-1][:-1]
            text = initial_case(text)
            if text.endswith('.') and not text.endswith('..'):
                text = text[:-1]
            elif len(text) > 1 and text.endswith(',') and text[-2].isalpha():
                text = text[:-1]
        return (tuple(surfaces), text)

    def observe(self, raw: str, units: Sequence[SourceUnit], stable_count: int,
                *, observation: int, media_now: float,
                eligible_ends: Sequence[int] | None = None,
                minimum_right_context_units: int = 0) -> VerifiedUnit | None:
        if (type(observation) is not int or observation <= self.last_observation
                or not math.isfinite(media_now) or type(stable_count) is not int
                or not 0 <= stable_count <= len(units)
                or type(minimum_right_context_units) is not int
                or minimum_right_context_units < 0):
            raise ValueError('A distinct ordered ASR observation and valid frontier are required')
        self.last_observation = observation
        self._verified = None
        proposal = Proposal.parse(raw, stable_count)
        if proposal.status == 'wait':
            self._candidate, self._count = None, 0
            return None
        if eligible_ends is not None and proposal.end_unit not in eligible_ends:
            raise InvalidProposal('The target boundary was not an eligible source timestamp')
        if stable_count - proposal.end_unit < minimum_right_context_units:
            raise InvalidProposal('Insufficient stable source words follow the proposed target chunk')
        prefix = tuple(units[:proposal.end_unit])
        if (proposal.end_unit < len(units)
                and units[proposal.end_unit].end <= prefix[-1].end + .025):
            raise InvalidProposal('A shared source timestamp group cannot be split')
        if (any(unit.end <= self.accepted_end for unit in prefix)
                or any(a.end > b.end for a, b in zip(prefix, prefix[1:]))
                or prefix[-1].end > media_now):
            raise InvalidProposal('Source replay, unordered timestamps or future audio')
        text = unicodedata.normalize('NFC', proposal.translation).strip()
        signature = self._signature(prefix, text)
        same = bool(self._candidate and self._candidate[0] == signature
                    and abs(self._candidate[1] - prefix[-1].end) <= self.timestamp_tolerance)
        self._count = self._count + 1 if same else 1
        self._candidate = (signature, prefix[-1].end)
        if (self._count < self.required_observations
                or media_now - prefix[-1].end < self.lookahead_seconds):
            return None
        self._verified = VerifiedUnit(prefix, text, self._count)
        return self._verified

    def reset_candidate(self) -> None:
        """Forget uncommitted target evidence without moving source ownership."""
        self._candidate, self._count, self._verified = None, 0, None

    def accept(self, unit: VerifiedUnit):
        """Advance source ownership only when the orchestrator accepts speech."""
        signature = self._signature(unit.source, unit.translation)
        if (unit is not self._verified or not self._candidate or self._candidate[0] != signature
                or unit.observations < self.required_observations
                or not unit.source or unit.source[-1].end <= self.accepted_end):
            raise InvalidProposal('Only the current verified source unit may be accepted')
        self.accepted_end = unit.source[-1].end
        self._candidate, self._count = None, 0
        self._verified = None
