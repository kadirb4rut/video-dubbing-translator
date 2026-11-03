import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from live_dubbing.adaptive import Word
from live_dubbing.simultaneous import AdaptiveSimultaneousTranslationController


def words(texts, start=0.0, step=.55):
    return tuple(Word(text, start+i*step, start+(i+1)*step,
                      text if i == 0 else ' '+text)
                 for i, text in enumerate(texts))


class SimultaneousTranslationTests(unittest.TestCase):
    def test_endpoint_commit_without_prefetch_has_a_safe_policy(self):
        from live_dubbing.session import LiveSession

        self.assertEqual(LiveSession._source_commit_policy(None), 'source_endpoint_policy')
        self.assertEqual(LiveSession._source_commit_policy(
            SimpleNamespace(evidence={'policy':'incremental_target_stable_read_wait_write'})),
            'incremental_target_stable_read_wait_write')
        self.assertEqual(LiveSession._source_commit_policy(
            SimpleNamespace(evidence={})), 'confirmed_source_boundary_prefetch')

    def test_only_an_exact_confirmed_source_chunk_can_be_written(self):
        controller = AdaptiveSimultaneousTranslationController('balanced')
        source = words(['We', 'can', 'start.'])
        extended = words(['We', 'can', 'start.', 'the', 'review'])
        controller.mark_query(source, 1.)
        controller.observe(source, {'text': 'Başlayabiliriz', 'metrics': {}}, 1.1,
                           source_asr_completed_at=.9)

        self.assertIsNone(controller.source_boundary_proposal(source,
            boundary_kind=None, right_context_seconds=0.))
        wrong_owner = controller.source_boundary_proposal(extended,
            boundary_kind='sentence', right_context_seconds=1.5)
        self.assertIsNone(wrong_owner)

        proposal = controller.source_boundary_proposal(source,
            boundary_kind='sentence', right_context_seconds=1.5)
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.source_words, source)
        self.assertEqual(proposal.source_text, 'We can start.')
        self.assertEqual(proposal.target_text, 'Başlayabiliriz')
        self.assertEqual(proposal.evidence['policy'], 'confirmed_source_boundary_prefetch')
        self.assertFalse(proposal.evidence['target_text_split_or_aligned'])
        self.assertTrue(proposal.evidence['right_context_policy_target_observed_met'])
        self.assertEqual(proposal.source_asr_completed_at, .9)

    def test_asr_revision_invalidates_prefetched_translation(self):
        controller = AdaptiveSimultaneousTranslationController('quality')
        source = words(['We', 'will', 'leave.'])
        controller.mark_query(source, 1.)
        controller.observe(source, {'text': 'Gideceğiz.', 'metrics': {}}, 1.1)
        revised = (Word('We', 0., .55, 'We'), Word('will', .55, 1.1, ' will'),
                   Word('not', 1.1, 1.65, ' not'), Word('leave.', 1.65, 2.2, ' leave.'))
        controller.refresh_source(revised)
        self.assertEqual(controller.hypotheses, [])
        self.assertEqual(controller.rejected_revision_count, 1)
        self.assertIsNone(controller.source_boundary_proposal(revised,
            boundary_kind='sentence', right_context_seconds=.0))

    def test_accepted_source_interval_is_monotonic_and_never_replayed(self):
        controller = AdaptiveSimultaneousTranslationController('balanced')
        source = words(['We', 'can', 'start.'])
        controller.mark_query(source, 1.)
        controller.observe(source, {'text': 'Başlayabiliriz.', 'metrics': {}}, 1.1)
        proposal = controller.source_boundary_proposal(source,
            boundary_kind='sentence', right_context_seconds=.0)
        self.assertIsNotNone(proposal)
        controller.accept(proposal)
        self.assertEqual(controller.committed_source_end, source[-1].end)
        self.assertEqual(controller.hypotheses, [])
        with self.assertRaises(ValueError):
            controller.accept(proposal)

    def test_source_revision_and_language_modes_remain_bounded_and_distinct(self):
        quality = AdaptiveSimultaneousTranslationController('quality')
        balanced = AdaptiveSimultaneousTranslationController('balanced')
        low = AdaptiveSimultaneousTranslationController('low_latency')
        self.assertGreater(quality.policy['right_context'], balanced.policy['right_context'])
        self.assertGreater(balanced.policy['right_context'], low.policy['right_context'])
        self.assertGreater(quality.policy['query_interval'], balanced.policy['query_interval'])
        self.assertGreater(balanced.policy['query_interval'], low.policy['query_interval'])
        self.assertEqual(quality.history_limit, 12)


class LiveSimultaneousIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_guarded_commit_adapter_preserves_translation_timing_contract(self):
        from live_dubbing.session import LiveSession

        source = words(['feeding', 'that', 'to', 'a', 'large', 'language', 'model'])
        commit = SimpleNamespace(
            source_words=source,
            verified=SimpleNamespace(translation='Bunu büyük bir dil modeline gönderiyoruz.'),
            evidence={'policy':'incremental_target_stable_read_wait_write'})
        proposal_metrics = {
            'proposal_started_at':10.0, 'proposal_completed_at':10.4,
            'proposal_seconds':.4, 'candidate_check_started_at':10.5,
            'candidate_check_completed_at':11.1,
            'translation_draft_metrics':{
                'translation_seconds':.31, 'translator_inference_seconds':.30,
                'translation_started_at':10.02, 'translation_completed_at':10.33,
                'translation_validation_seconds':.001,
            },
        }

        result = LiveSession._read_wait_write_commit_adapter(
            None, commit, proposal_metrics, asr_completed_at=9.9)

        self.assertEqual(result.draft['metrics']['translation_seconds'], .31)
        self.assertEqual(result.draft['metrics']['translation_started_at'], 10.02)
        self.assertEqual(result.draft['metrics']['translation_completed_at'], 10.33)
        self.assertEqual(result.draft['metrics']['translation_validation_seconds'], .001)
        self.assertEqual(result.draft['metrics']['simultaneous_semantic_check_started_at'], 10.5)
        self.assertEqual(result.draft['metrics']['simultaneous_semantic_check_completed_at'], 11.1)
        self.assertEqual(result.target_text, 'Bunu büyük bir dil modeline gönderiyoruz.')

    async def test_only_a_still_eligible_guarded_cut_maps_to_rww_source_units(self):
        from live_dubbing.session import LiveSession

        stable = words(['the', 'system', 'runs', 'while', 'source', 'continues'], step=.6)
        proposal = (('the', 'system', 'runs'), stable[2].end)
        self.assertEqual(LiveSession._approved_read_wait_write_end(
            stable, (2, 3), proposal), 3)
        self.assertIsNone(LiveSession._approved_read_wait_write_end(
            stable, (2, 4), proposal))
        revised = (stable[0], stable[1], type(stable[2])('keeps', stable[2].start,
            stable[2].end, ' keeps'), *stable[3:])
        self.assertIsNone(LiveSession._approved_read_wait_write_end(
            revised, (3,), proposal))
        self.assertIsNone(LiveSession._approved_read_wait_write_end(
            stable, (3,), (proposal[0], stable[2].end + .5)))

    async def test_guarded_rww_proposal_translates_exact_prefix_deterministically(self):
        import json
        from types import SimpleNamespace
        from live_dubbing.adaptive import Word
        from live_dubbing.models import LocalModels
        from translation.backends import CapabilityRouter

        model = object.__new__(LocalModels)
        model.live_translation_backend = 'hymt2-1.8b'
        model.target_language = 'tr'
        model.glossary = {}
        translator = SimpleNamespace(_translator=SimpleNamespace(
            last_generation={'prompt':'private source prompt', 'new_tokens':6}))
        model.translator = CapabilityRouter(translator)
        requests = []
        model.translate_draft = lambda **request: requests.append(request) or {
            'text':'İncelemeye devam etmeliyiz.', 'metrics':{'inference_scheduler_wait_seconds':.02}}
        model._translation_backend_name = lambda language: 'hymt2-1.8b'
        snapshot = (Word('We', 0., .5, 'We'), Word('should', .5, 1., ' should'),
            Word('continue', 1., 1.6, ' continue'), Word('the', 1.6, 1.9, ' the'),
            Word('review', 1.9, 2.4, ' review'))
        result = LocalModels.propose_simultaneous(model, words=snapshot,
            source_language='en', eligible_ends=(3,), preferred_end_unit=3,
            previous_source='Earlier source.', previous_translation='Önceki Türkçe.',
            approved_end_unit=3,
            approved_following_text=' a frozen, stable right context',
            approved_source_text='We should continue,',
            approved_duration_seconds=1.75)
        proposal = json.loads(result['raw'])
        self.assertEqual(proposal, {'status':'ready', 'end_unit':3,
            'translation':'İncelemeye devam etmeliyiz.'})
        self.assertEqual(requests, [{
            'source_text':'We should continue,', 'source_language':'en',
            'following_text':' a frozen, stable right context', 'previous_text':'Earlier source.',
            'previous_translation':'Önceki Türkçe.', 'duration':1.75,
            'include_following_context':True, 'deterministic_translation':True}])
        self.assertEqual(result['metrics']['proposal_strategy'],
            'qwen_guarded_cut_deterministic_hymt2_exact_prefix')
        self.assertEqual(result['metrics']['right_context_policy'],
            'frozen_first_eligible_stable_snapshot')
        self.assertEqual(result['metrics']['translation_prompt_context_policy'],
            'frozen_full_approved_cut_snapshot')
        self.assertNotIn('prompt', result['metrics']['translation_generation'])

    async def test_contextual_chunk_can_reach_write_before_source_utterance_ends(self):
        """Structural regression only: this fixture does not certify model quality or TTS."""
        from live_dubbing.adaptive import source_text
        from live_dubbing.session import LiveSession

        class Models:
            source_language = 'en'
            contextual_chunking = True
            load_seconds = 0.

            def __init__(self):
                self.translation_requests = []
                self.boundary_requests = []

            def select_boundary(self, **request):
                self.boundary_requests.append(request)
                return {'end_unit':4, 'metrics':{'backend':'boundary-fixture'}}

            def assess_source_boundary(self, **request):
                return {'crosses_dependency':False, 'metrics':{'backend':'boundary-fixture'}}

            def translate_draft(self, **request):
                self.translation_requests.append(request)
                return {'text':'Sistem yerel çalışıyor.', 'metrics':{
                    'translation_seconds':.1, 'translator_inference_seconds':.09,
                    'translation_started_at':10., 'translation_completed_at':10.1}}

            def close(self):
                pass

        source = words(['The','system','runs','locally','while','the','source','keeps',
            'moving','through','one','continuous','long','sentence','and','keeps',
            'adding','new','information','as'], step=1.)
        following = source + (Word('it',20.,20.5,' it'), Word('continues',20.5,21.,' continues'))
        with TemporaryDirectory() as directory:
            events = []

            async def emit(event):
                events.append(event)

            models = Models()
            session = LiveSession(models, emit, Path(directory), mode='balanced')
            session.phrase_start = source[0].start
            self.assertEqual(session.agreement.update(source), ())
            stable = session.agreement.update(following)
            hypothesis = session.agreement.hypothesis
            self.assertEqual(stable, source)
            self.assertEqual(len(hypothesis), len(following))
            committed = None
            decision = None

            # Two separate rolling-ASR observations, all-zero SaT scores, and
            # a Qwen-shaped contextual proposal ending after the first clause.
            for media_now in (21., 21.5):
                if media_now > 21.:
                    stable = session.agreement.update(following)
                    hypothesis = session.agreement.hypothesis
                sat_scores = [0.] * len(source_text(hypothesis))
                self.assertFalse(any(sat_scores))
                session.controller.update(hypothesis, sat_scores)
                selected = session.controller.candidate(stable)
                selected = await session._contextual_candidate(
                    stable, hypothesis, media_now, selected)
                self.assertEqual(selected, source[:4],
                    repr([(event['type'], event.get('reason'), event.get('accepted'),
                           event.get('stage')) for event in events]))
                boundary_kind = ('clause' if session.controller.clause_confirms(
                    selected, stable) else None)
                self.assertEqual(boundary_kind, 'clause')
                decision = session.controller.decide(stable=selected,
                    hypothesis=hypothesis, media_now=media_now, last_speech=21.,
                    phrase_start=0., stable_since=19.,
                    confirmed_clause_boundary=True, source_language='en')
                committed, _ = await session._advance_simultaneous(selected, 'en',
                    media_now, allow_query=decision.action != 'commit',
                    asr_completed_at=media_now-.1, boundary_kind=boundary_kind,
                    right_context_seconds=stable[-1].end-selected[-1].end,
                    following_text=source_text(hypothesis[len(selected):]))
                if decision.action != 'commit':
                    self.assertIsNone(committed)
                    await session.target_translation_task

            self.assertEqual(decision.action, 'commit')
            self.assertIsNotNone(committed, 'target_draft=' + str(len(models.translation_requests)) +
                ', stored_hypotheses=' + str(len(session.simultaneous.hypotheses)) +
                ', events=' + str([(event['type'], event.get('error_type')) for event in events]))
            self.assertEqual(committed.source_text, source_text(source[:4]))
            self.assertEqual(committed.target_text, 'Sistem yerel çalışıyor.')
            self.assertLess(committed.source_end, following[-1].end)
            self.assertEqual(len(models.translation_requests), 1)
            # The exact second observation reuses the already guarded cut.
            self.assertEqual(len(models.boundary_requests), 1)
            session.agreement.commit(committed.source_words)
            session.simultaneous.accept(committed)
            self.assertAlmostEqual(session.agreement.committed_end, committed.source_end)
            self.assertAlmostEqual(session.simultaneous.committed_source_end, committed.source_end)
            await session.stop()

    async def test_prefetch_uses_confirmed_source_context_without_emitting_target_text(self):
        from live_dubbing.session import LiveSession

        class Models:
            source_language = 'fr'
            contextual_chunking = False

            def __init__(self):
                self.requests = []

            def close(self):
                pass

            def translate_draft(self, **request):
                self.requests.append(request)
                return {'text': 'Nous le savons.', 'metrics': {
                    'translation_seconds': .1,
                    'translator_inference_seconds': .09,
                    'translation_started_at': 10.,
                    'translation_completed_at': 10.1,
                }}

        with TemporaryDirectory() as directory:
            events = []

            async def emit(event):
                events.append(event)

            models = Models()
            session = LiveSession(models, emit, Path(directory), mode='balanced')
            source = words(['Nous', 'le', 'savons.'])
            proposal, _ = await session._advance_simultaneous(source, 'fr', 2.5,
                allow_query=True, asr_completed_at=9.9, boundary_kind='clause',
                right_context_seconds=1.4, following_text='mais nous continuons.')
            self.assertIsNone(proposal)
            await session.target_translation_task

            proposal, _ = await session._advance_simultaneous(source, 'fr', 2.6,
                allow_query=False, asr_completed_at=10.2, boundary_kind='clause',
                right_context_seconds=1.5, following_text='mais nous continuons.')
            self.assertIsNotNone(proposal)
            self.assertEqual(len(models.requests), 1)
            request = models.requests[0]
            self.assertEqual(request['source_text'], 'Nous le savons.')
            self.assertEqual(request['following_text'], 'mais nous continuons.')
            self.assertTrue(request['include_following_context'])
            self.assertEqual(proposal.source_text, 'Nous le savons.')
            self.assertEqual(proposal.evidence['source_boundary_kind'], 'clause')
            self.assertTrue(all('target_text' not in event for event in events))
            self.assertEqual([event['type'] for event in events].count('target_draft_prefetch'), 1)
            await session.stop()

    async def test_endpoint_commit_reuses_only_exact_prefetch_and_keeps_source_ownership(self):
        from unittest.mock import AsyncMock
        from live_dubbing.session import LiveSession
        from live_dubbing.speech import Transcript

        class Boundary:
            def predict(self, text):
                return [0.0] * len(text)

            def close(self):
                pass

        class Models:
            source_language = 'en'
            contextual_chunking = False
            live_translation_backend = 'hymt2-1.8b'
            live_boundary_backend = 'qwen3.5-27b'

            def __init__(self):
                self.words = ()
                self.requests = []

            def recognize(self, _audio, _start, _language):
                return Transcript(self.words, 'en', 1., -.1)

            def translate_draft(self, **request):
                self.requests.append(request)
                return {'text': 'Başlayabiliriz.', 'metrics': {
                    'translation_seconds': .1,
                    'translator_inference_seconds': .09,
                    'translation_started_at': 10.,
                    'translation_completed_at': 10.1,
                }}

            def propose_simultaneous(self, **_request):
                raise AssertionError('The fixture holds RWW at WAIT.')

            def assess_simultaneous_candidate(self, **_request):
                raise AssertionError('No RWW candidate should reach semantic checks.')

            def close(self):
                pass

        with TemporaryDirectory() as directory:
            events = []

            async def emit(event):
                events.append(event)

            models = Models()
            session = LiveSession(models, emit, Path(directory), mode='balanced',
                                  boundary=Boundary())
            self.assertTrue(session.read_wait_write_enabled)
            # Model a safe RWW WAIT while preserving the ordinary boundary
            # prefetch path; no RWW source ownership is transferred here.
            session._advance_read_wait_write = AsyncMock(
                return_value=(None, None, 0.0))
            session.phrase_start = 0.
            session.origin = session.buffer_start = 0.
            session.buffer = bytearray(4*32000)
            source = words(['We', 'can', 'start.'])
            session.last_speech = source[-1].end
            session.pauses = [(source[-1].end, source[-1].end+1.5)]

            # The first matching ASR window establishes local agreement; the
            # next reads hold a confirmed acoustic turn while translation runs.
            for media in (3.4, 3.5, 3.6, 3.8):
                models.words = source
                await session._decode(media)
                if session.target_translation_task is not None:
                    await session.target_translation_task

            queued = session.phrase_queue.get_nowait()
            self.assertEqual(queued['source_text'], 'We can start.')
            self.assertEqual(queued['source_start'], source[0].start)
            self.assertEqual(queued['source_end'], source[-1].end)
            self.assertEqual(queued['translation_draft']['text'], 'Başlayabiliriz.')
            self.assertEqual(queued['target_commit_evidence']['policy'],
                             'confirmed_source_boundary_prefetch')
            self.assertIsNone(queued['translation_request'])
            self.assertEqual(session.agreement.committed_end, source[-1].end)
            self.assertEqual(session.simultaneous.committed_count, 1)
            self.assertEqual(len(models.requests), 1)
            await session.stop()


class LiveReadWaitWriteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_early_chunk_uses_exact_source_owner_and_spoken_context(self):
        import json
        import time
        from live_dubbing.adaptive import source_text
        from live_dubbing.session import LiveSession

        class Models:
            source_language = 'en'
            live_translation_backend = 'hymt2-1.8b'
            live_boundary_backend = 'qwen3.5-27b'
            load_seconds = 0.

            def __init__(self):
                self.proposals = []
                self.checks = []

            def propose_simultaneous(self, **request):
                self.proposals.append(request)
                return {'raw':json.dumps({'status':'ready','end_unit':3,
                    'translation':'Bu düşünceyi şimdi açıklayabiliriz.'}),
                    'metrics':{'proposal_started_at':time.perf_counter(),
                               'proposal_completed_at':time.perf_counter(),
                               'translation_backend':'hymt2-1.8b'}}

            def assess_simultaneous_candidate(self, **request):
                self.checks.append(request)
                return {'result':{'crosses_dependency':False,'supported':True},
                    'metrics':{'provider':'qwen3.5-27b_independent_of_hymt2'}}

            def close(self):
                pass

        def speech(count):
            return tuple(Word(f'w{i+1}', i*.5, (i+1)*.5,
                f' {"w"+str(i+1)}' if i else 'w1') for i in range(count))

        async def emit(_event):
            pass

        with TemporaryDirectory() as directory:
            models = Models()
            session = LiveSession(models, emit, Path(directory), mode='balanced')
            first = speech(8)
            commit, ownership, _ = await session._advance_read_wait_write(
                first, 'en', 6., allow_query=True, asr_completed_at=5.9)
            self.assertIsNone(commit)
            self.assertIsNone(ownership)
            await session.read_wait_write_task
            commit, ownership, _ = await session._advance_read_wait_write(
                first, 'en', 6.1, allow_query=True, asr_completed_at=6.)
            self.assertIsNone(commit)
            self.assertIsNone(ownership)
            self.assertEqual(len(models.proposals),1)
            self.assertEqual(models.checks,[])

            grown = speech(10)
            session.read_wait_write.last_query_at = time.perf_counter()-3.
            commit, ownership, _ = await session._advance_read_wait_write(
                grown, 'en', 7., allow_query=True, asr_completed_at=6.9)
            self.assertIsNone(commit)
            await session.read_wait_write_task
            commit, ownership, _ = await session._advance_read_wait_write(
                grown, 'en', 8., allow_query=False, asr_completed_at=7.9)

            self.assertIsNotNone(commit)
            self.assertIsNotNone(ownership)
            self.assertEqual(commit.source_text, source_text(grown[:3]))
            self.assertEqual(commit.target_text, 'Bu düşünceyi şimdi açıklayabiliriz.')
            self.assertLess(commit.source_end, grown[-1].end)
            self.assertEqual(models.checks[0]['prefix'],source_text(grown[:3]))
            self.assertEqual(models.checks[0]['continuation'],source_text(grown[3:]))
            self.assertTrue(commit.draft['metrics']['read_wait_write'])
            self.assertFalse(commit.evidence['translation_correctness_certified'])

            session.read_wait_write.accept(ownership)
            self.assertEqual(session.read_wait_write.gate.accepted_end,commit.source_end)
            self.assertEqual(tuple(w.text for w in session.read_wait_write.unowned_words(
                speech(12))),tuple(f'w{i}' for i in range(4,13)))
            await session.stop()

    async def test_approved_cut_freezes_complete_translation_context_across_growth(self):
        import json
        import time
        from live_dubbing.adaptive import Word, normalized
        from live_dubbing.session import LiveSession

        class Models:
            source_language = 'en'
            live_translation_backend = 'hymt2-1.8b'
            live_boundary_backend = 'qwen3.5-27b'
            load_seconds = 0.

            def __init__(self):
                self.proposals = []

            def propose_simultaneous(self, **request):
                self.proposals.append(request)
                return {'raw':json.dumps({'status':'wait','end_unit':0,'translation':''}),
                    'metrics':{'proposal_started_at':time.perf_counter(),
                               'proposal_completed_at':time.perf_counter(),
                               'translation_backend':'hymt2-1.8b'}}

            def assess_simultaneous_candidate(self, **_request):
                raise AssertionError('WAIT proposals do not reach candidate checks')

            def close(self):
                pass

        def speech(count, *, revised=False):
            result = []
            for index in range(count):
                start = index*.5 + (.02 if revised else 0.)
                end = (index+1)*.5 + (.18 if revised and index == 2 else
                                      .02 if revised else 0.)
                text = f'w{index+1}'
                surface = (text if index == 0 else ' '+text)
                if revised and index == 1:
                    surface += ','
                result.append(Word(text, start, end, surface))
            return tuple(result)

        async def emit(event):
            events.append(event)

        events = []
        with TemporaryDirectory() as directory:
            models = Models()
            session = LiveSession(models, emit, Path(directory), mode='balanced')
            session.source_history.append('earlier source phrase')
            session.target_translation_history.append('önceki çeviri')
            first = speech(8)
            cut = first[:3]
            session.chunk_proposal = (tuple(normalized(word.text) for word in cut),
                                      cut[-1].end)

            await session._advance_read_wait_write(first, 'en', 6.,
                allow_query=False, allow_approved_query=True, asr_completed_at=5.9)
            await session.read_wait_write_task
            await session._advance_read_wait_write(first, 'en', 6.1,
                allow_query=False, allow_approved_query=True, asr_completed_at=6.)
            self.assertEqual(len(models.proposals), 1)

            session.source_history.append('newly committed source')
            session.target_translation_history.append('yeni oynatılan çeviri')
            grown = speech(10, revised=True)
            session.read_wait_write.last_query_at = time.perf_counter()-3.
            await session._advance_read_wait_write(grown, 'en', 7.,
                allow_query=False, allow_approved_query=True, asr_completed_at=6.9)
            await session.read_wait_write_task
            await session._advance_read_wait_write(grown, 'en', 7.1,
                allow_query=False, allow_approved_query=True, asr_completed_at=7.)

            self.assertEqual(len(models.proposals), 2)
            frozen_fields = ('approved_source_text', 'approved_duration_seconds',
                             'previous_source', 'previous_translation',
                             'approved_following_text')
            self.assertEqual({key:models.proposals[0][key] for key in frozen_fields},
                             {key:models.proposals[1][key] for key in frozen_fields})
            self.assertEqual(models.proposals[0]['previous_source'], 'earlier source phrase')
            self.assertEqual(models.proposals[0]['previous_translation'], 'önceki çeviri')
            self.assertEqual(models.proposals[0]['approved_source_text'], 'w1 w2 w3')
            self.assertEqual(models.proposals[0]['approved_duration_seconds'], 1.5)
            proposal_events = [event for event in events
                               if event.get('type') == 'read_wait_write_proposal']
            self.assertEqual(len(proposal_events), 2)
            self.assertEqual(proposal_events[0]['approved_context_origin_observation_id'], 1)
            self.assertEqual(proposal_events[1]['approved_context_origin_observation_id'], 1)
            self.assertTrue(all(event['translation_prompt_context_policy'] ==
                'frozen_full_approved_cut_snapshot' for event in proposal_events))
            await session.stop()


if __name__ == '__main__':
    unittest.main()
