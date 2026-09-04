"""Benchmark the complete real dubbing pipeline without inventing timings.

The script uses the production provider adapters. Configure the production
translation provider through the normal environment, or pass a JSON array of
translated segment texts for a deterministic local fixture run.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.media import inspect_media, validate_output
from app.providers_real import (
    ChatterboxMultilingualVoiceProvider,
    DemucsStemSeparationProvider,
    ProviderUnavailable,
    WhisperTranscriptionProvider,
    translation_provider,
    validate_segments,
)


def runtime_metadata() -> dict:
    gpu_type = os.getenv("GPU_TYPE") or None
    gpu_memory_mib = None
    try:
        detected = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip().splitlines()[0]
        name, memory = [part.strip() for part in detected.split(",", 1)]
        gpu_type = gpu_type or name
        gpu_memory_mib = int(float(memory))
    except (FileNotFoundError, IndexError, ValueError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        pass
    hourly_price = os.getenv("GPU_HOURLY_PRICE_USD")
    try:
        hourly_price_usd = float(hourly_price) if hourly_price else None
    except ValueError:
        hourly_price_usd = None
    return {
        "worker_type": os.getenv("WORKER_TYPE") or None,
        "gpu_type": gpu_type,
        "gpu_memory_mib": gpu_memory_mib,
        "aws_instance_type": os.getenv("AWS_INSTANCE_TYPE") or os.getenv("EC2_INSTANCE_TYPE") or None,
        "aws_region": os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or None,
        "hourly_price_usd": hourly_price_usd,
        "pricing_source": os.getenv("GPU_PRICING_SOURCE") or None,
    }


def ffmpeg(args: list[str]) -> None:
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=3600)


def synthesize_voice(text: str, *, reference_voice: Path, language: str, output_path: Path, python_path: str | None) -> None:
    """Run Chatterbox in-process or through a compatible secondary runtime.

    The secondary-runtime option is benchmark-only. It lets a workstation
    measure a split audio/voice environment when the local Python ABI cannot
    load both provider dependency sets at once; production workers still use
    the in-process provider implementation.
    """
    if not python_path:
        ChatterboxMultilingualVoiceProvider().synthesize(text, reference_voice=reference_voice, language=language, output_path=output_path)
        return
    code = (
        "from pathlib import Path; "
        "from app.providers_real import ChatterboxMultilingualVoiceProvider; "
        f"ChatterboxMultilingualVoiceProvider(device='cpu').synthesize({text!r}, "
        f"reference_voice=Path({str(reference_voice)!r}), language={language!r}, "
        f"output_path=Path({str(output_path)!r}))"
    )
    environment = dict(os.environ, PYTHONPATH=str(BACKEND), CHATTERBOX_DEVICE=os.getenv("CHATTERBOX_DEVICE", "cpu"))
    subprocess.run([python_path, "-c", code], cwd=str(ROOT), env=environment, check=True, timeout=3600)
    validate_output(output_path, "audio")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("--reference-voice", type=Path, required=True)
    parser.add_argument("--target-language", default="es")
    parser.add_argument("--source-language")
    parser.add_argument("--translated-segments", type=Path, help="JSON array of translated texts for a deterministic local fixture")
    parser.add_argument("--chatterbox-python", help="Optional compatible Python executable for a benchmark-only secondary voice runtime")
    parser.add_argument("--output", type=Path, default=Path("dubbed-benchmark.mp4"))
    parser.add_argument("--json", dest="json_output", type=Path, default=Path("dubbed-benchmark.json"))
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.json_output.parent.mkdir(parents=True, exist_ok=True)

    total_started = time.monotonic()
    source_meta = None
    output_meta = None
    stages: dict[str, dict] = {}
    result = {
        "status": "running",
        "input": str(args.video),
        "output": str(args.output),
        "source": None,
        "output_metadata": None,
        "runtime": runtime_metadata(),
        "processed_minutes": None,
        "wall_clock_seconds": None,
        "cost_per_processed_minute_usd": None,
        "stages": stages,
    }
    try:
        source_meta = inspect_media(args.video)
        result["source"] = source_meta
        if source_meta["media_kind"] != "video":
            raise ValueError("Complete dubbing benchmark requires a video input")

        with tempfile.TemporaryDirectory(prefix="lingowave-dubbing-benchmark-") as temp:
            work = Path(temp)

            started = time.monotonic()
            source_audio = work / "source.wav"
            ffmpeg(["-i", str(args.video), "-vn", "-ac", "1", "-ar", "24000", str(source_audio)])
            stages["extract_audio"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4)}

            started = time.monotonic()
            transcriber = WhisperTranscriptionProvider()
            segments = validate_segments(list(transcriber.transcribe(source_audio, language=args.source_language)))
            stages["transcription"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "segment_count": len(segments)}
            if not segments:
                raise RuntimeError("Transcription returned no segments")

            started = time.monotonic()
            if args.translated_segments:
                translated_texts = json.loads(args.translated_segments.read_text(encoding="utf-8"))
                if not isinstance(translated_texts, list) or len(translated_texts) != len(segments):
                    raise ValueError("--translated-segments must contain one text value per transcribed segment")
                translated = [{**segment, "text": str(translated_texts[index])} for index, segment in enumerate(segments)]
                translation_name = "deterministic-fixture"
            else:
                translator = translation_provider()
                translated = validate_segments(list(translator.translate(segments, source=args.source_language or transcriber.detected_language or "auto", target=args.target_language)))
                translation_name = translator.name
            stages["translation"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "segment_count": len(translated), "provider": translation_name}

            started = time.monotonic()
            background = DemucsStemSeparationProvider().separate(source_audio, stems=2, output_dir=work / "background")["instrumental"]
            stages["background_separation"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "output_bytes": background.stat().st_size}

            started = time.monotonic()
            clips: list[Path] = []
            for index, segment in enumerate(translated):
                clip = work / f"segment-{index:04d}.wav"
                synthesize_voice(str(segment["text"]), reference_voice=args.reference_voice, language=args.target_language, output_path=clip, python_path=args.chatterbox_python)
                clips.append(clip)
            stages["synthesis"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "segment_count": len(clips), "output_bytes": sum(path.stat().st_size for path in clips), "runtime": args.chatterbox_python or sys.executable}

            started = time.monotonic()
            inputs: list[str] = []
            filters: list[str] = []
            for index, clip in enumerate(clips):
                inputs.extend(["-i", str(clip)])
                delay = max(0, round(float(translated[index]["start"]) * 1000))
                filters.append(f"[{index}:a]adelay={delay}|{delay},apad[a{index}]")
            mix_inputs = "".join(f"[a{index}]" for index in range(len(clips)))
            filters.append(f"{mix_inputs}amix=inputs={len(clips)}:duration=longest:normalize=0[dub]")
            dubbed_audio = work / "dubbed.wav"
            ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[dub]", "-ar", "48000", "-t", f"{source_meta['duration_seconds']:.3f}", str(dubbed_audio)])
            mixed_audio = work / "mixed.wav"
            ffmpeg(["-i", str(background), "-i", str(dubbed_audio), "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[mix]", "-map", "[mix]", "-ar", "48000", "-t", f"{source_meta['duration_seconds']:.3f}", str(mixed_audio)])
            ffmpeg(["-i", str(args.video), "-i", str(mixed_audio), "-map", "0:v?", "-map", "1:a", "-c:v", "copy", "-t", f"{source_meta['duration_seconds']:.3f}", str(args.output)])
            output_meta = validate_output(args.output, "video", expected_duration_seconds=source_meta["duration_seconds"])
            stages["mix_and_mux"] = {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "output_bytes": args.output.stat().st_size}

        result["status"] = "completed"
        result["output_metadata"] = output_meta
    except Exception as exc:  # noqa: BLE001 - benchmark must record unexpected provider failures
        result["status"] = "blocked" if isinstance(exc, (ImportError, ProviderUnavailable)) else "failed"
        result["error"] = {"type": type(exc).__name__, "message": str(exc)}

    processed_minutes = source_meta["duration_seconds"] / 60 if source_meta else None
    wall_clock_seconds = time.monotonic() - total_started
    result["runtime"] = runtime_metadata()
    result["processed_minutes"] = processed_minutes
    result["wall_clock_seconds"] = round(wall_clock_seconds, 4)
    if result["runtime"]["hourly_price_usd"] is not None and processed_minutes:
        result["cost_per_processed_minute_usd"] = round((wall_clock_seconds / 3600 * result["runtime"]["hourly_price_usd"]) / processed_minutes, 6)
    args.json_output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))
    if result["status"] != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
