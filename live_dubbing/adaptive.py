"""Explainable LocalAgreement and semantic endpointing; no synthetic scores."""
from __future__ import annotations

from dataclasses import dataclass
import re
import hashlib
import unicodedata
from pathlib import Path
from typing import Sequence

# Snapshot at import, so a long-running service cannot report a later file
# upload as though it had already loaded that implementation.
RUNTIME_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def normalized(word: str) -> str:
    text = unicodedata.normalize('NFC', word).casefold().strip()
    # ASR agreement must distinguish decimals, ranges and signed quantities.
    # Stripping all punctuation made 2.5 / 25 and -25 / 25 identical.
    if (any(unicodedata.category(c)[0] in 'NS' for c in text) or
            not any(unicodedata.category(c)[0] in 'LM' for c in text)):
        return text
    lexical = lambda c: unicodedata.category(c)[0] in 'LNM'
    # Keep internal punctuation too: contractions/elisions are actual source
    # content, not merely sentence formatting. This rule is script-neutral.
    return ''.join(c for i, c in enumerate(text) if lexical(c) or
                   (0 < i < len(text)-1 and lexical(text[i-1]) and lexical(text[i+1])))


INCOMPLETE_END = re.compile(r'\b(and|or|but|because|if|the|a|an|to|of|with|between|at|from|than|when|where|which|whose|that|is|are|was|were|be|being|been|am|will|would|should|could|must|may|might|has|have|had|such|like|as)\W*$', re.I)
QUANTITY_UNIT = (r'(?:thousand|million|billion|trillion|percent|percentage|'
    r'pounds?|dollars?|euros?|sterling|frames?|hertz|kilohertz|megahertz|'
    r'milliseconds?|seconds?|minutes?|hours?|days?|weeks?|months?|years?|'
    r'kilograms?|grams?|kilometers?|centimeters?|meters?|bytes?|'
    r'khz|mhz|hz|ms|kg|mg|km|cm|mm|gb|mb|kb|s|min|%)')


def incomplete_ending(text):
    # "Nothing wrong with that" ends in a pronoun, not the conjunction
    # "that" awaiting its subordinate clause. Real lecture audio exposed
    # this distinction; do not turn a valid ending into a forced timeout.
    pronoun_that = re.search(r'\b(?:with|about|of|on|like|for|to)\s+that[.!?]?$', text, re.I)
    preface = re.fullmatch(r'(?:(?:please\s+)?let\s+me\s+(?:underline|emphasize|clarify|explain)\s+(?:this|that)|(?:so[,\s]+)?(?:to|for)\s+me)[,.\s]*', text, re.I)
    discourse_comma = re.fullmatch(r'(?:now|so|well|okay|ok),', text, re.I)
    # At capture EOS, ASR can add a period to a visibly unfinished modal
    # predicate (a real VOD tail ended "the type that can make"). Keep common
    # object-taking verbs from looking complete solely because of that mark.
    modal_fragment = re.search(
        r'\b(?:can|could|will|would|should|may|might|must)\s+'
        r'(?:make|do|have|give|take|use|provide|create|build|find|include|contain|cause|lead|support|help)[.!?]*$',
        text, re.I)
    temporal_text = re.sub(r'\b(?:um|uh|erm)\b', '', text, flags=re.I)
    temporal_text = ' '.join(re.sub(r'[,;:]',' ',temporal_text).split())
    temporal_preface = re.fullmatch(r'(?:(?:and|but|so)\s+)?(?:in|during|over)\s+(?:the\s+)?'
        r'(?:(?:previous|past|recent|last|next|coming|earlier|later|many|several|few)\s+)?'
        r'(?:years?|months?|weeks?|days?)[,\s]*',temporal_text,re.I)
    return bool(preface or discourse_comma or temporal_preface or modal_fragment or
                (INCOMPLETE_END.search(text) and not pronoun_that) or re.search(r'(?:\.{2,}|…)$', text))


