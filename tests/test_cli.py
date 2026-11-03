import contextlib
import io
import unittest

from scripts.run_pipeline import parse_args


class CLITests(unittest.TestCase):
    def test_speaker_and_translation_options_parse(self):
        args = parse_args(
            [
                "input.mp4",
                "--num-speakers",
                "2",
                "--translation-model",
                "hymt2-7b",
                "--translation-device",
                "cuda:1",
            ]
        )
        self.assertEqual(args.num_speakers, 2)
        self.assertEqual(args.translation_model, "hymt2-7b")
        self.assertEqual(args.translation_device, "cuda:1")

    def test_modal_tts_is_an_explicit_optional_backend(self):
        args = parse_args(["input.mp4", "--tts-backend", "modal"])
        self.assertEqual(args.tts_backend, "modal")
        self.assertEqual(parse_args(["input.mp4"]).tts_backend, "native")

    def test_conflicting_speaker_controls_are_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["input.mp4", "--num-speakers", "2", "--min-speakers", "1"])

    def test_invalid_translation_device_is_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parse_args(["input.mp4", "--translation-device", "gpu"])


if __name__ == "__main__":
    unittest.main()
