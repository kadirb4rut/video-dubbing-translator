import hashlib
from pathlib import Path

import requests
from tqdm import tqdm


MODEL_REVISION = "0206a40f1c92aa7caa8303e022906e3da0d87fb0"
MODEL_SHA256 = "f0bf9cb226e20571aac8aeda9f6d5f70e495c7b9b3457afe4b11cfec3b515fc3"
MODEL_URL = (
    "https://huggingface.co/fabiogra/baseline_vocal_remover/resolve/"
    f"{MODEL_REVISION}/baseline.pth"
)
MODEL_PATH = Path(__file__).resolve().parents[1] / "vocal-remover" / "models" / "baseline.pth"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        actual_sha256 = sha256(MODEL_PATH)
        if actual_sha256 == MODEL_SHA256:
            print(f"Model already exists and passed checksum verification: {MODEL_PATH}")
            return
        raise RuntimeError(
            f"Existing model checksum does not match the pinned release: {MODEL_PATH}\n"
            f"Expected: {MODEL_SHA256}\nActual:   {actual_sha256}\n"
            "Remove the file and run this script again."
        )

    print(f"Downloading vocal-remover model to {MODEL_PATH}")
    partial_path = MODEL_PATH.with_suffix(".pth.part")
    try:
        with requests.get(MODEL_URL, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            with tqdm(total=total, unit="B", unit_scale=True, desc="baseline.pth") as progress:
                with partial_path.open("wb") as file:
                    for chunk in response.iter_content(chunk_size=1024 * 1024):
                        if chunk:
                            file.write(chunk)
                            progress.update(len(chunk))

        actual_sha256 = sha256(partial_path)
        if actual_sha256 != MODEL_SHA256:
            raise RuntimeError(
                "Downloaded model failed checksum verification.\n"
                f"Expected: {MODEL_SHA256}\nActual:   {actual_sha256}"
            )
        partial_path.replace(MODEL_PATH)
    finally:
        partial_path.unlink(missing_ok=True)

    print("Model download complete; checksum verified.")


if __name__ == "__main__":
    main()
