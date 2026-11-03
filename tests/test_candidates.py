import unittest

from dubbing.candidates import (
    TimingCandidateAssessment,
    assess_timing_candidate,
    choose_best_candidate,
    choose_best_timing_candidate,
    protected_tokens,
    score_candidate,
)


class CandidateTests(unittest.TestCase):
    def test_protected_names_numbers_and_terms_are_extracted(self):
        tokens = protected_tokens("OpenAI released GPT-4 in 2024.", {"OpenAI": "OpenAI"})
        self.assertIn("OpenAI", tokens)
        self.assertIn("GPT-4", tokens)
        self.assertIn("2024", tokens)

    def test_candidate_scoring_preserves_protected_information(self):
        complete = score_candidate(
            "OpenAI released GPT-4 in 2024.",
            "OpenAI GPT-4, 2024.",
            target_language="tr",
            speaker_rate=4.8,
            available_duration=2.0,
        )
        lossy = score_candidate(
            "OpenAI released GPT-4 in 2024.",
            "Yeni model.",
            target_language="tr",
            speaker_rate=4.8,
            available_duration=2.0,
        )
        self.assertIs(choose_best_candidate([complete, lossy]), complete)

    def test_selection_prefers_ideal_band_over_hard_band(self):
        ideal = assess_timing_candidate(
            version="ideal", text="GPT-4 2024.", duration=1.0,
            preferred_duration=1.0, safe_available_duration=1.2, protected=("GPT-4", "2024"),
        )
        hard_only = assess_timing_candidate(
            version="hard", text="GPT-4 2024.", duration=1.2,
            preferred_duration=1.0, safe_available_duration=1.2, protected=("GPT-4", "2024"),
        )
        self.assertIs(choose_best_timing_candidate([hard_only, ideal]), ideal)

    def test_selection_uses_bounded_sync_error_not_raw_duration_only(self):
        close_raw_but_unsafe = assess_timing_candidate(
            version="initial", text="complete", duration=1.4,
            preferred_duration=1.0, safe_available_duration=1.1,
        )
        safe_candidate = assess_timing_candidate(
            version="refined", text="complete", duration=1.12,
            preferred_duration=1.0, safe_available_duration=1.1,
        )
        self.assertIs(choose_best_timing_candidate([close_raw_but_unsafe, safe_candidate]), safe_candidate)

    def test_protected_candidate_beats_timing_when_refinement_loses_information(self):
        complete = assess_timing_candidate(
            version="initial", text="GPT-4 2024.", duration=1.2,
            preferred_duration=1.0, safe_available_duration=1.2, protected=("GPT-4", "2024"),
        )
        lossy = assess_timing_candidate(
            version="refined", text="A model.", duration=1.0,
            preferred_duration=1.0, safe_available_duration=1.2, protected=("GPT-4", "2024"),
        )
        self.assertIs(choose_best_timing_candidate([lossy, complete]), complete)

    def test_smaller_bounded_sync_error_wins(self):
        worse = assess_timing_candidate(
            version="initial", text="complete", duration=1.50,
            preferred_duration=1.0, safe_available_duration=1.3,
        )
        better = assess_timing_candidate(
            version="refined", text="complete", duration=1.40,
            preferred_duration=1.0, safe_available_duration=1.3,
        )
        self.assertIs(choose_best_timing_candidate([worse, better]), better)

    def test_lower_tempo_distortion_breaks_comparable_ties(self):
        closer = assess_timing_candidate(
            version="initial", text="complete", duration=0.95,
            preferred_duration=1.0, safe_available_duration=1.2,
        )
        farther = assess_timing_candidate(
            version="refined", text="complete", duration=1.08,
            preferred_duration=1.0, safe_available_duration=1.2,
        )
        self.assertIs(choose_best_timing_candidate([farther, closer]), closer)

    def test_less_borrowed_silence_is_final_tie_breaker(self):
        common = dict(
            text="complete",
            duration=1.0,
            required_tempo=1.0,
            applied_tempo=1.0,
            adjusted_duration=1.0,
            synchronization_error=0.0,
            safe_fit=True,
            ideal_band=True,
            hard_band=True,
            protected_fraction=1.0,
        )
        more_silence = TimingCandidateAssessment(
            version="initial", borrowed_silence=0.25, **common
        )
        less_silence = TimingCandidateAssessment(
            version="refined", borrowed_silence=0.05, **common
        )
        self.assertIs(choose_best_timing_candidate([more_silence, less_silence]), less_silence)


if __name__ == "__main__":
    unittest.main()
