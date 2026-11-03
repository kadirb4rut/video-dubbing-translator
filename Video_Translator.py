import pickle
import os
import sys
import argparse
import json
import shutil
import signal
import time
from dataclasses import asdict, replace
from pathlib import Path
from tqdm import tqdm
from fsorter import fsorter
import subprocess
from typing import Mapping, Optional, Sequence
from moviepy.editor import VideoFileClip, AudioFileClip, concatenate_videoclips
from diarization.client import DiarizationUnavailable, diarize
from diarization.assignment import validate_speaker_bounds
from dubbing.duration import duration_ratio, synthesize_with_duration_refinement
from dubbing.candidates import assess_timing_candidate, choose_best_candidate, protected_tokens, score_candidate
from dubbing.references import AudioQuality, SpeakerReference, SpeakerReferenceManager
from dubbing.segments import SpeechSegment, from_legacy_lists, surrounding_text
from dubbing.speech_length import SpeakerRateEstimator, estimate_spoken_length
from dubbing.timing import (
    TimingGroup,
    TimingGroupConfig,
    build_timing_groups,
    calculate_duration_budgets,
)
from translation.base import TranslationContext
from translation.hymt2 import HyMT2Translator, validate_device
from translation.registry import MODEL_BY_KEY
from voxcpm_runtime import (
    VOXCPM_OUTPUT_SAMPLE_RATE,
    VOXCPM_REFERENCE_SAMPLE_RATE,
    load_model,
    synthesize_cloned_speech,
)
from engine.qa import evaluate_tts
from engine.reports import QualityReport
from engine.prosody import extract_prosody, prosody_distance, style_instruction

BASE_DIR = Path(__file__).resolve().parent
VOCAL_REMOVER_DIR = BASE_DIR / "vocal-remover"
FFMPEG = shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
FFPROBE = shutil.which("ffprobe") or "/opt/homebrew/bin/ffprobe"
PYTHON = sys.executable
MIN_CLIP_DURATION = 0.04


def run_command(args, cwd=None):
    subprocess.run([str(arg) for arg in args], cwd=cwd, check=True)


def ensure_ffmpeg():
    if not FFMPEG or not Path(FFMPEG).exists():
        raise RuntimeError(
            "FFmpeg was not found. Install FFmpeg and try again."
        )
    return FFMPEG


