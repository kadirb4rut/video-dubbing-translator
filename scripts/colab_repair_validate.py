"""Short acceptance gate and, only after it passes, repaired full Colab pass.

Run with REG_MODE=gate|full. All models, intermediate audio, and reconstruction
stay on Colab. Each actual TTS payload and every translation request is logged.
"""
from __future__ import annotations

import builtins
from dataclasses import asdict, replace
import hashlib
import importlib
import json
import os
from pathlib import Path
import pickle
import sys
import time
import uuid

ROOT = Path('/content/video-dubbing-translator-main')
sys.path.insert(0, str(ROOT))
os.environ.update(HYMT2_MODELS_DIR='/content', HYMT2_PYTHON=sys.executable,
                  HYMT2_SEED='42', DIARIZATION_PYTHON=sys.executable,
                  DIARIZATION_MODEL_DIR=str(ROOT / 'third_party/diarization/community-1'))


def main():
    import numpy as np
    import soundfile as sf
    import regression_tools as D
    import Video_Translator as V
    import translation.model_manager as MM
    import translation.prompts as prompts
    import translation.hymt2 as hymt
    import voxcpm_runtime as runtime
    import engine.reports as reports
    importlib.reload(prompts)
    importlib.reload(hymt)
    importlib.reload(runtime)
    importlib.reload(reports)
    importlib.reload(V)
    MM.MODELS_DIR = Path('/content')
    from dubbing.references import SpeakerReferenceManager
    from dubbing.segments import from_legacy_lists
    from dubbing.timing import TimingGroupConfig, build_timing_groups, calculate_duration_budgets
    from engine.context import ContextMemory
    from engine.prosody import extract_prosody
    from engine.qa import evaluate_tts
    from huggingface_hub import snapshot_download

    tts, recognizer = D.models()
    snapshot_download('tencent/Hy-MT2-1.8B', local_dir='/content/hymt2-1.8b', allow_patterns=['*.safetensors', '*.json', '*.jinja'])
    mode = os.environ.get('REG_MODE', 'gate')
    if mode == 'full':
        gate = json.loads(Path('/content/regression-evidence/acceptance-gate.json').read_text())
        if not gate['passed']:
            raise RuntimeError('Short acceptance gate has not passed')
    run_id = f'colab-repaired-{mode}-{int(time.time())}'
    run = ROOT / 'temp' / run_id
    for name in ('wav', 'references', 'prosody', 'mp4', 'ffmpeg', 'final_movie', 'candidates'):
        (run / name).mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    vocal = ROOT / 'vocal-remover/vocals/test_multispeaker_Vocals.wav'
    source = ROOT / 'test_multispeaker.mp4'

    if not hasattr(builtins, '_reg_fixture'):
        transcript = pickle.loads((ROOT / 'transcripts/test_multispeaker').read_bytes())
        starts, ends = V.create_TimeStamps(transcript, ['.', '!', '?'])
        texts = V.create_Sentences(transcript, ['.', '!', '?'])
        segments = from_legacy_lists(texts, starts, ends)
        segments = V.speaker_aware_segments(segments, str(vocal), enabled=True, diarization_device='cuda', num_speakers=2)
        groups = calculate_duration_budgets(build_timing_groups(segments, config=TimingGroupConfig()), V.media_duration(source))
        builtins._reg_fixture = (segments, groups)
    segments, all_groups = builtins._reg_fixture
    gate_indices = [int(item)-1 for item in os.environ.get('REG_GROUPS', '1,2,8,9').split(',')]
    groups = [all_groups[i] for i in gate_indices] if mode == 'gate' else all_groups
    manager = SpeakerReferenceManager(segments, V.analyze_reference_quality(str(vocal), segments),
        lambda seg, path: V.write_reference_clip(seg, str(vocal), path), run / 'references')
    manager.prepare()
    glossary = json.loads((ROOT / 'temp/e2e-modal-glossary.json').read_text())
    memory = ContextMemory(run / 'context.sqlite3')
    calls, translations = [], []
    ref_cache = {}
    source_audio, sr = sf.read(vocal, always_2d=True, dtype='float32')
    source_audio = source_audio.mean(axis=1)

    class TracedTranslator(hymt.HyMT2Translator):
        def _request(self, operation, prompt):
            text = super()._request(operation, prompt)
            translations.append({**self.last_generation, 'output': text})
            (run / 'translation-requests.json').write_text(json.dumps(translations, ensure_ascii=False, indent=2))
            if self.last_generation.get('stop_reason') == 'length':
                raise RuntimeError('Translation reached its duration-aware generation ceiling without EOS')
            return text

    V.HyMT2Translator = TracedTranslator

    def synthesize(text, reference, output, *, seed=42, **kwargs):
        rid = uuid.uuid4().hex
        ref_hash = hashlib.sha256(reference.wav_path.read_bytes()).hexdigest()
        key = ref_hash + hashlib.sha256(reference.transcript.encode()).hexdigest()
        hit = key in ref_cache
        ref_cache[key] = reference.wav_path
        tick = time.perf_counter()
        runtime.synthesize_cloned_speech(tts, text, reference.wav_path, output, prompt_text=reference.transcript, seed=seed)
        info = sf.info(output)
        copy = run / 'candidates' / f'{rid}.wav'
        copy.write_bytes(Path(output).read_bytes())
        calls.append({'request_id': rid, 'target_text': text, 'prompt_text': runtime.continuation_prompt(text, reference.transcript),
            'conditioning': 'ultimate' if runtime.continuation_prompt(text, reference.transcript) else 'reference_only',
            'speaker': reference.speaker, 'reference_sha256': ref_hash, 'reference_file_cache_hit': hit,
            'output': str(output), 'preserved_candidate': str(copy), 'seed': seed, 'inference_timesteps': 10,
            'wall_seconds': time.perf_counter()-tick, 'audio_seconds': info.duration,
            'sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest()})
        (run / 'tts-requests.json').write_text(json.dumps(calls, ensure_ascii=False, indent=2))

    def evaluator(path, text, **kwargs):
        recovered = D.asr(recognizer, path)
        quality = evaluate_tts(path, text, recovered_text=recovered['text'], **kwargs)
        hard = list(quality.hard_failures)
        if recovered['language'] != 'tr' and recovered['language_probability'] >= 0.8:
            hard.append('wrong_language')
        return replace(quality, hard_failures=tuple(dict.fromkeys(hard)), metadata={'asr': recovered})

    def prosody(group):
        path = run / 'prosody' / f'group_{group.index+1}.wav'
        sf.write(path, source_audio[int(group.start*sr):int(group.end*sr)], sr, subtype='PCM_16')
        return extract_prosody(path)

    results, qualities = V.generate_dubbed_speech(groups, str(run/'wav'), 'en', 'tr', manager,
        translation_model='hymt2-1.8b', translation_device='cuda', glossary=glossary,
        tts_synthesizer=synthesize, tts_backend_name='voxcpm2-colab-native',
        context_memory=memory, source_prosody_fn=prosody, use_prosody_style=False,
        qa_retries=1, tts_evaluator=evaluator)
    rows = [{'group': group.index+1, 'source': group.text, 'selected_translation': result.translation,
        'synthesis': asdict(result), 'quality': asdict(q),
        'safe_fit': result.final_duration/result.applied_tempo <= result.safe_available_duration+0.02}
        for group, result, q in zip(groups, results, qualities)]
    passed = all(q.passed for q in qualities) and all(row['safe_fit'] for row in rows)
    payload = {'passed': passed, 'run_root': str(run), 'mode': mode, 'groups': rows,
        'tts_requests': len(calls), 'reference_file_cache_hits': sum(c['reference_file_cache_hit'] for c in calls),
        'wall_seconds': time.perf_counter()-started}
    (run / 'validation.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    if mode == 'gate':
        D.record('acceptance-gate.json', payload)
    else:
        from moviepy.editor import VideoFileClip
        video = VideoFileClip(str(source))
        for step in ('mp4', 'ffmpeg', 'final_movie'):
            (run/step).mkdir(exist_ok=True)
        V.save_Subclips(groups, video, str(run/'mp4')+'/', str(source))
        V.change_ClipSpeed(groups, str(run/'ffmpeg')+'/', str(run/'mp4')+'/', str(run/'wav')+'/', str(run/'final_movie')+'/')
        gaps = V.clip_Parts_without_speaker(groups, video)
        V.remove_and_setting_video_ends(groups, gaps, str(run/'final_movie')+'/', str(run/'mp4')+'/')
        final = V.concatenate_All_Clip_Parts(gaps)
        old_cwd = Path.cwd()
        os.chdir(run)
        try:
            pre_mix, mixed = V.save_Final_Video(final, source.name)
        finally:
            os.chdir(old_cwd)
        V.concatenate_Video_And_Background_Sound(pre_mix, str(ROOT/'vocal-remover/vocals/test_multispeaker_Instruments.wav'), mixed)
        payload.update(final_video=mixed, final_probe=D.probe(mixed), pre_mix_probe=D.probe(pre_mix), wall_seconds=time.perf_counter()-started)
        final.close()
        video.close()
        (run / 'validation.json').write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        D.record('full-validation.json', payload)
    memory.close()


if __name__ == '__main__':
    main()
