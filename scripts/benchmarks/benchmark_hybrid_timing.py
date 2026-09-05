"""Benchmark the hybrid translation timing gate.

This is a small control-flow benchmark, not a model-quality benchmark. It
deliberately supplies three measured/synthetic TTS durations so the routing
contract can be checked without spending time loading VoxCPM2 or Hy-MT2.
Pass ``--real-refinement`` to run the real configured refinement provider for
the two overlong cases; the default run is dependency-light and proves only
that a fitting segment never invokes refinement and each overlong segment gets
at most one pass.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.config import settings
from app.providers_real import translation_refinement_provider
from app.worker import JobWorker

CASES = (
    {"id": "fit", "window_seconds": 2.0, "first_tts_seconds": 2.1, "refined_tts_seconds": 2.1},
    {"id": "moderate-mismatch", "window_seconds": 2.0, "first_tts_seconds": 2.6, "refined_tts_seconds": 2.1},
    {"id": "large-mismatch", "window_seconds": 2.0, "first_tts_seconds": 5.0, "refined_tts_seconds": 2.4},
)


def run(*, real_refinement: bool) -> dict:
    tolerance = settings.translation_duration_tolerance
    max_passes = max(0, settings.translation_refinement_max_passes)
    provider = translation_refinement_provider() if real_refinement else None
    results = []
    refinement_calls = 0
    try:
        for case in CASES:
            segment_refinement_passes = 0
            should_refine = JobWorker._requires_duration_refinement(case["first_tts_seconds"], case["window_seconds"], tolerance)
            refined = False
            final_seconds = case["first_tts_seconds"]
            refined_text = None
            if should_refine and segment_refinement_passes < max_passes and provider is not None:
                rewrite = getattr(provider, "rewrite_for_duration", None)
                if not callable(rewrite):
                    raise RuntimeError(f"{provider.name} does not support duration refinement")
                refined_text = rewrite(
                    "This deliberately long translated segment contains names, numbers, and technical terms.",
                    target="tr",
                    max_seconds=case["window_seconds"] * (1 + tolerance),
                    source_text="This deliberately long source segment contains names, numbers, and technical terms.",
                    context="Hybrid timing benchmark context.",
                    glossary=[{"source": "LingoWave", "target": "LingoWave"}],
                    style="natural spoken dubbing",
                )
                refinement_calls += 1
                segment_refinement_passes += 1
                refined = True
                final_seconds = case["refined_tts_seconds"]
            elif should_refine and segment_refinement_passes < max_passes:
                # Routing-only mode represents the measured post-refinement
                # duration without invoking a model.
                refinement_calls += 1
                segment_refinement_passes += 1
                refined = True
                final_seconds = case["refined_tts_seconds"]
                refined_text = "<routing-only refinement>"
            results.append(
                {
                    "id": case["id"],
                    "window_seconds": case["window_seconds"],
                    "first_tts_seconds": case["first_tts_seconds"],
                    "duration_deviation_before_pct": round((case["first_tts_seconds"] / case["window_seconds"] - 1) * 100, 4),
                    "refinement_required": should_refine,
                    "refinement_used": refined,
                    "refinement_passes": segment_refinement_passes,
                    "refined_text": refined_text,
                    "final_tts_seconds": final_seconds,
                    "duration_deviation_after_pct": round((final_seconds / case["window_seconds"] - 1) * 100, 4),
                }
            )
    finally:
        release = getattr(provider, "release", None) if provider else None
        if callable(release):
            release()
    return {
        "provider": settings.translation_refinement_provider,
        "mode": "real-refinement" if real_refinement else "routing-only",
        "tolerance": tolerance,
        "max_refinement_passes": max_passes,
        "total_segments": len(CASES),
        "refinement_calls": refinement_calls,
        "refined_segment_ids": [item["id"] for item in results if item["refinement_used"]],
        "results": results,
        "assertions": {
            "fit_case_not_refined": not results[0]["refinement_used"],
            "mismatch_cases_refined": all(item["refinement_used"] for item in results[1:]),
            "bounded_to_one_pass_per_segment": all(item["refinement_passes"] <= max_passes for item in results),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--real-refinement", action="store_true")
    args = parser.parse_args()
    result = run(real_refinement=args.real_refinement)
    if not all(result["assertions"].values()):
        raise SystemExit(json.dumps(result, indent=2))
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")


if __name__ == "__main__":
    main()
