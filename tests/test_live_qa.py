from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import numpy as np
import soundfile as sf
from engine.qa import evaluate_tts
from live_dubbing.qa import (tolerate_surface_difference, retry_verified_presentation,
                             retry_conservative_presentation)


class LiveSurfaceQATests(unittest.TestCase):
    def test_faster_presentation_falls_back_only_to_same_qa_verified_conservative_audio(self):
        with patch('live_dubbing.timing.presentation_pcm', return_value=(
                b'conservative', Path('/tmp/conservative.wav'), {'tempo':1.20,
                    'maximum_tempo':1.20})) as retime:
            seen=[]
            def assess(pcm, path):
                seen.append((pcm,path))
                return {'failures':[] if pcm==b'conservative' else ['high_wer']}
            result=retry_conservative_presentation(Path('/tmp/native.wav'),1.5,
                original={'failures':['high_wer']},timing={'tempo':1.30},assess=assess)
            self.assertTrue(result['selected'])
            self.assertEqual(result['pcm'],b'conservative')
            self.assertEqual([value[0] for value in seen],[b'conservative'])
            retime.assert_called_once_with(Path('/tmp/native.wav'),1.5,maximum_tempo=1.20)

            retime.reset_mock()
            result=retry_conservative_presentation(Path('/tmp/native.wav'),1.5,
                original={'failures':['high_wer']},timing={'tempo':1.40},assess=assess,
                maximum_tempo=1.30)
            self.assertTrue(result['selected'])
            retime.assert_called_once_with(Path('/tmp/native.wav'),1.5,maximum_tempo=1.30)

            retime.reset_mock()
            result=retry_conservative_presentation(Path('/tmp/native.wav'),1.5,
                original={'failures':[]},timing={'tempo':1.30},assess=assess)
            self.assertIsNone(result)
            retime.assert_not_called()

            result=retry_conservative_presentation(Path('/tmp/native.wav'),1.5,
                original={'failures':['high_wer']},timing={'tempo':1.20},assess=assess)
            self.assertIsNone(result)
            retime.assert_not_called()

            result=retry_conservative_presentation(Path('/tmp/native.wav'),1.5,
                original={'failures':['wrong_target_language']},timing={'tempo':1.30},assess=assess)
            self.assertIsNone(result)
            retime.assert_not_called()

    def test_retiming_requires_native_and_alternate_audio_to_pass_the_same_qa(self):
        original = {'failures':['tts_text_meaning_mismatch']}
        timing = {'tempo':1.20}
        with patch('live_dubbing.timing.rubberband_available', return_value=True), \
             patch('live_dubbing.timing.presentation_pcm', return_value=(
                 b'alternate', Path('/tmp/alternate.wav'), {'tempo':1.15})) as retime:
            seen = []
            def assess(pcm, path):
                seen.append((pcm, path))
                return {'failures':[]}
            result = retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.4,
                original=original, timing=timing, assess=assess)
            self.assertTrue(result['selected'])
            self.assertEqual(result['pcm'], b'alternate')
            self.assertEqual([value[0] for value in seen], [b'native', b'alternate'])
            self.assertEqual(retime.call_args.kwargs['maximum_tempo'], 1.15)

            seen.clear()
            def reject_native(pcm, path):
                seen.append(pcm)
                return {'failures':['high_wer']}
            retime.reset_mock()
            result = retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.4,
                original=original, timing=timing, assess=reject_native)
            self.assertFalse(result['selected'])
            self.assertEqual(result['reason'], 'native_rejected')
            self.assertEqual(seen, [b'native'])
            retime.assert_not_called()

            def reject_alternate(pcm, path):
                return {'failures':[] if pcm == b'native' else ['tts_text_meaning_mismatch']}
            result = retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.4,
                original=original, timing=timing, assess=reject_alternate)
            self.assertFalse(result['selected'])
            self.assertEqual(result['reason'], 'alternate_rejected')

    def test_retiming_does_not_override_wrong_language_or_missing_filter(self):
        with patch('live_dubbing.timing.rubberband_available', return_value=True), \
             patch('live_dubbing.timing.presentation_pcm', side_effect=RuntimeError('filter unavailable')) as retime:
            assess = lambda pcm, path: {'failures':[]}
            self.assertIsNone(retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.,
                original={'failures':['wrong_target_language']}, timing={'tempo':1.2}, assess=assess))
            retime.assert_not_called()
            result = retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.,
                original={'failures':['high_wer']}, timing={'tempo':1.2}, assess=assess)
            self.assertFalse(result['selected'])
            self.assertEqual(result['reason'], 'alternate_unavailable')
        with patch('live_dubbing.timing.rubberband_available', return_value=False):
            def unexpected_assessment(pcm, path):
                self.fail('Unavailable filter must not trigger an additional ASR pass')
            result = retry_verified_presentation(Path('/tmp/native.wav'), b'native', 1.,
                original={'failures':['high_wer']}, timing={'tempo':1.2}, assess=unexpected_assessment)
            self.assertFalse(result['selected'])
            self.assertEqual(result['reason'], 'alternate_unavailable')
            self.assertIsNone(result['native'])

    def test_numeric_spelling_and_small_continuous_script_error_are_live_warnings(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.wav'
            sf.write(path,.1*np.sin(np.arange(48000)*.1),48000)
            for expected,recovered in [
                ('yılda üç bin beş yüz sterlin maliyetine mal olan ulusal borç',
                 'Yılda 3500 sterlin maliyetine mal olan ulusal borç.'),
                ('我们今天收到了三千五百英镑的付款','我们今天收到了3500英镑的付款')]:
                with self.subTest(expected=expected):
                    original=evaluate_tts(path,expected,recovered_text=recovered)
                    self.assertIn('high_wer',original.hard_failures)
                    live=tolerate_surface_difference(original,assessment={'minor':True})
                    self.assertNotIn('high_wer',live.hard_failures)
                    self.assertIn('live_model_assessed_minor_difference',live.warnings)
                    self.assertEqual(live.wer,original.wer)
                    self.assertEqual(live.cer,original.cer)
                    self.assertIn('high_wer',original.hard_failures)  # Offline default is unchanged.

    def test_major_repetition_and_signal_failure_remain_hard(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.wav'
            sf.write(path,.1*np.sin(np.arange(48000)*.1),48000)
            quality=evaluate_tts(path,'The meeting begins tomorrow.',
                recovered_text='The meeting begins tomorrow. The meeting begins tomorrow.')
            self.assertIn('high_wer',tolerate_surface_difference(quality,assessment={'minor':True}).hard_failures)
            sf.write(path,np.zeros(480),48000)
            quality=evaluate_tts(path,'hello world',recovered_text='hello world',expected_duration=1.)
            self.assertIn('truncated_audio',tolerate_surface_difference(quality).hard_failures)

    def test_small_negation_omission_can_fail_even_when_wer_is_low(self):
        with TemporaryDirectory() as directory:
            path=Path(directory)/'fixture.wav'
            sf.write(path,.1*np.sin(np.arange(48000)*.1),48000)
            quality=evaluate_tts(path,'The device is not ready.',recovered_text='The device is ready.')
            self.assertNotIn('high_wer',quality.hard_failures)
            checked=tolerate_surface_difference(quality,assessment={'minor':False})
            self.assertIn('tts_text_meaning_mismatch',checked.hard_failures)
