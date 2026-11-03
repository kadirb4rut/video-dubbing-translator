import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from diarization.assignment import validate_speaker_bounds  # noqa: E402
from translation.hymt2 import validate_device  # noqa: E402
from translation.registry import MODEL_BY_KEY  # noqa: E402
from dubbing.timing import TimingGroupConfig  # noqa: E402
OUTPUT_DIR = BASE_DIR / "vocal-remover" / "final_video" / "final"
LATENTSYNC_DIR = BASE_DIR / "third_party" / "LatentSync"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def run(command, cwd=None):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def ensure_ffmpeg():
    if not FFMPEG or not Path(FFMPEG).exists():
        raise RuntimeError("FFmpeg was not found. Install FFmpeg and try again.")
    return FFMPEG


def expected_dubbed_output(video_path):
    return OUTPUT_DIR / Path(video_path).name


def expected_lipsync_output(video_path):
    input_path = Path(video_path)
    return OUTPUT_DIR / f"{input_path.stem}_lipsync{input_path.suffix or '.mp4'}"


def latentsync_python(latentsync_dir):
    env_python = os.environ.get("LATENTSYNC_PYTHON")
    if env_python:
        return Path(env_python).expanduser()

    for relative_path in ((".venv", "bin", "python"), (".venv", "Scripts", "python.exe")):
        local_venv_python = latentsync_dir.joinpath(*relative_path)
        if local_venv_python.exists():
            return local_venv_python

    return Path(sys.executable)


