import tempfile
import unittest
from pathlib import Path

from dubbing.references import (
    AudioQuality,
    SpeakerReferenceManager,
    select_reference_segments,
)
from dubbing.segments import SpeechSegment


class SpeakerReferenceTests(unittest.TestCase):
    def test_selects_one_deterministic_clean_reference_per_speaker(self):
        segments = [
            SpeechSegment(0, 0, 2, "Short reference", "SPEAKER_00"),
            SpeechSegment(1, 2, 8, "The exact clean reference transcript.", "SPEAKER_00"),
            SpeechSegment(2, 8, 14, "Overlapping", "SPEAKER_00", overlap=True),
            SpeechSegment(3, 14, 18, "Other speaker", "SPEAKER_01"),
        ]
        qualities = {
            0: AudioQuality(0.05, 0.3, 0),
            1: AudioQuality(0.10, 0.5, 0),
            2: AudioQuality(0.20, 0.6, 0),
            3: AudioQuality(0.08, 0.4, 0),
        }
        selected = select_reference_segments(segments, qualities)
        self.assertEqual(selected["SPEAKER_00"].index, 1)
        self.assertEqual(selected["SPEAKER_01"].index, 3)

    def test_reference_transcript_matches_selected_audio_metadata(self):
        segment = SpeechSegment(7, 1, 7, "Actual words in selected clip", "SPEAKER_00")
        writes = []
        with tempfile.TemporaryDirectory() as directory:
            manager = SpeakerReferenceManager(
                [segment],
                {7: AudioQuality(0.1, 0.5, 0)},
                lambda source, output: (writes.append((source.index, output)), output.touch()),
                Path(directory),
            )
            manager.prepare()
            reference = manager.reference_for(segment)
            self.assertEqual(reference.transcript, segment.text)
            self.assertEqual(reference.source_segment_index, 7)
            self.assertEqual(writes[0][0], 7)
            manifest = (Path(directory) / "manifest.json").read_text()
            self.assertIn("Actual words in selected clip", manifest)

    def test_prefers_a_useful_duration_over_a_louder_fragment(self):
        segments = [
            SpeechSegment(0, 0, 1, "Too short to be a strong prompt", "SPEAKER_00"),
            SpeechSegment(1, 1, 6, "A clean five second reference", "SPEAKER_00"),
        ]
        qualities = {
            0: AudioQuality(0.20, 0.5, 0),
            1: AudioQuality(0.05, 0.3, 0),
        }
        selected = select_reference_segments(segments, qualities)
        self.assertEqual(selected["SPEAKER_00"].index, 1)

    def test_no_clean_reference_falls_back_to_current_segment(self):
        segment = SpeechSegment(0, 0, 0.5, "Tiny", "SPEAKER_00")
        with tempfile.TemporaryDirectory() as directory:
            manager = SpeakerReferenceManager(
                [segment],
                {0: AudioQuality(0.1, 0.5, 0)},
                lambda source, output: output.touch(),
                Path(directory),
            )
            manager.prepare()
            reference = manager.reference_for(segment)
            self.assertTrue(reference.fallback)
            self.assertEqual(reference.transcript, "Tiny")


if __name__ == "__main__":
    unittest.main()
