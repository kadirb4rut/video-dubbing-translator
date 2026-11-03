import argparse
import json
import sys
import tempfile
import time
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from translation.base import TranslationContext  # noqa: E402
from translation.hymt2 import HyMT2Translator  # noqa: E402
from dubbing.duration import synthesize_with_duration_refinement  # noqa: E402
from dubbing.references import SpeakerReference  # noqa: E402
from dubbing.segments import SpeechSegment  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description="Run real local Hy-MT2 translation and duration-rewrite smoke tests."
    )
    parser.add_argument("--model", default="hymt2-1.8b", choices=("hymt2-1.8b",))
    parser.add_argument("--device", default="auto")
    return parser.parse_args()


def main():
    args = parse_args()
    started = time.perf_counter()
    with HyMT2Translator(args.model, args.device) as translator:
        translation = translator.translate(
            "Hello, this video demonstrates fully local translation.",
            "en",
            "tr",
            context=TranslationContext(speaker="SPEAKER_00"),
            glossary={"video": "video"},
        )
        translated_at = time.perf_counter()
        duration_source = (
            "Our engineering team will deliver twelve carefully tested components tomorrow morning."
        )
        with tempfile.TemporaryDirectory() as directory:
            output_path = Path(directory) / "controlled.wav"
            synthesis_calls = []
            measured_durations = iter((5.0, 3.2))
            refinement = synthesize_with_duration_refinement(
                segment=SpeechSegment(0, 0.0, 3.0, duration_source, "SPEAKER_00"),
                initial_translation=(
                    "Mühendislik ekibimiz dikkatle test edilmiş on iki bileşeni "
                    "yarın sabah teslim edecek."
                ),
                reference=SpeakerReference(
                    "SPEAKER_00", Path(directory) / "reference.wav", "Reference text", 0
                ),
                output_path=output_path,
                translator=translator,
                source_language="en",
                target_language="tr",
                synthesize=lambda text, reference, path: (
                    synthesis_calls.append(text),
                    path.touch(),
                ),
                measure_duration=lambda path: next(measured_durations),
                context=TranslationContext(speaker="SPEAKER_00"),
                glossary={"engineering": "mühendislik"},
            )
        rewritten_at = time.perf_counter()
    if not translation.strip() or not refinement.translation.strip():
        raise RuntimeError("Hy-MT2 returned empty smoke-test output.")
    print(
        json.dumps(
            {
                "model": args.model,
                "device": args.device,
                "translation": translation,
                "duration_rewrite": refinement.translation,
                "refinement_triggered": refinement.refined,
                "refinement_passes": refinement.refinement_passes,
                "controlled_initial_tts_seconds": refinement.initial_duration,
                "controlled_refined_tts_seconds": refinement.final_duration,
                "synthesis_calls": len(synthesis_calls),
                "translation_seconds_including_load": round(translated_at - started, 3),
                "rewrite_seconds_model_reused": round(rewritten_at - translated_at, 3),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
