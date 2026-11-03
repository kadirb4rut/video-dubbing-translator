import unittest

from dubbing.segments import SpeechSegment
from dubbing.timing import (
    TimingGroupConfig,
    build_timing_groups,
    calculate_duration_budgets,
)


class TimingGroupTests(unittest.TestCase):
    def test_merges_safe_same_speaker_micro_segments_and_keeps_trace(self):
        segments = [
            SpeechSegment(0, 0.0, 0.20, "Yes.", "SPEAKER_00"),
            SpeechSegment(1, 0.35, 0.65, "I understand.", "SPEAKER_00"),
            SpeechSegment(2, 1.5, 2.5, "A new thought.", "SPEAKER_00"),
        ]
        groups = build_timing_groups(segments)
        self.assertEqual([group.original_segment_indices for group in groups], [(0, 1), (2,)])
        self.assertEqual(groups[0].text, "Yes. I understand.")
        self.assertAlmostEqual(groups[0].internal_pauses[0], 0.15)

    def test_does_not_merge_across_speaker_change(self):
        segments = [
            SpeechSegment(0, 0.0, 0.20, "Yes.", "SPEAKER_00"),
            SpeechSegment(1, 0.25, 0.55, "Yes.", "SPEAKER_01"),
        ]
        self.assertEqual(len(build_timing_groups(segments)), 2)

    def test_does_not_merge_across_overlap_boundary(self):
        segments = [
            SpeechSegment(0, 0.0, 0.20, "Yes.", "SPEAKER_00"),
            SpeechSegment(1, 0.25, 0.55, "I agree.", "SPEAKER_00", overlap=True),
        ]
        self.assertEqual(len(build_timing_groups(segments)), 2)

    def test_budget_borrows_only_safe_trailing_silence(self):
        segments = [
            SpeechSegment(0, 1.0, 1.3, "Yes.", "SPEAKER_00"),
            SpeechSegment(1, 2.0, 3.0, "Next.", "SPEAKER_01"),
        ]
        groups = calculate_duration_budgets(build_timing_groups(segments), media_duration=4.0)
        first = groups[0].budget
        self.assertIsNotNone(first)
        self.assertAlmostEqual(first.safe_leading_silence, 1.0)
        self.assertAlmostEqual(first.safe_trailing_silence, 0.7)
        self.assertAlmostEqual(first.available_duration, 1.0)
        self.assertAlmostEqual(first.render_end, 2.0)

    def test_preferred_duration_is_distinct_from_safe_available_window(self):
        segments = [
            SpeechSegment(0, 1.0, 2.0, "A", "SPEAKER_00"),
            SpeechSegment(1, 3.0, 4.0, "B", "SPEAKER_01"),
        ]
        groups = calculate_duration_budgets(build_timing_groups(segments), media_duration=6.0)
        self.assertAlmostEqual(groups[0].budget.preferred_duration, 1.0)
        self.assertAlmostEqual(groups[0].budget.safe_available_duration, 2.0)
        self.assertNotEqual(
            groups[0].budget.preferred_duration,
            groups[0].budget.safe_available_duration,
        )

    def test_config_rejects_inconsistent_thresholds(self):
        with self.assertRaises(ValueError):
            TimingGroupConfig(max_gap_seconds=0.2, tight_gap_seconds=0.3)


if __name__ == "__main__":
    unittest.main()