def safe_float(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_media_end(clip, requested_end=None, start=0.0):
    durations = []
    if requested_end is not None:
        durations.append(safe_float(requested_end))
    if getattr(clip, "duration", None) is not None:
        durations.append(safe_float(clip.duration))
    if getattr(clip, "audio", None) is not None and getattr(clip.audio, "duration", None) is not None:
        durations.append(safe_float(clip.audio.duration))
    if not durations:
        return None

    # All candidate durations are already upper bounds. Trimming an additional
    # epsilon here shortens every dubbed clip and accumulates visible timeline
    # drift across a multi-segment video.
    end = min(durations)
    return end if end - start >= MIN_CLIP_DURATION else None

def mp4_to_wav(video_path):
    video_path = Path(video_path).expanduser().resolve()
    if not video_path.exists():
        raise FileNotFoundError(f"Video file not found: {video_path}")

    output_dir = BASE_DIR / "original_wav"
    output_dir.mkdir(exist_ok=True)
    video_wav_name = video_path.stem
    wav_path = output_dir / f"{video_wav_name}.wav"
    run_command([ensure_ffmpeg(), "-y", "-i", video_path, wav_path])

    return str(wav_path), str(video_path)


def seperate_Speaker_and_Background_Sound_from_audio(audio_path, file_path): #  1
    """
    STEP 1 :
        Seperate Speaker and Audio from original audio (wav, mp3, etc.)

    """
    
    model_path = VOCAL_REMOVER_DIR / "models" / "baseline.pth"
    if not model_path.exists():
        raise FileNotFoundError(
            f"Vocal-remover model is missing: {model_path}\n"
            "Run: python scripts/setup_models.py"
        )
    run_command(
        [PYTHON, "inference.py", "--input", audio_path, "--output_dir", file_path],
        cwd=VOCAL_REMOVER_DIR,
    )
    return True


def transcript_Text(vocal_audio_path, source_language): # 
    """
    STEP 2 :
        Extract transcript from seperated audio(return --> seperate_Speaker)
        
    """
    
    import torch
    import whisper
    import whisperx

    device = "cuda" if torch.cuda.is_available() else "cpu"
    # transcribe with original whisper
    model = whisper.load_model("base", device=device) # 1. parameter --> (tiny, base, small, medium, large) and 2. parameter is device
    audio = whisperx.load_audio(vocal_audio_path)
    
    if source_language != None:
        result = model.transcribe(audio, language=source_language, verbose=True) # batch_size=batch_size
    else:
        audio_for_detection = whisper.pad_or_trim(audio)
        mel = whisper.log_mel_spectrogram(audio_for_detection).to(model.device)
        _, probs = model.detect_language(mel)
        auto_lang = max(probs, key=probs.get)
        print(f"Detected language: {max(probs, key=probs.get)}")
        result = model.transcribe(audio, language=auto_lang, verbose=True)
        
    print(result["segments"]) # before alignment
    
    # load alignment model and metadata
    model_a, metadata = whisperx.load_align_model(language_code=result["language"], device=device)
    # align whisper output
    result_aligned = whisperx.align(result["segments"], model_a, metadata, audio, device, return_char_alignments=True) # return_char_alignments --> Character based TimeStamps!
    transcript = result_aligned["word_segments"]
    
    removable_index = list()
    for i in range(len(transcript)):
        if transcript[i].get('start') == None: # and result.get('segments')[i].get('start') != None
            removable_index.append(i)
    transcript_edit = transcript
    i = 0
    for i in range(len(removable_index)):        
        transcript_edit.pop(removable_index[i])
        if i == (len(removable_index) - 1): 
            break
        removable_index[i+1] -= 1

    return transcript


def save_Transcript(vocal_audio_path, transcript_path, source_language): #  2
    transcript = transcript_Text(vocal_audio_path, source_language)
    with open(transcript_path, "wb") as fp:   #Pickling
      pickle.dump(transcript, fp)
    

def read_Transcript(transcript_path):  #  3
    with open(transcript_path, "rb") as fp:   # Unpickling
        loaded_transcript = pickle.load(fp)
        
    return loaded_transcript


def create_TimeStamps(loaded_transcript, character_set): #  5 
    start = list()
    end = list()
    for i in tqdm(range(len(loaded_transcript))):
        if i == 0 and len(loaded_transcript) == 1:
            start.append(loaded_transcript[i].get('start'))
            end.append(loaded_transcript[i].get('end'))
        elif i == 0:
            start.append(loaded_transcript[i].get('start')) # round(loaded_transcript[i].get('start'), 4)
        elif i == len(loaded_transcript) - 1:
            end.append(loaded_transcript[i].get('end') )
        
        elif loaded_transcript[i].get('word')[-1] in character_set:
                end.append(loaded_transcript[i].get('end') )
                start.append(loaded_transcript[i+1].get('start')) # round(loaded_transcript[i+1].get('start'), 4)
    
    return start, end


def create_Sentences(loaded_transcript, character_set): #  6
    sentences = list()
    text = str()
    for i in tqdm(range(len(loaded_transcript))):
        text +=  loaded_transcript[i].get('word')
        if (text[-1] in character_set) != True:
            text += ' '
        else:
            sentences.append((text))
            text = ''
    if text.strip():
        sentences.append(text.strip())
    
    return sentences


def create_Speaker_Reference_Clips(start, end, vocal_audio_path):
    speaker_reference_dir = BASE_DIR / "speaker_reference_clips"
    speaker_reference_dir.mkdir(exist_ok=True)
    for i in range(len(start)):
        output_path = speaker_reference_dir / f"{i+1}.wav"
        duration = safe_float(end[i]) - safe_float(start[i])
        if duration < MIN_CLIP_DURATION:
            raise RuntimeError(f"Speaker reference segment {i + 1} is too short: {duration:.3f}s")
        run_command(
            [
                ensure_ffmpeg(),
                "-y",
                "-ss",
                start[i],
                "-t",
                duration,
                "-i",
                vocal_audio_path,
                "-vn",
                "-ac",
                "1",
                "-ar",
                VOXCPM_REFERENCE_SAMPLE_RATE,
                "-c:a",
                "pcm_s16le",
                output_path,
            ]
        )
    return str(speaker_reference_dir) + "/"


def write_reference_clip(segment, vocal_audio_path, output_path):
    duration = segment.duration
    if duration < MIN_CLIP_DURATION:
        raise RuntimeError(
            f"Speaker reference segment {segment.index + 1} is too short: {duration:.3f}s"
        )
    run_command(
        [
            ensure_ffmpeg(),
            "-y",
            "-ss",
            segment.start,
            "-t",
            duration,
            "-i",
            vocal_audio_path,
            "-vn",
            "-ac",
            "1",
            "-ar",
            VOXCPM_REFERENCE_SAMPLE_RATE,
            "-c:a",
            "pcm_s16le",
            output_path,
        ]
    )


def analyze_reference_quality(vocal_audio_path, segments):
    import numpy as np
    import soundfile as sf

    audio, sample_rate = sf.read(vocal_audio_path, always_2d=True, dtype="float32")
    mono = audio.mean(axis=1)
    qualities = {}
    for segment in segments:
        start_frame = max(0, int(segment.start * sample_rate))
        end_frame = min(len(mono), int(segment.end * sample_rate))
        samples = mono[start_frame:end_frame]
        if len(samples) == 0:
            qualities[segment.index] = AudioQuality(0.0, 0.0, 1.0)
            continue
        absolute = np.abs(samples)
        qualities[segment.index] = AudioQuality(
            rms=float(np.sqrt(np.mean(np.square(samples)))),
            peak=float(np.max(absolute)),
            clipped_fraction=float(np.mean(absolute >= 0.995)),
        )
    return qualities


def speaker_aware_segments(
    segments,
    vocal_audio_path,
    *,
    enabled=True,
    diarization_device="auto",
    num_speakers=None,
    min_speakers=None,
    max_speakers=None,
):
    if not enabled:
        print("Speaker diarization disabled; using per-segment voice references.", flush=True)
        return list(segments)
    try:
        result = diarize(
            Path(vocal_audio_path),
            device=diarization_device,
            num_speakers=num_speakers,
            min_speakers=min_speakers,
            max_speakers=max_speakers,
        )
    except DiarizationUnavailable as exc:
        print(
            f"Warning: speaker diarization unavailable ({exc}). "
            "Falling back to per-segment references.",
            flush=True,
        )
        return list(segments)
    from diarization.assignment import assign_speakers

    assigned = assign_speakers(segments, result)
    speakers = sorted({segment.speaker for segment in assigned if segment.speaker})
    overlaps = sum(segment.overlap for segment in assigned)
    print(
        f"Diarization assigned {len(speakers)} stable speaker(s); "
        f"{overlaps} transcript segment(s) overlap simultaneous speech.",
        flush=True,
    )
    return assigned


def load_glossary(path):
    if path is None:
        return None
    glossary_path = Path(path).expanduser().resolve()
    payload = json.loads(glossary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in payload.items()
    ):
        raise ValueError("Glossary must be a JSON object mapping source terms to target terms.")
    return payload


def generate_dubbed_speech(
    groups: Sequence[TimingGroup],
    path_wav,
    source_language,
    target_language,
    reference_manager: SpeakerReferenceManager,
    *,
    translation_model="hymt2-1.8b",
    translation_device="auto",
    glossary: Optional[Mapping[str, str]] = None,
    duration_tolerance=0.20,
    max_refinement_passes=1,
    min_tempo=0.85,
    max_tempo=1.25,
    ideal_min_tempo=0.90,
    ideal_max_tempo=1.10,
    tts_synthesizer=None,
    tts_backend_name="voxcpm2-native",
    context_memory=None,
    source_prosody_fn=None,
    use_prosody_style=False,
    qa_retries=1,
    tts_evaluator=None,
):
    if tts_synthesizer is None:
        print("Loading pinned local VoxCPM2 voice-cloning model...", flush=True)
    else:
        print(f"Using {tts_backend_name} speech generation...", flush=True)
    model = None if tts_synthesizer is not None else load_model()
    reference_manager.prepare()
    rate_estimator = SpeakerRateEstimator()
    if context_memory is None:
        from engine.context import ContextMemory

        context_memory = ContextMemory()
    synthesis_results = []
    quality_results = []
    prosody_profiles = []
    evaluate_audio = tts_evaluator or evaluate_tts
    with HyMT2Translator(translation_model, translation_device) as translator:
        for i, group in enumerate(tqdm(groups)):
            budget = group.budget
            if budget is None:
                budget = calculate_duration_budgets(groups)[i].budget
            assert budget is not None
            previous = groups[i - 1].text if i else None
            following = groups[i + 1].text if i + 1 < len(groups) else None
            protected = protected_tokens(group.text, glossary)
            remembered = context_memory.relevant(group.text, speaker_id=group.speaker)
            remembered_context = " | ".join(
                f"{item.source} → {item.target}" for item in remembered if item.target
            )
            if remembered_context:
                previous = f"{previous or ''} | memory: {remembered_context}"
            preferred_duration = budget.preferred_duration
            safe_available_duration = budget.safe_available_duration
            target_units = rate_estimator.rate_for(group.speaker) * preferred_duration
            context = TranslationContext(
                previous,
                following,
                group.speaker,
                available_duration_seconds=safe_available_duration,
                target_spoken_units=target_units,
                source_pause_seconds=sum(group.internal_pauses),
                protected_tokens=protected,
                preferred_duration_seconds=preferred_duration,
                safe_available_duration_seconds=safe_available_duration,
            )
            text = translator.translate(
                group.text,
                source_language,
                target_language,
                context=context,
                glossary=glossary,
            )
            candidates = [
                score_candidate(
                    group.text,
                    text,
                    target_language=target_language,
                    speaker_rate=rate_estimator.rate_for(group.speaker),
                    available_duration=safe_available_duration,
                    preferred_duration=preferred_duration,
                    glossary=glossary,
                )
            ]
            # Spend a second Hy-MT2 call only when the measured speech-length
            # estimate is clearly outside the safe natural window.
            if candidates[0].predicted_duration > preferred_duration * 1.15:
                compact_context = TranslationContext(
                    previous,
                    following,
                    group.speaker,
                    available_duration_seconds=safe_available_duration,
                    target_spoken_units=target_units,
                    source_pause_seconds=sum(group.internal_pauses),
                    candidate_style="compact",
                    protected_tokens=protected,
                    preferred_duration_seconds=preferred_duration,
                    safe_available_duration_seconds=safe_available_duration,
                )
                compact_text = translator.translate(
                    group.text,
                    source_language,
                    target_language,
                    context=compact_context,
                    glossary=glossary,
                )
                candidates.append(
                    score_candidate(
                        group.text,
                        compact_text,
                        target_language=target_language,
                        speaker_rate=rate_estimator.rate_for(group.speaker),
                        available_duration=safe_available_duration,
                        preferred_duration=preferred_duration,
                        glossary=glossary,
                    )
                )
            chosen = choose_best_candidate(candidates)
            reference = reference_manager.reference_for(group.as_reference_segment())
            wav_file = Path(path_wav) / f"{i + 1}.wav"
            source_prosody = source_prosody_fn(group) if source_prosody_fn else None
            style = style_instruction(source_prosody) if use_prosody_style and source_prosody else None
            prosody_profiles.append(source_prosody)

            def synthesize(text_to_speak, selected_reference, output_path, *, attempt=0):
                if tts_synthesizer is not None:
                    tts_synthesizer(
                        text_to_speak,
                        selected_reference,
                        output_path,
                        seed=42 + group.index + 1000 * attempt,
                        style=style,
                        prosody=source_prosody,
                    )
                else:
                    synthesize_cloned_speech(
                        model=model,
                        text=text_to_speak,
                        reference_wav_path=selected_reference.wav_path,
                        output_wav_path=output_path,
                        prompt_text=selected_reference.transcript,
                        seed=42 + group.index + 1000 * attempt,
                        style=style,
                        prosody=source_prosody,
                    )

            result = synthesize_with_duration_refinement(
                segment=group.as_reference_segment(),
                initial_translation=chosen.text,
                reference=reference,
                output_path=wav_file,
                translator=translator,
                source_language=source_language,
                target_language=target_language,
                synthesize=synthesize,
                measure_duration=media_duration,
                context=context,
                glossary=glossary,
                tolerance=duration_tolerance,
                max_refinement_passes=max_refinement_passes,
                preferred_duration_seconds=preferred_duration,
                safe_available_duration_seconds=safe_available_duration,
                target_spoken_units=chosen.estimate.spoken_units,
                min_tempo=min_tempo,
                max_tempo=max_tempo,
                ideal_min_tempo=ideal_min_tempo,
                ideal_max_tempo=ideal_max_tempo,
            )
            quality = evaluate_audio(
                result.output_path,
                result.translation,
                expected_duration=result.preferred_duration,
                reference_wav=reference.wav_path,
            )
            qa_candidates = [asdict(quality)]
            if quality.hard_failures and qa_retries:
                qa_retry_path = wav_file.with_name(wav_file.stem + ".qa_retry.wav")
                # A retry must use new sampling state; resetting the same
                # seed reproduced the exact failing audio in the old path.
                synthesize(result.translation, reference, qa_retry_path, attempt=1)
                retry_quality = evaluate_audio(
                    qa_retry_path,
                    result.translation,
                    expected_duration=result.preferred_duration,
                    reference_wav=reference.wav_path,
                    retry_count=1,
                )
                qa_candidates.append(asdict(retry_quality))
                # Keep the better valid candidate.  A failed retry never
                # replaces a usable initial/refined result.
                if retry_quality.passed and (not quality.passed or len(retry_quality.hard_failures) < len(quality.hard_failures)):
                    wav_file.write_bytes(qa_retry_path.read_bytes())
                    timing = assess_timing_candidate(
                        version="qa_retry", text=result.translation,
                        duration=media_duration(wav_file),
                        preferred_duration=preferred_duration,
                        safe_available_duration=safe_available_duration,
                        protected=protected, min_tempo=min_tempo, max_tempo=max_tempo,
                        ideal_min_tempo=ideal_min_tempo, ideal_max_tempo=ideal_max_tempo,
                    )
                    result = replace(
                        result, selected_version="qa_retry", final_duration=timing.duration,
                        final_tempo=timing.required_tempo, applied_tempo=timing.applied_tempo,
                        timing_limited=not timing.hard_band or not timing.safe_fit,
                        synchronization_error=timing.synchronization_error,
                        borrowed_silence=timing.borrowed_silence,
                    )
                    quality = retry_quality
                qa_retry_path.unlink(missing_ok=True)
            # Count attempted retries even when the first candidate wins, and
            # retain both measured candidates for a reviewable QA decision.
            quality = replace(quality, retry_count=len(qa_candidates) - 1, metadata={
                **quality.metadata, "qa_candidates": qa_candidates,
                "qa_selected": "retry" if result.selected_version == "qa_retry" else "initial",
            })
            quality_results.append(quality)
            rate_estimator.update(
                group.speaker,
                chosen.estimate,
                result.final_duration,
                available_seconds=preferred_duration,
                min_tempo=min_tempo,
                max_tempo=max_tempo,
            )
            try:
                context_memory.remember_translation(
                    group.text,
                    result.translation,
                    speaker_id=group.speaker,
                    group_index=i,
                )
                context_memory.remember_entities(group.text, speaker_id=group.speaker, group_index=i)
            except Exception as exc:
                print(f"Warning: context memory update failed: {type(exc).__name__}: {exc}", flush=True)
            synthesis_results.append(result)
            print(
                "Group {number}: segments={segments} speaker={speaker} model={model} "
                "preferred={preferred:.3f}s safe_available={available:.3f}s pauses={pauses:.3f}s "
                "src_words={src_words} src_chars={src_chars} tgt_words={tgt_words} "
                "tgt_chars={tgt_chars} target_syllables={units} target_phonemes={phonemes} "
                "predicted={predicted:.3f}s "
                "initial={initial:.3f}s refined_duration={refined_duration} "
                "rewritten={rewritten} selected={selected} "
                "final={final:.3f}s required_tempo={tempo:.3f}x applied_tempo={applied:.3f}x "
                "sync_error={sync:.3f} borrowed={borrowed:.3f}s direction={direction} "
                "timing_limited={limited} candidates={candidates} reference={reference}".format(
                    number=i + 1,
                    segments=','.join(str(n + 1) for n in group.original_segment_indices),
                    speaker=group.speaker or "SEGMENT_FALLBACK",
                    model=translation_model,
                    preferred=preferred_duration,
                    available=safe_available_duration,
                    pauses=sum(group.internal_pauses),
                    src_words=len(group.text.split()),
                    src_chars=len(group.text.replace(" ", "")),
                    tgt_words=len(chosen.text.split()),
                    tgt_chars=len(chosen.text.replace(" ", "")),
                    units=chosen.estimate.spoken_units,
                    phonemes=chosen.estimate.phonemes,
                    predicted=chosen.predicted_duration,
                    initial=result.initial_duration,
                    refined_duration=(
                        f"{result.refined_duration:.3f}s"
                        if result.refined_duration is not None
                        else "n/a"
                    ),
                    rewritten=result.refined,
                    selected=result.selected_version,
                    final=result.final_duration,
                    tempo=result.final_tempo,
                    applied=result.applied_tempo,
                    sync=result.synchronization_error,
                    borrowed=result.borrowed_silence,
                    direction=result.refinement_direction,
                    limited=result.timing_limited,
                    candidates=len(candidates),
                    reference=(
                        reference.wav_path.name
                        + (" (segment fallback)" if reference.fallback else "")
                    ),
                ),
                flush=True,
            )
    if synthesis_results:
        tempos = [result.final_tempo for result in synthesis_results]
        sorted_tempos = sorted(tempos)
        median = sorted_tempos[len(sorted_tempos) // 2]
        if len(sorted_tempos) % 2 == 0:
            median = (sorted_tempos[len(sorted_tempos) // 2 - 1] + median) / 2
        ideal = sum(ideal_min_tempo <= tempo <= ideal_max_tempo for tempo in tempos)
        hard = sum(min_tempo <= tempo <= max_tempo for tempo in tempos)
        hard_limits = len(tempos) - hard
        sync_errors = [result.synchronization_error for result in synthesis_results]
        sorted_errors = sorted(sync_errors)
        error_median = sorted_errors[len(sorted_errors) // 2]
        if len(sorted_errors) % 2 == 0:
            error_median = (sorted_errors[len(sorted_errors) // 2 - 1] + error_median) / 2
        print(
            "Timing summary: groups={groups} max_required_tempo={maximum:.3f}x "
            "min_required_tempo={minimum:.3f}x mean_required_tempo={mean:.3f}x "
            "median_required_tempo={median:.3f}x ideal_band={ideal}/{groups} "
            "hard_band={hard}/{groups} timing_limited={limited} "
            "mean_sync_error={error_mean:.3f} median_sync_error={error_median:.3f}.".format(
                groups=len(synthesis_results),
                maximum=max(tempos),
                minimum=min(tempos),
                mean=sum(tempos) / len(tempos),
                median=median,
                ideal=ideal,
                hard=hard,
                limited=hard_limits,
                error_mean=sum(sync_errors) / len(sync_errors),
                error_median=error_median,
            ),
            flush=True,
        )

    # Release artifact: every synthesis run gets a versioned quality report.
    # Signal/timing/speaker checks are always local and cheap.  Recovered-text
    # WER/CER is populated by deployments that enable an ASR evaluator; we do
    # not claim a transcription score when no ASR pass was requested.
    try:
        report = QualityReport(
            run_id=Path(path_wav).resolve().parent.name,
            source=source_language,
            target=target_language,
            metadata={
                "pipeline": "Video_Translator.generate_dubbed_speech",
                "qa_policy": "hard failures retry once; soft warnings retained",
                "translation_model": translation_model,
                "tts_model": "VoxCPM2",
                "tts_backend": tts_backend_name,
                "prosody_style": bool(use_prosody_style),
            },
        )
        for index, (group, result) in enumerate(zip(groups, synthesis_results), 1):
            reference = reference_manager.reference_for(group.as_reference_segment())
            quality = quality_results[index - 1] if index - 1 < len(quality_results) else evaluate_tts(
                result.output_path,
                result.translation,
                expected_duration=result.preferred_duration,
                reference_wav=reference.wav_path,
            )
            distance = None
            if index - 1 < len(prosody_profiles) and prosody_profiles[index - 1] is not None:
                try:
                    distance = prosody_distance(prosody_profiles[index - 1], extract_prosody(result.output_path))
                except Exception:
                    distance = None
            report.add_group(
                index,
                quality=quality,
                translation_backend=translation_model,
                tts_backend=tts_backend_name,
                prosody_distance=distance,
                extra={
                    "original_group_index": group.index + 1,
                    "speaker": group.speaker,
                    "source_start_seconds": group.start,
                    "source_end_seconds": group.end,
                    "source_duration_seconds": group.duration,
                    "preferred_duration_seconds": result.preferred_duration,
                    "safe_available_duration_seconds": result.safe_available_duration,
                    "initial_duration_seconds": result.initial_duration,
                    "final_duration_seconds": result.final_duration,
                    "final_tempo": result.final_tempo,
                    "applied_tempo": result.applied_tempo,
                    "synchronization_error": result.synchronization_error,
                    "selected_version": result.selected_version,
                    "timing_limited": result.timing_limited,
                    "refinement_direction": result.refinement_direction,
                    "qa_retry_count": quality.retry_count,
                    "qa_hard_failures": list(quality.hard_failures),
                    "qa_warnings": list(quality.warnings),
                },
            )
        report.finish()
        report_paths = report.write(Path(path_wav).resolve().parent)
        print(
            "Quality report: "
            + ", ".join(f"{kind}={path}" for kind, path in report_paths.items()),
            flush=True,
        )
    except Exception as exc:
        # QA must never delete or invalidate a successful audio artifact; the
        # exception is explicit in logs so release checks can investigate it.
        print(f"Warning: quality report generation failed: {type(exc).__name__}: {exc}", flush=True)
    return synthesis_results, quality_results


def save_Subclips(groups, videoclip, path_mp4, video_path_concat): #  10
    for i, group in enumerate(tqdm(groups)):
        budget = group.budget
        render_end = budget.render_end if budget is not None else group.end
        run_command([
            ensure_ffmpeg(), "-y", "-i", video_path_concat,
            "-ss", group.start, "-to", render_end,
            "-c:v", "libx264", "-c:a", "aac", f"{path_mp4}{i+1}.mp4",
        ])
        

def media_duration(path):
    if not FFPROBE or not Path(FFPROBE).exists():
        raise RuntimeError("ffprobe was not found. Install FFmpeg and try again.")
    probe = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    return float(json.loads(probe.stdout)["format"]["duration"])


def atempo_filter(tempo, *, min_tempo=0.85, max_tempo=1.25):
    if tempo <= 0:
        raise ValueError(f"Audio tempo must be positive, got {tempo}.")
    if min_tempo <= 0 or max_tempo < min_tempo:
        raise ValueError("Invalid tempo limits.")
    bounded = min(max(tempo, min_tempo), max_tempo)
    return f"atempo={bounded:.8f}"


def change_ClipSpeed(
    groups,
    path_ffmpeg,
    path_mp4,
    path_wav,
    final_movie,
    *,
    min_tempo=0.85,
    max_tempo=1.25,
): #  11
    sort_wav = fsorter.fileSort(path_wav, ['.wav'])    
    sort_mp4 = fsorter.fileSort(path_mp4, ['.mp4'])
    for i, group in enumerate(tqdm(groups)):
        video_path = Path(path_mp4) / sort_mp4[i]
        audio_path = Path(path_wav) / sort_wav[i]
        video_duration = media_duration(video_path)
        audio_duration = media_duration(audio_path)
        adjusted_audio_path = Path(path_ffmpeg) / sort_wav[i]
        budget = group.budget
        preferred_duration = budget.preferred_duration if budget is not None else group.duration
        tempo = audio_duration / preferred_duration
        bounded_tempo = min(max(tempo, min_tempo), max_tempo)
        if abs(tempo - bounded_tempo) > 1e-6:
            print(
                f"Timing limitation group {i + 1}: required tempo vs preferred="
                f"{tempo:.3f}x; "
                f"bounded to {bounded_tempo:.3f}x ({min_tempo:.2f}–{max_tempo:.2f}).",
                flush=True,
            )
        run_command(
            [
                ensure_ffmpeg(), "-y", "-i", audio_path,
                "-filter:a", atempo_filter(
                    tempo, min_tempo=min_tempo, max_tempo=max_tempo
                ),
                "-ar", VOXCPM_OUTPUT_SAMPLE_RATE,
                "-ac", "1",
                "-c:a", "pcm_s24le",
                adjusted_audio_path,
            ]
        )
        output_path = Path(final_movie) / sort_mp4[i]
        run_command(
            [
                ensure_ffmpeg(), "-y", "-i", video_path, "-i", adjusted_audio_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-ar", VOXCPM_OUTPUT_SAMPLE_RATE, "-ac", "1",
                "-af", "apad", "-t", f"{video_duration:.6f}",
                output_path,
            ]
        )
            
def clip_Parts_without_speaker(groups, videoclip): #  12
    videoclip = videoclip.without_audio()
    video_bosluklari = list()

    def make_gap(gap_start, gap_end):
        gap_start = max(0.0, safe_float(gap_start))
        gap_end = min(safe_float(gap_end), safe_float(videoclip.duration))
        if gap_end - gap_start >= MIN_CLIP_DURATION:
            return videoclip.subclip(gap_start, gap_end)
        return None

    if not groups:
        return [videoclip]

    first_start = groups[0].start
    video_bosluklari.append(make_gap(0.0, first_start))
    for i in tqdm(range(1, len(groups))):
        previous_end = groups[i - 1].budget.render_end if groups[i - 1].budget else groups[i - 1].end
        video_bosluklari.append(make_gap(previous_end, groups[i].start))
    last_end = groups[-1].budget.render_end if groups[-1].budget else groups[-1].end
    video_bosluklari.append(make_gap(last_end, videoclip.duration))
    
    return video_bosluklari


def timestamps_Without_Speaker_Parts(groups, path_mp4, sort_mp4): #  13
    videoclip_durations = list()
    for i in range(len(groups)):
        videoclip = VideoFileClip(path_mp4 + sort_mp4[i])
        videoclip_durations.append(videoclip.duration) 
        videoclip.close()
        
    return videoclip_durations 
        

def remove_and_setting_video_ends(groups, video_bosluklari, final_movie, path_mp4): #  14
    sort_mp4 = fsorter.fileSort(path_mp4, ['.mp4'])
    videoclip_durations = timestamps_Without_Speaker_Parts(groups, path_mp4, sort_mp4)
    list_extend = list()
    for i in range(len(groups)):
        clip = VideoFileClip(final_movie + sort_mp4[i])
        clip_end = safe_media_end(clip, requested_end=videoclip_durations[i])
        if clip_end is None:
            clip.close()
            print(f"Warning: {final_movie + sort_mp4[i]} cok kisa oldugu icin atlandi.")
            continue
        list_extend.append(clip.subclip(0.0, clip_end))
        
    combined_clips = []
    for i in tqdm(range(len(groups))):
        if i < len(video_bosluklari) and video_bosluklari[i] is not None:
            combined_clips.append(video_bosluklari[i])
        if i < len(list_extend):
            combined_clips.append(list_extend[i])
    # Group-based reconstruction has no legacy ``start`` timestamp list;
    # ``video_bosluklari`` is explicitly built as one leading/between gap per
    # group plus an optional trailing gap.
    if len(video_bosluklari) > len(groups) and video_bosluklari[-1] is not None:
        combined_clips.append(video_bosluklari[-1])

    video_bosluklari[:] = combined_clips


def concatenate_All_Clip_Parts(video_bosluklari): #  15
    final = concatenate_videoclips(video_bosluklari)
    return final


def save_Final_Video(final, final_video_name): #  16
    final_video_dir = Path(os.getcwd()) / "final_video"
    final_video_full_dir = final_video_dir / "final"
    final_video_dir.mkdir(exist_ok=True)
    final_video_full_dir.mkdir(exist_ok=True)
    final_video_path = str(final_video_dir / final_video_name)
    final__full_video_path = str(final_video_full_dir / final_video_name)
    final.write_videofile(
        final_video_path,
        codec="libx264",
        audio_codec="aac",
        audio_fps=VOXCPM_OUTPUT_SAMPLE_RATE,
        audio_bitrate="192k",
    )
    
    return final_video_path, final__full_video_path


def concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path): #  18
    # loudnorm outputs 192 kHz internally. On Colab FFmpeg 6.1, feeding that
    # directly to amix(duration=first) dropped ~2.9 s of real tail audio.
    # Resample before mixing, then use the video as the timeline authority.
    output_duration = media_duration(final_video_path)
    run_command([
        ensure_ffmpeg(), "-y", "-i", final_video_path, "-i", instrumental_audio_path,
        "-c:v", "copy",
        "-filter_complex",
        "[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,"
        "loudnorm=I=-20:TP=-2:LRA=11,aresample=48000[dub];"
        "[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo,volume=0.35[bg];"
        "[dub][bg]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,apad[a]",
        "-map", "0:v:0", "-map", "[a]",
        "-c:a", "aac", "-b:a", "192k", "-ar", VOXCPM_OUTPUT_SAMPLE_RATE,
        "-ac", "2", "-t", f"{output_duration:.6f}", final__full_video_path,
    ])

SOURCE_LANGUAGES = {
    'Auto Detect': 'automatic',
    'English': 'en',
    'Italian': 'it',
    'Spanish': 'es',
    'French': 'fr',
    'German': 'de',
    'Portuguese': 'pt',
    'Japanese': 'ja',
    'Russian': 'ru',
    'Turkish': 'tr',
    'Dutch': 'nl',
    'Polish': 'pl',
    'Czech (source only)': 'cs',
}

TARGET_LANGUAGES = {
    name: code
    for name, code in SOURCE_LANGUAGES.items()
    if code not in {"automatic", "cs"}
}


def normalize_language(value, allow_auto=False):
    if value is None:
        return None
    value = value.strip()
    if allow_auto and value.lower() in {"auto", "automatic", "auto detect"}:
        return None
    languages = SOURCE_LANGUAGES if allow_auto else TARGET_LANGUAGES
    for name, code in languages.items():
        if value.lower() == name.lower() or value.lower() == code.lower():
            return None if code == "automatic" else code
    valid = ", ".join(f"{name}={code}" for name, code in languages.items())
    raise ValueError(f"Invalid language: {value}. Valid values: {valid}")


def ask_language(prompt, allow_auto=False):
    languages = SOURCE_LANGUAGES if allow_auto else TARGET_LANGUAGES
    [print(element, ' ----> ', number + 1) for number, element in enumerate(list(languages.keys()))]
    selected_index = int(input(prompt))
    selected_code = list(languages.values())[selected_index - 1]
    return None if selected_code == "automatic" else selected_code


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Fully local speaker-aware video dubbing pipeline")
    parser.add_argument("video", nargs="?", help="Path to the input video")
    parser.add_argument("--source-language", "-s", help="Source language code or name, for example: en, tr, auto")
    parser.add_argument("--target-language", "-t", help="Target language code or name, for example: en, tr")
    parser.add_argument(
        "--speaker-wav-dir",
        help="Directory containing 1.wav, 2.wav, etc. for VoxCPM2 speaker references. Generated from the source vocals when omitted.",
    )
    parser.add_argument(
        "--no-diarization",
        action="store_false",
        dest="diarization",
        help="Disable speaker diarization and use one source reference per segment.",
    )
    parser.set_defaults(diarization=True)
    parser.add_argument("--num-speakers", type=int, help="Exact known speaker count.")
    parser.add_argument("--min-speakers", type=int, help="Minimum automatic speaker count.")
    parser.add_argument("--max-speakers", type=int, help="Maximum automatic speaker count.")
    parser.add_argument(
        "--diarization-device", default="auto", help="auto, cpu, cuda, or cuda:N"
    )
    parser.add_argument(
        "--translation-model",
        choices=tuple(MODEL_BY_KEY),
        default="hymt2-1.8b",
        help="Local Hy-MT2 model variant.",
    )
    parser.add_argument(
        "--translation-device",
        default="auto",
        help="Hy-MT2 device: auto, cpu, mps, cuda, or cuda:N.",
    )
    parser.add_argument(
        "--tts-backend",
        choices=("native", "modal"),
        default="native",
        help="Speech-generation location. Modal offloads VoxCPM2 speech only; other stages stay local.",
    )
    parser.add_argument("--glossary", help="Optional JSON term mapping.")
    parser.add_argument(
        "--duration-tolerance",
        type=float,
        default=0.20,
        help="Legacy diagnostic tolerance; refinement uses the ideal tempo band (default: 0.20).",
    )
    parser.add_argument(
        "--max-refinement-passes",
        type=int,
        default=1,
        help="Maximum Hy-MT2 duration rewrite passes per segment (default: 1).",
    )
    parser.add_argument(
        "--timing-max-gap",
        type=float,
        default=0.75,
        help="Maximum same-speaker gap eligible for timing-group merging (seconds).",
    )
    parser.add_argument(
        "--timing-micro-seconds",
        type=float,
        default=0.85,
        help="Segments at or below this duration receive micro-segment merge preference.",
    )
    parser.add_argument(
        "--timing-max-group-seconds",
        type=float,
        default=14.0,
        help="Maximum duration of a merged timing group.",
    )
    parser.add_argument(
        "--min-tempo",
        type=float,
        default=0.85,
        help="Minimum final FFmpeg tempo correction factor.",
    )
    parser.add_argument(
        "--max-tempo",
        type=float,
        default=1.25,
        help="Maximum final FFmpeg tempo correction factor.",
    )
    parser.add_argument(
        "--ideal-min-tempo",
        type=float,
        default=0.90,
        help="Lower bound of the ideal tempo band used for refinement (default: 0.90).",
    )
    parser.add_argument(
        "--ideal-max-tempo",
        type=float,
        default=1.10,
        help="Upper bound of the ideal tempo band used for refinement (default: 1.10).",
    )
    parser.add_argument("--list-languages", action="store_true", help="Print supported language codes")
    args = parser.parse_args(argv)
    try:
        validate_speaker_bounds(args.num_speakers, args.min_speakers, args.max_speakers)
        validate_device(args.translation_device)
        if args.duration_tolerance < 0:
            raise ValueError("duration-tolerance cannot be negative.")
        if args.max_refinement_passes < 0:
            raise ValueError("max-refinement-passes cannot be negative.")
        if (
            args.min_tempo <= 0
            or args.max_tempo < args.min_tempo
            or args.ideal_min_tempo <= 0
            or args.ideal_max_tempo < args.ideal_min_tempo
        ):
            raise ValueError("Tempo bands are invalid.")
        TimingGroupConfig(
            max_gap_seconds=args.timing_max_gap,
            micro_segment_seconds=args.timing_micro_seconds,
            max_group_duration_seconds=args.timing_max_group_seconds,
        )
        if args.min_tempo <= 0 or args.max_tempo < args.min_tempo:
            raise ValueError("Tempo limits must be positive and ordered.")
    except ValueError as exc:
        parser.error(str(exc))
    return args


class ExistingSegmentReferenceManager:
    """Compatibility adapter for the legacy --speaker-wav-dir option."""

    def __init__(self, directory):
        self.directory = Path(directory).expanduser().resolve()

    def prepare(self):
        return None

    def reference_for(self, segment):
        path = self.directory / f"{segment.index + 1}.wav"
        if not path.is_file():
            raise FileNotFoundError(f"Speaker reference audio was not found: {path}")
        return SpeakerReference(
            speaker=segment.speaker,
            wav_path=path,
            transcript=segment.text.strip(),
            source_segment_index=segment.index,
            fallback=True,
        )


def main():
    
    # 1 ---------------------------------------
    
    args = parse_args()
    if args.list_languages:
        print("VoxCPM2 target languages:")
        for name, code in TARGET_LANGUAGES.items():
            print(f"{name}: {code}")
        print("Source-only transcription languages:")
        print("Czech: cs")
        return

    modal_synthesizer = None
    modal_previous_sigterm = None
    if args.tts_backend == "modal":
        from compute_backends.modal_tts import create_modal_synthesizer

        modal_synthesizer = create_modal_synthesizer()
        modal_previous_sigterm = signal.getsignal(signal.SIGTERM)

        def release_modal_on_sigterm(signum, _frame):
            close_remote_compute = getattr(modal_synthesizer, "close", None)
            if callable(close_remote_compute):
                close_remote_compute()
            raise SystemExit(128 + signum)

        signal.signal(signal.SIGTERM, release_modal_on_sigterm)

    print('\n\n'+' '*30 + '* VIDEO TRANSLATOR *\n\n')

    video_path = Path(args.video or input("\nVideo file path: ").strip()).expanduser().resolve()
    source_language = normalize_language(args.source_language, allow_auto=True) if args.source_language else ask_language('\nPlease select the original video language number : ', allow_auto=True)
    target_language = normalize_language(args.target_language) if args.target_language else ask_language('\nPlease select the target video language number : ')
    
    audio_path, video_path_concat = mp4_to_wav(video_path)
    videoclip = VideoFileClip(video_path_concat)
    os.chdir(VOCAL_REMOVER_DIR)
    if ('vocals' in os.listdir()) != True:
        os.mkdir('vocals')
    file_path = 'vocals/'
    
    try:
        if (os.path.basename(audio_path).split('.')[0] + '_Vocals.wav' in os.listdir('vocals/')) != True:
            seperate_Speaker_and_Background_Sound_from_audio(audio_path, file_path)
        os.chdir(BASE_DIR)
    except Exception as exc:
        os.chdir(BASE_DIR)
        raise RuntimeError('Vocal separating is not working!') from exc
     
    # 2 ----------------------------------------
    
    if ('transcripts' in os.listdir()) != True:
        os.mkdir('transcripts')
    transcript_path = os.path.splitext(audio_path)[0].split('/')[-1]
    transcript_path = str(os.getcwd() + '/transcripts/' + transcript_path)
    
    os.chdir(VOCAL_REMOVER_DIR)
    vocal_audio_path = os.getcwd() + "/" + file_path + os.path.splitext(audio_path)[0].split('/')[-1] +'_Vocals.wav'
    instrumental_audio_path = os.getcwd() + "/" + file_path + os.path.splitext(audio_path)[0].split('/')[-1] +'_Instruments.wav'
    
    split_transcript_name = transcript_path.split('/')[-1]
    split_transcript_folder = transcript_path.split('/')
    split_transcript_folder.pop(-1)
    split_transcript_folder = '/'.join(split_transcript_folder) + str('/')
    if (split_transcript_name in os.listdir(split_transcript_folder)) == False:
        save_Transcript(vocal_audio_path, transcript_path, source_language)
    
    # 3 -----------------------------------------
    
    loaded_transcript = read_Transcript(transcript_path)
    
    # 4 -----------------------------------------
    character_set = ['.', '!', '?']
    start, end = create_TimeStamps(loaded_transcript, character_set)
    
    # 5 -----------------------------------------
    
    sentences = create_Sentences(loaded_transcript, character_set)
    segments = from_legacy_lists(sentences, start, end)

    segments = speaker_aware_segments(
        segments,
        vocal_audio_path,
        enabled=args.diarization,
        diarization_device=args.diarization_device,
        num_speakers=args.num_speakers,
        min_speakers=args.min_speakers,
        max_speakers=args.max_speakers,
    )
    timing_config = TimingGroupConfig(
        max_gap_seconds=args.timing_max_gap,
        micro_segment_seconds=args.timing_micro_seconds,
        max_group_duration_seconds=args.timing_max_group_seconds,
    )
    timing_groups = calculate_duration_budgets(
        build_timing_groups(segments, config=timing_config),
        media_duration=float(videoclip.duration),
    )
    merged = [group.original_segment_indices for group in timing_groups if len(group.segments) > 1]
    print(
        f"Timing groups created: {len(timing_groups)} from {len(segments)} source segments; "
        f"merged={[[index + 1 for index in indices] for indices in merged]}",
        flush=True,
    )
    
    # 6 -----------------------------------------
    
    if args.speaker_wav_dir is not None:
        reference_manager = ExistingSegmentReferenceManager(args.speaker_wav_dir)
    else:
        qualities = analyze_reference_quality(vocal_audio_path, segments)
        reference_manager = SpeakerReferenceManager(
            segments=segments,
            qualities=qualities,
            clip_writer=lambda segment, output: write_reference_clip(
                segment, vocal_audio_path, output
            ),
            output_dir=BASE_DIR / "speaker_reference_clips",
        )
    # 7 -----------------------------------------
    
    # Use a run-scoped workspace so merged groups cannot be mixed with stale
    # per-segment artifacts from an earlier invocation.
    run_root = BASE_DIR / "temp" / "timing-runs" / f"{video_path.stem}-{int(time.time())}"
    for name in ("wav", "mp4", "final_movie", "ffmpeg"):
        (run_root / name).mkdir(parents=True, exist_ok=True)
    path_wav = str(run_root / "wav") + "/"
    path_mp4 = str(run_root / "mp4") + "/"
    final_movie = str(run_root / "final_movie") + "/"
    path_ffmpeg = str(run_root / "ffmpeg") + "/"
    
    glossary = load_glossary(args.glossary)
    try:
        generate_dubbed_speech(
            timing_groups,
            path_wav,
            source_language,
            target_language,
            reference_manager,
            translation_model=args.translation_model,
            translation_device=args.translation_device,
            glossary=glossary,
            duration_tolerance=args.duration_tolerance,
            max_refinement_passes=args.max_refinement_passes,
            min_tempo=args.min_tempo,
            max_tempo=args.max_tempo,
            ideal_min_tempo=args.ideal_min_tempo,
            ideal_max_tempo=args.ideal_max_tempo,
            tts_synthesizer=modal_synthesizer,
            tts_backend_name="voxcpm2-modal" if modal_synthesizer is not None else "voxcpm2-native",
        )
    finally:
        # Modal is optional and exposes this cleanup hook only for the remote
        # TTS adapter. Local TTS and the dubbing pipeline are unchanged.
        close_remote_compute = getattr(modal_synthesizer, "close", None)
        try:
            if callable(close_remote_compute):
                close_remote_compute()
        finally:
            if modal_previous_sigterm is not None:
                signal.signal(signal.SIGTERM, modal_previous_sigterm)
    
    save_Subclips(timing_groups, videoclip, path_mp4, video_path_concat)
    
    # 10 -----------------------------------------
    
    change_ClipSpeed(
        timing_groups,
        path_ffmpeg,
        path_mp4,
        path_wav,
        final_movie,
        min_tempo=args.min_tempo,
        max_tempo=args.max_tempo,
    )
    
    # 11 -----------------------------------------
    
    video_bosluklari = clip_Parts_without_speaker(timing_groups, videoclip)
    
    # 12 -----------------------------------------
    
    remove_and_setting_video_ends(timing_groups, video_bosluklari, final_movie, path_mp4)
    
    # 13 -----------------------------------------
    
    final = concatenate_All_Clip_Parts(video_bosluklari)
    
    # 14 -----------------------------------------
    final_video_name = os.path.basename(video_path_concat)
    final_video_path, final__full_video_path = save_Final_Video(final, final_video_name)
    
    # 15 -----------------------------------------
    
    concatenate_Video_And_Background_Sound(final_video_path, instrumental_audio_path, final__full_video_path)
    

if __name__ == '__main__':
    main()
