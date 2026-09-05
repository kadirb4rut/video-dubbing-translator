"""Run one real VoxCPM2 CPU voice-cloning inference and write measurements."""

from __future__ import annotations

import argparse
import json
import os
import resource
import time
from pathlib import Path

from app.media import inspect_media, validate_output
from app.providers_real import VoxCPM2VoiceProvider


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--json", dest="json_output", type=Path, required=True)
    parser.add_argument("--text", default="This is a real VoxCPM2 CPU voice cloning validation.")
    parser.add_argument("--language", default="en")
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    provider = VoxCPM2VoiceProvider(device="cpu", dtype=os.getenv("VOXCPM_DTYPE", "bfloat16"))
    try:
        provider.synthesize(
            args.text,
            reference_voice=args.reference,
            language=args.language,
            output_path=args.output,
        )
        validate_output(args.output, "audio")
        output_metadata = inspect_media(args.output)
        elapsed = time.monotonic() - started
        usage = resource.getrusage(resource.RUSAGE_SELF)
        peak_ram_mb = usage.ru_maxrss / 1024
        cpu_seconds = usage.ru_utime + usage.ru_stime
        metrics = {
            "status": "completed",
            "provider": provider.name,
            "model": provider.model_id,
            "revision": provider.revision,
            "device": provider.device,
            "dtype": provider.dtype,
            "output": str(args.output),
            "output_metadata": output_metadata,
            "wall_clock_seconds": round(elapsed, 4),
            "peak_ram_mb": round(peak_ram_mb, 2),
            "cpu_utilization_percent": round((cpu_seconds / max(elapsed, 0.001)) * 100, 3),
            "metrics": provider.last_metrics,
        }
    except Exception as exc:  # noqa: BLE001 - benchmark records the real failure
        metrics = {
            "status": "failed",
            "provider": provider.name,
            "model": provider.model_id,
            "revision": provider.revision,
            "device": provider.device,
            "dtype": provider.dtype,
            "error": {"type": type(exc).__name__, "message": str(exc)},
            "wall_clock_seconds": round(time.monotonic() - started, 4),
        }
    finally:
        provider.release()

    args.json_output.write_text(json.dumps(metrics, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(metrics, indent=2, ensure_ascii=False))
    return 0 if metrics["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