def quantity_continues(prefix, following):
    """Keep observed units and explicit numeric ranges with their value."""
    if not prefix or len(prefix) >= len(following):
        return False
    text = ' '.join(word.text.strip() for word in prefix)
    if not re.search(r'\d[\d.,]*(?:\s+(?:thousand|million|billion|trillion))?\W*$',text,re.I):
        return False
    unit = following[len(prefix)].text.strip('.,!?;:').casefold()
    # An already observed "25 | to 30 years" cut also fragments the range,
    # even though the next token is not itself a unit. Do not guess when the
    # next number has not actually been observed.
    if unit in ('to', '-', '–', '—') and len(following) > len(prefix)+1:
        endpoint = following[len(prefix)+1].text.strip('.,!?;:')
        if re.fullmatch(r'[+-]?\d[\d.,]*', endpoint):
            return True
    return bool(re.fullmatch(QUANTITY_UNIT,unit))


@dataclass(frozen=True)
class Word:
    text: str
    start: float
    end: float
    surface: str | None = None


def source_text(words: Sequence[Word]) -> str:
    """Retain the decoder's spacing, including scripts without word spaces."""
    return ''.join(w.surface if w.surface is not None else
                   ((' ' if i else '') + w.text) for i, w in enumerate(words)).strip()


class LocalAgreement:
    """Confirm a prefix only after agreement of two actual decoding windows.

    Revisions may retract a provisional prefix. Committed source positions are
    immutable and are excluded from future windows by timestamp, not substring
    deletion (which loses legitimate repeated words).
    """

    def __init__(self, timestamp_tolerance=0.35):
        self.previous: tuple[Word, ...] = ()
        self.stable: tuple[Word, ...] = ()
        self.confirmed: tuple[Word, ...] = ()
        self.hypothesis: tuple[Word, ...] = ()
        self.committed_end = -1.0
        self.timestamp_tolerance = timestamp_tolerance
        self.observations = 0
        self.last_observation: tuple[Word, ...] = ()
        self.committed_suffix: tuple[Word, ...] = ()
        self.revisions = 0
        self.frontier_clip_seconds = 0.0

    def reset_uncommitted(self):
        """Invalidate provisional language hypotheses without replaying speech."""
        self.previous = self.stable = self.confirmed = self.hypothesis = ()
        self.last_observation = ()
        self.frontier_clip_seconds = 0.0
        self.revisions += 1

    def update(self, words: Sequence[Word]) -> tuple[Word, ...]:
        self.frontier_clip_seconds = 0.0
        # A partial phrase commit can leave a twice-confirmed next word whose
        # estimated start straddles the new frontier. Rebase retained words
        # too: if a later ASR revision changes the confirmed tail, the refresh
        # below may deliberately keep the old confirmation even when no anchor
        # matches. Without this step that retained start can overlap forever.
        if self.committed_end >= 0 and self.confirmed:
            retained = []
            for word in self.confirmed:
                if word.start < self.committed_end < word.end:
                    self.frontier_clip_seconds = max(
                        self.frontier_clip_seconds, self.committed_end-word.start)
                    retained.append(Word(word.text, self.committed_end, word.end,
                                         word.surface))
                else:
                    retained.append(word)
            self.confirmed = tuple(retained)
        words = tuple(w for w in words if w.end > self.committed_end+.025)
        if self.committed_suffix and words and words[0].start < self.committed_end-.05:
            # Include the same 25-ms boundary quantization allowance as the
            # timestamp filter. Real rolling "tents" alignment moved .36 s;
            # only an overlapping, matching committed suffix is removed.
            for count in range(min(5,len(words),len(self.committed_suffix)),0,-1):
                if (tuple(normalized(w.text) for w in words[:count]) ==
                        tuple(normalized(w.text) for w in self.committed_suffix[-count:]) and
                        words[count-1].end <= self.committed_end+self.timestamp_tolerance+.025):
                    words = words[count:]
                    break
        # A confirmed new word may straddle the immutable source frontier
        # because rolling ASR timestamps drift. Suffix matching above removes
        # known replays first; any remaining word belongs to the uncommitted
        # phrase. Keep its text and end time, but anchor its audio interval at
        # the frontier so source PCM, phrase ownership, and delayed playback
        # never overlap an already committed interval.
        if self.committed_end >= 0 and words:
            rebased = []
            for word in words:
                if word.start < self.committed_end:
                    self.frontier_clip_seconds = max(
                        self.frontier_clip_seconds, self.committed_end-word.start)
                    rebased.append(Word(word.text, self.committed_end, word.end, word.surface))
                else:
                    rebased.append(word)
            words = tuple(rebased)
        # Confirmed ASR is still revisable until translation commitment. A
        # later pair of matching decodes may correct tense/polarity or remove
        # an ASR insertion. Anchor the region by time and its last word so a
        # single changed/clipped first word cannot erase the confirmed tail.
        if self.confirmed and self.last_observation:
            anchor = self.confirmed[-1]
            current_anchors = sorted((i for i,w in enumerate(words)
                if normalized(w.text)==normalized(anchor.text) and abs(w.end-anchor.end)<=self.timestamp_tolerance),
                key=lambda i:abs(words[i].end-anchor.end))
            for index in current_anchors:
                end = words[index].end
                prefix = tuple(w for w in words[:index+1] if w.end>self.committed_end+.025)
                old_prefix = tuple(w for w in self.last_observation
                    if self.committed_end+.025 < w.end <= end+.025)
                if (prefix and tuple(normalized(w.text) for w in prefix) ==
                        tuple(normalized(w.text) for w in old_prefix) and
                        abs(prefix[-1].end-old_prefix[-1].end)<=self.timestamp_tolerance):
                    if tuple(normalized(w.text) for w in self.confirmed) != tuple(normalized(w.text) for w in prefix):
                        self.revisions += 1
                    self.confirmed = prefix
                    break
        # Accumulate the twice-observed prefix independently of translation
        # endpointing, as in streaming LocalAgreement. Re-decoding an old
        # clipped first word must not erase the already confirmed continuation.
        # Lexical revisions remain provisional until their second observation.
        refreshed = []
        for old in self.confirmed:
            matches = [word for word in words if normalized(word.text) == normalized(old.text)
                       and abs(word.end - old.end) <= self.timestamp_tolerance]
            match = min(matches, key=lambda word: abs(word.end-old.end)) if matches else None
            refreshed.append(Word(match.text, old.start, old.end, match.surface) if match else old)
        self.confirmed = tuple(refreshed)
        floor = self.confirmed[-1].end if self.confirmed else self.committed_end
        current = tuple(w for w in words if w.end > floor + 0.025)
        # Timing jitter can leave an already confirmed suffix in the new
        # prefix. Remove only matching words that actually overlap it in time;
        # a later legitimate repetition is never deleted by text alone.
        if self.confirmed and current and current[0].start < floor - .05:
            for count in range(min(5, len(current), len(self.confirmed)), 0, -1):
                if (tuple(normalized(w.text) for w in current[:count]) ==
                    tuple(normalized(w.text) for w in self.confirmed[-count:]) and
                    current[count-1].end <= floor + self.timestamp_tolerance):
                    current = current[count:]
                    break
        previous = tuple(w for w in self.previous if w.end > floor + 0.025)
        shared = []
        for left, right in zip(previous, current):
            if normalized(left.text) != normalized(right.text) or abs(left.end - right.end) > self.timestamp_tolerance:
                break
            shared.append(right)
        # Keep unresolved words with the same endpoint together. Confirming
        # half of a zero-duration word group would discard its remaining tail.
        while shared and len(shared) < len(current) and current[len(shared)].end <= shared[-1].end + .025:
            shared.pop()
        self.confirmed += tuple(shared)
        self.stable = self.confirmed
        self.previous = current[len(shared):]
        self.hypothesis = self.confirmed + self.previous
        self.observations += 1
        self.last_observation = words
        return self.stable

    def commit(self, words: Sequence[Word]):
        if not words or any(w.end <= self.committed_end for w in words):
            raise ValueError('Cannot replay an already committed source position')
        if self.committed_end >= 0 and words[0].start < self.committed_end - 0.025:
            raise ValueError('Cannot overlap an already committed source interval')
        if tuple(words) != self.confirmed[:len(words)]:
            raise ValueError('Only a twice-observed source prefix may be committed')
        self.committed_end = words[-1].end
        self.committed_suffix = (self.committed_suffix+tuple(words))[-5:]
        self.previous = tuple(w for w in self.previous if w.end > self.committed_end + 0.025)
        self.confirmed = self.confirmed[len(words):]
        self.stable = self.confirmed
        self.hypothesis = self.confirmed + self.previous


