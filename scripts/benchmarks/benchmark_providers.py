"""Run real provider benchmarks when the selected worker dependencies are installed.

Missing packages or model weights are reported as skipped; no synthetic timings are emitted.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.providers_real import ChatterboxMultilingualVoiceProvider, DeepFilterNetNoiseProvider, DemucsStemSeparationProvider, ProviderUnavailable, WhisperTranscriptionProvider


def run_stage(name: str, callback) -> dict:
    started = time.monotonic()
    try:
        result = callback()
        return {"status": "completed", "wall_clock_seconds": round(time.monotonic() - started, 4), "result": str(result)}
    except (ImportError, ProviderUnavailable, OSError, RuntimeError) as exc:
        return {"status": "skipped", "wall_clock_seconds": round(time.monotonic() - started, 4), "reason": str(exc)}
    except Exception as exc:
        return {"status": "failed", "wall_clock_seconds": round(time.monotonic() - started, 4), "reason": f"{type(exc).__name__}: {exc}"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("media", type=Path)
    parser.add_argument("--voice-reference", type=Path)
    parser.add_argument("--voice-text", default="This is a LingoWave benchmark.")
    parser.add_argument("--language", default="en")
    parser.add_argument("--output", type=Path, default=Path("provider-benchmark.json"))
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="lingowave-benchmark-") as temp:
        work = Path(temp)
        result = {"media": str(args.media), "stages": {}}
        result["stages"]["transcription"] = run_stage("transcription", lambda: WhisperTranscriptionProvider().transcribe(args.media))
        result["stages"]["demucs_2stem"] = run_stage("demucs_2stem", lambda: DemucsStemSeparationProvider().separate(args.media, stems=2, output_dir=work / "demucs2"))
        result["stages"]["demucs_4stem"] = run_stage("demucs_4stem", lambda: DemucsStemSeparationProvider().separate(args.media, stems=4, output_dir=work / "demucs4"))
        result["stages"]["noise"] = run_stage("noise", lambda: DeepFilterNetNoiseProvider().enhance(args.media, output_path=work / "enhanced.wav"))
        if args.voice_reference:
            result["stages"]["voice"] = run_stage("voice", lambda: ChatterboxMultilingualVoiceProvider().synthesize(args.voice_text, reference_voice=args.voice_reference, language=args.language, output_path=work / "speech.wav"))
        else:
            result["stages"]["voice"] = {"status": "skipped", "reason": "--voice-reference was not supplied"}
    args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
