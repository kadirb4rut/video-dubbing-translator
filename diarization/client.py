from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Optional

from .assignment import DiarizationResult, validate_speaker_bounds
from .model_manager import BASE_DIR, MODEL_DIR, runtime_python, status


class DiarizationUnavailable(RuntimeError):
    pass


def diarize(
    audio_path: Path,
    *,
    device: str = "auto",
    num_speakers: Optional[int] = None,
    min_speakers: Optional[int] = None,
    max_speakers: Optional[int] = None,
) -> DiarizationResult:
    validate_speaker_bounds(num_speakers, min_speakers, max_speakers)
    readiness = status()
    if not readiness.installed or not readiness.runtime_ready:
        raise DiarizationUnavailable(readiness.detail)
    request = {
        "audio_path": str(Path(audio_path).expanduser().resolve()),
        "model_dir": str(MODEL_DIR.resolve()),
        "device": device,
        "num_speakers": num_speakers,
        "min_speakers": min_speakers,
        "max_speakers": max_speakers,
    }
    completed = subprocess.run(
        [str(runtime_python()), str(BASE_DIR / "diarization" / "worker.py")],
        input=json.dumps(request),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise DiarizationUnavailable(f"Diarization failed: {detail}")
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise DiarizationUnavailable("Diarization worker returned invalid JSON.") from exc
    if not payload.get("ok"):
        raise DiarizationUnavailable(str(payload.get("error") or "Unknown diarization error."))
    return DiarizationResult.from_dict(payload)
