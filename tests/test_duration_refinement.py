import tempfile
import unittest
from pathlib import Path

from dubbing.duration import (
    refinement_direction,
    should_refine,
    synthesize_with_duration_refinement,
)
from dubbing.references import SpeakerReference
from dubbing.segments import SpeechSegment
from translation.base import TranslationContext, Translator


class FakeTranslator(Translator):
    def __init__(self):
        self.rewrites = []
        self.rewrite_contexts = []
        self.rewrite_result = "short natural version"

    def translate(self, source_text, source_language, target_language, **kwargs):
        return "initial"

    def rewrite_for_duration(
        self,
        source_text,
        current_translation,
        source_language,
        target_language,
        target_duration_seconds,
        actual_duration_seconds,
        **kwargs,
    ):
        self.rewrites.append((current_translation, target_duration_seconds, actual_duration_seconds))
        self.rewrite_contexts.append(kwargs.get("context"))
        return self.rewrite_result


class DurationRefinementTests(unittest.TestCase):
    def test_threshold_is_strictly_more_than_twenty_percent(self):
        self.assertFalse(should_refine(3.6, 3.0, 0.20))
        self.assertTrue(should_refine(3.61, 3.0, 0.20))

    def test_refinement_direction_is_bidirectional(self):
        self.assertEqual(refinement_direction(5.0, 4.0), "shorten")
        self.assertEqual(refinement_direction(3.0, 4.0), "expand")
        self.assertEqual(refinement_direction(4.2, 4.0), "none")

    def test_acceptable_ideal_timing_does_not_refine(self):
        translator = FakeTranslator()
        segment = SpeechSegment(0, 0, 1, "Original")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.wav"
            reference = SpeakerReference(None, Path(directory) / "ref.wav", "Original", 0)
            result = synthesize_with_duration_refinement(
                segment=segment,
                initial_translation="Translation",
                reference=reference,
                output_path=output,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=lambda text, reference, path: path.touch(),
                measure_duration=lambda path: 1.05,
                preferred_duration_seconds=1.0,
                safe_available_duration_seconds=1.4,
            )
        self.assertEqual(translator.rewrites, [])
        self.assertEqual(result.refinement_direction, "none")

    def test_short_audio_triggers_quantitative_expansion(self):
        translator = FakeTranslator()
        translator.rewrite_result = "expanded GPT-4 version"
        segment = SpeechSegment(0, 0, 4, "Original words", "SPEAKER_00")
        durations = iter((2.0, 3.8))
        context = TranslationContext(
            preferred_duration_seconds=4.0,
            safe_available_duration_seconds=5.0,
            target_spoken_units=20,
            protected_tokens=("GPT-4",),
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.wav"
            reference = SpeakerReference("SPEAKER_00", Path(directory) / "ref.wav", "Reference", 0)
            result = synthesize_with_duration_refinement(
                segment=segment,
                initial_translation="Initial GPT-4",
                reference=reference,
                output_path=output,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=lambda text, reference, path: path.touch(),
                measure_duration=lambda path: next(durations),
                context=context,
                preferred_duration_seconds=4.0,
                safe_available_duration_seconds=5.0,
                target_spoken_units=20,
            )
        self.assertEqual(result.refinement_direction, "expand")
        self.assertEqual(translator.rewrites[0][1:], (4.0, 2.0))
        self.assertAlmostEqual(translator.rewrite_contexts[0].target_spoken_units, 40.0)
        self.assertEqual(translator.rewrite_contexts[0].refinement_direction, "expand")
        self.assertEqual(result.selected_version, "refined")

    def test_refined_longer_candidate_can_win_for_short_initial_audio(self):
        result, _ = self._run_two_candidate_case((0.6, 0.95))
        self.assertEqual(result.refinement_direction, "expand")
        self.assertEqual(result.selected_version, "refined")
        self.assertEqual(result.final_duration, 0.95)

    def test_long_audio_is_rewritten_once_and_second_synthesis_is_final(self):
        translator = FakeTranslator()
        synthesized = []
        durations = iter((5.0, 3.2))
        segment = SpeechSegment(0, 0, 3, "Original words", "SPEAKER_00")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.wav"
            reference = SpeakerReference(
                "SPEAKER_00", Path(directory) / "speaker.wav", "Reference words", 4
            )

            def synthesize(text, selected_reference, path):
                synthesized.append((text, selected_reference.transcript, path))
                path.touch()

            result = synthesize_with_duration_refinement(
                segment=segment,
                initial_translation="Initial long translation",
                reference=reference,
                output_path=output,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=synthesize,
                measure_duration=lambda path: next(durations),
            )
            self.assertTrue(output.exists())
        self.assertEqual(len(translator.rewrites), 1)
        self.assertEqual([call[0] for call in synthesized], ["Initial long translation", "short natural version"])
        self.assertEqual([call[2].name for call in synthesized], ["final.initial.wav", "final.refined.wav"])
        self.assertEqual(result.final_duration, 3.2)
        self.assertEqual(result.refined_duration, 3.2)
        self.assertTrue(result.refined)

    def test_audio_within_tolerance_is_not_rewritten(self):
        translator = FakeTranslator()
        segment = SpeechSegment(0, 0, 3, "Original")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.wav"
            reference = SpeakerReference(None, Path(directory) / "ref.wav", "Original", 0)
            synthesize_with_duration_refinement(
                segment=segment,
                initial_translation="Translation",
                reference=reference,
                output_path=output,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=lambda text, reference, path: path.touch(),
                measure_duration=lambda path: 3.0,
            )
        self.assertEqual(translator.rewrites, [])

    def _run_two_candidate_case(self, durations):
        translator = FakeTranslator()
        synthesized = []
        segment = SpeechSegment(0, 0, 1, "Original words", "SPEAKER_00")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "final.wav"
            reference = SpeakerReference("SPEAKER_00", Path(directory) / "ref.wav", "Reference", 0)

            def synthesize(text, selected_reference, path):
                synthesized.append(path.name)
                path.write_bytes(b"wav")

            result = synthesize_with_duration_refinement(
                segment=segment,
                initial_translation="Initial",
                reference=reference,
                output_path=output,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=synthesize,
                measure_duration=lambda path: durations[0] if path.name.endswith(".initial.wav") else durations[1],
            )
            self.assertTrue(output.exists())
            self.assertFalse((Path(directory) / "final.initial.wav").exists())
            return result, synthesized

    def test_refined_wav_wins_when_timing_is_better(self):
        result, _ = self._run_two_candidate_case((2.0, 1.2))
        self.assertEqual(result.selected_version, "refined")
        self.assertEqual(result.final_duration, 1.2)
        self.assertEqual(result.refined_duration, 1.2)

    def test_initial_wav_wins_when_refinement_is_worse_or_equal(self):
        result, _ = self._run_two_candidate_case((1.9, 2.8))
        self.assertEqual(result.selected_version, "initial")
        self.assertEqual(result.final_duration, 1.9)
        self.assertEqual(result.refined_duration, 2.8)

    def test_equal_timing_error_keeps_initial_generation(self):
        result, _ = self._run_two_candidate_case((1.2, 0.8))
        self.assertEqual(result.selected_version, "initial")
        self.assertEqual(result.final_duration, 1.2)
        self.assertEqual(result.refined_duration, 0.8)


if __name__ == "__main__":
    unittest.main()
