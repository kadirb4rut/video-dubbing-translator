import tempfile
import unittest
from pathlib import Path

import subtitle_utils


class SubtitleParserTests(unittest.TestCase):
    def write(self, directory, name, content, encoding="utf-8"):
        path = Path(directory) / name
        path.write_text(content, encoding=encoding)
        return path

    def test_parses_srt_cues_and_joins_multi_line_text(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(
                directory,
                "dialogue.srt",
                "1\n00:00:02,180 --> 00:00:08,460\n"
                "During the war, a small group of skilled fighters\n"
                "pushed the Imperial Army to the brink of defeat.\n"
                "\n"
                "2\n00:00:08,740 --> 00:00:10,550\n"
                "They were known as the Ninku Brigade.\n",
            )
            cues = subtitle_utils.load_subtitles(srt)

        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0][0], 2.18)
        self.assertAlmostEqual(cues[0][1], 8.46)
        self.assertEqual(
            cues[0][2],
            "During the war, a small group of skilled fighters "
            "pushed the Imperial Army to the brink of defeat.",
        )
        self.assertAlmostEqual(cues[1][0], 8.74)
        self.assertEqual(cues[1][2], "They were known as the Ninku Brigade.")

    def test_counts_hours_and_handles_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(
                directory,
                "late.srt",
                "1\n00:23:32,917 --> 00:23:37,040\nEnd theme line.\n",
                encoding="utf-8-sig",
            )
            cues = subtitle_utils.load_subtitles(srt)

        self.assertEqual(len(cues), 1)
        self.assertAlmostEqual(cues[0][0], 1412.917)
        self.assertAlmostEqual(cues[0][1], 1417.04)

    def test_skips_cues_without_speech_text(self):
        with tempfile.TemporaryDirectory() as directory:
            srt = self.write(
                directory,
                "sparse.srt",
                "1\n00:00:01,000 --> 00:00:02,000\n...\n"
                "\n2\n00:00:02,500 --> 00:00:03,000\n\u266a\n"
                "\n3\n00:00:03,500 --> 00:00:04,000\nWait.\n",
            )
            cues = subtitle_utils.load_subtitles(srt)

        self.assertEqual([cue[2] for cue in cues], ["Wait."])
        self.assertAlmostEqual(cues[0][0], 3.5)

    def test_parses_vtt_with_minutes_seconds_and_strips_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            vtt = self.write(
                directory,
                "dialogue.vtt",
                "WEBVTT\nKind: captions\nLanguage: en\n"
                "\n00:01.200 --> 00:02.500\n<i>Hello</i> world\n"
                "\n00:02,800 is not a timing line\n"
                "\n00:03.000 --> 00:04.000 position:0%\\\\00\n"
                "Second line.\n",
            )
            cues = subtitle_utils.load_subtitles(vtt)

        self.assertEqual(len(cues), 2)
        self.assertAlmostEqual(cues[0][0], 1.2)
        self.assertAlmostEqual(cues[0][1], 2.5)
        self.assertEqual(cues[0][2], "Hello world")
        self.assertAlmostEqual(cues[1][0], 3.0)
        self.assertEqual(cues[1][2], "Second line.")


if __name__ == "__main__":
    unittest.main()
