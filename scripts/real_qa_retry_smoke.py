#!/usr/bin/env python3
"""Real-audio QA retry proof using two native Modal candidates."""
from __future__ import annotations
import io, json, sys, time
from pathlib import Path
import soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.qa import evaluate_tts
import modal

def main() -> int:
    ref_path = ROOT / "speaker_reference_clips/SPEAKER_00.wav"
    out_dir = ROOT / "temp/modal-real-qa-retry"
    out_dir.mkdir(parents=True, exist_ok=True)
    fn = modal.Function.from_name("video-dubbing-translator", "synthesize_batch")
    ref = ref_path.read_bytes()
    text = "Bu gerçek bir kalite güvencesi yeniden üretim testidir."
    started = time.perf_counter()
    responses = fn.remote([
        {"text": text, "reference_wav": ref, "reference_text": "So it is a big fan.", "seed": 101, "inference_timesteps": 5, "cfg_value": 2.0},
        {"text": text, "reference_wav": ref, "reference_text": "So it is a big fan.", "seed": 102, "inference_timesteps": 5, "cfg_value": 2.0},
    ])
    if any(r.get("status") != "passed" for r in responses):
        raise RuntimeError(responses)
    good_bytes = responses[0]["wav"]
    good_path = out_dir / "initial_generated.wav"
    good_path.write_bytes(good_bytes)
    info = sf.info(io.BytesIO(good_bytes))
    full_duration = info.frames / info.samplerate
    audio, sr = sf.read(io.BytesIO(good_bytes), dtype="float32")
    cut_frames = max(1, int(len(audio) * 0.18))
    bad_path = out_dir / "initial_defective_truncated.wav"
    sf.write(bad_path, audio[:cut_frames], sr, subtype="PCM_16")
    regen_path = out_dir / "regenerated_candidate.wav"
    regen_path.write_bytes(responses[1]["wav"])
    bad_quality = evaluate_tts(bad_path, text, expected_duration=full_duration, reference_wav=ref_path)
    regen_quality = evaluate_tts(regen_path, text, expected_duration=full_duration, reference_wav=ref_path, retry_count=1)
    selected = "regenerated" if regen_quality.passed and (not bad_quality.passed or len(regen_quality.hard_failures) < len(bad_quality.hard_failures)) else "initial"
    result = {
        "status": "passed" if bad_quality.hard_failures and regen_quality.passed and selected == "regenerated" else "failed",
        "text": text,
        "initial_candidate": {"path": str(bad_path), "defect_injected_by_physical_truncation": True, "quality": bad_quality.__dict__},
        "regenerated_candidate": {"path": str(regen_path), "modal_cache_hit": responses[1].get("cache_hit"), "quality": regen_quality.__dict__},
        "selected": selected,
        "modal": [{k:v for k,v in r.items() if k != "wav"} for r in responses],
        "elapsed_wall_seconds": time.perf_counter() - started,
    }
    path = out_dir / "qa-retry-evidence.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    print(json.dumps({"status": result["status"], "initial_hard_failures": list(bad_quality.hard_failures), "regenerated_passed": regen_quality.passed, "selected": selected, "cache_hit": responses[1].get("cache_hit"), "path": str(path)}))
    return 0 if result["status"] == "passed" else 1

if __name__ == "__main__": raise SystemExit(main())
