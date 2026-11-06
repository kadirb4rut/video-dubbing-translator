#!/usr/bin/env python3
"""Create the synthetic, rights-cleared media used by the CUDA stability matrix.

Requires FFmpeg, FFprobe, and eSpeak/eSpeak NG on PATH. The generated media is
local test data; the script does not download or upload anything.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path


RATE = 22_050


def executable(*names: str) -> str:
    for name in names:
        path = shutil.which(name)
        if path:
            return path
    raise SystemExit(f"Required executable not found: {' or '.join(names)}")


def run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(part) for part in command],
        check=True,
        capture_output=True,
        text=True,
    )


def synthesize(espeak: str, text: str, *, voice: str = "en-us", speed: int = 150) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
        path = Path(handle.name)
    try:
        result = subprocess.run(
            [espeak, "-v", voice, "-s", str(speed), "-p", "50", "-a", "90", "-w", str(path), text],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or f"eSpeak exited {result.returncode}")
        with wave.open(str(path), "rb") as source:
            params = source.getparams()
            frames = source.readframes(source.getnframes())
        if (params.sampwidth, params.nchannels, params.framerate) != (2, 1, RATE):
            raise RuntimeError(f"Unexpected eSpeak WAV parameters: {params}")
        return frames
    finally:
        path.unlink(missing_ok=True)


def write_wav(path: Path, frames: bytes, duration: float) -> None:
    target_frames = round(RATE * duration)
    current_frames = len(frames) // 2
    if current_frames > target_frames:
        raise ValueError(
            f"Speech is {current_frames / RATE:.2f}s, longer than {duration:.2f}s."
        )
    frames += b"\0\0" * (target_frames - current_frames)
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(RATE)
        output.writeframes(frames)


def create_video(
    ffmpeg: str,
    audio: Path | None,
    target: Path,
    duration: float,
    *,
    width: int = 640,
    height: int = 360,
    fps: int = 24,
) -> None:
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}",
    ]
    if audio is not None:
        command += ["-i", str(audio), "-map", "0:v:0", "-map", "1:a:0"]
    else:
        command += ["-map", "0:v:0"]
    command += [
        "-t",
        f"{duration:.3f}",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "27",
        "-pix_fmt",
        "yuv420p",
        "-r",
        str(fps),
    ]
    if audio is not None:
        command += ["-c:a", "aac", "-b:a", "96k", "-ar", "44100"]
    command += ["-movflags", "+faststart", str(target)]
    run(command)


def probe(ffprobe: str, path: Path) -> dict[str, object]:
    result = run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_name,codec_type,width,height,r_frame_rate,sample_rate,channels,duration",
            "-of",
            "json",
            str(path),
        ]
    )
    return json.loads(result.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output_dir", type=Path, help="Directory for generated WAV/MP4 fixtures")
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg = executable("ffmpeg")
    ffprobe = executable("ffprobe")
    espeak = executable("espeak-ng", "espeak")

    single_15 = synthesize(
        espeak,
        "This short source clip has one English speaker. The observatory opens before sunrise. "
        "Scientists study distant stars and share their discoveries with students.",
        speed=155,
    )
    t1_wav = output_dir / "T1_single_speaker_15s.wav"
    write_wav(t1_wav, single_15, 15.0)
    create_video(ffmpeg, t1_wav, output_dir / "T1_single_speaker_15s.mp4", 15.0)

    single_60_text = (
        "The telescope begins its work before the sun appears above the horizon. "
        "A small team checks each instrument and carefully records the changing sky. "
        "Clear notes help scientists compare observations from different nights. "
        "Students learn how light travels across enormous distances through space. "
        "The observatory shares new pictures and explanations with people around the world. "
        "Every measurement adds another detail to our understanding of distant planets and stars. "
        "The researchers discuss their results, check the instruments, and prepare the next observation. "
        "This is a synthetic single speaker test for translation, voice preservation, and synchronization."
    )
    t2_wav = output_dir / "T2_single_speaker_60s.wav"
    write_wav(t2_wav, synthesize(espeak, single_60_text, speed=145), 60.0)
    create_video(ffmpeg, t2_wav, output_dir / "T2_single_speaker_60s.mp4", 60.0)

    t3_wav = output_dir / "T3_short_2p2s.wav"
    write_wav(t3_wav, synthesize(espeak, "Bright stars shine tonight.", speed=155), 2.2)
    create_video(ffmpeg, t3_wav, output_dir / "T3_short_2p2s.mp4", 2.2)

    turns = [
        ("A", "The observatory opens its doors before sunrise to welcome visiting students."),
        ("B", "Our small team checks the telescope and prepares the camera for tonight."),
        ("A", "Clear skies allow us to study planets and distant stars together."),
        ("B", "We record every measurement so researchers can compare their results later."),
        ("A", "The new instrument gathers light from objects far beyond our own galaxy."),
        ("B", "Students ask questions while the scientists explain how the telescope works."),
        ("A", "Careful observations help us learn how these bright stars change over time."),
        ("B", "The team shares fresh images with classrooms around the world each week."),
        ("A", "We begin another observation as the night sky slowly grows darker."),
        ("B", "A quiet control room lets everyone listen and review the incoming data."),
        ("A", "Every discovery gives us a better view of the surrounding universe."),
        ("B", "Tomorrow we will compare these notes and plan our next experiment."),
    ]
    voices = {"A": "en-us+m3", "B": "en-us+f3"}
    combined = bytearray()
    ground_truth = []
    for label, text in turns:
        frames = synthesize(espeak, text, voice=voices[label], speed=145)
        start = len(combined) / 2 / RATE
        combined.extend(frames)
        end = len(combined) / 2 / RATE
        ground_truth.append(
            {
                "speaker": label,
                "voice": voices[label],
                "start": round(start, 3),
                "end": round(end, 3),
                "transcript": text,
            }
        )
        combined.extend(b"\0\0" * round(0.30 * RATE))
    t4_wav = output_dir / "T4_two_speaker_75s.wav"
    write_wav(t4_wav, bytes(combined), 75.0)
    create_video(ffmpeg, t4_wav, output_dir / "T4_two_speaker_75s.mp4", 75.0, fps=30)

    create_video(ffmpeg, None, output_dir / "T5_video_without_audio.mp4", 15.0)
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(t1_wav),
            "-c:a",
            "pcm_s16le",
            str(output_dir / "T6_audio_only.wav"),
        ]
    )

    t7_speech = synthesize(
        espeak,
        "The observatory studies the night sky. Scientists share their findings.",
        speed=155,
    )
    t7_wav = output_dir / "T7_trailing_silence_75s.wav"
    write_wav(t7_wav, t7_speech, 75.0)
    create_video(ffmpeg, t7_wav, output_dir / "T7_trailing_silence_75s.mp4", 75.0)
    create_video(
        ffmpeg,
        t1_wav,
        output_dir / "T8_portrait_30fps_15s.mp4",
        15.0,
        width=360,
        height=640,
        fps=30,
    )

    t9_wav = output_dir / "T9_subsecond_0p6s.wav"
    write_wav(t9_wav, b"\0\0" * round(RATE * 0.6), 0.6)
    create_video(ffmpeg, t9_wav, output_dir / "T9_subsecond_0p6s.mp4", 0.6)

    scenario_files = {
        "T1": {"file": "T1_single_speaker_15s.mp4", "duration_seconds": 15, "speakers": 1},
        "T2": {"file": "T2_single_speaker_60s.mp4", "duration_seconds": 60, "speakers": 1},
        "T3": {"file": "T3_short_2p2s.mp4", "duration_seconds": 2.2, "speakers": 1},
        "T4": {
            "file": "T4_two_speaker_75s.mp4",
            "duration_seconds": 75,
            "speakers": 2,
            "fps": 30,
            "ground_truth_turns": ground_truth,
        },
        "T5": {"file": "T5_video_without_audio.mp4", "expected": "safe pre-inference rejection"},
        "T6": {"file": "T6_audio_only.wav", "expected": "safe pre-inference rejection"},
        "T7": {
            "file": "T7_trailing_silence_75s.mp4",
            "duration_seconds": 75,
            "speech_seconds": round(len(t7_speech) / 2 / RATE, 3),
        },
        "T8": {
            "file": "T8_portrait_30fps_15s.mp4",
            "duration_seconds": 15,
            "resolution": "360x640",
            "fps": 30,
        },
        "T9": {"file": "T9_subsecond_0p6s.mp4", "duration_seconds": 0.6, "expected": "safe pre-inference rejection"},
    }
    files = []
    for item in sorted(output_dir.iterdir()):
        if not item.is_file() or item.name == "fixture-manifest.json":
            continue
        files.append(
            {
                "file": item.name,
                "bytes": item.stat().st_size,
                "sha256": hashlib.sha256(item.read_bytes()).hexdigest(),
                "probe": probe(ffprobe, item),
            }
        )
    manifest = {
        "rights": "Locally generated eSpeak synthetic speech, digital silence, and FFmpeg testsrc2 video; no third-party recordings.",
        "source_language": "English",
        "target_language": "Turkish",
        "audio": "Mono PCM S16LE WAV at 22050 Hz, AAC 44100 Hz in MP4 fixtures.",
        "video": "FFmpeg testsrc2, H.264/yuv420p, CRF 27, 640x360 except portrait T8 360x640.",
        "two_speaker_design": "Alternating eSpeak en-us+m3 (A) and en-us+f3 (B), 145 wpm, 0.30 s turn gaps.",
        "scenarios": scenario_files,
        "files": files,
    }
    (output_dir / "fixture-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Generated fixture set: {output_dir}")
    for scenario, details in scenario_files.items():
        print(f"{scenario}: {details['file']}")


if __name__ == "__main__":
    main()
