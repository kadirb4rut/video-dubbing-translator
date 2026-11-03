import asyncio
import json
import importlib.util
from pathlib import Path
import socket
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch
import io
import threading
import hashlib
import os
import time

import numpy as np

from live_dubbing.adaptive import AdaptiveLatencyController, LocalAgreement, Word
from live_dubbing.protocol import AudioPacket, CaptureClock, ProtocolError
from live_dubbing.security import Credentials, source_policy
from live_dubbing.speakers import SpeakerTracker


class AdaptivePresentationPacingTests(unittest.TestCase):
    def test_pitch_preserving_catchup_is_bounded_and_mode_sensitive(self):
        from live_dubbing.session import adaptive_presentation_speed_factor
        for mode, target in (('quality', 28.), ('balanced', 23.), ('low_latency', 12.)):
            self.assertEqual(adaptive_presentation_speed_factor(mode, target), 1.)
            self.assertEqual(adaptive_presentation_speed_factor(mode, target-5), 1.)
        quality = adaptive_presentation_speed_factor('quality', 31.)
        balanced = adaptive_presentation_speed_factor('balanced', 31.)
        low_latency = adaptive_presentation_speed_factor('low_latency', 31.)
        self.assertLess(quality, balanced)
        self.assertLess(balanced, low_latency)
        self.assertEqual(adaptive_presentation_speed_factor('quality', 100.), 1.04)
        self.assertEqual(adaptive_presentation_speed_factor('balanced', 100.), 1.08)
        self.assertEqual(adaptive_presentation_speed_factor('low_latency', 100.), 1.40)
        self.assertAlmostEqual(adaptive_presentation_speed_factor('low_latency', 15.), 1.24)
        self.assertEqual(adaptive_presentation_speed_factor('low_latency', 18.), 1.40)
        self.assertEqual(adaptive_presentation_speed_factor('low_latency', None), 1.)


