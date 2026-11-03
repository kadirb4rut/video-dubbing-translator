import math
import shutil
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from live_dubbing.timing import presentation_pcm


class LivePresentationTimingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is required for live tempo correction')
    def test_pitch_preserving_correction_uses_conservative_and_measured_balanced_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'native.wav'
            sample_rate = 48000
            with wave.open(str(path), 'wb') as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(sample_rate)
                samples = (int(12000 * math.sin(2*math.pi*440*i/sample_rate))
                           for i in range(2*sample_rate))
                output.writeframes(b''.join(struct.pack('<h', sample) for sample in samples))

            _pcm, _qa_path, timing = presentation_pcm(path, 1.0)

            self.assertEqual(timing['maximum_tempo'], 1.20)
            self.assertEqual(timing['tempo'], 1.20)
            self.assertAlmostEqual(timing['presentation_seconds'], 2/1.20, delta=.04)
            _pcm, _qa_path, faster = presentation_pcm(path, 1.0, maximum_tempo=1.30)
            self.assertEqual(faster['maximum_tempo'], 1.30)
            self.assertEqual(faster['tempo'], 1.30)
            self.assertAlmostEqual(faster['presentation_seconds'], 2/1.30, delta=.04)
            fastest, _path, low_latency = presentation_pcm(path, 1.0, maximum_tempo=1.40)
            self.assertEqual(low_latency['maximum_tempo'], 1.40)
            self.assertEqual(low_latency['tempo'], 1.40)
            self.assertAlmostEqual(low_latency['presentation_seconds'], 2/1.40, delta=.04)
            _pcm, _qa_path, catchup = presentation_pcm(path, 2.0,
                maximum_tempo=1.30, speed_factor=1.10)
            self.assertEqual(catchup['requested_speed_factor'], 1.10)
            self.assertEqual(catchup['applied_speed_factor'], 1.10)
            self.assertAlmostEqual(catchup['presentation_seconds'], 2/1.10, delta=.04)
            _pcm, _qa_path, capped_catchup = presentation_pcm(path, 2.0,
                maximum_tempo=1.30, speed_factor=1.40)
            self.assertEqual(capped_catchup['requested_speed_factor'], 1.40)
            self.assertEqual(capped_catchup['tempo'], 1.30)
            self.assertEqual(capped_catchup['applied_speed_factor'], 1.30)
            self.assertAlmostEqual(capped_catchup['presentation_seconds'], 2/1.30, delta=.04)
            with self.assertRaises(ValueError):
                presentation_pcm(path, 1.0, maximum_tempo=1.41)
            with self.assertRaises(ValueError):
                presentation_pcm(path, 1.0, speed_factor=1.41)


if __name__ == '__main__':
    unittest.main()
