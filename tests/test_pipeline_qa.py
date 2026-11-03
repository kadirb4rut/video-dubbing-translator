import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import numpy as np
import soundfile as sf

from dubbing.references import SpeakerReference
from dubbing.segments import SpeechSegment
from dubbing.timing import TimingGroup, calculate_duration_budgets


class PipelineQATests(unittest.TestCase):
    def test_retry_changes_seed_preserves_candidates_and_refreshes_timing(self):
        from Video_Translator import generate_dubbed_speech

        class Translator:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def translate(self, *args, **kwargs): return 'Evet.'

        with TemporaryDirectory() as directory:
            root = Path(directory)
            wav_dir = root / 'wav'
            wav_dir.mkdir()
            ref_path = root / 'ref.wav'
            sf.write(ref_path, 0.1 * np.sin(np.arange(16000) * 0.1), 16000)
            reference = SpeakerReference('SPEAKER_00', ref_path, 'Reference.', 0)

            class References:
                def prepare(self): pass
                def reference_for(self, segment): return reference

            segment = SpeechSegment(0, 0, 1, 'Yes.', 'SPEAKER_00')
            groups = calculate_duration_budgets([TimingGroup(0, (segment,), 0, 1, segment.text, ())], 1)
            seeds = []

            def synthesize(text, ref, path, *, seed, **kwargs):
                seeds.append(seed)
                count = 3200 if len(seeds) == 1 else 16000
                sf.write(path, 0.1 * np.sin(np.arange(count) * 0.1), 16000)

            with patch('Video_Translator.HyMT2Translator', Translator), patch(
                'Video_Translator.media_duration', side_effect=lambda p: sf.info(p).duration
            ):
                results, qualities = generate_dubbed_speech(
                    groups, wav_dir, 'en', 'tr', References(),
                    max_refinement_passes=0, tts_synthesizer=synthesize, qa_retries=1,
                )
            self.assertEqual(seeds, [42, 1042])
            self.assertTrue(qualities[0].passed)
            self.assertEqual(qualities[0].retry_count, 1)
            self.assertIn('truncated_audio', qualities[0].metadata['qa_candidates'][0]['hard_failures'])
            self.assertEqual(qualities[0].metadata['qa_candidates'][1]['hard_failures'], ())
            self.assertEqual(results[0].selected_version, 'qa_retry')
            self.assertEqual(results[0].final_tempo, 1.0)
            self.assertFalse(results[0].timing_limited)
            report = json.loads((root / 'quality_report.json').read_text())
            self.assertEqual(report['source_language'], 'en')
            self.assertEqual(report['target_language'], 'tr')


if __name__ == '__main__':
    unittest.main()
