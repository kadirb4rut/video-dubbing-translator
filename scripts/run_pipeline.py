import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
OUTPUT_DIR = BASE_DIR / "vocal-remover" / "final_video" / "final"
LATENTSYNC_DIR = BASE_DIR / "third_party" / "LatentSync"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"


def run(command, cwd=None):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def ensure_ffmpeg():
    if not FFMPEG or not Path(FFMPEG).exists():
        raise RuntimeError("ffmpeg bulunamadi. Once ffmpeg kurun ve tekrar deneyin.")
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

    local_venv_python = latentsync_dir / ".venv" / "bin" / "python"
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
            "LatentSync kurulu degil veya checkpoint dosyalari eksik.\n"
            "Kurulum icin: python scripts/setup_latentsync.py\n"
            f"Eksik dosyalar:\n{missing_text}"
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
            "LatentSync Python ortaminda torch yuklu degil veya acilamiyor.\n"
            "LatentSync bagimliliklarini kendi ortaminda kurun ve gerekirse "
            "LATENTSYNC_PYTHON degiskeniyle Python yolunu belirtin.\n"
            f"Cikti:\n{probe.stdout}"
        )
    if "cuda=True" not in probe.stdout:
        raise RuntimeError(
            "LatentSync upstream surumu NVIDIA CUDA GPU gerektiriyor. Bu Python "
            "ortami CUDA gormuyor.\n"
            "CUDA destekli bir makinede LatentSync ortamini kurun veya "
            "LATENTSYNC_PYTHON ile CUDA destekli Python yolunu belirtin."
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


def parse_args():
    parser = argparse.ArgumentParser(description="Run dubbing, optionally followed by LatentSync lip-sync.")
    parser.add_argument("video", help="Input video path")
    parser.add_argument("--source-language", default="auto")
    parser.add_argument("--target-language", default="en")
    parser.add_argument("--lip-sync", action="store_true")
    parser.add_argument("--latentsync-steps", type=int, default=20)
    parser.add_argument("--latentsync-guidance-scale", type=float, default=1.5)
    parser.add_argument("--latentsync-seed", type=int, default=1247)
    return parser.parse_args()


def main():
    args = parse_args()
    video_path = Path(args.video).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video dosyasi bulunamadi: {video_path}")

    run(
        [
            sys.executable,
            BASE_DIR / "Video_Translator.py",
            video_path,
            "--source-language",
            args.source_language,
            "--target-language",
            args.target_language,
        ]
    )

    final_output = expected_dubbed_output(video_path)
    if not final_output.exists():
        raise RuntimeError(f"Dublaj cikti dosyasi bulunamadi: {final_output}")

    if args.lip_sync:
        lip_sync_output = expected_lipsync_output(video_path)
        print(f"\nLatentSync lip-sync basliyor: {lip_sync_output}\n", flush=True)
        run_latentsync(
            str(final_output),
            lip_sync_output,
            args.latentsync_steps,
            args.latentsync_guidance_scale,
            args.latentsync_seed,
        )
        final_output = lip_sync_output

    print(f"\nFINAL_OUTPUT={final_output}\n", flush=True)


if __name__ == "__main__":
    main()
