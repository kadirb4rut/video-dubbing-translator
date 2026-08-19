import argparse
import importlib
import importlib.metadata
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from voxcpm_runtime import default_runtime_device, model_ready  # noqa: E402


VOCAL_MODEL = BASE_DIR / "vocal-remover" / "models" / "baseline.pth"
LATENTSYNC_DIR = Path(
    os.environ.get("LATENTSYNC_DIR", BASE_DIR / "third_party" / "LatentSync")
).expanduser().resolve()
IMPORTS = {
    "torch": "torch",
    "torchaudio": "torchaudio",
    "voxcpm": "voxcpm",
    "openai-whisper": "whisper",
    "whisperx": "whisperx",
    "deep-translator": "deep_translator",
    "moviepy": "moviepy",
    "fsorter": "fsorter",
    "requests": "requests",
    "huggingface-hub": "huggingface_hub",
    "librosa": "librosa",
    "matplotlib": "matplotlib",
    "opencv-python": "cv2",
    "resampy": "resampy",
    "soundfile": "soundfile",
    "tqdm": "tqdm",
    "numpy": "numpy",
    "transformers": "transformers",
    "safetensors": "safetensors",
}


def report(level, message):
    print(f"[{level}] {message}")


def command_version(command):
    path = shutil.which(command)
    if not path:
        return None, None
    probe = subprocess.run(
        [path, "-version"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    first_line = probe.stdout.splitlines()[0] if probe.stdout else "version unavailable"
    return path, first_line


def parse_args():
    parser = argparse.ArgumentParser(description="Check whether this machine is ready to run dubbing.")
    parser.add_argument(
        "--skip-imports",
        action="store_true",
        help="Skip dependency imports when checking a not-yet-installed environment.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    failures = 0

    if sys.version_info[:2] == (3, 10):
        report("PASS", f"Python {sys.version.split()[0]} (supported version)")
    else:
        failures += 1
        report(
            "FAIL",
            f"Python {sys.version.split()[0]} is active; use Python 3.10 because the pinned AI stack "
            "is not validated on other versions.",
        )

    for command in ("ffmpeg", "ffprobe"):
        path, version = command_version(command)
        if path:
            report("PASS", f"{command}: {path} ({version})")
        else:
            failures += 1
            report("FAIL", f"{command} was not found on PATH.")

    free_gb = shutil.disk_usage(BASE_DIR).free / (1024**3)
    if free_gb >= 10:
        report("PASS", f"Free disk space: {free_gb:.1f} GB")
    else:
        report("WARN", f"Free disk space is {free_gb:.1f} GB; model setup may need 10+ GB.")

    if not args.skip_imports:
        for distribution, module in IMPORTS.items():
            try:
                importlib.import_module(module)
                try:
                    version = importlib.metadata.version(distribution)
                except importlib.metadata.PackageNotFoundError:
                    version = "version unavailable"
                report("PASS", f"Import {module} ({distribution} {version})")
            except Exception as exc:
                failures += 1
                report("FAIL", f"Import {module}: {type(exc).__name__}: {exc}")

    if VOCAL_MODEL.exists() and VOCAL_MODEL.stat().st_size > 0:
        report("PASS", f"Vocal-remover model: {VOCAL_MODEL}")
    else:
        failures += 1
        report("FAIL", "Vocal-remover model is missing; run `python scripts/setup_models.py`.")

    voxcpm_ready, voxcpm_path = model_ready()
    if voxcpm_ready:
        report("PASS", f"VoxCPM2 model: {voxcpm_path}")
    else:
        failures += 1
        report(
            "FAIL",
            "VoxCPM2 is missing or incomplete; run `python scripts/setup_models.py`.",
        )

    try:
        with tempfile.NamedTemporaryFile(prefix=".write-check-", dir=BASE_DIR):
            pass
        report("PASS", f"Project path is writable: {BASE_DIR}")
    except OSError as exc:
        failures += 1
        report("FAIL", f"Project path is not writable: {exc}")

    required_latentsync = [
        LATENTSYNC_DIR / "scripts" / "inference.py",
        LATENTSYNC_DIR / "configs" / "unet" / "stage2_512.yaml",
        LATENTSYNC_DIR / "checkpoints" / "latentsync_unet.pt",
        LATENTSYNC_DIR / "checkpoints" / "whisper" / "tiny.pt",
    ]
    if all(path.exists() for path in required_latentsync):
        report("PASS", f"Optional LatentSync files: {LATENTSYNC_DIR}")
    else:
        report("WARN", "Optional LatentSync is not installed (normal for the base dubbing workflow).")

    try:
        import torch

        if torch.cuda.is_available():
            report("PASS", f"CUDA available: {torch.cuda.get_device_name(0)}")
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            selected = default_runtime_device()
            if selected == "cpu":
                report(
                    "WARN",
                    "Apple MPS is available, but this machine has less than 16 GB unified memory. "
                    "VoxCPM2 defaults to slower CPU/bfloat16 inference to avoid MPS/float32 exhaustion; "
                    "LatentSync remains unavailable.",
                )
            else:
                report(
                    "WARN",
                    "Apple MPS is available. VoxCPM2 uses float32 on MPS for stability and needs substantial "
                    "unified memory; LatentSync remains unavailable.",
                )
        else:
            report("WARN", "No GPU accelerator detected; expect slow CPU processing.")
    except ImportError:
        pass

    report(
        "INFO",
        "Translation is not offline: transcript text is sent to Google Translate through deep-translator.",
    )
    if failures:
        report("RESULT", f"Setup is not ready ({failures} required check(s) failed).")
        return 1
    report("RESULT", "Setup is ready for the base dubbing workflow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
