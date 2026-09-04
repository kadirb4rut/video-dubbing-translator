"""Measure media inspection and optional provider stages without inventing pricing data."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from app.media import inspect_media


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--output", type=Path, default=Path("benchmark-result.json"))
    args = parser.parse_args()
    started = time.monotonic()
    metadata = inspect_media(args.media)
    result = {
        "media": str(args.media),
        "metadata": metadata,
        "ffprobe_seconds": round(time.monotonic() - started, 4),
        "providers": {
            "transcription": {"measured": False, "reason": "Run from a worker image with the selected model."},
            "voice": {"measured": False, "reason": "Requires consented reference audio and Chatterbox model weights."},
            "stems": {"measured": False, "reason": "Requires Demucs checkpoint download."},
            "noise": {"measured": False, "reason": "Requires DeepFilterNet installation."},
        },
    }
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
