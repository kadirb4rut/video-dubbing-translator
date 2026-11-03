#!/usr/bin/env python3
"""Offline ASR/language diagnostics for preserved good/bad regression media."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "temp" / "regression-investigation-20261024"
GOOD = ROOT / "vocal-remover" / "final_video" / "final" / "test_multispeaker.mp4"
BAD = ROOT / "temp" / "modal-e2e-1789503194" / "final_video" / "final" / "test_multispeaker.mp4"
BAD_PRE_MIX = ROOT / "temp" / "modal-e2e-1789503194" / "final_video" / "test_multispeaker.mp4"
BACKGROUND = ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Instruments.wav"
SOURCE = ROOT / "test_multispeaker.mp4"
BAD_WAV_DIR = ROOT / "temp" / "modal-e2e-1789503194" / "wav"


def extract_audio(video: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(video),
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output),
        ],
        check=True,
    )


def audio_stats(path: Path) -> dict[str, float | int]:
    samples, rate = sf.read(path, dtype="float32", always_2d=False)
    values = np.asarray(samples, dtype=np.float32)
    if values.ndim > 1:
        values = values.mean(axis=1)
    return {
        "sample_rate": int(rate),
        "samples": int(values.size),
        "duration_seconds": float(values.size / rate),
        "peak": float(np.max(np.abs(values))) if values.size else 0.0,
        "rms": float(np.sqrt(np.mean(values * values))) if values.size else 0.0,
        "silence_fraction": float(np.mean(np.abs(values) < 1e-4)) if values.size else 1.0,
    }


def transcribe(model, path: Path) -> dict[str, object]:
    segments, info = model.transcribe(
        str(path),
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    rows = []
    text_parts = []
    for segment in segments:
        text = segment.text.strip()
        if text:
            text_parts.append(text)
        rows.append(
            {
                "start": float(segment.start),
                "end": float(segment.end),
                "text": text,
                "avg_logprob": float(getattr(segment, "avg_logprob", 0.0)),
                "no_speech_prob": float(getattr(segment, "no_speech_prob", 0.0)),
            }
        )
    return {
        "path": str(path),
        "language": getattr(info, "language", None),
        "language_probability": float(getattr(info, "language_probability", 0.0)),
        "text": " ".join(text_parts).strip(),
        "segments": rows,
        "audio": audio_stats(path),
    }


def main() -> int:
    INV.mkdir(parents=True, exist_ok=True)
    extracted = {}
    for label, video in (("source", SOURCE), ("good", GOOD), ("bad", BAD), ("bad_pre_mix", BAD_PRE_MIX), ("background", BACKGROUND)):
        path = INV / f"{label}.wav"
        if video.suffix.lower() == ".wav":
            path.write_bytes(video.read_bytes())
        else:
            extract_audio(video, path)
        extracted[label] = path

    from faster_whisper import WhisperModel

    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    result: dict[str, object] = {"status": "passed", "full": {}, "bad_groups": {}}
    for label, path in extracted.items():
        result["full"][label] = transcribe(model, path)
    for wav in sorted(BAD_WAV_DIR.glob("*.wav"), key=lambda p: int(p.stem)):
        result["bad_groups"][wav.stem] = transcribe(model, wav)
    output = INV / "audio-diagnosis.json"
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
