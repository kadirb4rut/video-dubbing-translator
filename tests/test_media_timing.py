import unittest
import json
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from Video_Translator import atempo_filter, safe_media_end
from dubbing.segments import SpeechSegment
from dubbing.timing import DurationBudget, TimingGroup


class _Audio:
    def __init__(self, duration):
        self.duration = duration


class _Clip:
    def __init__(self, duration, audio_duration=None):
        self.duration = duration
        self.audio = _Audio(audio_duration) if audio_duration is not None else None


class MediaTimingTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
    def test_loudnorm_mix_preserves_video_duration_and_tail_audio(self):
        # On Colab FFmpeg 6.1, the old -shortest mixer cut this 6 s clip to
        # 3.1 s. A long background track makes the buffering interaction real.
        import numpy as np
        from Video_Translator import concatenate_Video_And_Background_Sound

        with TemporaryDirectory() as directory:
            root = Path(directory)
            speech, background, output = [root / name for name in ('speech.mp4', 'background.wav', 'output.mp4')]
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=size=160x90:rate=30:duration=6', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000:duration=6', '-c:v', 'libx264', '-c:a', 'aac', '-t', '6', str(speech)], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=mono', '-t', '12', str(background)], check=True)
            concatenate_Video_And_Background_Sound(str(speech), str(background), str(output))
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'json', str(output)]))
            self.assertAlmostEqual(float(info['format']['duration']), 6.0, delta=0.05)
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '5.5', '-i', str(output), '-f', 'f32le', '-ac', '1', '-ar', '16000', 'pipe:1'])
            tail = np.frombuffer(raw, dtype='<f4')
            self.assertGreater(len(tail), 6000)
            self.assertGreater(float(np.sqrt(np.mean(tail ** 2))), 0.01)

    def test_safe_media_end_does_not_erode_each_segment(self):
        clip = _Clip(4.5, 4.4)
        self.assertEqual(safe_media_end(clip, requested_end=4.338), 4.338)

    def test_safe_media_end_clamps_to_available_media(self):
        clip = _Clip(4.3, 4.2)
        self.assertEqual(safe_media_end(clip, requested_end=4.5), 4.2)

    def test_atempo_is_bounded_to_natural_limits(self):
        self.assertEqual(atempo_filter(4.0), "atempo=1.25000000")
        self.assertEqual(atempo_filter(0.2), "atempo=0.85000000")
        self.assertEqual(atempo_filter(1.1), "atempo=1.10000000")

    def test_change_speed_reads_the_selected_final_wav(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg_dir, mp4_dir, wav_dir, final_dir = [root / name for name in ("ffmpeg", "mp4", "wav", "final")]
            for path in (ffmpeg_dir, mp4_dir, wav_dir, final_dir):
                path.mkdir()
            (wav_dir / "1.wav").write_bytes(b"selected-final-wav")
            (mp4_dir / "1.mp4").write_bytes(b"video")
            source = SpeechSegment(0, 0, 1, "One", "SPEAKER_00")
            group = TimingGroup(0, (source,), 0, 1, "One", ())
            commands = []

            def fake_duration(path):
                return 2.0 if str(path).endswith("wav/1.wav") else 1.0

            with patch("Video_Translator.ensure_ffmpeg", return_value="ffmpeg"), patch(
                "Video_Translator.media_duration", side_effect=fake_duration
            ), patch("Video_Translator.run_command", side_effect=commands.append):
                from Video_Translator import change_ClipSpeed

                change_ClipSpeed([group], str(ffmpeg_dir), str(mp4_dir), str(wav_dir), str(final_dir))
            self.assertEqual(len(commands), 2)
            self.assertIn(str(wav_dir / "1.wav"), [str(item) for item in commands[0]])
            self.assertIn("atempo=1.25000000", commands[0])

    def test_change_speed_targets_preferred_duration_not_safe_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            ffmpeg_dir, mp4_dir, wav_dir, final_dir = [root / name for name in ("ffmpeg", "mp4", "wav", "final")]
            for path in (ffmpeg_dir, mp4_dir, wav_dir, final_dir):
                path.mkdir()
            (wav_dir / "1.wav").write_bytes(b"selected-final-wav")
            (mp4_dir / "1.mp4").write_bytes(b"video")
            source = SpeechSegment(0, 0, 1, "One", "SPEAKER_00")
            budget = DurationBudget(0, 1, 1, (), 0, 2, 0, 1, 2, 2)
            group = TimingGroup(0, (source,), 0, 1, "One", (), budget)
            commands = []

            def fake_duration(path):
                return 1.2 if str(path).endswith("wav/1.wav") else 2.0

            with patch("Video_Translator.ensure_ffmpeg", return_value="ffmpeg"), patch(
                "Video_Translator.media_duration", side_effect=fake_duration
            ), patch("Video_Translator.run_command", side_effect=commands.append):
                from Video_Translator import change_ClipSpeed

                change_ClipSpeed([group], str(ffmpeg_dir), str(mp4_dir), str(wav_dir), str(final_dir))
            # 1.2 s audio / 1.0 s preferred duration, not / 2.0 s safe window.
            self.assertIn("atempo=1.20000000", commands[0])


if __name__ == "__main__":
    unittest.main()
