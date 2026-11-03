"""Shared invariants, not translation-quality/model-coverage assertions."""
import unittest
from live_dubbing.adaptive import LocalAgreement, Word, source_text, normalized
from live_dubbing.universal import UniversalLatencyController


class UniversalPolicyTests(unittest.TestCase):
    def test_guarded_contextual_cut_precedes_later_sat_boundary(self):
        words=tuple(Word(token,i*.5,(i+1)*.5,token if i==0 else ' '+token)
                    for i,token in enumerate(('A','complete','thought','continues','with','more','detail','today')))
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:7]))-1]=.95
        controller=UniversalLatencyController('balanced')
        controller.update(words,scores)
        selected=controller.candidate(words,proposed_end=3,source_language='en')
        self.assertEqual(selected,words[:3])
        self.assertTrue(controller.clause_confirms(selected,words))

    def test_rejected_learned_english_cut_does_not_hide_later_boundary(self):
        text='We spoke and then finished today'.split()
        words=tuple(Word(x,i*.4,(i+1)*.4,x if i==0 else ' '+x)
                    for i,x in enumerate(text))
        controller=UniversalLatencyController('balanced')
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:3]))-1]=.9  # dangling "and"
        scores[len(source_text(words[:5]))-1]=.9  # complete later boundary
        controller.update(words,scores)
        self.assertEqual(controller.candidate(words,source_language='en'),words[:5])
        scores[len(source_text(words[:5]))-1]=0.
        controller.update(words,scores)
        self.assertEqual(controller.candidate(words,source_language='en'),words)
        self.assertEqual(controller.candidate(words,proposed_end=5,source_language='en'),words[:5])

    def test_english_internal_cut_cannot_strand_observed_conjunction(self):
        # Replayed 4B selector / 27B guard: the latter mistakenly approved a
        # cut after "and" even though the next clause was already observed.
        text='A lot of people ended up getting hired just because they had AI on their resume and there is a big adjustment'.split()
        words=tuple(Word(x,i*.3,(i+1)*.3,x if i==0 else ' '+x) for i,x in enumerate(text))
        end=text.index('and')+1
        c=UniversalLatencyController('balanced');c.update(words,[0.]*len(source_text(words)))
        for _ in range(2):
            cut=c.candidate(words,proposed_end=end)
            result=c.decide(stable=cut,hypothesis=words,media_now=9.,last_speech=6.,
                phrase_start=0.,stable_since=6.,source_language='en')
            self.assertNotEqual(result.action,'commit')
            self.assertIn('incomplete_english_internal_boundary',result.reasons)
        # A complete standalone use of "that" remains a valid boundary.
        complete=tuple(Word(x,i*.3,(i+1)*.3,x if i==0 else ' '+x)
            for i,x in enumerate('Nothing wrong with that Next'.split()))
        c.update(complete,[0.]*len(source_text(complete)))
        cut=c.candidate(complete,proposed_end=4)
        for _ in range(2):
            result=c.decide(stable=cut,hypothesis=complete,media_now=4.,last_speech=1.5,
                phrase_start=0.,stable_since=2.,source_language='en')
        self.assertEqual(result.action,'commit')

    def test_observed_unit_cannot_be_separated_from_numeric_source(self):
        # Based on the real 25–30-year boundary failure: even a confident
        # learned or model-proposed cut cannot strand the observed unit.
        words=tuple(Word(text,i*.5,(i+1)*.5,text if i==0 else ' '+text)
                    for i,text in enumerate(('styles','lag','25','to','30','years','behind')))
        c=UniversalLatencyController('balanced')
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:5]))-1]=.95
        c.update(words,scores)
        self.assertEqual(c.candidate(words),words)
        self.assertEqual(c.candidate(words,proposed_end=5),words)
        for _ in range(2):
            d=c.decide(stable=words[:5],hypothesis=words,media_now=5.,last_speech=3.5,
                phrase_start=0.,stable_since=3.,source_language='en')
            self.assertNotEqual(d.action,'commit')
            self.assertIn('quantity_unit_continuation',d.reasons)
        scores[len(source_text(words[:6]))-1]=.9
        c.update(words,scores)
        self.assertEqual(c.candidate(words),words[:6])

        # A cut in the middle of the observed "25 to 30 years" range is
        # equally invalid, while a non-numeric next word is not guessed.
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:3]))-1]=.95
        c.update(words,scores)
        self.assertEqual(c.candidate(words),words)
        decision=c.decide(stable=words[:3],hypothesis=words,media_now=5.,last_speech=3.5,
            phrase_start=0.,stable_since=3.,source_language='en')
        self.assertNotEqual(decision.action,'commit')
        self.assertIn('quantity_unit_continuation',decision.reasons)

    def test_batch_preference_cannot_discard_the_only_valid_short_boundary(self):
        words=(Word('complete',0.,2.,'complete'),Word('unfinished',2.1,5.,' unfinished'))
        c=UniversalLatencyController();scores=[0.]*len(source_text(words));scores[len('complete')-1]=.9
        c.update(words,scores)
        self.assertEqual(c.candidate(words,preferred_seconds=4.),words[:1])
        self.assertTrue(c.clause_confirms(words[:1],words))

    def test_tiny_learned_internal_cut_is_filtered_by_mode_without_blocking_short_turns(self):
        words=(Word('Sure',0.,.45,'Sure'),Word('and',.5,.75,' and'),
               Word('then',.8,1.1,' then'))
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:1]))-1]=.95
        scores[len(source_text(words[:2]))-1]=.90
        expected={'quality':2,'balanced':2,'low_latency':1}
        for mode,count in expected.items():
            controller=UniversalLatencyController(mode)
            controller.update(words,scores)
            self.assertEqual(len(controller.candidate(words)),count)

        short_reply=(Word('Yes.',0.,.3,'Yes.'),)
        controller=UniversalLatencyController('quality')
        controller.update(short_reply,[0.]*len(source_text(short_reply)))
        result=controller.decide(stable=controller.candidate(short_reply),
            hypothesis=short_reply,media_now=5.,last_speech=.4,phrase_start=0.,
            stable_since=.3,source_language='en')
        self.assertEqual(result.action,'commit')

    def test_finite_eos_drain_is_not_live_processing_backpressure(self):
        words=(Word('complete',0.,1.,'complete'),)
        c=UniversalLatencyController();c.update(words,[0.]*len(source_text(words)))
        selected=c.candidate(words)
        args=dict(stable=selected,hypothesis=words,media_now=4.,last_speech=1.,
                  phrase_start=0.,stable_since=0.,pending_seconds=13.,
                  input_queue_seconds=1.6)
        self.assertEqual(c.decide(**args).action,'backpressure')
        self.assertEqual(c.decide(**args,end_of_stream=True).action,'commit')

    def test_old_render_without_source_or_ready_audio_backlog_does_not_stop_capture(self):
        words=(Word('complete',0.,1.,'complete'),)
        c=UniversalLatencyController();c.update(words,[0.]*len(source_text(words)))
        result=c.decide(stable=c.candidate(words),hypothesis=words,media_now=4.,
            last_speech=1.,phrase_start=0.,stable_since=0.,pending_seconds=13.,
            input_queue_seconds=.2,ready_speech_seconds=2.5)
        self.assertNotEqual(result.action,'backpressure')

    def test_ready_audio_backlog_combined_with_old_render_is_backpressure(self):
        words=(Word('complete',0.,1.,'complete'),)
        c=UniversalLatencyController();c.update(words,[0.]*len(source_text(words)))
        result=c.decide(stable=c.candidate(words),hypothesis=words,media_now=4.,
            last_speech=1.,phrase_start=0.,stable_since=0.,pending_seconds=13.,
            input_queue_seconds=.2,ready_speech_seconds=12.1)
        self.assertEqual(result.action,'backpressure')

    def test_terminal_agreed_sentence_commits_at_eos_without_fabricated_silence(self):
        words=tuple(Word(text,start,end,text) for text,start,end in (
            ('The',0.,.2),('final',.2,.45),('sentence',.45,.8),('ends.',.8,1.1)))
        c=UniversalLatencyController('balanced');c.update(words,[0.]*len(source_text(words)))
        result=c.decide(stable=c.candidate(words),hypothesis=words,media_now=1.35,
            last_speech=1.17,phrase_start=0.,stable_since=1.2,end_of_stream=True,
            source_language='en')
        self.assertEqual(result.action,'commit')
        self.assertIn('verified_terminal_sentence_at_end_of_stream',result.reasons)

    def test_confirmed_internal_boundary_can_commit_before_incomplete_eos_tail(self):
        words=(Word('complete',0.,2.,'complete'),
               Word('continuation',2.2,4.5,' continuation'))
        scores=[0.]*len(source_text(words))
        scores[len('complete')-1]=.9
        c=UniversalLatencyController('balanced');c.update(words,scores)
        selected=c.candidate(words)
        self.assertEqual(selected,words[:1])
        result=c.decide(stable=selected,hypothesis=words,media_now=4.5,
            last_speech=4.4,phrase_start=0.,stable_since=4.4,end_of_stream=True,
            source_language='en')
        self.assertEqual(result.action,'commit')
        self.assertIn('learned_internal_boundary',result.reasons)
        self.assertNotIn('awaiting_terminal_sentence_boundary_at_end_of_stream',result.reasons)

    def test_incomplete_or_uncovered_eos_tail_stays_uncommitted(self):
        incomplete=tuple(Word(text,i*.2,(i+1)*.2,text if i==0 else ' '+text)
            for i,text in enumerate('They are the type that can make.'.split()))
        c=UniversalLatencyController();c.update(incomplete,[0.]*len(source_text(incomplete)))
        result=c.decide(stable=c.candidate(incomplete),hypothesis=incomplete,media_now=1.55,
            last_speech=1.4,phrase_start=0.,stable_since=1.4,end_of_stream=True,
            source_language='en')
        self.assertNotEqual(result.action,'commit')
        self.assertIn('incomplete_english_phrase_at_end_of_stream',result.reasons)

        complete=tuple(Word(text,start,end,text if index==0 else ' '+text)
            for index,(text,start,end) in enumerate((('Complete',0.,.3),('sentence.',.3,.7))))
        c.update(complete,[0.]*len(source_text(complete)))
        uncovered=c.decide(stable=c.candidate(complete),hypothesis=complete,media_now=2.,
            last_speech=1.7,phrase_start=0.,stable_since=.8,end_of_stream=True,
            source_language='en')
        self.assertNotEqual(uncovered.action,'commit')
        self.assertIn('speech_after_confirmed_source_at_end_of_stream',uncovered.reasons)

    def test_punctuation_after_dangling_function_word_does_not_commit_stable_prefix_at_eos(self):
        stable = tuple(Word(text, 50.+i*.2, 50.+(i+1)*.2,
                            text if i == 0 else ' '+text)
                       for i, text in enumerate(('The', 'ending', 'depends', 'on', 'the.')))
        provisional = stable + (Word('next',51.2,51.4,' next'),
                                Word('example',51.4,51.6,' example'))
        controller = UniversalLatencyController('quality')
        controller.update(provisional, [0.]*len(source_text(provisional)))
        candidate = controller.candidate(stable)
        result = controller.decide(stable=candidate, hypothesis=provisional,
            media_now=51.62, last_speech=51.61, phrase_start=50., stable_since=51.,
            end_of_stream=True, source_language='en')
        self.assertEqual(candidate, stable)
        self.assertNotEqual(result.action, 'commit')
        self.assertIn('incomplete_english_phrase_at_end_of_stream', result.reasons)

    def test_busy_synthesizer_batches_real_boundaries_but_flushes_short_quiet_turns(self):
        for surfaces in [('A.', ' B.', ' C'), ('甲。', '乙。', '丙')]:
            words=tuple(Word(text.strip(),start,end,text) for text,start,end in
                        zip(surfaces,(0.,1.1,4.3),(1.,4.1,5.)))
            c=UniversalLatencyController();scores=[0.]*len(source_text(words))
            for count in (1,2):scores[len(source_text(words[:count]))-1]=.9
            c.update(words,scores)
            self.assertEqual(c.candidate(words),words[:1])
            self.assertEqual(c.candidate(words,preferred_seconds=4.),words[:2])
            short=words[:1];c.update(short,[0.]*len(source_text(short)))
            selected=c.candidate(short,preferred_seconds=4.)
            self.assertEqual(self.decide(c,selected,short,4.,1.).action,'commit')
            self.assertIn('acoustic_turn',self.decide(c,selected,short,4.,1.).reasons)

    def test_contextual_proposal_requires_agreed_source_and_right_context(self):
        words=(Word('完成',0.,3.,'完成'),Word('下一段',3.2,4.,'下一段'))
        agreement=LocalAgreement()
        self.assertEqual(agreement.update(words),())
        stable=agreement.update(words)
        self.assertEqual(stable,words)
        c=UniversalLatencyController('quality');c.update(words,[0.]*len(source_text(words)))
        selected=c.candidate(stable,proposed_end=1)
        first=self.decide(c,selected,words,4.3,4.)
        self.assertEqual(first.action,'wait')
        self.assertIn('awaiting_repeated_boundary_observation',first.reasons)
        result=self.decide(c,selected,words,4.4,4.)
        self.assertEqual(result.action,'commit')
        self.assertIn('contextual_chunk_proposal',result.reasons)
        with self.assertRaises(ValueError):c.candidate(words,proposed_end=2)
        tied=(words[0],Word('接续',3.,3.,'接续'))
        c.update(tied,[0.]*len(source_text(tied)))
        with self.assertRaises(ValueError):c.candidate(tied,proposed_end=1)

    def test_two_guarded_asr_contexts_confirm_a_contextual_cut(self):
        words=(Word('完成',0.,3.,'完成'),Word('下一段',3.2,4.,'下一段'))
        controller=UniversalLatencyController('balanced')
        controller.update(words,[0.]*len(source_text(words)))
        selected=controller.candidate(words,proposed_end=1)
        decision=controller.decide(stable=selected,hypothesis=words,media_now=4.4,
            last_speech=4.4,phrase_start=0.,stable_since=4.,
            confirmed_boundary_observations=2)
        self.assertEqual(decision.action,'commit')
        self.assertNotIn('awaiting_repeated_boundary_observation',decision.reasons)

    def test_long_uncertain_source_waits_within_budget_and_never_forces_commit(self):
        words=(Word('…',0.,1.,'…'),)
        quality=UniversalLatencyController('quality')
        balanced=UniversalLatencyController('balanced')
        low_latency=UniversalLatencyController('low_latency')
        self.assertGreater(quality.source_budget, balanced.source_budget)
        self.assertGreater(balanced.source_budget, low_latency.source_budget)
        self.assertEqual(balanced.source_budget, 16.0)
        for mode in ('quality','balanced','low_latency'):
            with self.subTest(mode=mode):
                c=UniversalLatencyController(mode)
                c.update(words,[.999])  # The final-input score is not evidence.
                self.assertLess(c.source_budget,30.)
                for now,expected in ((c.source_budget-.1,'wait'),
                                     (c.source_budget+.1,'fallback')):
                    result=c.decide(stable=c.candidate(words),hypothesis=words,
                        media_now=now,last_speech=now,phrase_start=0.,stable_since=0.)
                    self.assertEqual(result.action,expected)
                    self.assertIn('awaiting_contextual_boundary_or_acoustic_turn',result.reasons)

    def test_bounded_recovery_budget_extends_only_the_unresolved_wait(self):
        words=(Word('…',0.,1.,'…'),)
        expected={'quality':24.,'balanced':22.,'low_latency':20.}
        for mode,budget in expected.items():
            with self.subTest(mode=mode):
                c=UniversalLatencyController(mode)
                c.update(words,[.999])
                self.assertEqual(c.source_recovery_budget,budget)
                self.assertGreater(c.source_recovery_budget,c.source_budget)
                for now,action in ((budget-.1,'wait'),(budget+.1,'fallback')):
                    result=c.decide(stable=c.candidate(words),hypothesis=words,
                        media_now=now,last_speech=now,phrase_start=0.,stable_since=0.,
                        source_budget_seconds=c.source_recovery_budget)
                    self.assertEqual(result.action,action)

    def test_asr_agreement_cannot_erase_numeric_or_internal_punctuation_changes(self):
        from live_dubbing.adaptive import LocalAgreement
        for first, revised in [('2.5', '25'), ('-25', '25'), ('2-5', '25'),
                               ('٢٫٥', '٢٥'), ('%', '‰'), ('°C', 'C'),
                               ("can't", 'cant'), ("we're", 'were')]:
            with self.subTest(first=first, revised=revised):
                self.assertNotEqual(normalized(first), normalized(revised))
                agreement = LocalAgreement()
                agreement.update([Word(first, 0., 1.)])
                self.assertEqual(agreement.update([Word(revised, 0., 1.)]), ())
                self.assertEqual(agreement.update([Word(revised, 0., 1.)])[0].text, revised)
        self.assertEqual(normalized('Hello,'), normalized('hello.'))

    def test_source_and_target_model_capabilities_both_determine_routing(self):
        from unittest.mock import Mock
        from translation.backends import CapabilityRouter
        main,fallback=Mock(),Mock()
        router=CapabilityRouter(main,fallback)
        for source,target in [('lo','en'),('en','lo'),('sw','tr')]:
            router.translate('test',source,target)
        self.assertEqual(fallback.translate.call_count,3)
        main.translate.assert_not_called()
        router.translate('test','de','tr')
        main.translate.assert_called_once()

    def decide(self, controller, stable, hypothesis, now=3., last_speech=2.):
        return controller.decide(stable=stable,hypothesis=hypothesis,
            media_now=now,last_speech=last_speech,phrase_start=0.,stable_since=0.)

    def test_terminal_model_probability_never_proves_completion(self):
        for text in ('If it rains tomorrow,','Wenn es morgen regnet,',
                     'Eğer yarın yağmur yağarsa,','如果明天下雨，','إذا هطل المطر غدًا،'):
            with self.subTest(text=text):
                words=(Word(text,0.,2.,text),)
                controller=UniversalLatencyController()
                controller.update(words,[0.]*(len(text)-1)+[.99999])
                for _ in range(3):
                    stable=controller.candidate(words)
                    self.assertEqual(self.decide(controller,stable,words,2.2).action,'wait')

    def test_same_internal_boundary_policy_for_spacing_and_scripts(self):
        for first,tail in [('This is complete.',' Next thought'),('这是完整的。','下一句'),
                           ('هذه جملة كاملة.',' جملة أخرى'),('เสร็จแล้ว',' ต่อไป'),
                           ('E\u0301ste terminó.',' Sigue')]:
            with self.subTest(first=first):
                words=(Word(first,0.,1.,first),Word(tail.strip(),1.3,2.,tail))
                controller=UniversalLatencyController()
                text=source_text(words);scores=[0.]*len(text);scores[len(first)-1]=.9
                controller.update(words,scores)
                selected=controller.candidate(words)
                self.assertEqual(selected,words[:1])
                self.assertEqual(self.decide(controller,selected,words,1.4,1.4).action,'wait')
                selected=controller.candidate(words)
                result=self.decide(controller,selected,words,1.7,1.7)
                self.assertEqual(result.action,'commit')
                self.assertIn('learned_internal_boundary',result.reasons)

    def test_internal_boundary_lookahead_remains_mode_specific(self):
        for mode,lookahead in [('quality',.9),('balanced',.6),('low_latency',.35)]:
            with self.subTest(mode=mode):
                words=(Word('Complete.',0.,1.,'Complete.'),Word('Following',1.2,2.,' Following'))
                text=source_text(words);scores=[0.]*len(text);scores[len('Complete.')-1]=.9
                c=UniversalLatencyController(mode);c.update(words,scores)
                selected=c.candidate(words)
                self.assertEqual(selected,words[:1])
                early=self.decide(c,selected,words,1.+lookahead-.01,1.+lookahead-.01)
                self.assertEqual(early.action,'wait')
                selected=c.candidate(words)
                mature=self.decide(c,selected,words,1.+lookahead+.01,1.+lookahead+.01)
                self.assertEqual(mature.action,'commit')

    def test_mature_internal_boundary_needs_no_duplicate_controller_observation(self):
        words=(Word('Complete.',0.,1.,'Complete.'),Word('Following',1.2,2.,' Following'))
        text=source_text(words);scores=[0.]*len(text);scores[len('Complete.')-1]=.9
        c=UniversalLatencyController('quality');c.update(words,scores)
        selected=c.candidate(words)
        result=self.decide(c,selected,words,1.95,1.95)
        self.assertEqual(result.action,'commit')
        self.assertIn('mature_boundary_context',result.reasons)

    def test_contextual_boundary_is_reconfirmed_without_waiting_for_punctuation(self):
        words=(Word('complete',0.,2.,'complete'),Word('context',2.3,4.,' context'))
        c=UniversalLatencyController();c.update(words,[0.]*len(source_text(words)))
        selected=c.candidate(words,proposed_end=1)
        first=self.decide(c,selected,words,15.,15.)
        self.assertEqual(first.action,'wait')
        result=self.decide(c,selected,words,15.1,15.1)
        self.assertEqual(result.action,'commit')
        self.assertIn('repeated_boundary',result.reasons)

    def test_actual_right_context_and_acoustic_lookahead_are_required(self):
        words=(Word('A.',0.,1.,'A.'),Word(' B',1.1,1.3,' B'))
        c=UniversalLatencyController();c.update(words,[0.,.99,0.,0.])
        for _ in range(3):
            selected=c.candidate(words)
            self.assertEqual(self.decide(c,selected,words,1.3,1.3).action,'wait')
        self.assertEqual(self.decide(c,selected,words,1.7,1.7).action,'commit')

    def test_shared_timestamp_group_cannot_be_cut(self):
        words=(Word('第一。',0.,1.,'第一。'),Word('第二',1.,1.,'第二'))
        c=UniversalLatencyController();scores=[0.]*5;scores[2]=.99
        c.update(words,scores)
        self.assertEqual(c.candidate(words),words)

    def test_acoustic_turn_requires_all_source_to_agree(self):
        words=(Word('ຈົບ',0.,1.,'ຈົບ'),Word(' ຕໍ່',1.1,1.4,' ຕໍ່'))
        c=UniversalLatencyController();c.update(words,[0.]*len(source_text(words)))
        for _ in range(3):
            self.assertEqual(self.decide(c,c.candidate(words[:1]),words).action,'wait')
        for _ in range(2):
            selected=c.candidate(words)
            result=self.decide(c,selected,words,3.,1.4)
        self.assertEqual(result.action,'commit')
        self.assertIn('acoustic_turn',result.reasons)

    def test_decoding_surface_and_combining_marks_are_preserved(self):
        self.assertEqual(source_text([Word('你',0.,1.,'你'),Word('好',1.,2.,'好')]),'你好')
        self.assertEqual(source_text([Word('hello',0.,1.,'hello'),Word('world',1.,2.,' world')]),'hello world')
        self.assertEqual(normalized('E\u0301'),normalized('É'))
        self.assertNotEqual(normalized('กา'),normalized('ก้า'))


