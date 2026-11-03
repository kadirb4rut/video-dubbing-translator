import unittest

from dubbing.speech_length import SpeakerRateEstimator, estimate_spoken_length


class SpeechLengthTests(unittest.TestCase):
    def test_estimator_reports_words_chars_syllables_and_phonemes(self):
        estimate = estimate_spoken_length("Machine learning and AI.", "en")
        self.assertEqual(estimate.words, 4)
        self.assertGreater(estimate.characters, 0)
        self.assertGreaterEqual(estimate.phonemes, estimate.syllables)

    def test_multilingual_fallback_handles_non_latin_script(self):
        estimate = estimate_spoken_length("機械学習", "ja")
        self.assertGreater(estimate.syllables, 0)
        self.assertEqual(estimate.method, "vowel-group-fallback")

    def test_rate_updates_per_speaker_with_ewma(self):
        estimator = SpeakerRateEstimator()
        estimate = estimate_spoken_length("This is a normal sentence.", "en")
        self.assertTrue(estimator.update("SPEAKER_00", estimate, 1.5, available_seconds=1.5))
        self.assertNotEqual(estimator.rate_for("SPEAKER_00"), estimator.fallback_units_per_second)
        self.assertEqual(estimator.rate_for("SPEAKER_01"), estimator.fallback_units_per_second)

    def test_rate_rejects_micro_or_stretched_samples(self):
        estimator = SpeakerRateEstimator()
        estimate = estimate_spoken_length("Yes.", "en")
        self.assertFalse(estimator.update("SPEAKER_00", estimate, 2.0, available_seconds=0.5))
        self.assertEqual(estimator.rate_for("SPEAKER_00"), estimator.fallback_units_per_second)


if __name__ == "__main__":
    unittest.main()