@dataclass(frozen=True)
class Policy:
    silence: float
    simple_hold: float
    complex_hold: float
    maximum_phrase_seconds: float
    initial_video_delay: float


POLICIES = {
    'quality': Policy(0.65, 0.45, 1.25, 14.0, 4.0),
    'balanced': Policy(0.42, 0.25, 0.85, 12.0, 3.0),
    'low_latency': Policy(0.24, 0.12, 0.45, 10.0, 2.0),
}


@dataclass(frozen=True)
class Decision:
    action: str
    reasons: tuple[str, ...]
    text: str
    complexity: tuple[str, ...]
    waited_seconds: float


class AdaptiveLatencyController:
    def __init__(self, mode='balanced', syntax=None):
        if mode not in POLICIES:
            raise ValueError('Mode must be quality, balanced or low_latency')
        self.mode, self.policy = mode, POLICIES[mode]
        self.held_endpoint: tuple[Word, ...] = ()
        self.held_boundary_kind = None
        self.syntax = syntax
        self.complex_dependency_observed = False

    def clause_confirms(self, prefix, confirmed):
        """An already confirmed independent English clause follows this one.

        This narrow rule handles continuous lecture sentences without treating
        a breath inside an object ("wanting to support / causes") as an end.
        Other languages continue to use sentence/VAD boundaries.
        """
        if not prefix or len(prefix) >= len(confirmed):
            return False
        if self.syntax:
            if not self.syntax.complete(prefix) or self.syntax.boundary_reasons(prefix,confirmed):
                return False
            if len(prefix) in self.syntax.clause_boundaries(confirmed):
                return True
            if len(prefix) in self.syntax.topic_boundaries(confirmed):
                return True
        text = ' '.join(word.text.strip() for word in prefix)
        if (re.match(r'^(?:if|unless|although|because|when|while|until|since)\b', text, re.I)
                and (not self.syntax or not self.syntax.complete(prefix))):
            return False
        if incomplete_ending(text) or quantity_continues(prefix,confirmed):
            return False
        following = ' '.join(word.text.strip() for word in confirmed[len(prefix):len(prefix)+12]).replace('’', "'")
        # A spoken parenthetical is not the subject/predicate of a new
        # independent clause. Keep "this, you know, lecture" together.
        if re.match(r'^(?:you\s+know|you\s+see|i\s+mean),',following,re.I):
            return False
        # A contrast can introduce a conditional next clause. Its unresolved
        # matrix must not erase the complete preceding assertion (real live
        # speech: "the effects aren't funny / but when you ..."). This is
        # deliberately not used for a following bare "if", which could
        # qualify the assertion being translated.
        if (self.syntax and re.match(r'^but\s+(?:when|if|unless|although|while)\b',following,re.I)
                and not re.search(r'\bnot\s+only\b',text,re.I)):
            return True
        coordinated = bool(re.match(r'^(?:and|but|or)\b', following, re.I))
        discourse_transition = bool(re.match(r'^(?:and|but)\s+then\s+of course\b',following,re.I))
        if coordinated:
            following = re.sub(r'^(?:and|but|or)\s+(?:then\s+)?(?:of course\s+)?', '', following, flags=re.I)
        punctuation = prefix[-1].text.endswith((',', ';', ':'))
        # Whisper sometimes emits a capitalized new subject without keeping
        # its preceding full stop. I and proper names are excluded: their
        # capitalization alone does not establish a sentence boundary.
        capital_subject = bool(re.match(r'^(?:People|Everyone|They|We|You|It|This|That|There|He|She)\b', following))
        if not (punctuation or coordinated or capital_subject):
            return False
        finite = r'(?:am|is|are|was|were|have|has|had|can|will|do|does|did|think|believe|know|mean|used)'
        # A new coordinated clause can start with a preposed modifier:
        # "... in AI and in previous years I used to ...". Require an actual
        # confirmed subject + finite verb after the bounded modifier, rather
        # than treating the preposition alone as a semantic boundary.
        if coordinated:
            preposed = re.match(rf'^(?:in|during|over|since|after|before|at|on)\s+'
                rf'(?:\S+\s+){{1,6}}?(?=(?:i|you|we|they|he|she|it|there)\s+{finite}\b)', following, re.I)
            if preposed:
                following = following[preposed.end():]
        free_relative = coordinated and bool(re.match(
            r'^what\s+(?:\S+\s+){1,9}?(?:is|was|are|were)\b', following, re.I))
        predicate = rf"^(?:(?:there|i|you|we|they|he|she|it|this|that|people|everyone)\s+{finite}\b|(?:i'm|you're|we're|they're|he's|she's|it's|that's|there's)\b)"
        if not (free_relative or re.match(predicate, following, re.I)):
            nominal = coordinated and re.match(r'^(?:part|the|a|an|my|our|your|their|his|her|its|some|most|many|these|those)\b',following,re.I)
            verb = re.search(rf'\b{finite}\b', following, re.I) if nominal else None
            head = following[:verb.start()] if verb else ''
            # Do not treat a finite verb inside a relative clause as a new
            # independent predicate of a coordinated noun phrase.
            relative = re.search(r'\b(?:who|whom|which|whose)\b',head,re.I)
            relative_that = any(match.start() == 0 or not re.search(r'\b(?:for|about|with|of|to)\s+$',head[:match.start()],re.I)
                                for match in re.finditer(r'\bthat\b',head,re.I))
            if not (discourse_transition or (verb and not relative and not relative_that)):
                return False
        # Without punctuation, require evidence of a predicate in the prefix
        # too. A noun phrase such as "my friend and you are" is not complete.
        return punctuation or bool(re.search(rf"\b{finite}\b|\b(?:i'm|you're|we're|they're|he's|she's|it's)\b", text, re.I))

    def candidate(self, stable: Sequence[Word], pauses=()) -> tuple[Word, ...]:
        """Retain a confirmed sentence while the next sentence starts arriving.

        Waiting for silence after *all* ASR text joins consecutive turns into
        an ever-growing phrase. Timestamped committed boundaries avoid that.
        """
        if not stable:
            return ()
        boundaries = [i+1 for i, word in enumerate(stable)
            if re.search(r'[.!?。！？]$', word.text) and i >= 1
            and not re.search(r'(?:\.{2,}|…)$', word.text)
            and (i+1 == len(stable) or stable[i+1].end > word.end+.025)
            and not incomplete_ending(' '.join(w.text for w in stable[:i+1]))
            and not quantity_continues(stable[:i+1],stable)
            and (not self.syntax or self.syntax.complete(stable[:i+1]))
            and (not self.syntax or not self.syntax.boundary_reasons(stable[:i+1],stable))
            and not re.fullmatch(r'(?:Mr|Mrs|Ms|Dr|Prof|e\.g|i\.e)\.', word.text, re.I)]
        # A complete confirmed sentence outranks a hesitation inside it.
        # Real lecture speech paused before "every seven months"; selecting
        # that earlier pause unnecessarily detached the numeric qualifier.
        boundaries += [i+1 for i in range(1, len(stable)-1)
                       if self.clause_confirms(stable[:i+1], stable)]
        if boundaries:
            return tuple(stable[:min(boundaries)])
        if self.held_endpoint:
            count = len(self.held_endpoint)
            prefix = tuple(stable[:count])
            sentence_still_observed = (self.held_boundary_kind != 'sentence' or
                bool(prefix and re.search(r'[.!?。！？]$', prefix[-1].text)))
            if (len(prefix) == count and sentence_still_observed and not quantity_continues(prefix,stable) and (not self.syntax or (self.syntax.complete(prefix) and
                    not self.syntax.boundary_reasons(prefix,stable))) and
                    tuple(normalized(w.text) for w in prefix) ==
                    tuple(normalized(w.text) for w in self.held_endpoint) and
                    abs(prefix[-1].end-self.held_endpoint[-1].end) <= .35):
                # Rolling ASR may refine a word's alignment after its lexical
                # content agreed twice. Preserve the verified pause through
                # that bounded jitter, using the current word timestamps.
                self.held_endpoint = prefix
                return prefix
            self.held_endpoint = ()
            self.held_boundary_kind = None
        pause_minimum = {'quality': .35, 'balanced': .25, 'low_latency': .18}[self.mode]
        for pause_start, pause_end in pauses:
            if pause_end - pause_start < pause_minimum:
                continue
            eligible = [i+1 for i, word in enumerate(stable) if word.end <= pause_start+.06]
            if eligible and any(word.start < pause_end-.05 and word.end > stable[eligible[-1]-1].end+.05
                                for word in stable[eligible[-1]:]):
                continue
            # A brief hesitation cannot split confirmed lexical continuation.
            # Whisper's punctuation can change as the next noun arrives.
            if (eligible and eligible[-1] < len(stable) and pause_end-pause_start < .5
                    and not stable[eligible[-1]-1].text.endswith((',', ';', ':'))):
                continue
            if (eligible and not quantity_continues(stable[:eligible[-1]],stable)
                    and not incomplete_ending(' '.join(w.text for w in stable[:eligible[-1]]))
                    and (not self.syntax or self.syntax.complete(stable[:eligible[-1]]))):
                boundaries.append(eligible[-1])
        # Retain the earliest complete endpoint. Choosing the latest endpoint
        # grows the candidate whenever another sentence arrives and resets
        # its stability timer indefinitely during continuous conversation.
        return tuple(stable[:min(boundaries)]) if boundaries else tuple(stable)

    def pause_confirms(self, stable, hypothesis, pauses):
        if not stable or quantity_continues(stable,hypothesis):
            return False
        if tuple(stable) == self.held_endpoint and self.held_boundary_kind == 'pause':
            # This pause was verified before the additional semantic hold.
            # A later alignment refinement must not erase it; overlapping new
            # words still invalidate the endpoint.
            return not any(word.start < stable[-1].end-.05 and
                           word.end > stable[-1].end+.05
                           for word in hypothesis[len(stable):])
        minimum = self.policy.silence
        if not stable[-1].text.endswith(('.', '!', '?', ',', ';', ':')):
            minimum = max(.5, minimum)
        for start, end in pauses:
            if end-start < minimum or not -.06 <= start-stable[-1].end <= .5:
                continue
            if any(word.start < end-.05 and word.end > stable[-1].end+.05 for word in hypothesis[len(stable):]):
                continue
            before_pause = [word for word in hypothesis if word.end <= start+.10]
            # Never leave an unstable final word on the wrong side of an
            # endpoint merely because preceding words are already confirmed.
            if before_pause and abs(before_pause[-1].end-stable[-1].end) < .10:
                return True
        return False

    def decide(self, *, stable: Sequence[Word], hypothesis: Sequence[Word],
               media_now: float, last_speech: float, phrase_start: float,
               stable_since: float, pending_seconds: float = 0.0,
               speaker_uncertain: bool = False, end_of_stream=False,
               confirmed_pause_boundary=False, confirmed_clause_boundary=False) -> Decision:
        text = ' '.join(w.text.strip() for w in stable).strip()
        # Whisper can tokenize one decimal as "7 .4". Remove only the space
        # before a leading decimal point; retain the observed digits and words.
        text = re.sub(r'(?<=\d)\s+\.(?=\d)', '.', text)
        text = re.sub(r'(?<=\d)\.\s+(?='+QUANTITY_UNIT+r'\b)', ' ', text, flags=re.I)
        sentence_end = bool(re.search(r'[.!?。！？]$', text)) and not re.search(r'(?:\.{2,}|…)$', text)
        if self.held_boundary_kind == 'sentence' and not sentence_end:
            # LocalAgreement may retain the words while ASR revises a period
            # to a comma. That retracts the sentence evidence too; a previous
            # hold cannot override the newly observed continuation.
            self.held_endpoint = ()
            self.held_boundary_kind = None
        held_boundary = bool(stable and tuple(stable) == self.held_endpoint and self.held_boundary_kind)
        semantic_endpoint = (sentence_end or confirmed_pause_boundary or confirmed_clause_boundary or held_boundary) and len(stable) < len(hypothesis)
        endpoint = min(last_speech, stable[-1].end) if semantic_endpoint and stable else last_speech
        waited = max(0.0, media_now - endpoint)
        complexity = []
        if re.search(r'\b\d+(?:[.,]\d+)?\b', text):
            complexity.append('numbers_or_measurements')
        if re.search(r'\b[A-Z]{2,}\b|\b[A-Z][a-z]+\s+[A-Z][a-z]+\b', text):
            complexity.append('name_acronym_or_term')
        if len(text.split()) >= 18 or re.search(r'\b(if|unless|although|because|which|whose|until)\b', text, re.I):
            complexity.append('dependent_or_long_clause')
        if 'dependent_or_long_clause' in complexity:
            self.complex_dependency_observed = True
        syntax_reasons = ((self.syntax.incomplete_reasons(text) + self.syntax.boundary_reasons(stable,hypothesis))
                          if self.syntax and stable else ())
        quantity_pending = quantity_continues(stable,hypothesis)
        incomplete = incomplete_ending(text) or bool(syntax_reasons) or quantity_pending
        if incomplete:
            complexity.append('unfinished_clause')
        reasons = []
        if pending_seconds > 12:
            return Decision('backpressure', ('processing_cannot_keep_up',), text, tuple(complexity), waited)
        if not stable:
            reasons.append('awaiting_repeated_asr_agreement')
        elif len(stable) < len(hypothesis) and not semantic_endpoint:
            reasons.append('unconfirmed_sentence_ending')
        if incomplete:
            reasons.append('need_semantic_continuation')
            if quantity_pending:
                reasons.append('quantity_unit_continuation')
            reasons.extend('syntax_' + reason for reason in syntax_reasons)
        if (end_of_stream and stable and not sentence_end and
                not confirmed_pause_boundary and not confirmed_clause_boundary and
                media_now - last_speech < self.policy.silence):
            # User Stop is not evidence that the last spoken clause finished.
            # A real captured tail lost its ellipsis in a fresh decode and
            # ended "the type that can make". Keep it unplayed unless an
            # actual pause or a confirmed sentence/clause boundary exists.
            reasons.append('capture_ended_without_confirmed_boundary')
        # Both the real introduction and an ordinary long live question need
        # their final continuation confirmed. Capitalization is not a reliable
        # complexity signal; retain observed complexity until commitment.
        # The opt-in path remains capped at 16 seconds.
        phrase_budget = self.policy.maximum_phrase_seconds
        if self.syntax and self.complex_dependency_observed:
            phrase_budget = min(16.0, phrase_budget + 4.0)
            complexity.append('bounded_extra_confirmation_for_complex_clause')
        if media_now - phrase_start > phrase_budget and reasons:
            return Decision('fallback', tuple(reasons + ['bounded_uncertainty_exhausted']), text, tuple(complexity), waited)
        if reasons:
            return Decision('wait', tuple(reasons), text, tuple(complexity), waited)
        hold = self.policy.complex_hold if complexity else self.policy.simple_hold
        if speaker_uncertain:
            complexity.append('speaker_uncertain_use_temporary_voice')
        if not end_of_stream and waited < self.policy.silence and not semantic_endpoint:
            reasons.append('speech_or_short_pause')
        if not end_of_stream and media_now - stable_since < hold:
            reasons.append('additional_context_for_complexity' if complexity else 'confirm_stable_phrase')
        if reasons:
            # A verified pause endpoint must survive the next clause arriving
            # during the extra semantic hold. Otherwise every new ASR word
            # resets the hold and a continuous lecture never commits.
            if (not incomplete and stable and (sentence_end or confirmed_clause_boundary or
                    (confirmed_pause_boundary and waited >= self.policy.silence))):
                self.held_endpoint = tuple(stable)
                self.held_boundary_kind = ('clause' if confirmed_clause_boundary else
                                           'sentence' if sentence_end else 'pause')
            return Decision('wait', tuple(reasons), text, tuple(complexity), waited)
        boundary_reason = 'verified_end_of_stream' if end_of_stream else (
            f'held_confirmed_{self.held_boundary_kind}' if held_boundary else (
            'confirmed_independent_clause' if confirmed_clause_boundary else (
            'confirmed_pause_before_next_phrase' if confirmed_pause_boundary else (
            'confirmed_sentence_before_next_sentence' if semantic_endpoint else 'vad_endpoint'))
        ))
        self.held_endpoint = ()
        self.held_boundary_kind = None
        self.complex_dependency_observed = False
        return Decision('commit', ('local_agreement', 'semantic_boundary', boundary_reason), text, tuple(complexity), waited)