class IncrementalDecisionTests(unittest.TestCase):
    def test_native_compile_is_explicit_live_cuda_only(self):
        from live_dubbing.models import native_compile_requested
        with patch.dict(os.environ,{'LIVE_VOXCPM_COMPILE':'0'}):
            self.assertFalse(native_compile_requested('cpu'))
            self.assertFalse(native_compile_requested('cuda'))
        with patch.dict(os.environ,{'LIVE_VOXCPM_COMPILE':'1'}):
            self.assertTrue(native_compile_requested('cuda'))
            self.assertTrue(native_compile_requested('cuda:0'))
            with self.assertRaisesRegex(ValueError,'explicit CUDA'):
                native_compile_requested('mps')
        with patch.dict(os.environ,{'LIVE_VOXCPM_COMPILE':'sometimes'}):
            with self.assertRaisesRegex(ValueError,'0 or 1'):
                native_compile_requested('cuda')

    def test_observed_relative_where_cannot_be_committed_at_a_pause(self):
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'There have been times just for myself where'.split())]
        decision = AdaptiveLatencyController().decide(stable=words,hypothesis=words,
            media_now=3,last_speech=2,phrase_start=0,stable_since=1,
            confirmed_pause_boundary=True)
        self.assertEqual(decision.action,'wait')
        self.assertIn('need_semantic_continuation',decision.reasons)

    def test_uppercase_asr_sentence_is_not_a_glossary_of_ordinary_english_words(self):
        from live_dubbing.models import live_source_entities
        source = "THE DISMAL DUO'S SHAM TRIP TO NOTTINGHAM SAW THEM TRADING ON THE PAST GLORY OF THEIR FIRST VISIT IN 2017."
        entities, ambiguous = live_source_entities(source)
        self.assertTrue(ambiguous)
        self.assertIn(('number','2017'),entities)
        self.assertNotIn(('acronym','THE'),entities)
        self.assertNotIn(('acronym','SHAM'),entities)
        for text in ('Use CUDA and the RTX GPU at 48 Hz.', 'CUDA RTX GPU NVIDIA A100 32GB AI'):
            entities, ambiguous = live_source_entities(text)
            self.assertFalse(ambiguous)
            self.assertIn(('acronym','GPU'),entities)

    def test_real_repeated_noun_alignment_does_not_replay_committed_audio(self):
        agreement = LocalAgreement()
        pair = [Word('tents,',19.52,20.0), Word('tents.',20.16,20.48)]
        agreement.update(pair); agreement.commit(agreement.update(pair))
        shifted = [Word('Tents?',20.42,20.84), Word('You',21.52,22.0)]
        agreement.update(shifted)
        self.assertEqual([w.text for w in agreement.update(shifted)],['You'])
        later = [Word('Tents.',22.2,22.7)]
        agreement.commit(agreement.confirmed)
        agreement.update(later)
        self.assertEqual(agreement.update(later),tuple(later))

    def test_uncommitted_confirmed_polarity_can_be_corrected_after_two_decodes(self):
        agreement = LocalAgreement()
        old = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('There is nothing wrong with not wanting to help'.split())]
        corrected = [word for word in old if word.text != 'not']
        agreement.update(old); agreement.update(old)
        self.assertIn('not', [w.text for w in agreement.update(corrected)])
        revised = agreement.update(corrected)
        self.assertNotIn('not', [w.text for w in revised])
        self.assertGreater(agreement.revisions, 0)
        agreement.commit(revised)
        # Timestamp jitter must not replay the last committed word.
        jitter = [Word('help',old[-1].start,old[-1].end+.1)]
        self.assertEqual(agreement.update(jitter), ())
        self.assertEqual(agreement.update(jitter), ())

    def test_confirmed_prefix_survives_a_later_clipped_first_word(self):
        agreement = LocalAgreement()
        first = [Word('system,', 0, .3), Word('blah,', .3, .5),
                 Word('blah,', .5, .7), Word('blah.', .7, .9)]
        tail = [Word('Sign', 1, 1.2), Word('here.', 1.2, 1.5)]
        agreement.update(first)
        self.assertEqual(agreement.update(first+tail), tuple(first))
        # Real growing-window ASR sometimes omits or revises that old first
        # word. It must not erase the continuation already observed twice.
        confirmed = agreement.update(first[1:]+tail)
        self.assertEqual([word.text for word in confirmed], [word.text for word in first+tail])
        agreement.commit(confirmed)
        self.assertEqual(agreement.update(first+tail), ())

    def test_zero_duration_tail_is_not_discarded_at_the_confirmation_boundary(self):
        agreement = LocalAgreement()
        first = [Word('We', 0, .2), Word('go.', .2, .3), Word('now.', .3, .3)]
        revised = first[:2]+[Word('later.', .3, .3)]
        agreement.update(first)
        self.assertEqual(agreement.update(revised), tuple(first[:1]))
        final = agreement.update(revised)
        self.assertEqual(final, tuple(revised))
        self.assertEqual(AdaptiveLatencyController().candidate(final), final)

    def test_local_agreement_requires_observations_and_allows_safe_revisions(self):
        agreement = LocalAgreement()
        self.assertEqual(agreement.update([Word('forty', 0, .5), Word('two', .5, .9)]), ())
        stable = agreement.update([Word('forty', 0, .5), Word('three', .5, .9)])
        self.assertEqual([word.text for word in stable], ['forty'])
        stable = agreement.update([Word('forty', 0, .5), Word('three', .5, .9)])
        self.assertEqual([word.text for word in stable], ['forty', 'three'])
        agreement.commit(stable)
        with self.assertRaises(ValueError):
            agreement.commit(stable)
        # A later legitimate repetition must not be removed by text deduplication.
        future = [Word('forty', 2, 2.5), Word('three', 2.5, 2.9)]
        agreement.update(future)
        self.assertEqual(agreement.update(future), tuple(future))

    def test_update_rebases_a_confirmed_new_word_at_the_frontier(self):
        agreement = LocalAgreement()
        first = [Word('Earlier.', 0., .8)]
        agreement.update(first)
        agreement.commit(agreement.update(first))
        crossing = [Word('Crossing', .4, 1.2), Word('continues.', 1.2, 1.8)]
        agreement.update(crossing)
        stable = agreement.update(crossing)
        self.assertEqual(tuple(word.text for word in stable), ('Crossing', 'continues.'))
        self.assertAlmostEqual(stable[0].start, .8)
        self.assertAlmostEqual(agreement.frontier_clip_seconds, .4)
        agreement.commit(stable)
        self.assertAlmostEqual(agreement.committed_end, 1.8)

    def test_update_rebases_retained_confirmed_suffix_after_partial_commit(self):
        agreement = LocalAgreement()
        words = [Word('First.', 0., .8), Word('Next', .72, 1.0),
                 Word('sentence.', 1.0, 1.4)]
        agreement.update(words)
        stable = agreement.update(words)
        agreement.commit(stable[:1])

        # A changed tail can prevent the old confirmed anchor from matching.
        # The retained word must still move to the committed frontier.
        revised = [Word('Next', .72, 1.0), Word('revised.', 1.0, 1.5)]
        stable = agreement.update(revised)
        self.assertEqual(stable[0].text, 'Next')
        self.assertAlmostEqual(stable[0].start, .8)
        self.assertAlmostEqual(agreement.frontier_clip_seconds, .08)
        self.assertGreaterEqual(stable[0].start, agreement.committed_end)

    def test_commit_rejects_a_word_that_was_not_rebased_at_the_frontier(self):
        agreement = LocalAgreement()
        agreement.committed_end = .8
        crossing = [Word('Crossing', .4, 1.2), Word('continues.', 1.2, 1.8)]
        with self.assertRaisesRegex(ValueError, 'overlap an already committed source interval'):
            agreement.commit(crossing)

    def test_complexity_and_modes_change_hold_without_weakening_stability(self):
        words = [Word('CUDA', 0, .5), Word('48', .5, 1), Word('kHz.', 1, 1.1)]
        arguments = dict(stable=words, hypothesis=words, media_now=1.7,
                         last_speech=1.1, phrase_start=0, stable_since=1.1)
        self.assertEqual(AdaptiveLatencyController('low_latency').decide(**arguments).action, 'commit')
        self.assertEqual(AdaptiveLatencyController('balanced').decide(**arguments).action, 'wait')
        self.assertEqual(AdaptiveLatencyController('quality').decide(**arguments).action, 'wait')
        for mode in ('quality', 'balanced', 'low_latency'):
            decision = AdaptiveLatencyController(mode).decide(**{**arguments, 'stable': words[:1]})
            self.assertEqual(decision.action, 'wait')
            self.assertIn('unconfirmed_sentence_ending', decision.reasons)

    def test_uncertainty_deadline_never_fabricates_or_truncates_a_commit(self):
        words = [Word('between', 0, .5)]
        decision = AdaptiveLatencyController().decide(stable=words, hypothesis=words,
            media_now=20, last_speech=.5, phrase_start=0, stable_since=1)
        self.assertEqual(decision.action, 'fallback')
        self.assertIn('need_semantic_continuation', decision.reasons)

    def test_observed_multiplier_stays_with_its_amount_and_decimal_token(self):
        prefix = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('Bills have risen to a record £7 .4.'.split())]
        tail = [Word(w,2+i*.2,2+(i+1)*.2) for i,w in enumerate('billion. Now this is an analysis.'.split())]
        controller = AdaptiveLatencyController()
        pending = controller.decide(stable=prefix,hypothesis=prefix+tail,media_now=4,
            last_speech=3.9,phrase_start=0,stable_since=1)
        self.assertEqual(pending.action,'wait')
        self.assertIn('quantity_unit_continuation',pending.reasons)
        candidate = controller.candidate(prefix+tail)
        self.assertEqual(candidate,tuple(prefix+tail[:1]))
        result = controller.decide(stable=candidate,hypothesis=candidate,media_now=4.6,
            last_speech=3.9,phrase_start=0,stable_since=1,end_of_stream=True)
        self.assertEqual(result.action,'commit')
        self.assertIn('£7.4 billion',result.text)

    def test_si_unit_is_not_detached_by_provisional_punctuation(self):
        prefix = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('The sample rate is 48.'.split())]
        unit = Word('kHz.',1,1.2)
        controller=AdaptiveLatencyController()
        candidate=controller.candidate(prefix+[unit,Word('Now',1.3,1.5)])
        self.assertEqual(candidate,tuple(prefix+[unit]))

    def test_known_eos_commits_complete_confirmed_tail_without_fictional_silence(self):
        words = [Word('Thank', 0, .2), Word('you.', .2, .5)]
        decision = AdaptiveLatencyController('quality').decide(stable=words, hypothesis=words,
            media_now=.6, last_speech=.5, phrase_start=0, stable_since=.6, end_of_stream=True)
        self.assertEqual(decision.action, 'commit')

    def test_user_stop_does_not_confirm_an_unpunctuated_cutoff(self):
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('They are the type that can make'.split())]
        arguments = dict(stable=words,hypothesis=words,media_now=1.4,last_speech=1.4,
                         phrase_start=0,stable_since=0,end_of_stream=True)
        decision = AdaptiveLatencyController().decide(**arguments)
        self.assertEqual(decision.action,'wait')
        self.assertIn('capture_ended_without_confirmed_boundary',decision.reasons)
        complete = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('She continues to stay healthy'.split())]
        paused = AdaptiveLatencyController().decide(**{**arguments,'stable':complete,
            'hypothesis':complete,'media_now':2,'last_speech':1})
        self.assertEqual(paused.action,'commit')

    def test_pronoun_that_is_not_an_unfinished_conjunction(self):
        words = [Word(text,i*.2,(i+1)*.2) for i,text in enumerate('There is nothing wrong with that.'.split())]
        decision = AdaptiveLatencyController().decide(stable=words, hypothesis=words,
            media_now=2, last_speech=1.2, phrase_start=0, stable_since=1.2)
        self.assertEqual(decision.action, 'commit')

    def test_discourse_preface_waits_for_the_statement_it_introduces(self):
        for text in ('Please let me underline this,', 'So, to me,', 'Now,'):
            words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
            decision = AdaptiveLatencyController().decide(stable=words,hypothesis=words,
                media_now=2,last_speech=.5,phrase_start=0,stable_since=.5)
            self.assertEqual(decision.action,'wait')
            self.assertIn('need_semantic_continuation',decision.reasons)

    def test_next_sentence_does_not_prevent_committing_a_complete_stable_sentence(self):
        controller = AdaptiveLatencyController()
        words = [Word('I', 0, .1), Word('enjoy', .1, .4), Word('writing.', .4, 2.0),
                 Word('But', 2.1, 2.4), Word('the', 2.4, 2.6)]
        candidate = controller.candidate(words)
        self.assertEqual(candidate[-1].text, 'writing.')
        decision = controller.decide(stable=candidate, hypothesis=words,
            media_now=3.0, last_speech=2.9, phrase_start=0, stable_since=2.5)
        self.assertEqual(decision.action, 'commit')
        self.assertIn('confirmed_sentence_before_next_sentence', decision.reasons)

    def test_real_pause_can_confirm_an_asr_boundary_without_stable_punctuation(self):
        controller = AdaptiveLatencyController('quality')
        first = [Word('learning', 0, 1), Word('AI', 1, 2)]
        later = first + [Word('And', 2.9, 3.2), Word('actually', 3.2, 3.5)]
        pauses = [(2.2, 2.9)]
        candidate = controller.candidate(later, pauses)
        self.assertEqual(candidate, tuple(first))
        self.assertTrue(controller.pause_confirms(candidate, later, pauses))
        self.assertFalse(controller.pause_confirms(first[:1], later, pauses))
        decision = controller.decide(stable=candidate, hypothesis=later, media_now=4,
            last_speech=3.9, phrase_start=0, stable_since=2.5, confirmed_pause_boundary=True)
        self.assertEqual(decision.action, 'commit')
        self.assertIn('confirmed_pause_before_next_phrase', decision.reasons)

    def test_vad_gap_cannot_strand_a_terminal_word_crossing_that_gap(self):
        controller = AdaptiveLatencyController('quality')
        prefix = [Word('projects',0,.5),Word('at',.5,.7),Word('companies',.7,1)]
        pauses = [(1.02,1.8)]
        for start in (.98,1.15):
            incoming = prefix+[Word('fail',start,1.35),Word('And',1.8,2)]
            self.assertFalse(controller.pause_confirms(prefix,incoming,pauses))
            self.assertNotEqual(controller.candidate(incoming,pauses),tuple(prefix))

    def test_short_hesitation_does_not_detach_a_confirmed_object(self):
        words = [Word('wanting', 0, .4), Word('to', .4, .5), Word('support', .5, 1),
                 Word('causes,', 1.3, 1.6), Word('there', 1.7, 1.9)]
        controller = AdaptiveLatencyController()
        self.assertEqual(controller.candidate(words, [(1.02, 1.31)]), tuple(words))
        unfinished = [Word('wanting', 0, .4), Word('to', .4, .5), Word('support...', .5, 1)]
        result = controller.decide(stable=unfinished, hypothesis=unfinished, media_now=2,
            last_speech=1, phrase_start=0, stable_since=1)
        self.assertEqual(result.action, 'wait')
        self.assertIn('need_semantic_continuation', result.reasons)

    def test_independent_clause_can_commit_during_continuous_speech(self):
        words = [Word(text, i*.2, (i+1)*.2) for i,text in enumerate(
            'There is nothing wrong with activism, there is nothing wrong with helping'.split())]
        controller = AdaptiveLatencyController()
        prefix = controller.candidate(words)
        self.assertEqual(prefix[-1].text, 'activism,')
        self.assertTrue(controller.clause_confirms(prefix, words))
        decision = controller.decide(stable=prefix, hypothesis=words, media_now=3,
            last_speech=2.9, phrase_start=0, stable_since=1.5, confirmed_clause_boundary=True)
        self.assertEqual(decision.action, 'commit')
        self.assertIn('confirmed_independent_clause', decision.reasons)
        dependent = [Word('Although', 0, .1)] + words
        self.assertFalse(controller.clause_confirms(dependent[:len(prefix)+1], dependent))

    def test_confirmed_subject_and_conjunction_survive_missing_asr_full_stop(self):
        controller = AdaptiveLatencyController()
        texts = ('The main reason is that they are not well scoped People are jumping on the hype',
                 'People are jumping on the hype and they are not understanding the problem',
                 'I think the projects fail And part of the main reason for that is poor planning')
        expected = ('scoped', 'hype', 'fail')
        for text, ending in zip(texts, expected):
            words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
            candidate = controller.candidate(words)
            self.assertEqual(candidate[-1].text, ending)
            self.assertTrue(controller.clause_confirms(candidate, words))
        # Uppercase I can introduce a relative clause, not a new sentence.
        relative = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('I know people I have worked with'.split())]
        self.assertEqual(controller.candidate(relative), tuple(relative))

    def test_continuous_sentences_retain_earliest_complete_boundary(self):
        controller = AdaptiveLatencyController('quality')
        words = [Word('Yeah,', 0, .2), Word('exactly.', .2, .7),
                 Word("I've", .98, 1.1), Word('been', 1.1, 1.3), Word('busy.', 1.3, 1.7)]
        self.assertEqual(controller.candidate(words), tuple(words[:2]))

    def test_coordinated_clause_can_follow_a_preposed_modifier(self):
        cases = (
            ('What I want to do today is chat with you about career advice in AI and in previous years I used to teach this myself', 'AI'),
            ("In previous years I used to give this lecture myself but what I thought I'd do today is bring a guest", 'myself'),
        )
        for mode in ('quality', 'balanced', 'low_latency'):
            for text, ending in cases:
                words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
                controller = AdaptiveLatencyController(mode)
                candidate = controller.candidate(words)
                self.assertEqual(candidate[-1].text, ending)
                self.assertTrue(controller.clause_confirms(candidate, words))
        # A coordinated location/object with no following independent
        # subject-predicate must not be promoted to a clause boundary.
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'I have worked in AI and in previous years of computer science'.split())]
        self.assertEqual(AdaptiveLatencyController().candidate(words), tuple(words))
        disfluent = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'And, um, in previous years, I used to do most of this, you know, lecture by myself.'.split())]
        self.assertEqual(AdaptiveLatencyController().candidate(disfluent),tuple(disfluent))
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('And in previous years, I used to teach myself'.split())]
        self.assertEqual(AdaptiveLatencyController().candidate(words), tuple(words))

    def test_retracted_period_retracts_sentence_evidence_during_hold(self):
        controller = AdaptiveLatencyController()
        prefix = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'My friend Laurence Moroney kindly agreed to come to San Francisco.'.split())]
        first = controller.decide(stable=prefix, hypothesis=prefix, media_now=3,
            last_speech=2.8, phrase_start=0, stable_since=3)
        self.assertEqual(first.action,'wait')
        revised = prefix[:-1]+[Word('Francisco,',prefix[-1].start,prefix[-1].end)]
        words = revised+[Word('he',3,3.2),Word('lives',3.2,3.4),Word('in',3.4,3.6)]
        candidate = controller.candidate(words)
        self.assertEqual(candidate,tuple(words))
        self.assertFalse(controller.pause_confirms(candidate,words,()))
        decision = controller.decide(stable=candidate,hypothesis=words,media_now=4,
            last_speech=3.9,phrase_start=0,stable_since=3)
        self.assertEqual(decision.action,'wait')
        self.assertNotIn('held_confirmed_sentence',decision.reasons)
        self.assertIsNone(controller.held_boundary_kind)

    def test_complete_sentence_keeps_numeric_qualifier_after_a_hesitation(self):
        words = [Word('Tasks', 0, .3), Word('are', .3, .5), Word('doubling', .5, 1),
                 Word('every', 1.4, 1.6), Word('seven', 1.6, 1.8), Word('months.', 1.8, 2.2)]
        self.assertEqual(AdaptiveLatencyController().candidate(words, [(1, 1.4)]), tuple(words))

    def test_verified_endpoint_survives_new_words_during_complex_hold(self):
        controller = AdaptiveLatencyController()
        prefix = [Word(text,i*.14,(i+1)*.14) for i,text in enumerate(
            'If I change this, it breaks manually'.split())]
        later = prefix + [Word('and', 1.8, 2), Word('then', 2, 2.2)]
        first = controller.decide(stable=prefix, hypothesis=later, media_now=2.2,
            last_speech=2.2, phrase_start=0, stable_since=2.2, confirmed_pause_boundary=True)
        self.assertEqual(first.action, 'wait')
        self.assertEqual(controller.candidate(later), tuple(prefix))
        second = controller.decide(stable=prefix, hypothesis=later, media_now=3.2,
            last_speech=3.2, phrase_start=0, stable_since=2.2, confirmed_pause_boundary=True)
        self.assertEqual(second.action, 'commit')
        self.assertFalse(controller.held_endpoint)

    def test_complete_numeric_qualifier_outranks_an_earlier_held_pause(self):
        controller = AdaptiveLatencyController()
        first = [Word('Tasks', 0, .3), Word('are', .3, .5), Word('doubling', .5, 1)]
        controller.held_endpoint = tuple(first)
        extended = first + [Word('every', 1.4, 1.6), Word('seven', 1.6, 1.8), Word('months.', 1.8, 2.2)]
        self.assertEqual(controller.candidate(extended), tuple(extended))

    def test_verified_pause_survives_bounded_asr_alignment_revision(self):
        controller = AdaptiveLatencyController('quality')
        prefix = [Word('The', 0, .2), Word('AI', .2, .4), Word('projects', .4, .8),
                  Word('fail', .82, 1.18)]
        first = controller.decide(stable=prefix, hypothesis=prefix,
            media_now=2, last_speech=1.2, phrase_start=0, stable_since=1.6,
            confirmed_pause_boundary=True)
        self.assertEqual(first.action, 'wait')
        revised = prefix[:-1] + [Word('fail', .82, 1.44)]
        later = revised + [Word('and', 2.8, 3.1)]
        candidate = controller.candidate(later, [(1.2, 2.7)])
        self.assertEqual(candidate, tuple(revised))
        self.assertTrue(controller.pause_confirms(candidate, later, [(1.2, 2.7)]))
        decision = controller.decide(stable=candidate, hypothesis=later,
            media_now=3.2, last_speech=3.1, phrase_start=0, stable_since=1.6,
            confirmed_pause_boundary=True)
        self.assertEqual(decision.action, 'commit')

    def test_changed_lexical_ending_invalidates_held_pause(self):
        controller = AdaptiveLatencyController('quality')
        controller.held_endpoint = (Word('Projects', 0, .5), Word('fail', .5, 1))
        revised = [Word('Projects', 0, .5), Word('succeed', .5, 1), Word('because', 1.5, 2)]
        self.assertEqual(controller.candidate(revised), tuple(revised))
        self.assertFalse(controller.held_endpoint)

    def test_asr_punctuation_cannot_turn_an_incomplete_comparison_into_a_sentence(self):
        words = [Word('What', 0, .2), Word('I', .2, .3), Word('enjoy', .3, .7),
                 Word('more', .7, 1), Word('is', 1, 1.2), Word('like.', 1.2, 1.4)]
        for mode in ('quality', 'balanced', 'low_latency'):
            result = AdaptiveLatencyController(mode).decide(stable=words, hypothesis=words,
                media_now=3, last_speech=1.4, phrase_start=0, stable_since=1.5)
            self.assertEqual(result.action, 'wait')
            self.assertIn('need_semantic_continuation', result.reasons)
        extended = words + [Word('learning', 1.6, 2), Word('AI.', 2, 2.4)]
        self.assertEqual(AdaptiveLatencyController('low_latency').candidate(extended), tuple(extended))