class ContextualChunkTests(unittest.IsolatedAsyncioTestCase):
    async def test_pending_speculative_prefix_only_holds_its_exact_confirmed_source_end(self):
        from live_dubbing.session import _same_source_endpoint
        self.assertTrue(_same_source_endpoint(76.30,76.30))
        self.assertTrue(_same_source_endpoint(76.05,76.30))
        self.assertFalse(_same_source_endpoint(65.24,76.30))
        self.assertFalse(_same_source_endpoint(None,76.30))

    async def test_same_qwen_cut_counts_across_new_guarded_asr_context(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        tokens=('They','finished','the','project','and','continued','working','later')
        agreed=tuple(Word(token,i*.7,(i+1)*.7,token if i==0 else ' '+token)
                     for i,token in enumerate(tokens))
        next_words=(Word('They',5.6,6.3,'They'),Word('finished',6.3,7.0,' finished'))
        hypothesis=agreed+next_words
        agreed2=agreed+next_words
        hypothesis2=agreed2+(Word('again',7.0,7.7,' again'),)
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en',contextual_chunking=True)
            models.select_boundary.return_value={'end_unit':4,'metrics':{}}
            models.assess_source_boundary.return_value={'crosses_dependency':False,'metrics':{}}
            async def emit(event): pass
            session=LiveSession(models,emit,Path(directory),mode='balanced')
            session.controller.update(hypothesis,[0.]*len(source_text(hypothesis)))
            candidate=session.controller.candidate(agreed,source_language='en')
            first=await session._contextual_candidate(agreed,hypothesis,6.4,candidate)
            self.assertEqual(first,agreed[:4])
            self.assertEqual(session.chunk_proposal_observations,1)
            session.controller.update(hypothesis2,[0.]*len(source_text(hypothesis2)))
            candidate=session.controller.candidate(agreed2,source_language='en')
            second=await session._contextual_candidate(agreed2,hypothesis2,7.8,candidate)
            self.assertEqual(second,agreed2[:4])
            self.assertEqual(session.chunk_proposal_observations,2)
            self.assertEqual(models.select_boundary.call_count,2)
            self.assertEqual(models.assess_source_boundary.call_count,2)
            await session.stop()

    async def test_reuses_guarded_qwen_cut_before_a_later_sat_candidate(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        words=tuple(Word(token,i*.5,(i+1)*.5,token if i==0 else ' '+token)
                    for i,token in enumerate(('A','short','safe','clause','continues','with','detail','today')))
        scores=[0.]*len(source_text(words))
        scores[len(source_text(words[:7]))-1]=.95
        async def emit(event): pass
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en',contextual_chunking=True)
            session=LiveSession(models,emit,Path(directory),mode='balanced')
            session.controller.update(words,scores)
            source_signature=tuple((normalized(word.text),round(word.start/.35),round(word.end/.35))
                                    for word in words)
            session.boundary_source_signature=source_signature
            session.guarded_boundary_source_signature=source_signature
            session.chunk_proposal=(tuple(normalized(word.text) for word in words[:3]),words[2].end)
            candidate=session.controller.candidate(words,source_language='en')
            self.assertEqual(candidate,words[:7])
            selected=await session._contextual_candidate(words,words,5.,candidate)
            self.assertEqual(selected,words[:3])
            models.select_boundary.assert_not_called()
            await session.stop()

    async def test_repeated_guard_rejection_allows_a_fresh_guarded_alternative(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        text='we discussed constraints with clear details later'.split()
        words=tuple(Word(x,i*.8,(i+1)*.8,x if i==0 else ' '+x)
                    for i,x in enumerate(text))
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en',contextual_chunking=True)
            models.select_boundary.side_effect=lambda **k: {
                'end_unit': 2 if 2 in k['eligible_ends'] else 6, 'metrics': {}}
            models.assess_source_boundary.side_effect=[
                {'crosses_dependency':True}, {'crosses_dependency':True},
                {'crosses_dependency':False}]
            async def emit(event): pass
            session=LiveSession(models,emit,Path(directory),mode='balanced')
            try:
                observations=(words,
                    words+(Word('today',4.8,5.6,' today'),Word('again',5.6,6.4,' again')),
                    words+(Word('today',4.8,5.6,' today'),Word('again',5.6,6.4,' again'),
                           Word('last',6.4,7.2,' last'),Word('week',7.2,8.0,' week')))
                for i, observation in enumerate(observations):
                    session.controller.update(observation,[0.]*len(source_text(observation)))
                    candidate=session.controller.candidate(observation,source_language='en')
                    selected=await session._contextual_candidate(
                        observation,observation,9.+i,candidate)
                    self.assertEqual(selected,observation if i<2 else observation[:6])
                self.assertEqual(models.assess_source_boundary.call_count,3)
                self.assertNotIn(2,models.select_boundary.call_args.kwargs['eligible_ends'])
            finally: await session.stop()

    async def test_rejected_boundary_frontier_advances_contextual_target(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        tokens='The earlier section was complete and we continued with several new observations today'.split()
        words=tuple(Word(token,i*.55,(i+1)*.55,token if i==0 else ' '+token)
                    for i,token in enumerate(tokens))
        async def emit(event): pass
        with TemporaryDirectory() as directory:
            model=Mock(source_language='en',contextual_chunking=True)
            model.select_boundary.side_effect=lambda **kwargs: {
                'end_unit':next(end for end in kwargs['eligible_ends'] if end >= 7),
                'metrics':{}}
            model.assess_source_boundary.return_value={'crosses_dependency':True,'metrics':{}}
            session=LiveSession(model,emit,Path(directory),mode='balanced')
            try:
                rejected=tuple(normalized(word.text) for word in words[:4])
                session.rejected_chunk_proposals[rejected]=2
                session.controller.update(words,[0.]*len(source_text(words)))
                selected=await session._contextual_candidate(
                    words,words,9.,session.controller.candidate(words))
                self.assertEqual(selected,words)
                eligible=model.select_boundary.call_args.kwargs['eligible_ends']
                self.assertTrue(all(words[end-1].end >= words[3].end+1.0 for end in eligible))
                frontier=next(event for event in session.events
                              if event['type']=='source_chunk_frontier')
                self.assertTrue(frontier['advanced'])
                self.assertGreater(frontier['eligible_after'],0)
            finally:
                await session.stop()

    async def test_incomplete_english_cut_is_vetoed_before_model_guard(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            model=Mock(source_language='en',contextual_chunking=True)
            text='A lot of people ended up getting hired just because they had AI on their resume and there is a big adjustment'.split()
            words=tuple(Word(x,i*.3,(i+1)*.3,x if i==0 else ' '+x) for i,x in enumerate(text))
            model.select_boundary.return_value={'end_unit':text.index('and')+1,'metrics':{}}
            events=[]
            async def emit(event):events.append(event)
            session=LiveSession(model,emit,Path(directory),mode='balanced')
            session.controller.update(words,[0.]*len(source_text(words)))
            selected=await session._contextual_candidate(words,words,9.,session.controller.candidate(words))
            self.assertEqual(selected,words)
            model.assess_source_boundary.assert_not_called()
            event=next(e for e in events if e['type']=='source_chunk_guard')
            self.assertFalse(event['accepted'])
            self.assertEqual(event['reason'],'incomplete_english_internal_boundary')
            rejected_prefix=tuple(normalized(word.text) for word in words[:text.index('and')+1])
            self.assertEqual(session.rejected_chunk_proposals[rejected_prefix],2)

    async def test_model_cut_before_known_unit_is_vetoed_before_gpu_guard(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            model=Mock(source_language='en',contextual_chunking=True)
            model.select_boundary.return_value={'end_unit':1,'metrics':{}}
            events=[]
            async def emit(event): events.append(event)
            session=LiveSession(model,emit,Path(directory),mode='balanced')
            words=(Word('30',0.,1.2,'30'),Word('years',1.2,2.,' years'),
                   Word('later',2.1,3.,' later'))
            session.controller.update(words,[0.]*len(source_text(words)))
            selected=await session._contextual_candidate(words,words,5.,session.controller.candidate(words))
            self.assertEqual(selected,words)
            model.assess_source_boundary.assert_not_called()
            guard=next(e for e in events if e['type']=='source_chunk_guard')
            self.assertFalse(guard['accepted'])
            self.assertEqual(guard['reason'],'quantity_unit_continuation')
            self.assertIsNone(session.chunk_proposal)
    async def test_caption_recovery_sends_no_pcm_and_cannot_count_as_spoken_tts(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.models import RenderedPhrase
        from live_dubbing.session import LiveSession
        import time
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en');events=[]
            models.render.return_value=RenderedPhrase('caption-id',0.,2.,'source','translation',
                {'speaker_id':'LIVE_SPEAKER_00'},b'',[],{'presentation_mode':'caption_only'},[])
            async def emit(event):events.append(event)
            session=LiveSession(models,emit,Path(directory));now=time.perf_counter()
            await session.phrase_queue.put({'identifier':'caption-id','source_text':'source','pcm':b'',
                'source_start':0.,'source_end':2.,'source_language':'en','committed_at':now,
                'asr_completed_at':now,'speech_end':2.,'speech_end_to_asr_seconds':.2,
                'speech_end_method':'unit-fixture','endpoint_media':3.})
            await session.phrase_queue.put(None)
            await session._synthesize()
            self.assertEqual(models.render.call_args.kwargs['maximum_presentation_tempo'],1.30)
            self.assertFalse([e for e in events if e['type']=='pcm'])
            self.assertEqual(session.sent_count,0);self.assertEqual(session.caption_count,1)
            await session.acknowledge({'phrase_id':'caption-id','generated_audio_seconds':0,
                'video_playback_rate':.25,'requested_video_playback_rate':.125,
                'video_playback_rate_clamped':True,
                'video_play_request_to_first_frame_ms':18.5,
                'video_play_resolution_to_audio_schedule_ms':42.25,
                'video_play_succeeded':True})
            self.assertEqual(session.played_speech_count,0);self.assertEqual(session.played_caption_count,1)
            self.assertEqual(events[-1]['presentation_mode'],'caption_only')
            self.assertEqual(events[-1]['video_playback_rate'],.25)
            self.assertEqual(events[-1]['requested_video_playback_rate'],.125)
            self.assertTrue(events[-1]['video_playback_rate_clamped'])
            self.assertEqual(events[-1]['video_play_request_to_first_frame_ms'],18.5)
            self.assertEqual(events[-1]['video_play_resolution_to_audio_schedule_ms'],42.25)
            self.assertTrue(events[-1]['video_play_succeeded'])

    async def test_quality_mode_keeps_conservative_presentation_cap(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.models import RenderedPhrase
        from live_dubbing.session import LiveSession
        import time
        async def emit(_event): pass
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en')
            models.render.return_value=RenderedPhrase('quality-id',0.,2.,'source','translation',
                {'speaker_id':'LIVE_SPEAKER_00'},b'',[],{'presentation_mode':'caption_only'},[])
            session=LiveSession(models,emit,Path(directory),mode='quality')
            await session.phrase_queue.put({'identifier':'quality-id','source_text':'source','pcm':b'',
                'source_start':0.,'source_end':2.,'source_language':'en','committed_at':time.perf_counter(),
                'asr_completed_at':time.perf_counter(),'speech_end':2.,'speech_end_to_asr_seconds':.2,
                'speech_end_method':'unit-fixture','endpoint_media':3.})
            await session.phrase_queue.put(None)
            await session._synthesize()
            self.assertEqual(models.render.call_args.kwargs['maximum_presentation_tempo'],1.20)

    async def test_eos_releases_inference_without_waiting_for_playback_ack(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en');events=[]
            async def emit(event):events.append(event)
            session=LiveSession(models,emit,Path(directory))
            session.sent.add('already-buffered-audio')
            await session.phrase_queue.put(None)
            await session._synthesize()
            models.close.assert_called_once()
            self.assertEqual(events[0]['pending_playback'],1)
            self.assertFalse(session.played)

    async def test_source_proposal_is_reconsidered_with_new_context_and_source_revisions(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            models=Mock(source_language='auto',contextual_chunking=True)
            models.select_boundary.return_value={'end_unit':1,'metrics':{'backend':'unit-fixture'}}
            models.assess_source_boundary.return_value={'crosses_dependency':False,'metrics':{}}
            events=[]
            async def emit(event):events.append(event)
            session=LiveSession(models,emit,Path(directory),mode='quality')
            words=(Word('2.5',0.,2.,'2.5'),Word('units',2.1,5.,' units'),Word('next',5.1,7.,' next'))
            for _ in range(2):
                session.controller.update(words,[0.]*len(source_text(words)))
                selected=await session._contextual_candidate(words,words,8.,session.controller.candidate(words))
                self.assertEqual(selected,words[:1])
            self.assertEqual(models.select_boundary.call_count,1)
            self.assertEqual(models.select_boundary.call_args.kwargs['preferred_end_unit'],2)
            revised=(Word('25',0.,2.,'25'),*words[1:])
            session.controller.update(revised,[0.]*len(source_text(revised)))
            selected=await session._contextual_candidate(revised,revised,8.,session.controller.candidate(revised))
            self.assertEqual(selected,revised[:1],
                repr([(event['type'],event.get('reason'),event.get('accepted')) for event in events]))
            self.assertEqual(models.select_boundary.call_count,2)
            self.assertEqual([call.kwargs['preferred_end_unit']
                              for call in models.select_boundary.call_args_list],[2,2])
            self.assertEqual([call.kwargs['preferred_end_unit']
                              for call in models.select_boundary.call_args_list],[2,2])
            self.assertEqual(len([event for event in events if event['type']=='source_chunk_proposal']),2)
            self.assertEqual(len([event for event in events if event['type']=='source_chunk_guard']),2)

    async def test_contextual_cut_is_held_when_later_context_exposes_dependency(self):
        from unittest.mock import Mock
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from live_dubbing.session import LiveSession
        with TemporaryDirectory() as directory:
            models=Mock(source_language='en',contextual_chunking=True)
            # The deterministic guard now vetoes the older "who are | ..."
            # example before the model check. Use "people | who are ..." to
            # keep exercising later-context model re-evaluation here.
            models.select_boundary.return_value={'end_unit':9,'metrics':{}}
            models.assess_source_boundary.side_effect=[
                {'crosses_dependency':False,'metrics':{}},
                {'crosses_dependency':True,'metrics':{}}]
            events=[]
            async def emit(event):events.append(event)
            session=LiveSession(models,emit,Path(directory),mode='balanced')
            text=['They','have','ended','up','with','a','lot','of','people','who','are','underqualified']
            words=tuple(Word(token,i*.5,(i+1)*.5,('' if i==0 else ' ')+token)
                        for i,token in enumerate(text))
            observations=(words[:10], words)
            for index, observation in enumerate(observations):
                session.controller.update(observation,[0.]*len(source_text(observation)))
                original=session.controller.candidate(observation)
                selected=await session._contextual_candidate(observation,observation,8.+index*.2,original)
                decision=session.controller.decide(stable=selected,hypothesis=words,
                    media_now=8.+index*.2,last_speech=8.+index*.2,phrase_start=0.,
                    stable_since=0.,confirmed_clause_boundary=session.controller.clause_confirms(selected,words),
                    source_language='en')
                self.assertEqual(decision.action,'wait')
            self.assertEqual(selected,words)
            self.assertIsNone(session.chunk_proposal)
            self.assertEqual([e['accepted'] for e in events if e['type']=='source_chunk_guard'],[True,False])
            self.assertEqual(session.agreement.committed_end,-1.)


if __name__=='__main__':unittest.main()
