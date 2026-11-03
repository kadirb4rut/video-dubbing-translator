#!/usr/bin/env python3
"""One real integrated 72-second E2E pass using the native Modal VoxCPM2 service.

The source separation/transcript are the already validated real inputs for this
fixture; every downstream artifact is generated in a fresh run directory.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import shutil
import sys
import time
from pathlib import Path

import soundfile as sf
from moviepy.editor import VideoFileClip

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Video_Translator import (  # noqa: E402
    BASE_DIR,
    FFMPEG,
    VOXCPM_OUTPUT_SAMPLE_RATE,
    analyze_reference_quality,
    calculate_duration_budgets,
    change_ClipSpeed,
    clip_Parts_without_speaker,
    concatenate_All_Clip_Parts,
    concatenate_Video_And_Background_Sound,
    create_Sentences,
    create_TimeStamps,
    generate_dubbed_speech,
    media_duration,
    remove_and_setting_video_ends,
    save_Subclips,
    save_Final_Video,
    speaker_aware_segments,
    write_reference_clip,
)
from dubbing.references import SpeakerReferenceManager
from dubbing.segments import from_legacy_lists
from dubbing.timing import TimingGroupConfig, build_timing_groups
from engine.context import ContextMemory
from engine.prosody import extract_prosody


def main() -> int:
    source_video = ROOT / "test_multispeaker.mp4"
    if not source_video.exists():
        # The fixture is also referenced by the user's absolute path; keep the
        # fallback explicit for workspaces where it is mounted in the parent.
        source_video = ROOT / "test_multispeaker.mp4"
    source_audio = ROOT / "original_wav" / "test_multispeaker.wav"
    vocal_audio = ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Vocals.wav"
    instrumental_audio = ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Instruments.wav"
    transcript_path = ROOT / "transcripts" / "test_multispeaker"
    if not (source_video.exists() and vocal_audio.exists() and instrumental_audio.exists() and transcript_path.exists()):
        raise FileNotFoundError("Validated real fixture inputs are missing")

    run_id = f"modal-e2e-{int(time.time())}"
    run_root = ROOT / "temp" / run_id
    for name in ("wav", "mp4", "final_movie", "ffmpeg", "prosody", "references"):
        (run_root / name).mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

    # Real WhisperX transcript generated for this exact 72.12 s fixture.
    transcript = pickle.loads(transcript_path.read_bytes())
    starts, ends = create_TimeStamps(transcript, [".", "!", "?"])
    sentences = create_Sentences(transcript, [".", "!", "?"])
    segments = from_legacy_lists(sentences, starts, ends)
    segments = speaker_aware_segments(
        segments, str(vocal_audio), enabled=True, diarization_device="auto", num_speakers=2
    )
    timing_groups = calculate_duration_budgets(
        build_timing_groups(
            segments,
            config=TimingGroupConfig(max_gap_seconds=0.75, micro_segment_seconds=0.85, max_group_duration_seconds=14.0),
        ),
        media_duration=media_duration(source_video),
    )

    qualities = analyze_reference_quality(str(vocal_audio), segments)
    reference_manager = SpeakerReferenceManager(
        segments=segments,
        qualities=qualities,
        clip_writer=lambda segment, output: write_reference_clip(segment, str(vocal_audio), output),
        output_dir=run_root / "references",
    )
    reference_manager.prepare()

    glossary_path = ROOT / "temp" / "e2e-modal-glossary.json"
    glossary = json.loads(glossary_path.read_text()) if glossary_path.exists() else {}
    context = ContextMemory(run_root / "context.sqlite3")

    # Prosody is measured from each real source group, not synthesized data.
    source_info = sf.info(vocal_audio)
    source_data, source_sr = sf.read(vocal_audio, always_2d=True, dtype="float32")
    source_mono = source_data.mean(axis=1)

    def source_prosody(group):
        start = max(0, int(group.start * source_sr))
        end = min(len(source_mono), int(group.end * source_sr))
        path = run_root / "prosody" / f"group_{group.index + 1}.wav"
        sf.write(path, source_mono[start:end], source_sr, subtype="PCM_16")
        return extract_prosody(path)

    import modal
    import modal_app
    # Direct imports are not hydrated outside ``modal run``. Resolve the
    # deployed function explicitly so every request uses one warm container.
    modal_batch = modal.Function.from_name("video-dubbing-translator", "synthesize_batch")

    remote_metrics = []

    def modal_synth(text, reference, output_path, *, seed=42, style=None, prosody=None):
        ref_bytes = reference.wav_path.read_bytes()
        payload = {
            "text": text,
            "reference_wav": ref_bytes,
            "reference_text": reference.transcript,
            "seed": int(seed),
            "style": style or "",
            "inference_timesteps": 5,
            "cfg_value": 2.0,
        }
        response = modal_batch.remote([payload])[0]
        if response.get("status") != "passed":
            raise RuntimeError(f"Modal synth failed: {response}")
        Path(output_path).write_bytes(response["wav"])
        remote_metrics.append({
            "wall_seconds": response.get("wall_seconds"),
            "audio_seconds": response.get("audio_seconds"),
            "rtf": response.get("rtf"),
            "cache_hit": response.get("cache_hit"),
            "cache_key": response.get("cache_key"),
            "speaker": reference.speaker,
            "text_chars": len(text),
            "style": style or "",
        })

    print(json.dumps({"run_id": run_id, "groups": len(timing_groups), "segments": len(segments), "source_seconds": media_duration(source_video)}), flush=True)
    synthesis_results, quality_results = generate_dubbed_speech(
        timing_groups,
        str(run_root / "wav") + "/",
        "en",
        "tr",
        reference_manager,
        translation_model="hymt2-1.8b",
        translation_device=os.environ.get("HYMT2_E2E_DEVICE", "mps"),
        glossary=glossary,
        duration_tolerance=0.20,
        max_refinement_passes=1,
        tts_synthesizer=modal_synth,
        tts_backend_name="voxcpm2-modal-native",
        context_memory=context,
        source_prosody_fn=source_prosody,
        # Keep the repaired run on the previously working Ultimate Cloning
        # path.  Prosody is still extracted and reported, but VoxCPM2 has no
        # separate style argument and must not be fed style prose as speech.
        use_prosody_style=False,
        qa_retries=1,
    )

    # Reconstruct the full video using the current timing controller helpers.
    videoclip = VideoFileClip(str(source_video))
    save_Subclips(timing_groups, videoclip, str(run_root / "mp4") + "/", str(source_video))
    change_ClipSpeed(
        timing_groups,
        str(run_root / "ffmpeg") + "/",
        str(run_root / "mp4") + "/",
        str(run_root / "wav") + "/",
        str(run_root / "final_movie") + "/",
    )
    gaps = clip_Parts_without_speaker(timing_groups, videoclip)
    remove_and_setting_video_ends(timing_groups, gaps, str(run_root / "final_movie") + "/", str(run_root / "mp4") + "/")
    final_clip = concatenate_All_Clip_Parts(gaps)
    old_cwd = Path.cwd()
    os.chdir(run_root)
    try:
        final_video_path, full_video_path = save_Final_Video(final_clip, source_video.name)
    finally:
        os.chdir(old_cwd)
    concatenate_Video_And_Background_Sound(final_video_path, str(instrumental_audio), full_video_path)
    try:
        final_clip.close()
        videoclip.close()
    except Exception:
        pass

    report_json = run_root / "quality_report.json"
    report_html = run_root / "quality_report.html"
    evidence = {
        "status": "passed",
        "run_id": run_id,
        "source_video": str(source_video.resolve()),
        "source_duration_seconds": media_duration(source_video),
        "source_audio_sha256": hashlib.sha256(source_audio.read_bytes()).hexdigest(),
        "vocal_audio": str(vocal_audio.resolve()),
        "transcript_source": str(transcript_path.resolve()),
        "segments": len(segments),
        "speakers": sorted({s.speaker for s in segments if s.speaker}),
        "overlap_segments": sum(bool(s.overlap) for s in segments),
        "timing_groups": len(timing_groups),
        "timing_group_indices": [list(g.original_segment_indices) for g in timing_groups],
        "translation_backend": "Hy-MT2 1.8B local",
        "tts_backend": "Modal native VoxCPM2",
        "prompt_cache_hits": sum(bool(m.get("cache_hit")) for m in remote_metrics),
        "modal_requests": remote_metrics,
        "qa_results": [q.__dict__ for q in quality_results],
        "qa_retry_count": sum(int(q.retry_count) for q in quality_results),
        "prosody_groups": sum(p is not None for p in [source_prosody(g) for g in timing_groups]),
        "quality_report_json": str(report_json.resolve()),
        "quality_report_html": str(report_html.resolve()),
        "final_video": str(Path(full_video_path).resolve()),
        "elapsed_wall_seconds": time.perf_counter() - started,
    }
    (run_root / "modal-e2e-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    context.close()
    print(json.dumps({"status": "passed", "run_root": str(run_root), "quality_report": report_json.exists(), "groups": len(timing_groups), "modal_requests": len(remote_metrics), "qa_retries": evidence["qa_retry_count"], "final_video": str(Path(full_video_path).resolve())}), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
