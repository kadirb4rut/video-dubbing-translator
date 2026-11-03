#!/usr/bin/env python3
"""One repaired full fixture pass executed entirely on a Colab G4 VM.

Community-1, Hy-MT2 1.8B, native VoxCPM2, QA, timing, reconstruction, and
quality reports all run on the remote Colab filesystem.  Nothing except the
small JSON/log evidence is written back to the development Mac by default.
"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import sys
import tempfile
import time
from pathlib import Path

import soundfile as sf
from moviepy.editor import VideoFileClip


ROOT = Path("/content/video-dubbing-translator-main")
sys.path.insert(0, str(ROOT))

from Video_Translator import (  # noqa: E402
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
    save_Final_Video,
    save_Subclips,
    speaker_aware_segments,
    write_reference_clip,
)
from dubbing.references import SpeakerReferenceManager  # noqa: E402
from dubbing.segments import from_legacy_lists  # noqa: E402
from dubbing.timing import TimingGroupConfig, build_timing_groups  # noqa: E402
from engine.context import ContextMemory  # noqa: E402
from engine.prosody import extract_prosody  # noqa: E402


def main() -> int:
    # Keep all model/runtime paths on Colab's disk and use the already selected
    # CUDA device for both Community-1 and Hy-MT2's isolated worker.
    os.environ["HYMT2_MODELS_DIR"] = "/content/hymt2-1.8b"
    os.environ["HYMT2_PYTHON"] = sys.executable
    os.environ["DIARIZATION_MODEL_DIR"] = str(ROOT / "third_party/diarization/community-1")
    os.environ["DIARIZATION_PYTHON"] = sys.executable
    os.environ["HF_HUB_DISABLE_XET"] = "1"
    # ``Video_Translator`` is imported before ``main`` by this script, so make
    # the already-imported model manager point at the remote Colab cache too.
    # Without this explicit assignment its module-level default still points
    # at the bundled (tokenizer-only) repository directory.
    from translation import model_manager

    model_manager.MODELS_DIR = Path("/content")

    from huggingface_hub import snapshot_download
    from voxcpm import VoxCPM
    import torch

    # Remote-only model caches.  The 1.8B model is the only translation model
    # fetched; no 7B/30B variants are touched.
    snapshot_download(
        repo_id="tencent/Hy-MT2-1.8B",
        local_dir="/content/hymt2-1.8b",
        allow_patterns=["*.safetensors", "*.json", "*.jinja", "*.txt"],
    )
    snapshot_download(
        repo_id="openbmb/VoxCPM2",
        local_dir="/content/voxcpm2",
        allow_patterns=["*.safetensors", "*.pth", "*.json", "*.yaml", "*.txt", "*.py"],
    )
    tts_model = VoxCPM.from_pretrained("/content/voxcpm2", load_denoiser=False, device="cuda")
    prompt_cache_dir = Path("/content/voxcpm-prompt-cache")
    prompt_cache_dir.mkdir(parents=True, exist_ok=True)
    prompt_cache: dict[str, Path] = {}
    remote_metrics: list[dict[str, object]] = []

    source_video = ROOT / "test_multispeaker.mp4"
    source_audio = ROOT / "original_wav" / "test_multispeaker.wav"
    vocal_audio = ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Vocals.wav"
    instrumental_audio = ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Instruments.wav"
    transcript_path = ROOT / "transcripts" / "test_multispeaker"
    run_id = f"colab-e2e-{int(time.time())}"
    run_root = ROOT / "temp" / run_id
    for name in ("wav", "mp4", "final_movie", "ffmpeg", "prosody", "references"):
        (run_root / name).mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()

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
    source_data, source_sr = sf.read(vocal_audio, always_2d=True, dtype="float32")
    source_mono = source_data.mean(axis=1)

    def source_prosody(group):
        start = max(0, int(group.start * source_sr))
        end = min(len(source_mono), int(group.end * source_sr))
        path = run_root / "prosody" / f"group_{group.index + 1}.wav"
        sf.write(path, source_mono[start:end], source_sr, subtype="PCM_16")
        return extract_prosody(path)

    def native_synth(text, reference, output_path, *, seed=42, style=None, prosody=None):
        reference_bytes = reference.wav_path.read_bytes()
        key = hashlib.sha256(reference_bytes + reference.transcript.encode()).hexdigest()
        cache_hit = key in prompt_cache
        ref_path = prompt_cache.get(key)
        if ref_path is None:
            ref_path = prompt_cache_dir / f"{key}.wav"
            ref_path.write_bytes(reference_bytes)
            prompt_cache[key] = ref_path
        torch.manual_seed(int(seed))
        started_call = time.perf_counter()
        kwargs = {"text": text, "normalize": False, "denoise": False}
        kwargs.update(
            reference_wav_path=str(ref_path),
            prompt_wav_path=str(ref_path),
            prompt_text=reference.transcript,
            inference_timesteps=5,
            cfg_value=2.0,
        )
        waveform = tts_model.generate(**kwargs)
        sf.write(str(output_path), waveform, int(tts_model.tts_model.sample_rate), subtype="PCM_16")
        info = sf.info(str(output_path))
        wall = time.perf_counter() - started_call
        remote_metrics.append({
            "backend": "voxcpm2-colab-native",
            "speaker": reference.speaker,
            "text_chars": len(text),
            "cache_hit": cache_hit,
            "cache_key": key,
            "wall_seconds": wall,
            "audio_seconds": float(info.frames / info.samplerate),
            "rtf": wall / max(float(info.frames / info.samplerate), 1e-9),
        })

    print(json.dumps({
        "run_id": run_id,
        "groups": len(timing_groups),
        "segments": len(segments),
        "source_seconds": media_duration(source_video),
        "gpu": torch.cuda.get_device_name(0),
    }), flush=True)
    synthesis_results, quality_results = generate_dubbed_speech(
        timing_groups,
        str(run_root / "wav") + "/",
        "en",
        "tr",
        reference_manager,
        translation_model="hymt2-1.8b",
        translation_device="cuda",
        glossary=glossary,
        duration_tolerance=0.20,
        max_refinement_passes=1,
        tts_synthesizer=native_synth,
        tts_backend_name="voxcpm2-colab-native",
        context_memory=context,
        source_prosody_fn=source_prosody,
        use_prosody_style=False,
        qa_retries=1,
    )

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
        "source_video": str(source_video),
        "source_duration_seconds": media_duration(source_video),
        "source_audio_sha256": hashlib.sha256(source_audio.read_bytes()).hexdigest(),
        "transcript_source": str(transcript_path),
        "segments": len(segments),
        "speakers": sorted({s.speaker for s in segments if s.speaker}),
        "overlap_segments": sum(bool(s.overlap) for s in segments),
        "timing_groups": len(timing_groups),
        "timing_group_indices": [list(g.original_segment_indices) for g in timing_groups],
        "translation_backend": "Hy-MT2 1.8B Colab G4",
        "tts_backend": "Colab native VoxCPM2",
        "prompt_cache_hits": sum(bool(m.get("cache_hit")) for m in remote_metrics),
        "tts_requests": remote_metrics,
        "qa_results": [q.__dict__ for q in quality_results],
        "qa_retry_count": sum(int(q.retry_count) for q in quality_results),
        "prosody_groups": len(timing_groups),
        "quality_report_json": str(report_json),
        "quality_report_html": str(report_html),
        "final_video": str(Path(full_video_path).resolve()),
        "elapsed_wall_seconds": time.perf_counter() - started,
    }
    (run_root / "colab-e2e-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2, default=str))
    context.close()
    print(json.dumps({
        "status": "passed",
        "run_root": str(run_root),
        "quality_report_json": report_json.exists(),
        "quality_report_html": report_html.exists(),
        "groups": len(timing_groups),
        "tts_requests": len(remote_metrics),
        "qa_retries": evidence["qa_retry_count"],
        "prompt_cache_hits": evidence["prompt_cache_hits"],
        "final_video": str(Path(full_video_path).resolve()),
    }), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
