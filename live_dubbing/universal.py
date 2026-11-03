"""One streaming endpoint policy for every language; no grammatical lexicons.

SaT scores are segmentation signals, never semantic confidence. In particular
its input-final probability is discarded: truncated input also scores ~1 there.
Actual source agreement and acoustic lookahead remain separate gates.
"""
from __future__ import annotations

import math
import re

from .adaptive import (Decision, POLICIES, incomplete_ending, normalized,
                       quantity_continues, source_text)


_TERMINAL_PUNCTUATION = re.compile(r'[.!?。！？؟۔]["\'”’)}\]]*$')
_ELLIPSIS = re.compile(r'(?:\.{2,}|…)["\'”’)}\]]*$')


INPUT_QUEUE_BACKPRESSURE_SECONDS = 1.5
READY_SPEECH_BACKPRESSURE_SECONDS = 12.0


class UniversalLatencyController:
    def __init__(self, mode='balanced'):
        if mode not in POLICIES:
            raise ValueError('Mode must be quality, balanced or low_latency')
        self.mode, self.policy = mode, POLICIES[mode]
        self.syntax = None  # Legacy callers cannot activate a language parser.
        self.scores = ()
        self._full = ()
        self._kind = None
        self._previous = None
        self._observations = 0

    @property
    def source_budget(self):
        return min(20.0, self.policy.maximum_phrase_seconds + 4.0)

    @property
    def source_recovery_budget(self):
        """Bounded extra wait for a long phrase whose safe cut is uncertain.

        Normal endpoint policy still uses ``source_budget``. This reserve only
        prevents a difficult phrase from stopping capture at the ordinary
        segmentation budget; PCM and delayed-video caps remain explicit.
        """
        return min(24.0, self.source_budget + 6.0)

    @property
    def lookahead(self):
        return {'low_latency': .35, 'balanced': .6, 'quality': .9}[self.mode]

    def update(self, hypothesis, scores):
        text = source_text(hypothesis)
        if len(scores) != len(text):
            raise ValueError('Segmentation scores must align to the exact ASR surface')
        self._full, self.scores = tuple(hypothesis), tuple(scores)

    def candidate(self, stable, pauses=(), *, proposed_end=0, preferred_seconds=0.,
                  source_language='auto'):
        self._kind = None
        full_text = source_text(self._full)
        if proposed_end:
            if (type(proposed_end) is not int or not 0 < proposed_end <= len(stable)
                    or proposed_end >= len(self._full)
                    or self._full[proposed_end].end <= stable[proposed_end-1].end + .025):
                raise ValueError('Source proposal must end at a confirmed timestamp group with right context')
            # The caller supplies this only after the contextual proposal and
            # independent dependency guard agree. Preserve that chosen source
            # ownership instead of replacing it with a later generic SaT cut.
            if not quantity_continues(stable[:proposed_end], self._full):
                self._kind = 'contextual_chunk_proposal'
                return tuple(stable[:proposed_end])
        boundaries = []
        # Tiny learned internal cuts produced repeatable high-WER VoxCPM QA
        # fallbacks on real G4 audio (0.04-0.36 s of voiced source). Do not
        # create these fragments; standalone quiet turns, explicit pauses,
        # terminal sentences, and guarded contextual proposals keep their own
        # paths. The mode-specific floor only filters an internal SaT cut.
        minimum_internal_seconds = {'quality': .65, 'balanced': .50,
                                    'low_latency': .40}[self.mode]
        # Select only actual timestamp-unit ends. Equal-time groups remain
        # indivisible, regardless of whitespace, script or model segmentation.
        for count in range(1, len(stable) + 1):
            prefix = stable[:count]
            if count < len(self._full) and self._full[count].end <= prefix[-1].end + .025:
                continue
            # A scored boundary at a numeral must not detach an already
            # observed unit or multiplier from its value. Keep searching for
            # a later genuine boundary instead of repeatedly selecting it.
            if quantity_continues(prefix, self._full):
                continue
            if prefix[-1].end-prefix[0].start < minimum_internal_seconds:
                continue
            # A learned boundary that the decision gate must reject should
            # not mask another learned or separately guarded contextual cut.
            language = (source_language or 'auto').lower().split('-', 1)[0]
            if language == 'en' and incomplete_ending(source_text(prefix)):
                continue
            end = len(source_text(prefix))
            if (0 < end < len(full_text) and full_text[end:].strip()
                    and self.scores[end-1] >= .5):
                boundaries.append(tuple(prefix))
        if boundaries:
            # Throughput preference must never exclude the only confirmed
            # natural boundary and force a cut inside the following clause.
            selected = (min(boundaries, key=lambda words:abs(
                words[-1].end-words[0].start-preferred_seconds)) if preferred_seconds else boundaries[0])
            self._kind = 'learned_internal_boundary'
            return selected
        return tuple(stable)

    def pause_confirms(self, stable, hypothesis, pauses):
        # For a current turn, no unconfirmed source may be stranded at its end.
        return bool(stable and len(stable) == len(hypothesis) and any(
            end-start >= max(.65, self.policy.silence)
            and -.1 <= start-stable[-1].end <= .5 for start, end in pauses))

    def clause_confirms(self, stable, agreed):
        return self._kind in ('learned_internal_boundary', 'contextual_chunk_proposal')

    def decide(self, *, stable, hypothesis, media_now, last_speech, phrase_start,
               stable_since, pending_seconds=0.0, input_queue_seconds=0.0,
               ready_speech_seconds=0.0, speaker_uncertain=False,
               end_of_stream=False, confirmed_pause_boundary=False,
               confirmed_clause_boundary=False, source_language='auto',
               confirmed_boundary_observations=0, source_budget_seconds=None):
        if type(confirmed_boundary_observations) is not int or confirmed_boundary_observations < 0:
            raise ValueError('confirmed_boundary_observations must be a non-negative integer')
        fallback_budget = self.source_budget if source_budget_seconds is None else source_budget_seconds
        if (not isinstance(fallback_budget, (int, float))
                or not math.isfinite(fallback_budget) or fallback_budget <= 0):
            raise ValueError('source_budget_seconds must be a positive finite number')
        if (any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
                for value in (pending_seconds, input_queue_seconds, ready_speech_seconds))):
            raise ValueError('pending inference and audio backlog metrics must be finite and non-negative')
        text = source_text(stable)
        waited = max(0., media_now - (stable[-1].end if stable else last_speech))
        reasons = []
        # A single long render can exceed twelve seconds without proving that
        # capture is falling behind. Stop only when that age coincides with
        # measured source-queue pressure or a substantial ready-audio backlog;
        # PCM and transport capacities remain the hard bounded limits.
        queue_pressure = input_queue_seconds >= INPUT_QUEUE_BACKPRESSURE_SECONDS
        output_pressure = ready_speech_seconds >= READY_SPEECH_BACKPRESSURE_SECONDS
        if pending_seconds > 12 and not end_of_stream and (queue_pressure or output_pressure):
            return Decision('backpressure', ('processing_cannot_keep_up',), text, (), waited)
        if not stable:
            reasons.append('awaiting_repeated_asr_agreement')
        internal = self._kind in ('learned_internal_boundary', 'contextual_chunk_proposal')
        language = (source_language or 'auto').lower().split('-', 1)[0]
        terminal_sentence = bool(_TERMINAL_PUNCTUATION.search(text)) and not _ELLIPSIS.search(text)
        english_incomplete = language == 'en' and incomplete_ending(text)
        quantity_pending = quantity_continues(stable, hypothesis)
        if quantity_pending:
            reasons.append('quantity_unit_continuation')
        # A learned/model internal cut is not made complete merely because
        # a boundary model approved it. A real candidate ended in "and"
        # while its required clause was present in the next source units.
        # Keep quiet standalone turns on their existing acoustic policy.
        if internal and english_incomplete:
            reasons.append('incomplete_english_internal_boundary')
        speech_after_transcript = (bool(stable) and len(stable) == len(hypothesis) and
            last_speech - stable[-1].end > max(.65, self.policy.silence))
        # LocalAgreement has already confirmed every word in this full-source
        # candidate twice. At EOS, terminal punctuation can therefore resolve
        # a final sentence without inventing a silence interval. Keep explicit
        # incomplete-English and source-coverage guards; EOS by itself is not
        # evidence that a cut-off clause finished.
        terminal_eos = (end_of_stream and bool(stable) and len(stable) == len(hypothesis)
                        and terminal_sentence and not english_incomplete
                        and not quantity_pending and not speech_after_transcript)
        # A quiet turn is an acoustic unit, not a claim of grammatical
        # completeness. No artificial punctuation or dummy text is appended.
        turn = (bool(stable) and len(stable) == len(hypothesis)
                and media_now-last_speech >= max(.65, self.policy.silence)
                and media_now-stable[-1].end >= max(.65, self.policy.silence)
                and not speech_after_transcript)
        if not internal and not turn and not terminal_eos:
            reasons.append('awaiting_contextual_boundary_or_acoustic_turn')
        if end_of_stream and stable and not terminal_eos and not turn:
            if speech_after_transcript:
                reasons.append('speech_after_confirmed_source_at_end_of_stream')
            elif english_incomplete:
                reasons.append('incomplete_english_phrase_at_end_of_stream')
            elif quantity_pending:
                reasons.append('quantity_continues_at_end_of_stream')
            elif not internal and not terminal_sentence:
                reasons.append('awaiting_terminal_sentence_boundary_at_end_of_stream')
        if internal and waited < self.lookahead:
            reasons.append('awaiting_acoustic_lookahead')
        signature = (tuple(normalized(w.text) for w in stable),
                     round(stable[-1].end, 1) if stable else None,
                     'internal' if internal else 'terminal_eos' if terminal_eos else 'turn' if turn else None)
        if (self._previous and signature[0] == self._previous[0]
                and signature[2] == self._previous[2]
                and (signature[1] is None or abs(signature[1]-self._previous[1]) <= .35)):
            # Timestamp estimates can jitter; source ownership is enforced
            # separately by LocalAgreement and CommitmentGate.
            self._observations += 1
        else:
            self._observations = 1
        self._previous = signature
        # For a learned internal boundary, LocalAgreement has confirmed every
        # source unit twice and candidate() requires a following, independently
        # timestamped source unit. Once the mode's acoustic right-lookahead is
        # present, another identical observation adds no source evidence. A
        # contextual model proposal is different: it can move as new right
        # context arrives, so it always needs a second matching observation.
        # Turns without following source context retain the existing two-second
        # repeated-observation guard.
        confirmation_wait = self.lookahead if internal else max(2., self.lookahead)
        # A contextual model proposal can move when the next real ASR window
        # supplies more right context. Give the simultaneous translation
        # draft one window to run, then require the same proposed source prefix
        # to survive a second controller observation before ownership transfers.
        # A learned SaT boundary already has its own source-local evidence and
        # retains the existing lookahead-based confirmation rule.
        contextual_proposal = self._kind == 'contextual_chunk_proposal'
        contextual_evidence_confirmed = (contextual_proposal
            and confirmed_boundary_observations >= 2)
        if (self._observations < 2 and
                (contextual_proposal or waited < confirmation_wait) and not terminal_eos
                and not contextual_evidence_confirmed):
            reasons.append('awaiting_repeated_boundary_observation')
        if reasons:
            action = 'fallback' if media_now-phrase_start > fallback_budget else 'wait'
            if action == 'fallback':
                reasons.append('bounded_uncertainty_exhausted')
            return Decision(action, tuple(reasons), text, (), waited)
        boundary = ('verified_terminal_sentence_at_end_of_stream' if terminal_eos else
                    self._kind or 'acoustic_turn')
        return Decision('commit', ('local_agreement', boundary,
                                   'repeated_boundary' if self._observations >= 2 else 'mature_boundary_context',
                                   'boundary_score_is_not_semantic_confidence'), text, (), waited)
