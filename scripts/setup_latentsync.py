import argparse
import os
import shutil
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
LATENTSYNC_DIR = BASE_DIR / "third_party" / "LatentSync"
REPO_URL = "https://github.com/bytedance/LatentSync.git"
HF_REPO_ID = "ByteDance/LatentSync-1.6"
CHECKPOINT_FILES = ["latentsync_unet.pt", "whisper/tiny.pt"]


def run(command, cwd=None):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def clone_repo():
    if LATENTSYNC_DIR.exists():
        print(f"LatentSync repo already exists: {LATENTSYNC_DIR}")
        return
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git bulunamadi. LatentSync reposunu indirmek icin git gerekli.")
    LATENTSYNC_DIR.parent.mkdir(parents=True, exist_ok=True)
    run([git, "clone", REPO_URL, LATENTSYNC_DIR])


def download_checkpoints():
    if os.environ.get("HF_HUB_ENABLE_HF_TRANSFER") == "1":
        try:
            import hf_transfer  # noqa: F401
        except ImportError:
            os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "0"
            print("HF transfer disabled because hf_transfer is not installed.", flush=True)

    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise RuntimeError(
            "huggingface_hub yuklu degil. Once `pip install huggingface-hub` calistirin."
        ) from exc

    for filename in CHECKPOINT_FILES:
        print(f"Downloading {filename} from {HF_REPO_ID}...", flush=True)
        hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=filename,
            local_dir=LATENTSYNC_DIR / "checkpoints",
        )


def parse_args():
    parser = argparse.ArgumentParser(description="Clone LatentSync and download inference checkpoints.")
    parser.add_argument(
        "--skip-checkpoints",
        action="store_true",
        help="Only clone/update the LatentSync source tree.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    clone_repo()
    if not args.skip_checkpoints:
        download_checkpoints()
    print(
        "\nLatentSync code and checkpoints are ready.\n"
        "LatentSync has its own heavy CUDA dependencies. On a CUDA machine, install them with:\n\n"
        f"  cd {LATENTSYNC_DIR}\n"
        "  python3.10 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  pip install --upgrade pip\n"
        "  pip install -r requirements.txt\n\n"
        "If you use another Python environment, set LATENTSYNC_PYTHON=/path/to/python before starting the GUI.\n",
        flush=True,
    )


if __name__ == "__main__":
    main()
