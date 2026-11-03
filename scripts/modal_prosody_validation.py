#!/usr/bin/env python3
"""Real native-Modal baseline vs prosody-aware comparison on source groups."""
from __future__ import annotations
import io, json, pickle, sys
from pathlib import Path
import soundfile as sf
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from Video_Translator import create_Sentences, create_TimeStamps, media_duration, speaker_aware_segments
from dubbing.segments import from_legacy_lists
from dubbing.timing import TimingGroupConfig, build_timing_groups, calculate_duration_budgets
from engine.prosody import extract_prosody, prosody_distance, style_instruction
from engine.qa import evaluate_tts
import modal

def main() -> int:
    transcript = pickle.loads((ROOT / "transcripts/test_multispeaker").read_bytes())
    starts, ends = create_TimeStamps(transcript, [".", "!", "?"])
    sentences = create_Sentences(transcript, [".", "!", "?"])
    segs = from_legacy_lists(sentences, starts, ends)
    vocal = ROOT / "vocal-remover/vocals/test_multispeaker_Vocals.wav"
    segs = speaker_aware_segments(segs, str(vocal), enabled=True, num_speakers=2)
    groups = calculate_duration_budgets(build_timing_groups(segs, config=TimingGroupConfig()), media_duration=media_duration(ROOT / "test_multispeaker.mp4"))
    data, sr = sf.read(vocal, always_2d=True, dtype="float32")
    mono = data.mean(axis=1)
    profiles = {}
    for g in groups:
        path = ROOT / "temp" / "modal-prosody" / f"source_{g.index+1}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(path, mono[int(g.start*sr):int(g.end*sr)], sr, subtype="PCM_16")
        profiles[g.index+1] = extract_prosody(path)
    # Select representative real groups by measured energy/rate extrema.
    neutral = min(profiles, key=lambda i: abs(profiles[i].rms_db - sorted(p.rms_db for p in profiles.values())[len(profiles)//2]))
    high = max(profiles, key=lambda i: profiles[i].rms_db)
    fast = max(profiles, key=lambda i: profiles[i].speaking_rate)
    selected = []
    third_label, third_idx = ("faster_delivery", fast)
    if third_idx in [neutral, high]:
        third_label, third_idx = ("slower_delivery", min(profiles, key=lambda i: profiles[i].speaking_rate))
    for label, idx in (("neutral", neutral), ("higher_energy", high), (third_label, third_idx)):
        if idx not in [item[1] for item in selected]: selected.append((label, idx))
    ref = (ROOT / "speaker_reference_clips/SPEAKER_00.wav").read_bytes()
    text = "Bu grup için gerçek prosodi doğrulama cümlesidir."
    requests = []
    for label, idx in selected:
        style = style_instruction(profiles[idx])
        for strategy in ("baseline", "prosody_aware"):
            requests.append({"text": text, "reference_wav": ref, "reference_text": "So it is a big fan.", "seed": 300 + idx * 10 + (1 if strategy == "prosody_aware" else 0), "style": style if strategy == "prosody_aware" else "", "inference_timesteps": 5, "cfg_value": 2.0})
    fn = modal.Function.from_name("video-dubbing-translator", "synthesize_batch")
    responses = fn.remote(requests)
    rows=[]
    for (label, idx), pair in zip(selected, [responses[i:i+2] for i in range(0,len(responses),2)]):
        for strategy, response in zip(("baseline","prosody_aware"), pair):
            path = ROOT / "temp" / "modal-prosody" / f"{label}_{strategy}.wav"
            path.write_bytes(response["wav"])
            q = evaluate_tts(path, text, expected_duration=groups[idx-1].duration, reference_wav=ROOT / "speaker_reference_clips/SPEAKER_00.wav")
            target_profile = extract_prosody(path)
            rows.append({"label":label,"group_index":idx,"strategy":strategy,"style":requests[len(rows)].get("style",""),"modal":{k:v for k,v in response.items() if k!="wav"},"speaker_similarity":q.speaker_similarity.cosine,"intelligibility_proxy_signal_ok":not bool(q.signal.hard_failures),"timing_ratio":q.timing.ratio,"timing_error_seconds":q.timing.absolute_error_seconds,"prosody_distance":prosody_distance(profiles[idx],target_profile),"source_profile":profiles[idx].__dict__,"target_profile":target_profile.__dict__})
    result={"status":"passed","selected_groups":selected,"strategies":rows,"default_enable_prosody_aware":False,"decision":"retain opt-in until repeated language-specific evaluation justifies default"}
    out=ROOT/"temp/modal-prosody-validation.json"; out.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print(json.dumps({"status":"passed","selected_groups":selected,"rows":len(rows),"path":str(out)}))
    return 0
if __name__ == "__main__": raise SystemExit(main())
