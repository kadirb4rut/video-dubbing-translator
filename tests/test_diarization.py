import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from diarization.assignment import (
    DiarizationResult,
    DiarizationTurn,
    assign_speakers,
    reconcile_or_fallback,
    validate_speaker_bounds,
)
from dubbing.segments import SpeechSegment
from diarization.model_manager import model_installed


class DiarizationAssignmentTests(unittest.TestCase):
    def setUp(self):
        self.segments = [
            SpeechSegment(0, 0.0, 2.0, "First."),
            SpeechSegment(1, 2.0, 4.0, "Second."),
            SpeechSegment(2, 4.0, 6.0, "Third."),
        ]

    def test_exclusive_assignment_and_stable_speaker_mapping(self):
        result = DiarizationResult(
            turns=(
                DiarizationTurn(0.0, 2.0, "person-a"),
                DiarizationTurn(2.0, 4.0, "person-b"),
                DiarizationTurn(4.0, 6.0, "person-a"),
            ),
            exclusive_turns=(
                DiarizationTurn(0.0, 2.0, "person-a"),
                DiarizationTurn(2.0, 4.0, "person-b"),
                DiarizationTurn(4.0, 6.0, "person-a"),
            ),
        )
        assigned = assign_speakers(self.segments, result)
        self.assertEqual(
            [segment.speaker for segment in assigned],
            ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"],
        )

    def test_regular_output_flags_overlap_while_exclusive_output_assigns_one_speaker(self):
        result = DiarizationResult(
            turns=(
                DiarizationTurn(0.0, 3.0, "a"),
                DiarizationTurn(1.0, 2.0, "b"),
            ),
            exclusive_turns=(DiarizationTurn(0.0, 3.0, "a"),),
        )
        assigned = assign_speakers([self.segments[0]], result)
        self.assertEqual(assigned[0].speaker, "SPEAKER_00")
        self.assertTrue(assigned[0].overlap)

    def test_ambiguous_or_missing_assignment_is_safe(self):
        tied = DiarizationResult(
            turns=(
                DiarizationTurn(0.0, 1.0, "a"),
                DiarizationTurn(1.0, 2.0, "b"),
            )
        )
        assigned = assign_speakers([self.segments[0]], tied)
        self.assertIsNone(assigned[0].speaker)
        self.assertTrue(assigned[0].speaker_ambiguous)

    def test_unavailable_diarization_preserves_segments_for_fallback(self):
        self.assertEqual(reconcile_or_fallback(self.segments, None), self.segments)

    def test_speaker_count_validation(self):
        validate_speaker_bounds(num_speakers=2)
        validate_speaker_bounds(min_speakers=2, max_speakers=5)
        for arguments in (
            {"num_speakers": 0},
            {"num_speakers": 2, "min_speakers": 1},
            {"min_speakers": 4, "max_speakers": 2},
        ):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                validate_speaker_bounds(**arguments)

    def test_model_readiness_requires_both_component_weights(self):
        with TemporaryDirectory() as temporary:
            model_dir = Path(temporary)
            (model_dir / "config.yaml").touch()
            (model_dir / "embedding").mkdir()
            (model_dir / "segmentation").mkdir()
            (model_dir / "embedding" / "pytorch_model.bin").touch()
            self.assertFalse(model_installed(model_dir))
            (model_dir / "segmentation" / "model.safetensors").touch()
            self.assertTrue(model_installed(model_dir))


if __name__ == "__main__":
    unittest.main()