class MediaProtocolTests(unittest.TestCase):
    def test_sequence_and_clock_fail_closed_on_loss_or_replay(self):
        first = AudioPacket(0, 0, b'\x00\x00' * 320)
        self.assertEqual(AudioPacket.decode(first.encode()), first)
        clock = CaptureClock()
        clock.accept(first)
        for item in (first, AudioPacket(2, .02, first.pcm), AudioPacket(1, .5, first.pcm)):
            with self.assertRaises(ProtocolError):
                clock.accept(item)
        clock.accept(AudioPacket(1, .02, first.pcm))

    def test_bad_format_and_unbounded_chunks_are_rejected(self):
        for data in (b'', AudioPacket(0, float('nan'), b'\x00\x00').encode(),
                     AudioPacket(0, 0, b'\x00' * 16002).encode(),
                     AudioPacket(0, 0, b'\x00' * 3).encode()):
            with self.assertRaises(ProtocolError):
                AudioPacket.decode(data)

    @unittest.skipUnless(importlib.util.find_spec('faster_whisper') and importlib.util.find_spec('onnxruntime'), 'Optional real Silero assets required')
    def test_real_vad_recurrent_state_is_invariant_to_transport_partition(self):
        from live_dubbing.speech import SileroStream
        # A transport/recurrence test, not a claim of speech-detection accuracy.
        pcm = (np.random.default_rng(17).normal(0, 1000, 4096)).astype('<i2').tobytes()
        one = SileroStream().push(pcm)
        vad = SileroStream()
        fragmented = []
        for offset in range(0, len(pcm), 320):
            fragmented.extend(vad.push(pcm[offset:offset+320]))
        self.assertEqual(len(one), 8)
        np.testing.assert_allclose([p[2] for p in one], [p[2] for p in fragmented], atol=1e-7)


class SpeakerSafetyTests(unittest.TestCase):
    def test_missing_speaker_models_use_only_a_clean_current_segment_reference(self):
        from live_dubbing.speakers import segment_reference_fallback
        audio = (np.sin(np.arange(4 * 16000, dtype=np.float32) * .03) * 4000).astype('<i2').tobytes()
        with TemporaryDirectory() as directory:
            choice = segment_reference_fallback(audio, 'Current phrase transcript.',
                Path(directory), 'phrase-1')
            self.assertEqual(choice.speaker_id, 'UNCONFIRMED')
            self.assertEqual(choice.reason, 'speaker_models_unavailable_current_segment_reference_only')
            self.assertEqual(choice.transcript, 'Current phrase transcript.')
            self.assertEqual(len(choice.reference_sha256), 64)
            self.assertTrue(choice.reference.is_file())
            short = segment_reference_fallback(audio[:2 * 16000 * 2], 'Short phrase.',
                Path(directory), 'phrase-2')
            clipped = segment_reference_fallback(np.full(4 * 16000, 32767, dtype='<i2').tobytes(),
                'Clipped phrase.', Path(directory), 'phrase-3')
            self.assertIsNone(short.reference)
            self.assertIsNone(clipped.reference)

    def test_reference_leak_requires_an_extra_shared_word(self):
        from live_dubbing.models import reference_leak_terms
        ref = 'Companies like Matter, who else? Google.'
        target = 'Meta, Microsoft and another company.'
        self.assertEqual(reference_leak_terms(ref, target, 'Google, Meta, Microsoft and another company.'), ['google'])
        self.assertEqual(reference_leak_terms(ref, target, 'Meta, Microsoft and another company.'), [])
        self.assertEqual(reference_leak_terms(ref, 'Google and Meta.', 'Google and Meta.'), [])
        self.assertEqual(reference_leak_terms('Short source', 'hello world', 'hello new world'), [])

    def test_two_second_mixed_turn_is_not_given_a_speaker_reference(self):
        from types import SimpleNamespace
        from live_dubbing.speakers import PyannoteOnlineModels
        class Segmentation:
            model = SimpleNamespace(receptive_field=SimpleNamespace(start=0., step=.01, duration=.01))
            def __call__(self, payload):
                return SimpleNamespace(data=np.zeros((1, 200, 2)),
                                       sliding_window=SimpleNamespace(start=0., step=1.))
        def embedding(tensor):
            mean = float(tensor.mean())
            return np.array([[1., 0.] if mean >= 0 else [0., 1.]], dtype=np.float32)
        model = PyannoteOnlineModels.__new__(PyannoteOnlineModels)
        model.segmentation, model.embedding = Segmentation(), embedding
        a = np.full(16000, 5000, dtype='<i2')
        b = np.full(16000, -5000, dtype='<i2')
        pcm = np.concatenate((a, b)).tobytes()
        result = model.analyze(pcm)
        self.assertTrue(result['mixed_turn'])
        self.assertAlmostEqual(result['half_embedding_cosine'], 0.)
        with TemporaryDirectory() as directory:
            choice = SpeakerTracker(Path(directory)).select(pcm, 'mixed turn', result['embedding'],
                overlap=result['overlap'], mixed_turn=result['mixed_turn'])
            self.assertEqual(choice.speaker_id, 'UNCONFIRMED')
            self.assertIsNone(choice.reference)
        single = model.analyze(np.concatenate((a, a)).tobytes())
        self.assertFalse(single['mixed_turn'])
        short = model.analyze(a.tobytes())
        self.assertIsNone(short['half_embedding_cosine'])

    def test_reference_immutable_short_and_overlap_do_not_poison(self):
        pcm = (np.sin(np.arange(64000) / 10) * 4000).astype('<i2').tobytes()
        with TemporaryDirectory() as directory:
            tracker = SpeakerTracker(Path(directory))
            early = tracker.select(pcm[:64000], 'Uncalibrated two-second first turn.', [1, 0])
            self.assertIsNone(early.reference)
            self.assertEqual(len(tracker.profiles), 0)
            a = tracker.select(pcm, 'First actual reference transcript.', [1, 0])
            original = a.reference.read_bytes()
            b = tracker.select(pcm, 'Different speaker.', [0, 1])
            again = tracker.select(pcm[:12000], 'Short interjection.', [1, .01])
            self.assertEqual(again.speaker_id, a.speaker_id)
            self.assertNotEqual(a.speaker_id, b.speaker_id)
            self.assertEqual(again.reference.read_bytes(), original)
            self.assertEqual(again.transcript, 'First actual reference transcript.')
            short = tracker.select(pcm[:12000], 'Unknown short voice.', [-1, 0])
            overlap = tracker.select(pcm, 'Mixed.', [1, 0], overlap=True)
            self.assertIsNone(short.reference)
            self.assertIsNone(overlap.reference)
            self.assertEqual(len(tracker.profiles), 2)

    def test_conditioning_cache_reuses_recent_entries_and_evicts_oldest_at_capacity(self):
        import torch
        from live_dubbing.models import LocalModels
        from live_dubbing.speakers import SpeakerChoice
        from live_dubbing.inference_scheduler import PriorityInferenceScheduler
        class Core:
            def build_prompt_cache(self, **kwargs):
                return {'mode': 'reference', 'ref_audio_feat': torch.zeros(2)}
            def generate_with_prompt_cache_streaming(self, **kwargs):
                yield torch.ones(4), torch.zeros(1), []
        with TemporaryDirectory() as directory:
            runtime = object.__new__(LocalModels)
            runtime.inference_scheduler = PriorityInferenceScheduler(enabled=False)
            runtime.voxcpm = Mock(tts_model=Core())
            runtime._prompt_caches = {}
            choices = []
            for number in range(33):
                path = Path(directory) / f'{number}.wav'
                path.write_bytes(f'reference-{number}'.encode())
                choices.append(SpeakerChoice(str(number), path, '', 1, 'unit', hashlib.sha256(path.read_bytes()).hexdigest()))
            for choice in choices[:32]:
                list(runtime._stream('Merhaba.', choice, 42, reference_only=True))
            list(runtime._stream('Merhaba.', choices[0], 42, reference_only=True))
            self.assertTrue(runtime._last_conditioning['cache_hit'])
            list(runtime._stream('Merhaba.', choices[32], 42, reference_only=True))
            self.assertEqual(len(runtime._prompt_caches), 32)
            list(runtime._stream('Merhaba.', choices[0], 42, reference_only=True))
            self.assertTrue(runtime._last_conditioning['cache_hit'])
            list(runtime._stream('Merhaba.', choices[1], 42, reference_only=True))
            self.assertFalse(runtime._last_conditioning['cache_hit'])
            self.assertEqual(len(runtime._prompt_caches), 32)

    def test_encoded_reference_cache_cannot_store_decoder_mutations_or_old_target(self):
        import torch
        from live_dubbing.models import LocalModels
        from live_dubbing.speakers import SpeakerChoice
        class Core:
            builds = 0
            targets = []
            options = []
            def build_prompt_cache(self, **kwargs):
                self.builds += 1
                self.options.append(kwargs)
                return {'mode': 'reference', 'ref_audio_feat': torch.zeros(2)}
            def generate_with_prompt_cache_streaming(self, *, target_text, prompt_cache, **kwargs):
                self.targets.append(target_text)
                prompt_cache['ref_audio_feat'].add_(1)
                yield torch.ones(4), torch.zeros(1), []
        with TemporaryDirectory() as directory:
            path = Path(directory)/'reference.wav'
            path.write_bytes(b'unit-reference-identity-only')
            runtime = object.__new__(LocalModels)
            from live_dubbing.inference_scheduler import PriorityInferenceScheduler
            runtime.inference_scheduler = PriorityInferenceScheduler(enabled=False)
            core = Core()
            runtime.voxcpm = Mock(tts_model=core)
            runtime._prompt_caches = {}
            choice = SpeakerChoice('A', path, 'one two three four five six seven eight nine ten',
                1, 'unit', hashlib.sha256(path.read_bytes()).hexdigest())
            list(runtime._stream('Bir.', choice, 42))
            self.assertFalse(runtime._last_conditioning['cache_hit'])
            list(runtime._stream('İki.', choice, 42))
            self.assertTrue(runtime._last_conditioning['cache_hit'])
            self.assertEqual(core.builds, 1)
            self.assertEqual(core.targets, ['Bir.', 'İki.'])
            cached = next(iter(runtime._prompt_caches.values()))
            self.assertTrue(torch.equal(cached['ref_audio_feat'], torch.zeros(2)))
            target = 'Lütfen bunun altını çizmeme izin verin.'
            list(runtime._stream(target, choice, 42))
            self.assertEqual(core.options[-1]['prompt_text'], choice.transcript)
            list(runtime._stream(target, choice, 1042, reference_only=True))
            self.assertTrue(runtime._last_conditioning['cache_hit'])
            self.assertEqual(core.builds, 2)
            self.assertEqual(core.options[0]['reference_wav_path'], core.options[1]['reference_wav_path'])
            self.assertEqual(core.targets[-2:], [target, target])
            path.write_bytes(b'replaced-reference')
            with self.assertRaises(ValueError):
                list(runtime._stream('Üç.', choice, 42))

