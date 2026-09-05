"""Run a real Hy-MT2 translation benchmark over a contextual dubbing corpus.

This intentionally calls the configured production provider. It does not
replace inference with fixtures; the fixture file only supplies the corpus.
The JSON output is suitable for later side-by-side comparison with AWS
Translate using the same exact corpus.
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.providers_real import (
    ProviderUnavailable,
    translation_provider,
    validate_segments,
)


def peak_ram_mb() -> float | None:
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        value /= 1024 * 1024
    else:
        value /= 1024
    return round(value, 2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=Path(__file__).parent / "fixtures" / "hymt2-dubbing.json")
    parser.add_argument("--output", type=Path, default=Path("artifacts/translation-benchmark.json"))
    args = parser.parse_args()
    corpus = json.loads(args.corpus.read_text(encoding="utf-8"))
    segments = validate_segments(corpus["segments"])
    provider = translation_provider()
    started = time.monotonic()
    cpu_started = resource.getrusage(resource.RUSAGE_SELF)
    result = {
        "status": "running",
        "provider": getattr(provider, "name", type(provider).__name__),
        "model": os.getenv("TRANSLATION_MODEL", "tencent/Hy-MT2-1.8B"),
        "runtime": "transformers-in-process",
        "source_language": corpus["source_language"],
        "target_language": corpus["target_language"],
        "segment_count": len(segments),
        "input_chars": sum(len(item["text"]) for item in segments),
        "requested_batch_size": int(os.getenv("TRANSLATION_BATCH_SIZE", "4")),
        "corpus": str(args.corpus),
    }
    input_duration_seconds = max((float(item["end"]) for item in segments), default=0.0)
    try:
        hourly_price_usd = float(os.getenv("COMPUTE_HOURLY_PRICE_USD", ""))
    except ValueError:
        hourly_price_usd = None
    result["input_duration_seconds"] = input_duration_seconds
    result["hourly_price_usd"] = hourly_price_usd
    try:
        translated = validate_segments(
            list(
                provider.translate_segments(
                    segments,
                    source=corpus["source_language"],
                    target=corpus["target_language"],
                    context=" ".join(item["text"] for item in segments),
                    glossary=corpus.get("glossary"),
                    style=corpus.get("style"),
                    duration_aware=True,
                )
            )
        )
        wall = time.monotonic() - started
        cpu_finished = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = (cpu_finished.ru_utime - cpu_started.ru_utime) + (cpu_finished.ru_stime - cpu_started.ru_stime)
        metrics = getattr(provider, "last_metrics", {})
        result.update(
            {
                "status": "completed",
                "wall_clock_seconds": round(wall, 4),
                "cpu_seconds": round(cpu_seconds, 4),
                "cpu_utilization_percent": round(cpu_seconds / wall * 100, 2) if wall else None,
                "peak_ram_mb": peak_ram_mb(),
                "output_chars": sum(len(item["text"]) for item in translated),
                "segments_per_second": round(len(translated) / wall, 4) if wall else None,
                "chars_per_second": round(sum(len(item["text"]) for item in translated) / wall, 4) if wall else None,
                "provider_metrics": metrics,
                "translations": [{"id": item.get("id"), "source": source["text"], "target": item["text"]} for source, item in zip(segments, translated, strict=True)],
            }
        )
        if hourly_price_usd is not None:
            compute_cost = wall / 3600 * hourly_price_usd
            result["estimated_compute_cost_usd"] = round(compute_cost, 6)
            result["estimated_compute_cost_per_input_minute_usd"] = round(compute_cost / (input_duration_seconds / 60), 6) if input_duration_seconds > 0 else None
    except (ProviderUnavailable, RuntimeError, OSError) as exc:
        result.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}", "wall_clock_seconds": round(time.monotonic() - started, 4), "peak_ram_mb": peak_ram_mb()})
        raise
    finally:
        release = getattr(provider, "release", None)
        if callable(release):
            release()
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
