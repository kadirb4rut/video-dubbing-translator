#!/usr/bin/env python3
"""Add run-level timing/speaker/performance fields to a generated report."""
from __future__ import annotations
import json, pickle, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Video_Translator import create_Sentences, create_TimeStamps, media_duration, speaker_aware_segments
from dubbing.segments import from_legacy_lists
from dubbing.timing import TimingGroupConfig, build_timing_groups, calculate_duration_budgets

def main(run_dir: str) -> int:
    run = Path(run_dir).resolve()
    report_path = run / "quality_report.json"
    evidence = json.loads((run / "modal-e2e-evidence.json").read_text())
    payload = json.loads(report_path.read_text())
    transcript = pickle.loads((ROOT / "transcripts/test_multispeaker").read_bytes())
    starts, ends = create_TimeStamps(transcript, [".", "!", "?"])
    sentences = create_Sentences(transcript, [".", "!", "?"])
    segments = from_legacy_lists(sentences, starts, ends)
    segments = speaker_aware_segments(segments, str(ROOT / "vocal-remover/vocals/test_multispeaker_Vocals.wav"), enabled=True, num_speakers=2)
    groups = calculate_duration_budgets(build_timing_groups(segments, config=TimingGroupConfig()), media_duration=media_duration(ROOT / "test_multispeaker.mp4"))
    by_index = {g.index + 1: g for g in groups}
    for row in payload.get("groups", []):
        g = by_index.get(int(row["group_index"]))
        if g is None: continue
        row.update({"speaker": g.speaker, "source_start_seconds": g.start, "source_end_seconds": g.end, "source_duration_seconds": g.duration, "timing_group_segments": [i + 1 for i in g.original_segment_indices]})
    perf = payload.setdefault("performance", {})
    perf.update({"modal_backend": evidence["tts_backend"], "modal_request_count": len(evidence["modal_requests"]), "modal_prompt_cache_hits": evidence["prompt_cache_hits"], "e2e_elapsed_wall_seconds": evidence["elapsed_wall_seconds"], "source_duration_seconds": evidence["source_duration_seconds"], "qa_retry_count": evidence["qa_retry_count"], "prosody_groups": evidence["prosody_groups"]})
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2))
    # Keep the HTML synchronized with the verified JSON.
    from engine.reports import render_html
    (run / "quality_report.html").write_text(render_html(payload), encoding="utf-8")
    print(json.dumps({"status":"passed", "groups":len(payload.get("groups", [])), "report":str(report_path)}))
    return 0

if __name__ == "__main__": raise SystemExit(main(sys.argv[1]))
