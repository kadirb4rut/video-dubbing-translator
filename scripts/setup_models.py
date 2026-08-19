import argparse
import os
import subprocess
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
XTTS_MODEL_NAME = "tts_models/multilingual/multi-dataset/xtts_v2"
XTTS_LICENSE_URL = "https://huggingface.co/coqui/XTTS-v2/blob/main/LICENSE.txt"


def run(command):
    print("$ " + " ".join(str(part) for part in command), flush=True)
    subprocess.run([str(part) for part in command], cwd=BASE_DIR, check=True)


def xtts_model_path(manager):
    return Path(manager.output_prefix) / "tts_models--multilingual--multi-dataset--xtts_v2"


def xtts_model_ready(path):
    return all((path / name).exists() for name in ("model.pth", "config.json", "vocab.json"))


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and verify the vocal-remover and XTTS-v2 models."
    )
    parser.add_argument(
        "--accept-xtts-cpml",
        action="store_true",
        help="Confirm that you have read and accept the XTTS-v2 CPML terms.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    run([sys.executable, BASE_DIR / "scripts" / "download_model.py"])

    try:
        from TTS.utils.manage import ModelManager
    except ImportError as exc:
        raise RuntimeError(
            "Coqui TTS is not installed. Activate the Python 3.10 environment and run "
            "`pip install -r requirements.txt` first."
        ) from exc

    manager = ModelManager(progress_bar=True, verbose=True)
    model_path = xtts_model_path(manager)
    if xtts_model_ready(model_path):
        print(f"XTTS-v2 model already exists: {model_path}")
        return

    if not args.accept_xtts_cpml:
        raise RuntimeError(
            "XTTS-v2 is licensed under the non-commercial Coqui Public Model License.\n"
            f"Read {XTTS_LICENSE_URL}, then rerun with --accept-xtts-cpml if you accept it."
        )

    os.environ["COQUI_TOS_AGREED"] = "1"
    manager.download_model(XTTS_MODEL_NAME)
    if not xtts_model_ready(model_path):
        raise RuntimeError(f"XTTS-v2 download did not produce the expected files: {model_path}")
    print(f"XTTS-v2 model ready: {model_path}")


if __name__ == "__main__":
    main()