class PreparationProtocolTests(unittest.TestCase):
    def test_prepare_accepts_empty_text_only_with_worker_confirmation(self):
        from translation.hymt2 import HyMT2Translator
        instance = object.__new__(HyMT2Translator)
        instance._lock = threading.Lock()
        instance._process = Mock()
        instance._process.poll.return_value = None
        instance._process.stdin = io.StringIO()
        instance._process.stdout = io.StringIO(json.dumps({'id':'unit', 'ok':True,
            'text':'', 'stats':{'prepared':True}})+'\n')
        with patch('translation.hymt2.uuid.uuid4', return_value=Mock(hex='unit')):
            self.assertIsNone(instance.prepare())
        request = json.loads(instance._process.stdin.getvalue())
        self.assertEqual(request['operation'], 'prepare')
        self.assertEqual(instance.last_generation['prepared'], True)


class PairingTests(unittest.TestCase):
    def test_explicit_glossary_preserves_terms_and_normalizes_only_surrounding_space(self):
        from live_dubbing.security import validated_glossary
        self.assertEqual(validated_glossary(None), {})
        self.assertEqual(validated_glossary({' doubling time ': ' ikiye katlanma süresi '}),
                         {'doubling time': 'ikiye katlanma süresi'})

    def test_invalid_or_oversized_glossary_is_rejected_before_model_startup(self):
        from live_dubbing.security import validated_glossary
        for value in ([], 'text', {'term': 1}, {'': 'value'}, {'term': ''},
                      {'term': 'a\nb'}, {'term': 'x'*161},
                      {str(i): 'value' for i in range(51)}, {' a': 'b', 'a ': 'c'}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validated_glossary(value)

    def test_one_use_expiring_origin_bound_credentials(self):
        now = [0.0]
        auth = Credentials(clock=lambda: now[0])
        origin = 'chrome-extension://' + 'a' * 32
        code = auth.issue_pairing()
        ticket = auth.redeem(code, origin)
        with self.assertRaises(ValueError):
            auth.redeem(code, origin)
        self.assertFalse(auth.consume_ticket(ticket, 'http://evil.example'))
        # A wrong-origin attempt consumes the ticket; it cannot be retried later.
        self.assertFalse(auth.consume_ticket(ticket, origin))
        code = auth.issue_pairing()
        now[0] = 121
        with self.assertRaises(ValueError):
            auth.redeem(code, origin)

    def test_youtube_nomination_does_not_override_platform_restriction(self):
        allowed, reason = source_policy('https://www.youtube.com/watch?v=DFYaNjzI1aI', 'browser_tab')
        self.assertFalse(allowed)
        self.assertIn('authorization', reason)
        self.assertTrue(source_policy('https://www.youtube.com/watch?v=DFYaNjzI1aI',
            'browser_tab', user_authorized=True)[0])
        self.assertFalse(source_policy('https://www.youtube.com/watch?v=DFYaNjzI1aI',
            'youtube_api', user_authorized=True)[0])
        self.assertTrue(source_policy('http://127.0.0.1:8000/fixture', 'browser_tab')[0])


@unittest.skipUnless(importlib.util.find_spec('spacy') and importlib.util.find_spec('en_core_web_sm'),
                     'Explicitly installed optional English syntax pipeline required')
class EnglishSyntaxSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from live_dubbing.syntax import EnglishSyntaxGuard
        cls.guard = EnglishSyntaxGuard()

    @classmethod
    def tearDownClass(cls):
        cls.guard.close()

    def test_real_failure_fragments_are_vetoed_without_blocking_complete_clauses(self):
        for text in ('And, um, in previous years',
                     'this is in Seattle to share with us a very broad',
                     'all the way to San Francisco,', 'Because I need help.'):
            self.assertTrue(self.guard.incomplete_reasons(text),text)
        for text in ('What I want to do today is chat with you about career advice in AI',
                     'And in previous years, I used to teach this myself.',
                     'He lives in Seattle.', 'There is nothing wrong with that.', 'Yes.'):
            self.assertFalse(self.guard.incomplete_reasons(text),text)

    def test_modifier_stays_with_its_clause_and_shared_subject_is_not_an_imperative(self):
        text = 'And, um, in previous years I used to teach this myself but what I thought I would do today is share a few thoughts'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        candidate = AdaptiveLatencyController(syntax=self.guard).candidate(words)
        self.assertEqual(candidate[-1].text,'myself')
        shared = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate("I will share a few thoughts and then hand it over".split())]
        self.assertFalse(self.guard.clause_boundaries(shared))

    def test_real_nominal_headings_do_not_block_the_following_live_sentence(self):
        for text in ('Item number two.', 'Tents. Tents.',
                     'You know, the kind of things that people go to Glastonbury and kip in for three days and leave behind.'):
            self.assertFalse(self.guard.incomplete_reasons(text), text)
        text = 'Item number two. Tents. Tents. You know, the kind of things that people go to Glastonbury'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        candidate = AdaptiveLatencyController(syntax=self.guard).candidate(words)
        self.assertEqual(' '.join(w.text for w in candidate),'Item number two.')
        self.assertTrue(self.guard.incomplete_reasons('The kind of things that people go to'))

    def test_real_cutoff_is_rejected_even_when_asr_adds_a_period(self):
        cutoff = 'But the kind of attacks that they were under are the type that can make.'
        self.assertIn('missing_complement_after_make',self.guard.incomplete_reasons(cutoff))
        for text in ('What difference does it make?', 'This is the cake they can make.',
                     'They can make it difficult.', 'They can make a difference.'):
            self.assertFalse(self.guard.incomplete_reasons(text),text)

    def test_provisional_period_cannot_detach_the_recipient(self):
        prefix = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('I will hand it over.'.split())]
        tail = [Word(w,1+i*.2,1+(i+1)*.2) for i,w in enumerate('to my friend Laurence Moroney'.split())]
        self.assertTrue(self.guard.boundary_reasons(prefix,prefix+tail))
        candidate = AdaptiveLatencyController(syntax=self.guard).candidate(prefix+tail)
        self.assertNotEqual(candidate,tuple(prefix))

    def test_real_additive_continuation_stays_with_the_market_overview(self):
        text = "He will share what he is seeing in the job market. as well as tips for growing a career in AI."
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        index = next(i+1 for i,w in enumerate(words) if w.text=='market.')
        self.assertIn('additive_enumeration_continues_phrase',self.guard.boundary_reasons(words[:index],words))
        self.assertEqual(AdaptiveLatencyController(syntax=self.guard).candidate(words),tuple(words))

    def test_real_predicate_continuation_is_not_stranded_after_nominal_subject(self):
        text = ('I feel optimistic that AI will continue making progress, meaning the complexity '
                'of tasks as measured by how long a human takes to do something. is doubling rapidly.')
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        index = next(i+1 for i,w in enumerate(words) if w.text=='something.')
        self.assertIn('predicate_continuation_after_punctuation',
                      self.guard.boundary_reasons(words[:index],words))
        self.assertEqual(AdaptiveLatencyController(syntax=self.guard).candidate(words),tuple(words))
        independent = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('I will show you something. Is that clear?'.split())]
        self.assertFalse(self.guard.boundary_reasons(independent[:5],independent))

    def test_real_numeric_qualifier_invalidates_a_previously_held_period(self):
        text = 'The doubling time is even shorter, maybe like 70 days.'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        self.assertIn('numeric_qualifier_continues_phrase',
                      self.guard.boundary_reasons(words[:6],words))
        controller = AdaptiveLatencyController(syntax=self.guard)
        controller.held_endpoint = tuple(words[:6])
        controller.held_boundary_kind = 'sentence'
        self.assertEqual(controller.candidate(words),tuple(words))
        separate = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'The wait is shorter. Maybe 70 people will attend.'.split())]
        self.assertFalse(self.guard.boundary_reasons(separate[:4],separate))

    def test_observed_where_parenthetical_does_not_detach_its_relative_clause(self):
        prefix = 'and there have been a lot of times that just for myself where, frankly,'
        self.assertIn('missing_relative_clause_after_where', self.guard.incomplete_reasons(prefix))
        text = prefix + ' I was thinking of going in some technical direction.'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        self.assertIn('relative_clause_continues_phrase',
                      self.guard.boundary_reasons(words[:len(prefix.split())],words))
        self.assertEqual(AdaptiveLatencyController(syntax=self.guard).candidate(words),tuple(words))
        self.assertFalse(self.guard.incomplete_reasons('This is where I work.'))

    def test_parenthetical_you_know_does_not_detach_a_noun(self):
        text = 'And in previous years I used to do most of this, you know, lecture by myself.'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        candidate = AdaptiveLatencyController(syntax=self.guard).candidate(words)
        self.assertEqual(candidate,tuple(words))
        self.assertTrue(self.guard.boundary_reasons(words[:11],words))

    def test_repeated_topic_elaboration_releases_complete_preceding_assertion(self):
        text = ('We can now write powerful software by using AI building blocks, '
                'AI building blocks, including large language models, RAG,')
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        controller = AdaptiveLatencyController(syntax=self.guard)
        candidate = controller.candidate(words)
        self.assertEqual(' '.join(w.text for w in candidate),
                         'We can now write powerful software by using AI building blocks,')
        self.assertTrue(controller.clause_confirms(candidate,words))
        for text in ('AI building blocks, including large language models, RAG and agentic workflows.',):
            self.assertFalse(self.guard.incomplete_reasons(text),text)
        for text in ('AI building blocks, including.', 'AI building blocks, including a very.',
                     'AI building blocks, including models that can make.'):
            self.assertTrue(self.guard.incomplete_reasons(text),text)

    def test_topic_rule_does_not_split_an_unfinished_or_unrepeated_nominal(self):
        for text in ('If we use AI building blocks, AI building blocks, including language models,',
                     'We use AI building blocks, including language models,',
                     'We use blocks, blocks, including language models,'):
            words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
            self.assertFalse(self.guard.topic_boundaries(words),text)

    def test_infinitive_continuation_is_not_a_new_imperative(self):
        prefix = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(
            'I will hand it over to my friend, who I invited.'.split())]
        tail = [Word(w,3+i*.2,3+(i+1)*.2) for i,w in enumerate('to speak here.'.split())]
        self.assertTrue(self.guard.boundary_reasons(prefix,prefix+tail))
        self.assertTrue(self.guard.incomplete_reasons('to speak here.'))
        self.assertFalse(self.guard.incomplete_reasons('Speak here.'))

    def test_contrast_can_start_a_new_conditional_without_qualifying_the_previous_assertion(self):
        controller = AdaptiveLatencyController(syntax=self.guard)
        text = "Again it's it's not meant to be of course the implications the real life effects they're not funny but when you can make it out to be satire"
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        index = next(i for i,w in enumerate(words) if w.text=='but')
        self.assertTrue(controller.clause_confirms(words[:index],words))
        condition = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate('It will work if we receive enough funding'.split())]
        self.assertFalse(controller.clause_confirms(condition[:3],condition))

    def test_opt_in_named_dependency_gets_bounded_confirmation_time(self):
        text = 'What I thought I would do today is hand it over to my good friend Laurence Moroney who kindly agreed to'
        words = [Word(w,i*.2,(i+1)*.2) for i,w in enumerate(text.split())]
        arguments = dict(stable=words,hypothesis=words,media_now=13,last_speech=12.9,
                         phrase_start=0,stable_since=12.5)
        controller = AdaptiveLatencyController(syntax=self.guard)
        self.assertEqual(controller.decide(**arguments).action,'wait')
        lower = [Word(word.text.lower(),word.start,word.end) for word in words]
        self.assertEqual(controller.decide(**{**arguments,'stable':lower,'hypothesis':lower,'media_now':15}).action,'wait')
        fresh = AdaptiveLatencyController(syntax=self.guard)
        self.assertEqual(fresh.decide(**{**arguments,'stable':lower,'hypothesis':lower,'media_now':15}).action,'wait')
        self.assertEqual(controller.decide(**{**arguments,'media_now':16.1}).action,'fallback')
        self.assertEqual(AdaptiveLatencyController().decide(**arguments).action,'fallback')


