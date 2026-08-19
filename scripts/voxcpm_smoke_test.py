import argparse
import json
import resource
import sys
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from voxcpm_runtime import load_model, synthesize_cloned_speech  # noqa: E402


def peak_rss_mb():
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
    return round(value / divisor, 1)


def parse_args():
    parser = argparse.ArgumentParser(description="Run a real VoxCPM2 reference-voice synthesis test.")
    parser.add_argument("--reference", required=True, help="Reference speaker WAV path")
    parser.add_argument("--text", required=True, help="Text to synthesize")
    parser.add_argument("--prompt-text", help="Exact transcript of the reference audio")
    parser.add_argument("--output", required=True, help="Output WAV path")
    parser.add_argument("--device", default=None, help="auto, cpu, mps, cuda, or cuda:N")
    return parser.parse_args()


def main():
    import numpy as np
    import soundfile as sf

    args = parse_args()
    load_started = time.perf_counter()
    model = load_model(device=args.device)
    load_seconds = time.perf_counter() - load_started

    synthesis_started = time.perf_counter()
    output_path = synthesize_cloned_speech(
        model=model,
        text=args.text,
        reference_wav_path=args.reference,
        output_wav_path=args.output,
        prompt_text=args.prompt_text,
    )
    synthesis_seconds = time.perf_counter() - synthesis_started

    audio, sample_rate = sf.read(output_path, always_2d=False)
    duration = len(audio) / sample_rate
    metrics = {
        "output": str(output_path),
        "device": model.tts_model.device,
        "sample_rate": sample_rate,
        "channels": 1 if audio.ndim == 1 else audio.shape[1],
        "duration_seconds": round(duration, 3),
        "load_seconds": round(load_seconds, 3),
        "synthesis_seconds": round(synthesis_seconds, 3),
        "real_time_factor": round(synthesis_seconds / duration, 3),
        "peak_amplitude": round(float(np.max(np.abs(audio))), 6),
        "peak_rss_mb": peak_rss_mb(),
    }
    print(json.dumps(metrics, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