def check_latentsync_ready(latentsync_dir, python_executable):
    required_paths = [
        latentsync_dir / "scripts" / "inference.py",
        latentsync_dir / "configs" / "unet" / "stage2_512.yaml",
        latentsync_dir / "checkpoints" / "latentsync_unet.pt",
        latentsync_dir / "checkpoints" / "whisper" / "tiny.pt",
    ]
    missing = [path for path in required_paths if not path.exists()]
    if missing:
        missing_text = "\n".join(f"- {path}" for path in missing)
        raise RuntimeError(
            "LatentSync is not installed or checkpoint files are missing.\n"
            "Run: python scripts/setup_latentsync.py\n"
            f"Missing files:\n{missing_text}"
        )

    if os.environ.get("LATENTSYNC_ALLOW_NO_CUDA") == "1":
        return

    probe = subprocess.run(
        [
            str(python_executable),
            "-c",
            "import torch; print('cuda=' + str(torch.cuda.is_available()))",
        ],
        cwd=latentsync_dir,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    if probe.returncode != 0:
        raise RuntimeError(
            "PyTorch is missing or cannot be imported in the LatentSync environment.\n"
            "Install LatentSync dependencies in its environment and, if needed, set "
            "LATENTSYNC_PYTHON to that Python executable.\n"
            f"Output:\n{probe.stdout}"
        )
    if "cuda=True" not in probe.stdout:
        raise RuntimeError(
            "LatentSync 1.6 requires an NVIDIA CUDA GPU, and this environment cannot "
            "see CUDA. Use a CUDA machine or point LATENTSYNC_PYTHON to a CUDA-enabled "
            "environment."
        )


def run_latentsync(video_path, output_path, steps, guidance_scale, seed):
    latentsync_dir = Path(os.environ.get("LATENTSYNC_DIR", LATENTSYNC_DIR)).expanduser().resolve()
    python_executable = latentsync_python(latentsync_dir)
    check_latentsync_ready(latentsync_dir, python_executable)

    temp_dir = BASE_DIR / "temp" / "latentsync"
    temp_dir.mkdir(parents=True, exist_ok=True)
    audio_path = temp_dir / f"{Path(video_path).stem}_latentsync_audio.wav"
    run([ensure_ffmpeg(), "-y", "-i", video_path, "-vn", "-ar", "16000", "-ac", "1", audio_path])

    output_path.parent.mkdir(parents=True, exist_ok=True)
    command = [
        python_executable,
        "-m",
        "scripts.inference",
        "--unet_config_path",
        "configs/unet/stage2_512.yaml",
        "--inference_ckpt_path",
        "checkpoints/latentsync_unet.pt",
        "--inference_steps",
        str(steps),
        "--guidance_scale",
        str(guidance_scale),
        "--enable_deepcache",
        "--video_path",
        video_path,
        "--audio_path",
        audio_path,
        "--video_out_path",
        output_path,
        "--seed",
        str(seed),
    ]
    run(command, cwd=latentsync_dir)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run local speaker-aware dubbing, optionally followed by LatentSync.")
    parser.add_argument("video", help="Input video path")
    parser.add_argument("--source-language", default="auto")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--no-diarization", action="store_false", dest="diarization")
    parser.set_defaults(diarization=True)
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--diarization-device", default="auto")
    parser.add_argument(
        "--translation-model", choices=tuple(MODEL_BY_KEY), default="hymt2-1.8b"
    )
    parser.add_argument("--translation-device", default="auto")
    parser.add_argument(
        "--tts-backend", choices=("native", "modal"), default="native",
        help="Modal offloads only VoxCPM2 speech generation; other stages stay local.",
    )
    parser.add_argument("--glossary", help="Optional JSON glossary path.")
    parser.add_argument("--duration-tolerance", type=float, default=0.20)
    parser.add_argument("--max-refinement-passes", type=int, default=1)
    parser.add_argument("--timing-max-gap", type=float, default=0.75)
    parser.add_argument("--timing-micro-seconds", type=float, default=0.85)
    parser.add_argument("--timing-max-group-seconds", type=float, default=14.0)
    parser.add_argument("--min-tempo", type=float, default=0.85)
    parser.add_argument("--max-tempo", type=float, default=1.25)
    parser.add_argument("--ideal-min-tempo", type=float, default=0.90)
    parser.add_argument("--ideal-max-tempo", type=float, default=1.10)
    parser.add_argument("--lip-sync", action="store_true")
    parser.add_argument("--latentsync-steps", type=int, default=20)
    parser.add_argument("--latentsync-guidance-scale", type=float, default=1.5)
    parser.add_argument("--latentsync-seed", type=int, default=1247)
    args = parser.parse_args(argv)
    try:
        validate_speaker_bounds(args.num_speakers, args.min_speakers, args.max_speakers)
        validate_device(args.translation_device)
        if args.duration_tolerance < 0 or args.max_refinement_passes < 0:
            raise ValueError("Duration tolerance and maximum refinement passes cannot be negative.")
        TimingGroupConfig(
            max_gap_seconds=args.timing_max_gap,
            micro_segment_seconds=args.timing_micro_seconds,
            max_group_duration_seconds=args.timing_max_group_seconds,
        )
        if args.min_tempo <= 0 or args.max_tempo < args.min_tempo:
            raise ValueError("Tempo limits must be positive and ordered.")
        if args.ideal_min_tempo <= 0 or args.ideal_max_tempo < args.ideal_min_tempo:
            raise ValueError("Ideal tempo limits must be positive and ordered.")
    except ValueError as exc:
        parser.error(str(exc))
    return args


def main():
    args = parse_args()
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        print(f"Error: video file not found: {video_path}", file=sys.stderr)
        return 2

    command = [
        sys.executable,
        BASE_DIR / "Video_Translator.py",
        video_path,
        "--source-language",
        args.source_language,
        "--target-language",
        args.target_language,
        "--translation-model",
        args.translation_model,
        "--translation-device",
        args.translation_device,
        "--tts-backend",
        args.tts_backend,
        "--diarization-device",
        args.diarization_device,
        "--duration-tolerance",
        str(args.duration_tolerance),
        "--max-refinement-passes",
        str(args.max_refinement_passes),
        "--timing-max-gap",
        str(args.timing_max_gap),
        "--timing-micro-seconds",
        str(args.timing_micro_seconds),
        "--timing-max-group-seconds",
        str(args.timing_max_group_seconds),
        "--min-tempo",
        str(args.min_tempo),
        "--max-tempo",
        str(args.max_tempo),
        "--ideal-min-tempo",
        str(args.ideal_min_tempo),
        "--ideal-max-tempo",
        str(args.ideal_max_tempo),
    ]
    if not args.diarization:
        command.append("--no-diarization")
    for option, value in (
        ("--num-speakers", args.num_speakers),
        ("--min-speakers", args.min_speakers),
        ("--max-speakers", args.max_speakers),
        ("--glossary", args.glossary),
    ):
        if value is not None:
            command.extend([option, str(value)])
    run(command)

    final_output = expected_dubbed_output(video_path)
    if not final_output.exists():
        raise RuntimeError(f"Dubbed output was not created: {final_output}")

    if args.lip_sync:
        lip_sync_output = expected_lipsync_output(video_path)
        print(f"\nStarting LatentSync lip-sync: {lip_sync_output}\n", flush=True)
        run_latentsync(
            str(final_output),
            lip_sync_output,
            args.latentsync_steps,
            args.latentsync_guidance_scale,
            args.latentsync_seed,
        )
        final_output = lip_sync_output

    print(f"\nFINAL_OUTPUT={final_output}\n", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
