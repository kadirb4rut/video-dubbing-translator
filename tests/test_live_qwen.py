import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

from live_dubbing.qwen_translation import (selected_backend, selected_boundary_backend,
                                           installed)

ROOT = Path(__file__).resolve().parents[1]


class OptionalLiveQwenTests(unittest.TestCase):
    def test_live_translation_retains_recent_and_distant_source_without_unowned_lookahead(self):
        import types
        from unittest.mock import Mock
        from live_dubbing.models import LocalModels
        from live_dubbing.inference_scheduler import PriorityInferenceScheduler
        models=LocalModels.__new__(LocalModels)
        models.glossary={};models.target_language='tr';models.live_translation_backend='hymt2-1.8b'
        models.inference_scheduler=PriorityInferenceScheduler()
        recent='older '*180+'LATEST SOURCE'
        distant='The distant project RAG uses 64 MB.'
        models.memory=Mock();models.memory.relevant.return_value=[
            types.SimpleNamespace(kind='live_committed',source=recent),
            types.SimpleNamespace(kind='live_committed',source=distant)]
        models.translator=Mock();models.translator.translate.return_value='translation'
        result=models.translate_draft(source_text='owned source',source_language='en',
            previous_text=recent,following_text='2019 and a statement on Friday',duration=3.)
        context=models.translator.translate.call_args.kwargs['context']
        self.assertIsNone(context.next_text)
        self.assertIn('LATEST SOURCE',context.previous_text)
        self.assertIn(distant,context.previous_text)
        self.assertLessEqual(len(context.previous_text),800)
        self.assertNotIn('2019',context.previous_text)
        self.assertEqual(result['metrics']['used_retrieved_source_items'],1)
        self.assertEqual(result['metrics']['following_source_usage'],'endpoint_only_not_sent_to_mt')

    def test_invalid_optional_boundary_waits_but_worker_failure_is_not_hidden(self):
        from live_dubbing.qwen_translation import QwenTranslator, InvalidModelResponse
        from unittest.mock import Mock
        translator=QwenTranslator.__new__(QwenTranslator)
        translator._request=Mock(side_effect=InvalidModelResponse('end_unit is not eligible'))
        self.assertEqual(translator.select_boundary(['a','b'],[1],1),0)
        translator._request.side_effect=RuntimeError('CUDA worker exited')
        with self.assertRaisesRegex(RuntimeError,'CUDA worker exited'):
            translator.select_boundary(['a','b'],[1],1)

    def test_concurrent_rpc_callers_keep_their_own_generation_statistics(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from live_dubbing.qwen_translation import QwenTranslator
        translator=QwenTranslator.__new__(QwenTranslator)
        translator._statistics=threading.local()
        barrier=threading.Barrier(2)
        def call(identifier):
            translator.last_generation={'request':identifier}
            barrier.wait(timeout=2)
            return translator.last_generation['request']
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(list(pool.map(call,['boundary','tts_qa'])),['boundary','tts_qa'])

    def test_default_remains_existing_backend_and_unknown_selection_fails(self):
        with patch.dict(os.environ, {'LIVE_TRANSLATION_BACKEND':'hymt2-1.8b'}):
            self.assertEqual(selected_backend(), 'hymt2-1.8b')
        with patch.dict(os.environ, {'LIVE_TRANSLATION_BACKEND':'unsupported'}):
            with self.assertRaises(ValueError): selected_backend()

    def test_boundary_provider_is_independent_and_defaults_preserve_existing_routes(self):
        with patch.dict(os.environ, {'LIVE_TRANSLATION_BACKEND':'hymt2-1.8b'}, clear=True):
            self.assertEqual(selected_boundary_backend(), 'sat')
            os.environ['LIVE_BOUNDARY_BACKEND'] = 'qwen3.5-27b'
            self.assertEqual(selected_backend(), 'hymt2-1.8b')
            self.assertEqual(selected_boundary_backend(), 'qwen3.5-27b')
        with patch.dict(os.environ, {'LIVE_TRANSLATION_BACKEND':'qwen3.5-27b'}, clear=True):
            self.assertEqual(selected_boundary_backend(), 'qwen3.5-27b')
            os.environ['LIVE_BOUNDARY_BACKEND'] = 'sat'
            self.assertEqual(selected_boundary_backend(), 'sat')
        with patch.dict(os.environ, {'LIVE_BOUNDARY_BACKEND':'unsupported'}, clear=True):
            with self.assertRaisesRegex(ValueError, 'LIVE_BOUNDARY_BACKEND'):
                selected_boundary_backend('hymt2-1.8b')

    def test_contextual_boundary_uses_its_own_provider_not_the_mt_backend(self):
        from unittest.mock import Mock
        from live_dubbing.models import LocalModels
        from live_dubbing.inference_scheduler import PriorityInferenceScheduler
        models = LocalModels.__new__(LocalModels)
        models.inference_scheduler = PriorityInferenceScheduler()
        models.contextual_chunking = True
        models.live_translation_backend = 'hymt2-1.8b'
        models.translator = Mock()
        models.translator.select_boundary.side_effect = AssertionError('MT backend used as boundary provider')
        models.boundary_selector = Mock()
        models.boundary_selector.select_boundary.return_value = 4
        models.boundary_selector.last_generation = {'backend':'qwen3.5-27b', 'wall_seconds':.8}
        result = models.select_boundary(units=['one','two','three','four'],
            eligible_ends=[2,4], preferred_end_unit=4)
        self.assertEqual(result['end_unit'],4)
        self.assertEqual(result['metrics']['backend'],'qwen3.5-27b')
        self.assertEqual(result['metrics']['wall_seconds'],.8)
        self.assertGreaterEqual(result['metrics']['inference_scheduler_wait_seconds'],0)
        models.translator.select_boundary.assert_not_called()
        models.boundary_selector.assess_source_boundary.return_value = True
        checked = models.assess_source_boundary(prefix='who are', continuation='underqualified')
        self.assertTrue(checked['crosses_dependency'])
        models.boundary_selector.assess_source_boundary.assert_called_once_with('who are','underqualified')

    def test_invalid_boundary_guard_never_becomes_safe(self):
        from unittest.mock import Mock
        from live_dubbing.qwen_translation import QwenTranslator, InvalidModelResponse
        translator=QwenTranslator.__new__(QwenTranslator)
        translator._request=Mock(return_value='{"crosses_dependency":true}')
        self.assertTrue(translator.assess_source_boundary('who are','underqualified'))
        translator._request.side_effect=InvalidModelResponse('invalid response')
        self.assertIsNone(translator.assess_source_boundary('who are','underqualified'))
        translator._request.side_effect=RuntimeError('worker failed')
        with self.assertRaises(RuntimeError):
            translator.assess_source_boundary('who are','underqualified')

    def test_simultaneous_candidate_check_binds_boundary_and_meaning_in_one_call(self):
        from unittest.mock import Mock
        from live_dubbing.qwen_translation import QwenTranslator, InvalidModelResponse
        translator=QwenTranslator.__new__(QwenTranslator)
        translator._request=Mock(return_value='{"crosses_dependency":false,"supported":true}')
        result=translator.assess_simultaneous_candidate(
            'we can start','the review is next','Başlayabiliriz.')
        self.assertEqual(result,{'crosses_dependency':False,'supported':True})
        translator._request.assert_called_once_with('assess_candidate',
            prefix='we can start',continuation='the review is next',target_text='Başlayabiliriz.')
        translator._request.side_effect=InvalidModelResponse('invalid check')
        self.assertIsNone(translator.assess_simultaneous_candidate(
            'we can start','the review is next','Başlayabiliriz.'))

    def test_translation_support_check_is_bound_to_exact_source_and_target(self):
        from live_dubbing.qwen_translation import QwenTranslator, InvalidModelResponse
        from unittest.mock import Mock
        translator=QwenTranslator.__new__(QwenTranslator)
        translator._request=Mock(return_value='{"supported":false}')
        self.assertEqual(translator.assess_translation_support(
            'edrych yn ymddangos ymddangos',
            'Bakanlar bu konuyu tekrar gündeme getirdi.'), {'supported':False})
        translator._request.assert_called_once_with('assess_translation_support',
            source_text='edrych yn ymddangos ymddangos',
            target_text='Bakanlar bu konuyu tekrar gündeme getirdi.')
        translator._request.side_effect=InvalidModelResponse('invalid check')
        self.assertIsNone(translator.assess_translation_support('source','target'))

    def test_models_translation_support_uses_only_independent_local_qwen(self):
        from unittest.mock import Mock
        from live_dubbing.models import LocalModels
        from live_dubbing.inference_scheduler import PriorityInferenceScheduler
        models=LocalModels.__new__(LocalModels)
        models.live_translation_backend='hymt2-1.8b'
        models.live_boundary_backend='qwen3.5-27b'
        models.translator=object()
        models.boundary_selector=Mock()
        models.boundary_selector.assess_translation_support.return_value={'supported':False}
        models.boundary_selector.last_generation={'wall_seconds':.7}
        models.inference_scheduler=PriorityInferenceScheduler()
        result=models.assess_translation_support(source_text='exact source',target_text='unsupported target')
        self.assertFalse(result['supported'])
        self.assertEqual(result['metrics']['provider'],'qwen3.5-27b_independent_of_hymt2')
        models.boundary_selector.assess_translation_support.assert_called_once_with(
            'exact source','unsupported target')

        models.live_translation_backend='qwen3.5-27b'
        self.assertIsNone(models.assess_translation_support(
            source_text='exact source',target_text='unsupported target'))

    def test_translation_support_worker_rejects_empty_pair_before_model_load(self):
        result=subprocess.run([sys.executable,str(ROOT/'live_dubbing/qwen_translation_worker.py'),
            '--model',str(ROOT/'nonexistent-test-model'),'--device','cpu'],
            input=json.dumps({'id':'test','operation':'assess_translation_support',
                'source_text':'','target_text':'not grounded'})+'\n',
            capture_output=True,text=True,timeout=5)
        response=json.loads(result.stdout)
        self.assertFalse(response['ok'])
        self.assertIn('Invalid bounded translation support comparison',response['error'])
        self.assertEqual(response['error_code'],'worker_error')
        self.assertEqual(result.stderr,'')

    def test_empty_boundary_check_fails_before_any_model_import(self):
        result=subprocess.run([sys.executable,str(ROOT/'live_dubbing/qwen_translation_worker.py'),
            '--model',str(ROOT/'nonexistent-test-model'),'--device','cpu'],
            input=json.dumps({'id':'test','operation':'assess_boundary','prefix':'','continuation':'word'})+'\n',
            capture_output=True,text=True,timeout=5)
        response=json.loads(result.stdout)
        self.assertFalse(response['ok'])
        self.assertIn('Invalid bounded Live boundary comparison',response['error'])
        self.assertEqual(result.stderr,'')

    def test_empty_simultaneous_check_fails_before_any_model_import(self):
        result=subprocess.run([sys.executable,str(ROOT/'live_dubbing/qwen_translation_worker.py'),
            '--model',str(ROOT/'nonexistent-test-model'),'--device','cpu'],
            input=json.dumps({'id':'test','operation':'assess_candidate','prefix':'',
                'continuation':'next clause','target_text':'çeviri'})+'\n',
            capture_output=True,text=True,timeout=5)
        response=json.loads(result.stdout)
        self.assertFalse(response['ok'])
        self.assertIn('Invalid bounded simultaneous candidate comparison',response['error'])
        self.assertEqual(response['error_code'],'worker_error')
        self.assertEqual(result.stderr,'')

    def test_missing_model_status_never_fetches_or_initializes_models(self):
        with patch.dict(os.environ, {'LIVE_QWEN_MODEL':str(ROOT/'nonexistent-test-model')}):
            self.assertFalse(installed())

    def test_invalid_worker_operation_returns_error_before_any_ai_import(self):
        result = subprocess.run([sys.executable, str(ROOT/'live_dubbing/qwen_translation_worker.py'),
            '--model', str(ROOT/'nonexistent-test-model'), '--device', 'cpu'],
            input=json.dumps({'id':'test', 'operation':'invalid'})+'\n',
            capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        response = json.loads(result.stdout)
        self.assertEqual(response['id'], 'test')
        self.assertFalse(response['ok'])
        self.assertIn('Unsupported Live MT operation', response['error'])
        self.assertEqual(result.stderr, '')
