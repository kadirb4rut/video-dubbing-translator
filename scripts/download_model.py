from pathlib import Path

import requests
from tqdm import tqdm


MODEL_URL = "https://huggingface.co/fabiogra/baseline_vocal_remover/resolve/main/baseline.pth"
MODEL_PATH = Path(__file__).resolve().parents[1] / "vocal-remover" / "models" / "baseline.pth"


def main():
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    if MODEL_PATH.exists() and MODEL_PATH.stat().st_size > 0:
        print(f"Model already exists: {MODEL_PATH}")
        return

    print(f"Downloading vocal-remover model to {MODEL_PATH}")
    with requests.get(MODEL_URL, stream=True, timeout=30) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0))
        with tqdm(total=total, unit="B", unit_scale=True, desc="baseline.pth") as progress:
            with MODEL_PATH.open("wb") as file:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        file.write(chunk)
                        progress.update(len(chunk))

    print("Model download complete.")


if __name__ == "__main__":
    main()
