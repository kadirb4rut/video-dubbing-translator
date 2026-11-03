"""Isolated pyannote Community-1 worker. Reads one JSON request from stdin."""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path


def _turns(annotation) -> list[dict[str, object]]:
    return [
        {"start": float(turn.start), "end": float(turn.end), "speaker": str(speaker)}
        for turn, speaker in annotation
    ]


def main() -> int:
    try:
        request = json.loads(sys.stdin.read())
        from pyannote.audio import Pipeline
        import torch

        pipeline = Pipeline.from_pretrained(str(Path(request["model_dir"]).resolve()))
        requested_device = request.get("device", "auto")
        if requested_device == "auto":
            requested_device = "cuda" if torch.cuda.is_available() else "cpu"
        if requested_device != "cpu":
            pipeline.to(torch.device(requested_device))
        options = {
            key: request[key]
            for key in ("num_speakers", "min_speakers", "max_speakers")
            if request.get(key) is not None
        }
        output = pipeline(request["audio_path"], **options)
        result = {
            "ok": True,
            "turns": _turns(output.speaker_diarization),
            "exclusive_turns": _turns(output.exclusive_speaker_diarization),
        }
        sys.stdout.write(json.dumps(result))
        return 0
    except Exception as exc:
        sys.stdout.write(
            json.dumps({"ok": False, "error": f"{type(exc).__name__}: {exc}"})
        )
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
