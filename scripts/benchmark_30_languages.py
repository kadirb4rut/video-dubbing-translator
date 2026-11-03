#!/usr/bin/env python3
"""Run the honest 30-language VoxCPM2 benchmark.

The default command is a validation harness, not a synthetic pass generator:
each unavailable backend is recorded as ``blocked`` with the exact exception.
Use ``--real`` only after explicitly caching VoxCPM2, Hy-MT2 1.8B, and the
optional M2M100 fallback model.
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.backends import NativeVoxCPMBackend  # noqa: E402
from engine.contracts import SpeakerProfile  # noqa: E402
from engine.reports import render_html  # noqa: E402
from translation.backends import (  # noqa: E402
    M2M100Backend,
    M2M100_FALLBACK_LANGUAGES,
    VOXCPM2_LANGUAGES,
    HyMT2Backend,
    LANGUAGE_NAMES,
)


CASES = (
    ("normal", "The local dubbing engine keeps every speaker consistent."),
    ("short", "Ready."),
    ("name", "OpenBMB built VoxCPM2."),
    ("number", "Ship 12 units on 2026-09-15."),
    ("technical", "The CUDA kernel uses a 48 kHz PCM stream."),
    ("expressive", "This is wonderful—let's begin!"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "temp" / "benchmark-30-languages")
    parser.add_argument("--reference-wav", type=Path, default=ROOT / "speaker_reference_clips" / "SPEAKER_00.wav")
    parser.add_argument("--reference-text", default="So it's a big fan and that's really what got me into AI was, it's actually a long story.")
    parser.add_argument("--real", action="store_true", help="Perform model inference; without this only environment checks run.")
    parser.add_argument("--max-languages", type=int, default=30)
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    speaker = SpeakerProfile("SPEAKER_00", args.reference_wav, args.reference_text)
    translators = {}
    tts = None
    if args.real:
        try:
            translators["hymt2-1.8b"] = HyMT2Backend(device=args.device)
        except Exception as exc:
            translators["hymt2-1.8b"] = exc
        try:
            translators["m2m100-418m"] = M2M100Backend(device=args.device)
        except Exception as exc:
            translators["m2m100-418m"] = exc
        try:
            tts = NativeVoxCPMBackend(device=args.device)
        except Exception as exc:
            tts = exc
    started = time.perf_counter()
    for language in VOXCPM2_LANGUAGES[: max(0, min(30, args.max_languages))]:
        backend_name = "m2m100-418m" if language in M2M100_FALLBACK_LANGUAGES else "hymt2-1.8b"
        backend = translators.get(backend_name)
        for case_name, source_text in CASES:
            row = {"language": language, "language_name": LANGUAGE_NAMES[language], "case": case_name, "source_text": source_text, "translation_backend": backend_name, "tts_backend": "voxcpm2-native", "status": "not_run", "wall_seconds": None, "audio_seconds": None, "error": None}
            if not args.real:
                row["status"] = "blocked"
                row["error"] = "--real not supplied; no inference claimed"
                rows.append(row)
                continue
            try:
                if isinstance(backend, Exception):
                    raise backend
                if isinstance(tts, Exception):
                    raise tts
                if not args.reference_wav.is_file():
                    raise FileNotFoundError(f"Reference WAV missing: {args.reference_wav}")
                started_call = time.perf_counter()
                translated = backend.translate(source_text, "en", language)
                target = args.output_dir / language / f"{case_name}.wav"
                result = tts.synthesize(translated, speaker, output_wav=target, seed=42)
                row.update(status="passed", translated_text=translated, wall_seconds=time.perf_counter() - started_call, audio_seconds=result.duration_seconds)
            except Exception as exc:
                row["status"] = "blocked"
                row["error"] = f"{type(exc).__name__}: {exc}"
            rows.append(row)
    payload = {"schema_version": "1.0", "benchmark": "voxcpm2-30-language", "created_at": time.time(), "host": platform.platform(), "languages": list(VOXCPM2_LANGUAGES[: max(0, min(30, args.max_languages))]), "cases": [name for name, _ in CASES], "summary": {"languages": len(set(row["language"] for row in rows)), "cases": len(CASES), "rows": len(rows), "passed": sum(row["status"] == "passed" for row in rows), "blocked": sum(row["status"] == "blocked" for row in rows), "wall_seconds": time.perf_counter() - started}, "rows": rows}
    (args.output_dir / "benchmark_30_languages.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    fields = ["language", "language_name", "case", "translation_backend", "tts_backend", "status", "wall_seconds", "audio_seconds", "translated_text", "error"]
    with (args.output_dir / "benchmark_30_languages.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({field: row.get(field) for field in fields} for row in rows)
    (args.output_dir / "benchmark_30_languages.html").write_text(render_html({"run_id": "benchmark_30_languages", "summary": payload["summary"], "groups": [{"group_index": index, "quality": {"hard_failures": [row["error"]] if row["status"] == "blocked" else [], "wer": None, "cer": None}, "prosody_distance": None} for index, row in enumerate(rows)]}), encoding="utf-8")
    for value in translators.values():
        if hasattr(value, "close"):
            value.close()
    return 0 if payload["summary"]["blocked"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
