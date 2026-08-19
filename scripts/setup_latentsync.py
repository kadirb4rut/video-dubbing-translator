import argparse
import os
import shutil
import subprocess
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
LATENTSYNC_DIR = Path(
    os.environ.get("LATENTSYNC_DIR", BASE_DIR / "third_party" / "LatentSync")
).expanduser().resolve()
REPO_URL = "https://github.com/bytedance/LatentSync.git"
REPO_REVISION = "a229c3948406bc2cf6eaf4873e662e70c6a04746"
HF_REPO_ID = "ByteDance/LatentSync-1.6"
HF_REVISION = "c42c7e6c8e9c213626389fa7d9a3c444b8536353"
CHECKPOINT_FILES = ["latentsync_unet.pt", "whisper/tiny.pt"]


def run(command, cwd=None):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=cwd, check=True)


def clone_repo():
    if LATENTSYNC_DIR.exists():
        if not (LATENTSYNC_DIR / ".git").is_dir():
            raise RuntimeError(
                f"LatentSync path exists but is not a git checkout: {LATENTSYNC_DIR}"
            )
        current_revision = subprocess.run(
            ["git", "-C", str(LATENTSYNC_DIR), "rev-parse", "HEAD"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=True,
        ).stdout.strip()
        if current_revision != REPO_REVISION:
            raise RuntimeError(
                f"Existing LatentSync checkout is at {current_revision}, but this project is "
                f"validated against {REPO_REVISION}. Move or remove {LATENTSYNC_DIR}, then rerun "
                "the setup script."
            )
        print(f"Pinned LatentSync source already exists: {LATENTSYNC_DIR}")
        return
    git = shutil.which("git")
    if not git:
        raise RuntimeError("git was not found. It is required to download LatentSync.")
    LATENTSYNC_DIR.parent.mkdir(parents=True, exist_ok=True)
    LATENTSYNC_DIR.mkdir()
    try:
        run([git, "init"], cwd=LATENTSYNC_DIR)
        run([git, "remote", "add", "origin", REPO_URL], cwd=LATENTSYNC_DIR)
        run([git, "fetch", "--depth", "1", "origin", REPO_REVISION], cwd=LATENTSYNC_DIR)
        run([git, "checkout", "--detach", "FETCH_HEAD"], cwd=LATENTSYNC_DIR)
    except Exception:
        # This directory was created immediately above, so a failed setup can be
        # retried without leaving a misleading partial checkout behind.
        shutil.rmtree(LATENTSYNC_DIR, ignore_errors=True)
        raise


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
            "huggingface_hub is not installed. Run `pip install huggingface-hub` first."
        ) from exc

    for filename in CHECKPOINT_FILES:
        print(f"Downloading {filename} from {HF_REPO_ID}...", flush=True)
        hf_hub_download(
            repo_id=HF_REPO_ID,
            filename=filename,
            local_dir=LATENTSYNC_DIR / "checkpoints",
            revision=HF_REVISION,
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
    ready_message = (
        "LatentSync source is ready; checkpoint download was skipped."
        if args.skip_checkpoints
        else "LatentSync source and checkpoints are ready."
    )
    print(
        f"\n{ready_message}\n"
        "LatentSync 1.6 needs an NVIDIA CUDA GPU with approximately 18 GB of VRAM and "
        "has its own heavy dependencies. Install them with:\n\n"
        f"  cd {LATENTSYNC_DIR}\n"
        "  python3.10 -m venv .venv\n"
        "  source .venv/bin/activate\n"
        "  pip install --upgrade pip\n"
        "  pip install -r requirements.txt\n\n"
        "If you use another Python environment, set LATENTSYNC_PYTHON=/path/to/python before starting the GUI.\n"
        "The source is Apache-2.0; downloaded checkpoints are marked OpenRAIL++ on Hugging Face.\n",
        flush=True,
    )


if __name__ == "__main__":
    main()
