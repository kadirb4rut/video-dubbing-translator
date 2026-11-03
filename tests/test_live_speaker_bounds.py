import base64
import unittest

from live_dubbing.models import SpeakerClient
from live_dubbing.speakers import (bounded_speaker_windows,
                                   combine_speaker_window_results)


class LiveSpeakerBoundsTests(unittest.TestCase):
    def test_long_pcm_is_covered_by_bounded_ordered_overlapping_windows(self):
        pcm = bytes(32000 * 41)
        windows = bounded_speaker_windows(pcm)

        self.assertGreater(len(windows), 1)
        self.assertTrue(all(0 < len(window) <= 32000 * 20 for window in windows))
        self.assertGreater(sum(map(len, windows)), len(pcm))
        overlap_bytes = sum(map(len, windows)) - len(pcm)
        expected_overlap_bytes = 32000 * (len(windows) - 1)
        self.assertGreaterEqual(overlap_bytes, expected_overlap_bytes)
        self.assertLessEqual(overlap_bytes, expected_overlap_bytes + 2 * len(windows))

    def test_windowed_client_fuses_voice_and_detects_cross_window_change(self):
        client = SpeakerClient.__new__(SpeakerClient)
        calls = []

        def fake_request(payload):
            chunk = base64.b64decode(payload['pcm'])
            calls.append(len(chunk))
            vector = [1.0, 0.0] if len(calls) == 1 else [0.0, 1.0]
            return {'id': str(len(calls)), 'ok': True, 'embedding': vector,
                    'overlap': len(calls) == 2, 'overlap_fraction': .07 if len(calls) == 2 else 0,
                    'mixed_turn': False, 'half_embedding_cosine': .8,
                    'valid_segmentation_frames': 20,
                    'source_duration_seconds': len(chunk) / 32000,
                    'backend': 'test'}

        client._request = fake_request
        client.inference_scheduler = None
        result = client.analyze(bytes(32000 * 20 + 6400))

        self.assertEqual(len(calls), 2)
        self.assertTrue(all(size <= 32000 * 20 for size in calls))
        self.assertEqual(result['analysis_window_count'], 2)
        self.assertAlmostEqual(result['source_duration_seconds'], 20.2)
        self.assertTrue(result['mixed_turn'])
        self.assertTrue(result['overlap'])
        self.assertAlmostEqual(result['overlap_fraction'], .07)

    def test_single_window_keeps_worker_result_shape(self):
        client = SpeakerClient.__new__(SpeakerClient)
        client.inference_scheduler = None
        client._request = lambda payload: {'embedding': [1.0], 'ok': True}

        self.assertEqual(client.analyze(bytes(32000)), {'embedding': [1.0], 'ok': True})

    def test_invalid_pcm_is_rejected_before_worker_request(self):
        for pcm in (b'', b'\x00'):
            with self.subTest(pcm_length=len(pcm)):
                with self.assertRaises(ValueError):
                    bounded_speaker_windows(pcm)


if __name__ == '__main__':
    unittest.main()
