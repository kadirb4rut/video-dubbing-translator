import argparse
import shutil
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE_DIR / "third_party" / "hymt2"
VENV_DIR = RUNTIME_DIR / ".venv"


def python_in_venv():
    return VENV_DIR / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def find_python():
    candidates = ["python3.12", "python3.11", "python3.10", sys.executable, "python"]
    for candidate in candidates:
        path = Path(candidate) if Path(candidate).is_absolute() else Path(shutil.which(candidate) or "")
        if not path.is_file():
            continue
        probe = subprocess.run(
            [str(path), "-c", "import sys; print(sys.version_info[:2] >= (3, 10))"],
            text=True,
            stdout=subprocess.PIPE,
            check=False,
        )
        if probe.returncode == 0 and probe.stdout.strip() == "True":
            return path
    raise RuntimeError("Python 3.10 or newer is required for the Hy-MT2 runtime.")


def run(command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=BASE_DIR, check=True)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Create the isolated Hy-MT2 Transformers runtime."
    )
    parser.add_argument(
        "--download-model",
        choices=("hymt2-1.8b", "hymt2-7b", "hymt2-30b-a3b"),
        help="Explicitly download one model after installing the runtime.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    python = python_in_venv()
    if not python.is_file():
        run([find_python(), "-m", "venv", VENV_DIR])
    run([python, "-m", "pip", "install", "--upgrade", "pip"])
    run([python, "-m", "pip", "install", "-r", BASE_DIR / "requirements-hymt2.txt"])
    print(f"Hy-MT2 isolated runtime ready: {python}")
    if args.download_model:
        run(
            [
                python,
                BASE_DIR / "scripts" / "manage_hymt2_models.py",
                "download",
                args.download_model,
            ]
        )
    else:
        print(
            "No weights were downloaded. Choose one explicitly with:\n"
            "  python scripts/manage_hymt2_models.py download hymt2-1.8b"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