class BrowserPlaybackBackpressureTests(unittest.IsolatedAsyncioTestCase):
    async def test_synthesis_waits_for_a_real_played_ack_at_browser_high_watermark(self):
        from live_dubbing.session import LiveSession, BROWSER_PLAYBACK_HIGH_WATERMARK
        events=[]
        async def emit(value): events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(Mock(source_language='en'),emit,Path(directory),boundary=Mock())
            session.sent={f'phrase-{index}' for index in range(BROWSER_PLAYBACK_HIGH_WATERMARK)}
            try:
                waiter=asyncio.create_task(session._wait_for_playback_capacity())
                await asyncio.sleep(0)
                self.assertFalse(waiter.done(), 'a full client queue must pause synthesis')
                await session.acknowledge({'phrase_id':'phrase-0',
                    'pending_speech_audio_seconds':9.0})
                self.assertTrue(await asyncio.wait_for(waiter,timeout=.5))
                backpressure=[event for event in events if event['type']=='playback_backpressure']
                self.assertEqual([event['action'] for event in backpressure],
                    ['waiting_for_played_ack','released'])
                self.assertEqual(backpressure[0]['outstanding_phrase_count'],
                    BROWSER_PLAYBACK_HIGH_WATERMARK)
                self.assertEqual(backpressure[1]['outstanding_phrase_count'],
                    BROWSER_PLAYBACK_HIGH_WATERMARK-1)
                self.assertEqual(len(session.sent),BROWSER_PLAYBACK_HIGH_WATERMARK-1)
            finally:
                await session.stop()


class LiveASRWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_crossing_source_word_rebases_and_commits_without_interval_overlap(self):
        from unittest.mock import AsyncMock
        from live_dubbing.adaptive import Decision
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        for overlap in (.08, .6):
            with self.subTest(overlap_seconds=overlap):
                words=(Word('Crossing',.8-overlap,1.2), Word('continues.',1.2,1.8))
                models=Mock(source_language='en')
                models.recognize.return_value=Transcript(words,'en',1.,-.1)
                boundary=Mock();boundary.predict.side_effect=lambda text:[0.]*len(text)
                events=[]
                async def emit(value):events.append(value)
                with TemporaryDirectory() as directory:
                    session=LiveSession(models,emit,Path(directory),boundary=boundary)
                    session.phrase_start=.8;session.last_speech=1.8
                    session.buffer.extend(bytes(3*32000))
                    session.agreement.committed_end=.8
                    session.controller.candidate=lambda agreed,*args,**kwargs: agreed
                    session._contextual_candidate=AsyncMock(
                        side_effect=lambda agreed,hypothesis,media_now,stable,**kwargs: stable)
                    session.controller.decide=Mock(side_effect=[
                        Decision('wait',('unconfirmed',),'',(),0.),
                        Decision('commit',(), 'Crossing continues.',(), 0.)])
                    try:
                        await session._decode(2.)
                        await session._decode(2.)
                        hypotheses=[e for e in events if e['type']=='hypothesis']
                        self.assertEqual(hypotheses[-1]['decision']['action'],'commit')
                        self.assertNotIn('source_interval_regression',hypotheses[-1]['decision']['reasons'])
                        self.assertAlmostEqual(
                            hypotheses[-1]['source_interval_overlap_seconds'],0.)
                        self.assertAlmostEqual(
                            hypotheses[-1]['source_interval_frontier_clip_seconds'],overlap)
                        committed=[e for e in events if e['type']=='committed']
                        self.assertEqual(len(committed),1)
                        self.assertEqual(committed[0]['source_start'],.8)
                        self.assertEqual(committed[0]['source_end'],1.8)
                        self.assertEqual(committed[0]['source_text'],'Crossing continues.')
                        self.assertAlmostEqual(
                            committed[0]['source_interval_frontier_clip_seconds'],overlap)
                        self.assertEqual(session.phrase_queue.qsize(),1)
                        self.assertEqual(session.agreement.committed_end,1.8)
                    finally:await session.stop()

    async def test_auto_language_recovers_without_pinning_or_replaying_committed_source(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        models=Mock(source_language=None)
        wrong=Transcript((Word('provisional',1.,2.),),'cy',.74,-.1)
        corrected=Transcript((Word('corrected',1.,2.),),'en',.97,-.1)
        models.recognize.side_effect=[wrong,wrong,corrected,corrected]
        boundary=Mock();boundary.predict.side_effect=lambda text:[0.]*len(text)
        events=[]
        async def emit(value):events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=boundary)
            session.buffer.extend(bytes(4*32000));session.phrase_start=1.;session.last_speech=4.
            session.agreement.committed_end=.5
            try:
                await session._decode(4.);await session._decode(4.)
                self.assertEqual(session.agreement.confirmed[0].text,'provisional')
                await session._decode(4.)
                self.assertFalse(session.agreement.confirmed)
                await session._decode(4.)
                self.assertEqual(session.agreement.confirmed[0].text,'corrected')
                self.assertEqual(session.agreement.committed_end,.5)
                self.assertIsNone(session.source_language)
                self.assertTrue(all(call.args[2]is None for call in models.recognize.call_args_list))
                self.assertEqual(len([e for e in events if e['type']=='source_language_changed']),1)
                models.translate_draft.assert_not_called()
            finally:await session.stop()

    async def test_confirmed_source_does_not_require_identical_mt_retranslations(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        for first, following in [('A complete statement.', ' Actual right context'),
                                  ('这是完整的。', '下一句'), ('هذه جملة كاملة.', ' جملة أخرى')]:
            with self.subTest(first=first), TemporaryDirectory() as directory:
                words = (Word(first,0.,1.,first), Word(following.strip(),1.3,2.,following))
                models = Mock(source_language='en')
                models.recognize.return_value = Transcript(words,'en',1.,-.1)
                models.translate_draft.return_value = {'text':'complete contextual target', 'metrics':{
                    'translation_seconds':.01,'translation_started_at':time.perf_counter(),
                    'translation_completed_at':time.perf_counter()}}
                boundary = Mock()
                def scores(text):
                    values=[0.]*len(text)
                    if len(text)>=len(first):values[len(first)-1]=.9
                    return values
                boundary.predict.side_effect = scores
                events=[]
                async def emit(value):events.append(value)
                session=LiveSession(models,emit,Path(directory),boundary=boundary)
                session.phrase_start=0.;session.last_speech=2.;session.buffer.extend(bytes(3*32000))
                try:
                    await session._decode(2.5)
                    models.translate_draft.assert_not_called()  # no ASR agreement
                    await session._decode(2.5)
                    models.translate_draft.assert_not_called()  # endpoint not held yet
                    await session._decode(3.)
                    models.translate_draft.assert_not_called()
                    queued = session.phrase_queue.get_nowait()
                    self.assertEqual(queued['translation_request']['source_text'], first)
                    self.assertEqual(queued['translation_request']['following_text'], following.strip())
                    self.assertEqual(len([e for e in events if e['type']=='committed']),1)
                    self.assertEqual(session.agreement.committed_end,1.)
                finally:await session.stop()

    async def test_full_queue_holds_source_then_sustained_backpressure_drains(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        words=(Word('Completed.',0.,1.,'Completed.'), Word('Next',1.3,2.,' Next'))
        models=Mock(source_language='en')
        models.recognize.return_value=Transcript(words,'en',1.,-.1)
        boundary=Mock()
        boundary.predict.side_effect=lambda text: tuple(.9 if i==len('Completed.')-1 else 0. for i in range(len(text)))
        events=[]
        async def emit(value):events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=boundary)
            session.phrase_start=0.;session.last_speech=2.;session.buffer.extend(bytes(3*32000))
            for i in range(3):session.phrase_queue.put_nowait({'id':i})
            try:
                await session._decode(2.5);await session._decode(2.5);await session._decode(3.)
                self.assertFalse(any(e['type']=='warning' for e in events))
                self.assertEqual(events[-1]['decision']['reasons'], ('processing_queue_full_holding_source',))
                session.processing_pending['old'] = time.perf_counter() - 13.
                session.queued_seconds = 1.6
                await session._decode(3.);await session._decode(3.)
                warnings=[e for e in events if e['type']=='warning' and e['code']=='processing_overload_drain']
                self.assertEqual(len(warnings),1)
                self.assertEqual(warnings[0]['reason'],'processing_cannot_keep_up')
                self.assertFalse(session.failure)
                self.assertEqual(session.phrase_queue.qsize(),3)
                self.assertLess(session.agreement.committed_end,0.)
                self.assertFalse(any(e['type']=='committed' for e in events))
            finally:await session.stop()

    async def test_output_pressure_uses_remaining_browser_audio_not_full_unacknowledged_audio(self):
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en')
            async def emit(value):pass
            session=LiveSession(models,emit,Path(directory))
            try:
                session.note_playback_audio_ready('active',10.95)
                session.note_playback_audio_ready('queued',8.75)
                self.assertAlmostEqual(session.ready_speech_audio_demand(now=100.),19.7)

                self.assertTrue(session.observe_playback_audio_demand(2.7,now=100.))
                self.assertAlmostEqual(session.ready_speech_audio_demand(now=100.1),2.7)
                session.note_playback_audio_ready('newly_ready',8.75)
                self.assertAlmostEqual(session.ready_speech_audio_demand(now=100.2),11.45)

                self.assertFalse(session.observe_playback_audio_demand(float('nan'),now=101.))
                self.assertTrue(session.observe_playback_audio_demand(10.9,now=101.))
                self.assertAlmostEqual(session.ready_speech_audio_demand(now=101.1),10.9)
                self.assertAlmostEqual(session.ready_speech_audio_demand(now=103.6),28.45)
            finally:await session.stop()

    async def test_playback_metrics_control_only_fresh_video_delay_and_fall_back_when_stale(self):
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            async def emit(value):pass
            session=LiveSession(Mock(source_language='en'),emit,Path(directory),mode='balanced')
            try:
                self.assertTrue(session.observe_playback_audio_demand(8.,
                    capture_media_time=31.,video_media_time=0.,now=100.))
                self.assertEqual(session.live_video_delay_seconds(now=102.),31.)
                self.assertAlmostEqual(session.presentation_speed_factor(now=102.),1.064)
                self.assertIsNone(session.live_video_delay_seconds(now=102.6))
                self.assertEqual(session.presentation_speed_factor(now=102.6),1.)
            finally:await session.stop()

    async def test_playback_delay_drives_fresh_bounded_presentation_pacing(self):
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            async def emit(value):pass
            session=LiveSession(Mock(source_language='en'),emit,Path(directory),mode='low_latency')
            try:
                self.assertEqual(session.presentation_speed_factor(now=100.),1.)
                self.assertTrue(session.observe_playback_audio_demand(8.,
                    capture_media_time=31.,video_media_time=0.,now=100.))
                self.assertAlmostEqual(session.live_video_delay_seconds(now=102.),31.)
                self.assertAlmostEqual(session.presentation_speed_factor(now=102.),1.40)
                self.assertIsNone(session.live_video_delay_seconds(now=103.))
                self.assertEqual(session.presentation_speed_factor(now=103.),1.)
            finally:await session.stop()

    async def test_bounded_live_uncertainty_waits_for_physical_pcm_capacity(self):
        from live_dubbing.adaptive import Decision
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        models=Mock(source_language='en')
        models.recognize.return_value=Transcript((), 'en', 1., -.1)
        boundary=Mock();boundary.predict.return_value=()
        events=[]
        async def emit(value):events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=boundary)
            buffered=session._maximum_pcm_buffer_seconds()-.05
            session.phrase_start=0.;session.last_speech=buffered
            session.buffer.extend(bytes(round(buffered*32000)))
            session.clock.end=buffered
            session.controller.decide=Mock(return_value=Decision(
                'fallback',('bounded_uncertainty_exhausted',),'',(),buffered))
            try:
                await session._decode(buffered)
                warnings=[e for e in events if e['type']=='warning']
                hypotheses=[e for e in events if e['type']=='hypothesis']
                self.assertEqual(len(warnings),0)
                self.assertEqual(hypotheses[-1]['decision']['action'],'wait')
                self.assertIn('using_existing_pcm_context_reserve',
                              hypotheses[-1]['decision']['reasons'])
                self.assertFalse(session.overload_drain_requested)
                self.assertFalse(session.failure)
                self.assertFalse(any(e['type']=='error' for e in events))
                self.assertEqual(session.phrase_queue.qsize(),0)
            finally:await session.stop()

    async def test_staged_pcm_is_sliced_in_order_and_compacted_after_boundary(self):
        from live_dubbing.session import LiveSession
        models=Mock(source_language='en')
        async def emit(value):pass
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=Mock())
            maximum_bytes=int(session._maximum_pcm_buffer_seconds()*32000)
            session.buffer.extend(bytes(maximum_bytes))
            session.buffer_start=0.
            session.phrase_start=maximum_bytes/32000
            packets=[AudioPacket(index,maximum_bytes/32000+index*.02,
                                 bytes([index+1])*640) for index in range(3)]
            try:
                for packet in packets:
                    result=await session._wait_for_pcm_capacity(packet)
                    self.assertEqual(result,'staged')
                expected=b''.join(packet.pcm for packet in packets)
                self.assertEqual(bytes(session.overflow_audio),expected)
                self.assertEqual(session._slice(packets[0].start,packets[-1].end),expected)
                session.phrase_start=packets[0].start-.5
                session._trim_pcm_buffer(packets[-1].end,merge_overflow=True)
                self.assertFalse(session.overflow_audio)
                self.assertEqual(session._slice(packets[0].start,packets[-1].end),expected)
                self.assertLessEqual(len(session.buffer),maximum_bytes)
            finally:await session.stop()

    async def test_staged_pcm_keeps_packet_order_when_adaptive_capacity_grows(self):
        from live_dubbing.session import LiveSession
        async def emit(value): pass
        with TemporaryDirectory() as directory:
            session=LiveSession(Mock(source_language='en'),emit,Path(directory),boundary=Mock())
            session._maximum_pcm_buffer_seconds=Mock(return_value=1.)
            session.buffer.extend(bytes(32000))
            session.buffer_start=0.
            first=AudioPacket(0,1.,bytes([1])*640)
            second=AudioPacket(1,1.02,bytes([2])*640)
            try:
                self.assertEqual(await session._wait_for_pcm_capacity(first),'staged')
                session._maximum_pcm_buffer_seconds.return_value=2.
                self.assertEqual(await session._wait_for_pcm_capacity(second),'staged')
                self.assertEqual(session._slice(1.,1.04),first.pcm+second.pcm)
                self.assertEqual(len(session.buffer),32000)
            finally:await session.stop()

    async def test_admitted_pcm_survives_a_later_smaller_adaptive_limit(self):
        from unittest.mock import AsyncMock
        from live_dubbing.session import LiveSession
        events=[]
        async def emit(value): events.append(value)
        models=Mock(source_language='en')
        models.vad.push.return_value=[]
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=Mock())
            session.asr_work_seconds=1.6
            admitted=int(session._maximum_pcm_buffer_seconds()*32000)//640*640
            session.buffer.extend(bytes(admitted))
            session.buffer_start=session.origin=session.phrase_start=0.
            session.asr_work_seconds=.48
            self.assertGreater(len(session.buffer)/32000,session._maximum_pcm_buffer_seconds())
            packet=AudioPacket(0,admitted/32000,bytes([1])*640)
            session._decode=AsyncMock(return_value=None)
            ingest=asyncio.create_task(session._ingest())
            session.tasks=[ingest]
            try:
                await session.push(packet.encode())
                await asyncio.wait_for(session.audio_queue.join(),2.)
                self.assertFalse(any(e['type']=='error' for e in events))
                self.assertEqual(session._slice(packet.start,packet.end),packet.pcm)
                self.assertFalse(ingest.done())
            finally: await session.stop()

    async def test_bounded_staging_drain_reports_unprocessed_input_span(self):
        from unittest.mock import AsyncMock
        from live_dubbing.session import LiveSession
        events=[]
        async def emit(value):events.append(value)
        models=Mock(source_language='en')
        models.vad.push.return_value=[]
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=Mock())
            maximum_bytes=int(session._maximum_pcm_buffer_seconds()*32000)
            session.buffer.extend(bytes(maximum_bytes))
            session.buffer_start=0.
            session.origin=0.
            session.phrase_start=0.
            session.last_speech=maximum_bytes/32000
            session.overflow_audio.extend(bytes(round(1.49*32000)))
            session.overflow_start=maximum_bytes/32000
            packet=AudioPacket(0,maximum_bytes/32000+1.49,bytes(640))
            session.queued_seconds=.02
            # The next transport packet must begin at the prior source end.
            session.clock.end=packet.start
            session._decode=AsyncMock(return_value=None)
            decoder=asyncio.create_task(session._decode_loop())
            session.decode_task=decoder
            ingest=asyncio.create_task(session._ingest())
            session.tasks=[ingest,decoder]
            try:
                await session.push(packet.encode())
                await asyncio.wait_for(session.audio_queue.join(),2.)
                warnings=[event for event in events if event['type']=='warning']
                self.assertEqual(len(warnings),1)
                self.assertEqual(warnings[0]['reason'],'bounded_source_uncertainty')
                self.assertTrue(session.overload_drain_requested)
                self.assertFalse(session.failure)
                await session.finish()
                await asyncio.wait_for(asyncio.gather(ingest,decoder),2.)
                unresolved=[event for event in events if event['type']=='source_input_unprocessed']
                self.assertEqual(unresolved,[])
                self.assertAlmostEqual(session.processed_source_end,packet.end)
                self.assertAlmostEqual(len(session.overflow_audio)/32000,1.51)
            finally:await session.stop()

    async def test_slow_recognizer_does_not_block_pcm_vad_or_build_old_decode_queue(self):
        import threading
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        entered, release = threading.Event(), threading.Event()
        windows = []
        def recognize(audio, start, language):
            windows.append((start, bytes(audio)))
            entered.set()
            if not release.wait(5):
                raise TimeoutError('Test failed to release the recognizer')
            return Transcript((), 'en', 1., -.1)
        models = Mock(source_language='en', load_seconds=0)
        models.recognize.side_effect = recognize
        models.vad.push.return_value = []
        boundary = Mock(); boundary.predict.return_value = ()
        async def emit(value): pass
        with TemporaryDirectory() as directory:
            session = LiveSession(models, emit, Path(directory), boundary=boundary)
            session.phrase_start = 0.
            await session.start()
            chunks = [np.full(320, i, dtype='<i2').tobytes() for i in range(200)]
            try:
                for i, chunk in enumerate(chunks[:40]):
                    await session.push(AudioPacket(i, i*.02, chunk).encode())
                    await asyncio.sleep(0)
                self.assertTrue(await asyncio.to_thread(entered.wait, 2))
                # More than the entire two-second transport capacity arrives
                # while the actual recognize call is still blocked.
                for i, chunk in enumerate(chunks[40:], 40):
                    await session.push(AudioPacket(i, i*.02, chunk).encode())
                    await asyncio.sleep(0)
                await session.audio_queue.join()
                self.assertEqual(models.vad.push.call_count, 200)
                self.assertEqual(bytes(session.buffer), b''.join(chunks))
                self.assertEqual(len(windows), 1)
                self.assertEqual(session.queued_seconds, 0.)
                self.assertFalse(session.failure)
                await session.finish()
                release.set()
                await asyncio.wait_for(asyncio.gather(*session.tasks), 3)
                self.assertFalse(session.failure)
                self.assertLessEqual(len(windows), 5)  # one active + bounded EOS
                self.assertEqual(session.clock.next_sequence, 200)
            finally:
                release.set()
                await session.stop()

    async def test_legacy_english_guard_cannot_activate_in_shared_live_path(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        for language in ('en', 'tr'):
            models = Mock(source_language=None, load_seconds=0)
            models.recognize.return_value = Transcript((),language,1.0,-.1)
            events = []
            async def emit(value): events.append(value)
            with TemporaryDirectory() as directory, patch.dict(os.environ, {'LIVE_SYNTAX_MODEL':'explicit-test-model'}), \
                    patch('live_dubbing.syntax.EnglishSyntaxGuard') as factory:
                boundary = Mock();boundary.predict.return_value=()
                session = LiveSession(models,emit,Path(directory),boundary=boundary)
                await session.start()
                self.assertIsNone(session.controller.syntax)
                session.phrase_start=0;session.last_speech=1;session.buffer.extend(bytes(32000))
                await session._decode(1)
                self.assertIsNone(session.controller.syntax)
                self.assertFalse(any(e['type']=='syntax_guard_activated' for e in events))
                factory.assert_not_called()
                await session.stop()
                boundary.close.assert_called_once()

    async def test_input_backlog_coalesces_decodes_without_dropping_pcm_or_vad_frames(self):
        from unittest.mock import AsyncMock
        from live_dubbing.session import LiveSession
        models=Mock(source_language='en');models.vad.push.return_value=[]
        async def emit(value):pass
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory));session.phrase_start=0
            session._decode=AsyncMock()
            chunks=[np.full(320,i,dtype='<i2').tobytes() for i in range(20)]
            for i,chunk in enumerate(chunks):
                await session.push(AudioPacket(i,i*.02,chunk).encode())
            task=asyncio.create_task(session._ingest());await session.audio_queue.join()
            task.cancel();await asyncio.gather(task,return_exceptions=True)
            self.assertEqual(bytes(session.buffer),b''.join(chunks))
            self.assertEqual(models.vad.push.call_count,20)
            self.assertEqual(session._decode.await_count,1)
            self.assertGreaterEqual(session._decode.call_args.args[0],.28)
            self.assertEqual(session.clock.next_sequence,20)
            self.assertFalse(session.failure)
            await session.stop()

    async def test_eos_drains_complete_sentences_before_reporting_only_incomplete_tail(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        text = 'She is healthy. We hope she stays healthy. But she needs help because'
        words = tuple(Word(w, i*.2, (i+1)*.2) for i,w in enumerate(text.split()))
        models = Mock(source_language='en')
        models.recognize.return_value = Transcript(words, 'en', 1.0, -.1)
        models.translate_draft.side_effect=lambda **kw: {'text':'translated '+kw['source_text'],
            'metrics':{'translation_seconds':0.,'translation_started_at':time.perf_counter(),
                       'translation_completed_at':time.perf_counter()}}
        boundary=Mock();boundary.predict.side_effect=lambda text: tuple(.99 if c=='.' else 0. for c in text)
        events = []
        async def emit(value): events.append(value)
        with TemporaryDirectory() as directory:
            session = LiveSession(models, emit, Path(directory), boundary=boundary)
            session.phrase_start = 0
            session.last_speech = session.clock.end = 2.6
            session.buffer.extend(bytes(round(2.6*32000)))
            await session.audio_queue.put(None)
            await session._ingest()
            self.assertIsNone(session.failure)
            self.assertEqual([e['source_text'] for e in events if e['type']=='committed'],
                             ['She is healthy.', 'We hope she stays healthy.'])
            self.assertEqual(session.untranslated_final_tail['source_text'], 'But she needs help because')
            self.assertGreaterEqual(session.untranslated_final_tail['source_start'], 1.6)
            self.assertLessEqual(models.recognize.call_count, 12)
            await session.stop()

    async def test_eos_surfaces_unassigned_vad_activity_without_claiming_missing_words(self):
        from live_dubbing.session import LiveSession
        events=[]
        async def emit(value):events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(Mock(source_language='en'),emit,Path(directory),boundary=Mock())
            session.vad_speech_seconds_total=2.0
            session.committed_vad_speech_seconds=.75
            try:
                await session._drain_decoder()
                warning=next(e for e in events if e.get('type')=='warning')
                self.assertEqual(warning['code'],'uncommitted_vad_speech_activity')
                self.assertEqual(warning['uncommitted_vad_speech_seconds'],1.25)
                self.assertFalse(warning['transcript_omission_confirmed'])
                self.assertIsNone(session.untranslated_final_tail)
                self.assertIsNone(await session.phrase_queue.get())
            finally:await session.stop()

    async def test_partial_user_eos_keeps_verified_speech_and_reports_untranslated_tail(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        models=Mock(source_language='en')
        models.recognize.return_value=Transcript(
            (Word('I',0,.2),Word('need',.2,.4),Word('help',.4,.7),Word('because',.7,1)),
            'en',1.0,-.1)
        boundary=Mock();boundary.predict.side_effect=lambda text: (0.,)*len(text)
        events=[]
        async def emit(value):events.append(value)
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory),boundary=boundary)
            session.phrase_start=0;session.last_speech=1;session.clock.end=1
            session.buffer.extend(bytes(32000))
            verified={'identifier':'previously-verified'}
            await session.phrase_queue.put(verified)
            await session.audio_queue.put(None)
            await session._ingest()
            self.assertIsNone(session.failure)
            self.assertIs(await session.phrase_queue.get(),verified)
            self.assertIsNone(await session.phrase_queue.get())
            warning=next(e for e in events if e['type']=='warning')
            self.assertEqual(warning['code'],'untranslated_final_tail')
            self.assertEqual(warning['source_text'],'I need help because')
            self.assertFalse(any(e['type']=='committed' for e in events))
            await session.stop()
            summary=json.loads((Path(directory)/'session-summary.json').read_text())
            self.assertEqual(summary['untranslated_final_tail']['source_end'],1)

    async def test_eos_does_not_hide_real_asr_failure(self):
        from live_dubbing.session import LiveSession
        models=Mock(source_language='en');models.recognize.side_effect=RuntimeError('actual decoder failure')
        async def emit(value):pass
        with TemporaryDirectory() as directory:
            session=LiveSession(models,emit,Path(directory))
            session.phrase_start=0;session.clock.end=1;session.buffer.extend(bytes(32000))
            await session.audio_queue.put(None);await session._ingest()
            self.assertIn('actual decoder failure',session.failure)
            self.assertIsNone(session.untranslated_final_tail)
            await session.stop()

    async def test_context_preserves_the_whole_previous_committed_word(self):
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            session=LiveSession(Mock(source_language='en'),Mock(),Path(directory))
            session.buffer_start=22.8;session.phrase_start=25.14
            session.agreement.committed_end=24.52
            self.assertAlmostEqual(session._asr_context_start(),23.92)
            session.phrase_start=28
            self.assertAlmostEqual(session._asr_context_start(),27.4)

    async def test_decode_keeps_acoustic_context_and_absolute_word_clock(self):
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript
        with TemporaryDirectory() as directory:
            models = Mock(source_language='en')
            models.recognize.return_value = Transcript((), 'en', 1.0, None)
            boundary=Mock();boundary.predict.return_value=()
            async def emit(event):
                pass
            session = LiveSession(models,emit,Path(directory),boundary=boundary)
            session.buffer_start = 5
            session.buffer = bytearray(np.arange(64000,dtype=np.int16).tobytes())
            session.phrase_start = 7.5
            await session._decode(9)
            pcm, start, language = models.recognize.call_args.args
            self.assertAlmostEqual(start,6.9)
            self.assertEqual(language,'en')
            self.assertEqual(pcm,bytes(session.buffer[60800:128000]))
            self.assertEqual(session.events[-1]['asr_window_start'],6.9)

    async def test_ingest_retains_context_before_uncommitted_audio(self):
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            models = Mock(source_language='en')
            models.vad.push.return_value = []
            async def emit(event):
                pass
            session = LiveSession(models,emit,Path(directory))
            session.buffer_start = session.origin = 5
            session.buffer = bytearray(bytes(3*32000))
            session.phrase_start = 7.5
            session.last_decode = 9
            session.audio_queue.put_nowait(AudioPacket(0,8,bytes(640)))
            task = asyncio.create_task(session._ingest())
            await session.audio_queue.join()
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
            self.assertAlmostEqual(session.buffer_start,6.9)
            self.assertFalse(session.failure)

    async def test_context_and_configured_hold_have_bounded_pcm_headroom(self):
        from live_dubbing.session import LiveSession
        # Balanced has a 22 s unresolved-source recovery budget plus measured
        # ASR/context headroom. Retained source stays bounded above that cap.
        for seconds, rejected in [(18.02,False),(21.0,False),(23.0,False),(28.5,True)]:
            with self.subTest(buffer_seconds=seconds), TemporaryDirectory() as directory:
                models=Mock(source_language='en');models.vad.push.return_value=[]
                async def emit(value):pass
                session=LiveSession(models,emit,Path(directory))
                session.controller.syntax=Mock()  # Explicit optional syntax mode.
                session.origin=0;session.buffer_start=3.5;session.phrase_start=5
                session.agreement.committed_end=3.96;session.last_decode=100
                session.buffer.extend(bytes(round((seconds-.02)*32000)))
                session.audio_queue.put_nowait(AudioPacket(0,3.5+seconds-.02,bytes(640)))
                task=asyncio.create_task(session._ingest())
                await session.audio_queue.join()
                task.cancel();await asyncio.gather(task,return_exceptions=True)
                self.assertEqual(bool(session.failure),rejected)
                await session.stop()


class LiveHTTPBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_ready_without_capture_expires_and_closes_models(self):
        import socket
        from aiohttp.test_utils import TestClient, TestServer
        from live_dubbing.server import create_app
        from unittest.mock import Mock, patch
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        models=Mock(source_language='en',load_seconds=0.,contextual_chunking=False)
        with TemporaryDirectory() as directory, patch('live_dubbing.segmentation.BoundaryClient',return_value=Mock()):
            async with TestClient(TestServer(create_app(port=port,runtime_factory=lambda *args:models,
                    evidence_root=Path(directory),capture_start_timeout=.05),host='127.0.0.1',port=port)) as client:
                origin=f'http://127.0.0.1:{port}';response=await client.get('/')
                cookie=response.cookies['live_dubbing_local_session'].value
                headers={'Origin':origin,'Cookie':f'live_dubbing_local_session={cookie}'}
                response=await client.post('/api/ticket',headers=headers);ticket=(await response.json())['ticket']
                ws=await client.ws_connect('/ws',headers={'Origin':origin})
                await ws.send_json({'ticket':ticket,'settings':{'source_kind':'browser_tab',
                    'source_url':'https://www.youtube.com/watch?v=DFYaNjzI1aI','source_authorized':True}})
                self.assertEqual((await ws.receive_json(timeout=2))['type'],'loading')
                self.assertEqual((await ws.receive_json(timeout=2))['type'],'ready')
                error=await ws.receive_json(timeout=2)
                self.assertIn('capture_start_timeout',error['error'])
                await ws.receive(timeout=2)
                models.close.assert_called_once()
                self.assertFalse((await (await client.get('/api/status')).json())['active'])

    async def asyncSetUp(self):
        from aiohttp.test_utils import TestClient, TestServer
        from live_dubbing.server import create_app
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            self.port = sock.getsockname()[1]
        self.calls = 0
        def factory(*args):
            self.calls += 1
            raise RuntimeError('Models intentionally unavailable in boundary tests')
        self.dashboard_origin = 'http://127.0.0.1:8765'
        self.client = TestClient(TestServer(create_app(port=self.port, runtime_factory=factory,
                                           dashboard_origin=self.dashboard_origin),
                                           host='127.0.0.1', port=self.port))
        await self.client.start_server()
        self.origin = f'http://127.0.0.1:{self.port}'

    async def asyncTearDown(self):
        await self.client.close()

    async def test_page_load_and_hostile_origins_never_load_models(self):
        page = await self.client.get('/')
        self.assertEqual(page.status, 200)
        self.assertIn('Live Dubbing', await page.text())
        self.assertIn('frame-ancestors', page.headers['Content-Security-Policy'])
        status = await self.client.get('/api/status')
        self.assertEqual((await status.json())['pairing_api_version'], 1)
        for path in ('/api/ticket', '/api/pair'):
            response = await self.client.post(path, headers={'Origin': 'http://evil.example'})
            self.assertEqual(response.status, 403)
        response = await self.client.get('/api/status', headers={'Host': 'evil.example'})
        self.assertEqual(response.status, 403)
        self.assertEqual(self.calls, 0)

    async def test_dashboard_can_create_one_use_pairing_code(self):
        session = await self.client.get('/api/pairing-session',
            headers={'Origin': self.dashboard_origin})
        self.assertEqual(session.status, 200)
        self.assertEqual(session.headers.get('Access-Control-Allow-Origin'), self.dashboard_origin)
        self.assertEqual(session.headers.get('Access-Control-Allow-Credentials'), 'true')
        cookie = session.cookies['live_dubbing_local_session'].value
        headers = {'Origin': self.dashboard_origin,
                   'Cookie': f'live_dubbing_local_session={cookie}'}

        preflight = await self.client.options('/api/pair', headers={
            'Origin': self.dashboard_origin,
            'Access-Control-Request-Method': 'POST',
            'Access-Control-Request-Headers': 'content-type',
        })
        self.assertEqual(preflight.status, 200)
        self.assertEqual(preflight.headers.get('Access-Control-Allow-Origin'), self.dashboard_origin)
        self.assertIn('POST', preflight.headers.get('Access-Control-Allow-Methods', ''))

        response = await self.client.post('/api/pair', headers=headers, json={})
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers.get('Access-Control-Allow-Origin'), self.dashboard_origin)
        data = await response.json()
        self.assertTrue(data['code'])
        self.assertEqual(data['expires_seconds'], 120)

        extension_origin = 'chrome-extension://' + 'a' * 32
        redemption = await self.client.post('/api/redeem',
            headers={'Origin': extension_origin}, json={'code': data['code']})
        self.assertEqual(redemption.status, 200)
        self.assertTrue((await redemption.json())['ticket'])

        replay = await self.client.post('/api/redeem',
            headers={'Origin': extension_origin}, json={'code': data['code']})
        self.assertEqual(replay.status, 403)
        self.assertIn('expired or already used', await replay.text())

    async def test_dashboard_pairing_is_rejected_for_untrusted_origins(self):
        hostile = await self.client.get('/api/pairing-session',
            headers={'Origin': 'https://evil.example'})
        self.assertEqual(hostile.status, 403)
        response = await self.client.post('/api/pair',
            headers={'Origin': 'https://evil.example'}, json={})
        self.assertEqual(response.status, 403)

    async def test_colab_service_upgrade_refuses_to_stop_an_active_capture(self):
        from pathlib import Path
        from scripts.colab_live_remote import _stop_outdated_idle_service
        with self.assertRaisesRegex(RuntimeError, 'active and needs an update'):
            _stop_outdated_idle_service(Path('/content/project'),
                {'active': True, 'initializing': False})

    async def test_colab_tunnel_refreshes_only_live_server_module_before_reuse(self):
        from unittest.mock import patch
        from compute_backends.colab_tunnel import ColabLiveTunnel
        import json
        tunnel = object.__new__(ColabLiveTunnel)
        tunnel.session = 'video-dubbing-g4'
        tunnel.remote_project = '/content/.vdt-managed/project'
        status = {'app': 'video-dubbing-translator-live-v1', 'pairing_api_version': 1,
                  'active': False, 'initializing': False, 'models': {}}
        calls = []
        class Result:
            returncode = 0
            stdout = 'VDT_REMOTE_LIVE_STATUS=' + json.dumps(status)
            stderr = ''
        def run(command, **kwargs):
            calls.append((command, kwargs))
            return Result()
        with patch('compute_backends.colab_tunnel.subprocess.run', side_effect=run):
            result = tunnel._ensure_remote_service('/opt/bin/colab')
        self.assertEqual(result['pairing_api_version'], 1)
        self.assertIn('upload', calls[0][0])
        self.assertTrue(calls[0][0][2].endswith('/live_dubbing/server.py'))
        self.assertIn('exec', calls[1][0])

    async def test_restricted_source_rejected_before_any_model_or_capture(self):
        response = await self.client.get('/')
        cookie = response.cookies['live_dubbing_local_session'].value
        headers = {'Origin': self.origin, 'Cookie': f'live_dubbing_local_session={cookie}'}
        response = await self.client.post('/api/ticket', headers=headers)
        self.assertEqual(response.status, 200)
        ticket = (await response.json())['ticket']
        ws = await self.client.ws_connect('/ws', headers={'Origin': self.origin})
        await ws.send_json({'ticket': ticket, 'settings': {'source_kind': 'browser_tab',
            'source_url': 'https://www.youtube.com/watch?v=DFYaNjzI1aI'}})
        result = await ws.receive_json()
        self.assertEqual(result['type'], 'error')
        self.assertIn('authorization', result['error'])
        self.assertEqual(self.calls, 0)
        await ws.close()


class TranslationGuardRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_expansion_becomes_caption_and_records_qwen_evidence(self):
        from live_dubbing.session import _source_caption_fallback
        item={'identifier':'outlier','source_start':1.0,'source_end':4.0,
            'source_text':'source utterance'}
        details={'action':'caption_source_only_retry_unsupported',
            'target_words_per_source_second':11.7,
            'first_check':{'supported':False,'metrics':{'wall_seconds':.7}},
            'retry_check':{'supported':False,'metrics':{'wall_seconds':.8}}}
        error='translation_semantic_support_failed: '+json.dumps(details)
        fallback=_source_caption_fallback(item,error)
        self.assertEqual(fallback.metrics['presentation_mode'],'caption_only')
        self.assertEqual(fallback.metrics['fallback_reason'],
            'translation_semantic_support_rejected')
        self.assertFalse(fallback.metrics['speech_generated'])
        self.assertEqual(fallback.metrics['translation_outlier_guard'],details)
        malformed=_source_caption_fallback(item,
            'translation_semantic_support_failed: {not-json')
        self.assertEqual(malformed.metrics['translation_outlier_guard']['action'],
            'semantic_support_rejected')

    async def test_number_preservation_failure_becomes_source_caption_and_later_phrase_runs(self):
        from types import SimpleNamespace
        from live_dubbing.session import LiveSession

        class Models:
            source_language = 'en'

            def render(self, **item):
                if item['identifier'] == 'rejected':
                    raise RuntimeError('ValueError: translation_preservation_failed: '
                        '[{"kind": "number", "value": "2", "actual": "private target text"}]')
                return SimpleNamespace(identifier=item['identifier'],
                    source_start=item['source_start'], source_end=item['source_end'],
                    source_text=item['source_text'], target_text='next translation',
                    speaker={'speaker_id':'LIVE_SPEAKER_00','reason':'embedding_match'},
                    pcm=b'', chunk_sizes=[],
                    metrics={'presentation_mode':'speech','speech_generated':True}, candidates=[])

            def close(self):
                return None

        events = []
        async def emit(event):
            events.append(event)

        now = time.perf_counter()
        base = {'source_start':0., 'source_end':1., 'source_language':'en',
            'asr_completed_at':now, 'speech_end_to_asr_seconds':.1,
            'speech_end':.9, 'speech_end_method':'test', 'endpoint_media':1., 'committed_at':now}
        with TemporaryDirectory() as directory:
            session = LiveSession(Models(), emit, Path(directory))
            await session.phrase_queue.put({**base, 'identifier':'rejected',
                'source_text':'first source phrase'})
            await session.phrase_queue.put({**base, 'identifier':'next',
                'source_text':'next source phrase'})
            await session.phrase_queue.put(None)
            await session._synthesize()

        fallback = [event for event in events if event['type']=='caption_fallback']
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]['reason'], 'translation_number_preservation_failed')
        self.assertTrue(fallback[0]['source_caption_only'])
        self.assertEqual([event['type'] for event in events].count('phrase_ready'), 2)
        rejected = next(event for event in events if event['type']=='phrase' and
                        event['phrase_id']=='rejected')
        self.assertEqual(rejected['presentation_mode'], 'caption_only')
        self.assertEqual(rejected['source_text'], 'first source phrase')
        self.assertEqual(rejected['target_text'], '')
        self.assertIsNone(rejected['metrics']['speech_end_to_usable_audio_seconds'])
        self.assertIsNotNone(rejected['metrics']['commit_to_caption_seconds'])
        following = next(event for event in events if event['type']=='phrase' and
                         event['phrase_id']=='next')
        self.assertEqual(following['target_text'], 'next translation')
        self.assertNotIn('private target text', json.dumps(events))
        self.assertFalse(any(event['type']=='error' for event in events))


if __name__ == '__main__':
    unittest.main()
