import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE_DIR / "third_party" / "diarization"
VENV_DIR = RUNTIME_DIR / ".venv"
MODEL_DIR = RUNTIME_DIR / "community-1"
MODEL_ID = "pyannote/speaker-diarization-community-1"


def python_in_venv():
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def find_python():
    for candidate in (sys.executable, "python3.12", "python3.11", "python3.10", "python"):
        path = Path(candidate) if Path(candidate).is_absolute() else Path(shutil.which(candidate) or "")
        if path.is_file():
            return path
    raise RuntimeError("Python 3.10 or newer is required for diarization setup.")


def run(command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=BASE_DIR, check=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create the isolated pyannote Community-1 runtime."
    )
    parser.add_argument(
        "--download-model",
        action="store_true",
        help="Download Community-1 after runtime setup (requires accepted terms and HF_TOKEN).",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    python = python_in_venv()
    if not python.is_file():
        run([find_python(), "-m", "venv", VENV_DIR])
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-r", BASE_DIR / "requirements-diarization.txt"])
    print(f"Diarization runtime ready: {python}")
    if not args.download_model:
        print(
            "No model was downloaded. Accept the Community-1 terms, set HF_TOKEN, then run:\n"
            "  python scripts/setup_diarization.py --download-model"
        )
        return 0
    token = os.environ.get("HF_TOKEN")
    if not token:
        try:
            from huggingface_hub import HfFolder

            token = HfFolder.get_token()
        except ImportError:
            token = None
    if not token:
        raise RuntimeError(
            "HF_TOKEN is not set. Accept the Community-1 model terms and export a read token."
        )
    script = f"""
import os
import sys
from huggingface_hub import snapshot_download
from huggingface_hub.errors import GatedRepoError

try:
    snapshot_download(
        repo_id={MODEL_ID!r},
        local_dir={str(MODEL_DIR)!r},
        token=os.environ["HF_TOKEN"],
    )
except GatedRepoError:
    print(
        "Community-1 access is not enabled for this Hugging Face account. "
        "Accept the terms at https://huggingface.co/{MODEL_ID} and retry.",
        file=sys.stderr,
    )
    raise SystemExit(3)
"""
    # The token stays in the child environment and never appears in argv or logs.
    child_environment = os.environ.copy()
    child_environment["HF_TOKEN"] = token
    download = subprocess.run(
        [str(python), "-c", script], cwd=BASE_DIR, env=child_environment
    )
    if download.returncode:
        return download.returncode
    config_ready = (MODEL_DIR / "config.yaml").is_file()
    embedding_ready = any((MODEL_DIR / "embedding").glob("*.bin")) or any(
        (MODEL_DIR / "embedding").glob("*.safetensors")
    )
    segmentation_ready = any((MODEL_DIR / "segmentation").glob("*.bin")) or any(
        (MODEL_DIR / "segmentation").glob("*.safetensors")
    )
    if not (config_ready and embedding_ready and segmentation_ready):
        raise RuntimeError(
            "Community-1 download is incomplete. Confirm that model access was accepted."
        )
    print(f"Community-1 ready for local/offline inference: {MODEL_DIR}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
