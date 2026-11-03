import threading
import time
import unittest
from collections import deque
from types import SimpleNamespace
from unittest.mock import patch

from live_dubbing.inference_scheduler import PriorityInferenceScheduler
from live_dubbing.models import LocalModels
from live_dubbing.session import LiveSession


class PriorityInferenceSchedulerTests(unittest.TestCase):
    def _wait_for_waiters(self, scheduler, count):
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            with scheduler._condition:
                if len(scheduler._waiters) >= count:
                    return
            time.sleep(.002)
        self.fail('Inference request did not reach the bounded scheduler queue')

    def test_asr_is_admitted_before_waiting_boundary_work(self):
        scheduler = PriorityInferenceScheduler()
        order = []

        def request(stage):
            with scheduler.lease(stage):
                order.append(stage)

        with scheduler.lease('tts'):
            boundary = threading.Thread(target=request, args=('boundary',))
            boundary.start()
            self._wait_for_waiters(scheduler, 1)
            asr = threading.Thread(target=request, args=('asr',))
            asr.start()
            self._wait_for_waiters(scheduler, 2)
        asr.join(1.0)
        boundary.join(1.0)
        self.assertFalse(asr.is_alive())
        self.assertFalse(boundary.is_alive())
        self.assertEqual(order, ['asr', 'boundary'])

    def test_aged_boundary_request_gets_service_after_high_priority_burst(self):
        scheduler = PriorityInferenceScheduler(
            starvation_after_seconds=.01, high_priority_burst=1)
        order = []

        def request(stage):
            with scheduler.lease(stage):
                order.append(stage)

        with scheduler.lease('tts'):
            boundary = threading.Thread(target=request, args=('boundary',))
            boundary.start()
            self._wait_for_waiters(scheduler, 1)
            time.sleep(.02)
            asr = threading.Thread(target=request, args=('asr',))
            asr.start()
            self._wait_for_waiters(scheduler, 2)
        asr.join(1.0)
        boundary.join(1.0)
        self.assertFalse(asr.is_alive())
        self.assertFalse(boundary.is_alive())
        self.assertEqual(order, ['boundary', 'asr'])

    def test_disabled_mode_preserves_overlap_and_reports_zero_wait(self):
        scheduler = PriorityInferenceScheduler(enabled=False)
        with scheduler.lease('boundary') as lease:
            self.assertEqual(lease.wait_seconds, 0.0)
        self.assertEqual(scheduler.thread_metrics(), {})

    def test_simultaneous_proposal_uses_translation_lease(self):
        class LeaseScheduler:
            def __init__(self):
                self.stages = []
                self.active = False

            def lease(self, stage):
                self.stages.append(stage)
                scheduler = self

                class Context:
                    def __enter__(self):
                        scheduler.active = True
                        return SimpleNamespace(wait_seconds=.125)

                    def __exit__(self, *_args):
                        scheduler.active = False

                return Context()

        scheduler = LeaseScheduler()

        class FakeCapabilityRouter:
            def __init__(self):
                self.hymt2 = SimpleNamespace(_translator=SimpleNamespace(last_generation={}))

            def propose_simultaneous(self, *_args, **_kwargs):
                if not scheduler.active:
                    raise AssertionError('Hy-MT2 proposal ran outside the shared GPU lease')
                return {'status': 'wait', 'end_unit': 0}

        model = object.__new__(LocalModels)
        model.inference_scheduler = scheduler
        model.live_translation_backend = 'hymt2-1.8b'
        model.target_language = 'tr'
        model.glossary = {}
        model.translator = FakeCapabilityRouter()
        words = (SimpleNamespace(surface=' hello', text='hello'),)
        with patch('translation.backends.CapabilityRouter', FakeCapabilityRouter), \
             patch('translation.base.glossary_preservation', return_value=()):
            result = model.propose_simultaneous(words=words,
                source_language='en', eligible_ends=(1,), preferred_end_unit=1)
        self.assertEqual(scheduler.stages, ['translation'])
        self.assertFalse(scheduler.active)
        self.assertEqual(result['raw']['status'], 'wait')
        self.assertEqual(result['metrics']['inference_scheduler_wait_seconds'], .125)

    def test_source_speech_coverage_is_vad_based_and_only_committed_once(self):
        session = object.__new__(LiveSession)
        session.vad_speech_intervals = deque()
        session.vad_speech_seconds_total = 0.0
        session.committed_vad_speech_seconds = 0.0
        session.committed_source_timeline_seconds = 0.0
        session._record_vad_speech(0.0, .1)
        session._record_vad_speech(.1, .2)
        session._record_vad_speech(.4, .5)
        self.assertEqual(list(session.vad_speech_intervals), [(0.0, .2), (.4, .5)])
        self.assertAlmostEqual(session._commit_vad_speech(.05, .45), .2)
        self.assertAlmostEqual(session.committed_vad_speech_seconds, .2)
        self.assertAlmostEqual(session.vad_speech_seconds_total, .3)
        self.assertEqual(list(session.vad_speech_intervals), [(0.0, .05), (.45, .5)])
        self.assertAlmostEqual(session._commit_vad_speech(.45, .6), .05)
        self.assertAlmostEqual(session.committed_vad_speech_seconds, .25)
        self.assertEqual(list(session.vad_speech_intervals), [(0.0, .05)])

    def test_vad_commit_preserves_uncommitted_speech_outside_both_source_edges(self):
        session = object.__new__(LiveSession)
        session.vad_speech_intervals = deque()
        session.vad_speech_seconds_total = 0.0
        session.committed_vad_speech_seconds = 0.0
        session.committed_source_timeline_seconds = 0.0
        session._record_vad_speech(0.0, .2)
        session._record_vad_speech(.4, .5)

        self.assertAlmostEqual(session._commit_vad_speech(.05, .45), .2)
        self.assertEqual(list(session.vad_speech_intervals), [(0.0, .05), (.45, .5)])
        self.assertAlmostEqual(session.vad_speech_seconds_total-
            session.committed_vad_speech_seconds, .1)
        self.assertEqual(session._uncommitted_vad_coverage(.45, .6), [{
            'source_start': .45, 'source_end': .5, 'speech_seconds': .05,
        }])
        self.assertAlmostEqual(session._commit_vad_speech(.0, .05), .05)
        self.assertEqual(list(session.vad_speech_intervals), [(.45, .5)])

    def test_overlapping_vad_reports_union_coverage_once(self):
        session = object.__new__(LiveSession)
        session.vad_speech_intervals = deque()
        session.vad_speech_seconds_total = 0.0
        session.committed_vad_speech_seconds = 0.0
        session.committed_source_timeline_seconds = 0.0
        session._record_vad_speech(0.0, .2)
        session._record_vad_speech(.1, .25)
        self.assertAlmostEqual(session.vad_speech_seconds_total, .25)
        self.assertEqual(list(session.vad_speech_intervals), [(0.0, .25)])


if __name__ == '__main__':
    unittest.main()
