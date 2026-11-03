"""Bounded regression diagnostics on Colab's G4 disk and GPU only."""
from __future__ import annotations

import builtins
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import time

ROOT = Path('/content/video-dubbing-translator-main')
OUT = Path('/content/regression-evidence')


def record(name, payload):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


def prepare():
    for archive in ('code-fixture.tar.gz', 'comparison-inputs.tar.gz'):
        ROOT.mkdir(exist_ok=True)
        with tarfile.open('/content/' + archive) as handle:
            handle.extractall(ROOT, filter='data')
    subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', 'voxcpm==2.0.3', 'pyannote.audio', 'faster-whisper', 'fsorter==2.4'], check=True)
    sys.path.insert(0, str(ROOT))
    import torch
    record('runtime.json', {
        'python': sys.version, 'torch': torch.__version__, 'cuda': torch.version.cuda,
        'gpu': torch.cuda.get_device_name(0),
        'versions': {name: importlib.metadata.version(name) for name in ('voxcpm', 'transformers', 'pyannote.audio', 'faster-whisper', 'av')},
    })


def probe(path):
    return json.loads(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type,duration,sample_rate,nb_frames:format=duration', '-of', 'json', str(path)
    ]))


def media():
    sys.path.insert(0, str(ROOT))
    import importlib
    import Video_Translator
    importlib.reload(Video_Translator)
    from Video_Translator import concatenate_Video_And_Background_Sound
    OUT.mkdir(exist_ok=True)
    background = ROOT / 'vocal-remover/vocals/test_multispeaker_Instruments.wav'
    rows = []
    # Reproduce the exact background mix on a known-duration synthetic speech
    # track.  This isolates mux/filter duration from all model generation.
    for duration in (6, 12):
        speech = OUT / f'synthetic_{duration}.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color=size=320x180:rate=30:duration={duration}', '-f', 'lavfi', '-i', f'sine=frequency=440:sample_rate=48000:duration={duration}', '-c:v', 'libx264', '-c:a', 'aac', '-t', str(duration), str(speech)], check=True)
        mixed = OUT / f'mixed_{duration}.mp4'
        concatenate_Video_And_Background_Sound(str(speech), str(background), str(mixed))
        rows.append({'input': probe(speech), 'mixed': probe(mixed), 'duration': duration})
    record('mux-diagnosis.json', {'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0], 'cases': rows})


def models():
    sys.path.insert(0, str(ROOT))
    if not hasattr(builtins, '_reg_tts'):
        from huggingface_hub import snapshot_download
        from voxcpm import VoxCPM
        from voxcpm_runtime import VOXCPM_MODEL_REVISION, VOXCPM_REQUIRED_FILES
        snapshot_download('openbmb/VoxCPM2', revision=VOXCPM_MODEL_REVISION, local_dir='/content/voxcpm2', allow_patterns=list(VOXCPM_REQUIRED_FILES))
        builtins._reg_tts = VoxCPM.from_pretrained('/content/voxcpm2', load_denoiser=False, optimize=False, device='cuda')
    if not hasattr(builtins, '_reg_asr'):
        from faster_whisper import WhisperModel
        builtins._reg_asr = WhisperModel('small', device='cpu', compute_type='int8', download_root='/content/asr-cache')
    return builtins._reg_tts, builtins._reg_asr


def asr(model, path, language=None):
    import numpy as np
    raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'f32le', '-ar', '16000', '-ac', '1', 'pipe:1'])
    segments, info = model.transcribe(np.frombuffer(raw, dtype='<f4'), language=language, beam_size=3, condition_on_previous_text=False, vad_filter=True)
    rows = [{'start': s.start, 'end': s.end, 'text': s.text, 'avg_logprob': s.avg_logprob} for s in segments]
    return {'language': info.language, 'language_probability': info.language_probability, 'text': ' '.join(s['text'].strip() for s in rows), 'segments': rows}


def short():
    import hashlib
    import soundfile as sf
    import torch
    model, recognizer = models()
    from engine.qa import evaluate_tts
    from dataclasses import asdict
    ref_root = ROOT / 'temp/modal-e2e-1789503194/references'
    refs = json.loads((ref_root / 'manifest.json').read_text())
    ref = ref_root / 'SPEAKER_00.wav'
    prompt = refs[0]['transcript']
    cases = [
        (1, 'Evet, tam olarak. Yoğundum. Yazmayı çok seviyorum ama yapay zekâyı öğrenmek ve öğretmek daha çok hoşuma gidiyor.', 6.106, 42),
        (2, 'Bahsettiğiniz uzmanlık programlarından ve kurslarınızdan çok şey öğrendim. Burada olmak benim için büyük bir onur.', 4.728, 43),
        (8, 'Teşekkür ederim. Bilmiyordum.', 1.204, 49),
        (9, 'Bunu paylaştığınız için teşekkürler. Sizi şaşırttım. Evet.', 2.107, 50),
    ]
    rows = []
    for group, text, target, seed in cases:
        for steps in (5, 10):
            path = OUT / f'group_{group}_steps_{steps}.wav'
            torch.manual_seed(seed)
            started = time.perf_counter()
            waveform = model.generate(text=text, reference_wav_path=str(ref), prompt_wav_path=str(ref), prompt_text=prompt, normalize=False, denoise=False, inference_timesteps=steps, cfg_value=2.0)
            sf.write(path, waveform, int(model.tts_model.sample_rate), subtype='PCM_24')
            wall = time.perf_counter() - started
            recovered = asr(recognizer, path)
            quality = evaluate_tts(path, text, recovered_text=recovered['text'], expected_duration=target, reference_wav=ref)
            rows.append({'group': group, 'target': text, 'steps': steps, 'seed': seed, 'generation_seconds': wall, 'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'asr': recovered, 'quality': asdict(quality)})
            record('short-comparison.json', {'cases': rows})


def compare():
    _, recognizer = models()
    sources = {
        'SOURCE': ROOT / 'test_multispeaker.mp4',
        'GOOD': ROOT / 'vocal-remover/final_video/final/test_multispeaker.mp4',
        'BAD': ROOT / 'temp/modal-e2e-1789503194/final_video/final/test_multispeaker.mp4',
        'COLAB_PREVIOUS': ROOT / 'temp/regression-investigation-20261024/colab-e2e-1792796873/test_multispeaker.mp4',
    }
    full_evidence = OUT / 'full-validation.json'
    if full_evidence.exists():
        sources['REPAIRED'] = Path(json.loads(full_evidence.read_text())['final_video'])
    rows = {}
    for key, path in sources.items():
        rows[key] = {'probe': probe(path), 'asr': asr(recognizer, path)}
        record('independent-comparison.json', rows)
    windows = []
    for group, start, end in [(1, .1, 7.15), (2, 7.15, 12.502), (8, 39.318, 41.024), (9, 41.024, 44.696)]:
        row = {'group': group, 'start': start, 'end': end}
        for key in ('GOOD', 'BAD', 'REPAIRED'):
            if key not in sources:
                continue
            path = OUT / f'{key.lower()}_group_{group}.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(start), '-i', str(sources[key]), '-t', str(end-start), '-ar', '16000', '-ac', '1', str(path)], check=True)
            row[key] = asr(recognizer, path)
        windows.append(row)
        record('aligned-comparison.json', {'windows': windows})


def translation():
    import sqlite3
    import torch
    from dataclasses import replace
    sys.path.insert(0, str(ROOT))
    from huggingface_hub import snapshot_download
    from translation.hymt2_worker import Runtime
    from translation.prompts import translation_prompt, duration_prompt
    from translation.base import TranslationContext
    from engine.context import ContextMemory
    snapshot_download('tencent/Hy-MT2-1.8B', local_dir='/content/hymt2-1.8b', allow_patterns=['*.safetensors', '*.json', '*.jinja'])
    runtime = getattr(builtins, '_reg_hymt', None)
    if runtime is None:
        runtime = Runtime(Path('/content/hymt2-1.8b'), 'cuda')
        runtime.load()
        builtins._reg_hymt = runtime
    old = ROOT / 'temp/regression-investigation-20261024/colab-e2e-1792796873'
    db = sqlite3.connect(old / 'context.sqlite3')
    history = db.execute("SELECT group_index, source, target, speaker_id FROM context_items WHERE kind='phrase' ORDER BY group_index").fetchall()
    memory = ContextMemory()
    for index, source, target, speaker in history[:8]:
        memory.remember_translation(source, target, speaker_id=speaker, group_index=index)
    source = history[8][1]
    retrieved = memory.relevant(source, speaker_id='SPEAKER_00')
    previous = history[7][1] + ' | memory: ' + ' | '.join(f'{r.source} → {r.target}' for r in retrieved if r.target)
    context = TranslationContext(previous_text=previous, next_text=history[9][1], speaker='SPEAKER_00', target_spoken_units=10, preferred_duration_seconds=2.107, safe_available_duration_seconds=3.672)
    rows = []
    for label, ctx in [('memory', context), ('adjacent_only', replace(context, previous_text=history[7][1])), ('current_only', replace(context, previous_text=None, next_text=None))]:
        prompt = translation_prompt(source, 'en', 'tr', context=ctx)
        torch.manual_seed(42)
        started = time.perf_counter()
        text, stats = runtime.generate(prompt)
        rows.append({'variant': label, 'operation': 'translate', 'source': source, 'prompt': prompt, 'text': text, 'stats': stats, 'seconds': time.perf_counter() - started})
        record('translation-isolation.json', {'retrieved': [r.source for r in retrieved], 'requests': rows})
    for label, ctx in [('memory', context), ('current_only', replace(context, previous_text=None, next_text=None))]:
        ctx = replace(ctx, refinement_direction='shorten', candidate_style='duration_refinement_shorten')
        prompt = duration_prompt(source, 'Bunu paylaştığınız için teşekkür ederim. Sizi şaşırttım. Evet.', 'en', 'tr', 2.107, 3.52, context=ctx)
        torch.manual_seed(42)
        text, stats = runtime.generate(prompt)
        rows.append({'variant': label, 'operation': 'rewrite', 'source': source, 'prompt': prompt, 'text': text, 'stats': stats})
        record('translation-isolation.json', {'retrieved': [r.source for r in retrieved], 'requests': rows})


def retry():
    import soundfile as sf
    import torch
    from dataclasses import asdict
    model, recognizer = models()
    from engine.qa import evaluate_tts
    ref_root = ROOT / 'temp/modal-e2e-1789503194/references'
    ref = ref_root / 'SPEAKER_00.wav'
    transcript = json.loads((ref_root / 'manifest.json').read_text())[0]['transcript']
    torch.manual_seed(1049)
    path = OUT / 'group_8_retry_1049.wav'
    text = 'Teşekkür ederim. Bilmiyordum.'
    wave = model.generate(text=text, reference_wav_path=str(ref), prompt_wav_path=str(ref), prompt_text=transcript, normalize=False, denoise=False, inference_timesteps=10, cfg_value=2.0)
    sf.write(path, wave, 48000, subtype='PCM_24')
    recovered = asr(recognizer, path)
    quality = evaluate_tts(path, text, recovered_text=recovered['text'], expected_duration=1.204, reference_wav=ref, retry_count=1)
    record('natural-retry.json', {'target': text, 'seed': 1049, 'asr': recovered, 'quality': asdict(quality)})


def verify():
    with tarfile.open('/content/repair-source.tar.gz') as handle:
        handle.extractall(ROOT, filter='data')
    if Path('/content/readme-linked-evidence.tar.gz').exists():
        with tarfile.open('/content/readme-linked-evidence.tar.gz') as handle:
            handle.extractall(ROOT, filter='data')
    result = subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', '-p', 'test_*.py'], cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    OUT.mkdir(exist_ok=True)
    (OUT / 'unit-suite.log').write_text(result.stdout)
    print(result.stdout[-8000:])
    record('verification.json', {'unit_exit_code': result.returncode})


def conditioning():
    import soundfile as sf
    import torch
    from dataclasses import asdict
    model, recognizer = models()
    from engine.qa import evaluate_tts
    ref_root = ROOT / 'temp/modal-e2e-1789503194/references'
    ref = ref_root / 'SPEAKER_00.wav'
    transcript = json.loads((ref_root / 'manifest.json').read_text())[0]['transcript']
    text = 'Teşekkür ederim. Bilmiyordum.'
    rows = []
    for label, options in [
        ('reference_only', {'reference_wav_path': str(ref)}),
        ('continuation_only', {'prompt_wav_path': str(ref), 'prompt_text': transcript}),
    ]:
        torch.manual_seed(49)
        path = OUT / f'group8_{label}.wav'
        wave = model.generate(text=text, normalize=False, denoise=False, inference_timesteps=10, cfg_value=2.0, **options)
        sf.write(path, wave, 48000, subtype='PCM_24')
        recovered = asr(recognizer, path)
        quality = evaluate_tts(path, text, recovered_text=recovered['text'], expected_duration=1.204, reference_wav=ref)
        rows.append({'mode': label, 'seed': 49, 'target': text, 'asr': recovered, 'quality': asdict(quality), 'path': str(path)})
        record('conditioning.json', {'cases': rows})


def muxvariants():
    import numpy as np
    speech = OUT / 'synthetic_6.mp4'
    background = ROOT / 'vocal-remover/vocals/test_multispeaker_Instruments.wav'
    rows = []
    for label, filters in [
        ('norm_only', '[0:a]loudnorm=I=-20:TP=-2:LRA=11,aresample=48000[a]'),
        ('first_resample', '[0:a]loudnorm=I=-20:TP=-2:LRA=11,aresample=48000[d];[1:a]volume=0.35[b];[d][b]amix=inputs=2:duration=first:normalize=0[a]'),
        ('longest', '[0:a]loudnorm=I=-20:TP=-2:LRA=11[d];[1:a]volume=0.35[b];[d][b]amix=inputs=2:duration=longest:normalize=0[a]'),
    ]:
        output = OUT / f'mux_{label}.wav'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', str(speech), '-i', str(background), '-filter_complex', filters, '-map', '[a]', '-t', '6', '-ar', '48000', str(output)], check=True)
        raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-ss', '5.5', '-i', str(output), '-f', 'f32le', '-ac', '1', '-ar', '16000', 'pipe:1'])
        values = np.frombuffer(raw, dtype='<f4')
        rows.append({'variant': label, 'probe': probe(output), 'tail_rms': float(np.sqrt(np.mean(values ** 2))) if values.size else 0})
    record('mux-variants.json', {'cases': rows})


def isolation():
    from concurrent.futures import ThreadPoolExecutor
    import hashlib
    import numpy as np
    import soundfile as sf
    import importlib
    import voxcpm_runtime
    importlib.reload(voxcpm_runtime)
    from engine.backends import NativeVoxCPMBackend
    from engine.contracts import SpeakerProfile
    from engine.qa import word_error_rate
    model, recognizer = models()
    ref_root = ROOT / 'temp/modal-e2e-1789503194/references'
    ref = ref_root / 'SPEAKER_00.wav'
    transcript = json.loads((ref_root / 'manifest.json').read_text())[0]['transcript']
    backend = NativeVoxCPMBackend(model=model, device='cuda')
    speaker = SpeakerProfile('SPEAKER_00', ref, transcript)
    targets = ['Bugün hava çok güzel, dışarıda yürüyüş yapıyoruz.', 'Yarın sabah yeni bir kitap okumaya başlayacağım.']
    rows = []
    for index, which in enumerate((0, 1, 0, 1)):
        path = OUT / f'isolation_{index}.wav'
        result = backend.synthesize(targets[which], speaker, output_wav=path, seed=123 + which)
        recovered = asr(recognizer, path)
        rows.append({'index': index, 'target': targets[which], 'reference_sha256': hashlib.sha256(ref.read_bytes()).hexdigest(),
            'mode': 'same_reference_file', 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'asr': recovered, 'wer': word_error_rate(targets[which], recovered['text'])})
    # Run two requests through the real native backend's inference lock.
    def concurrent(which):
        path = OUT / f'concurrent_{which}.wav'
        result = backend.synthesize(targets[which], speaker, output_wav=path, seed=123 + which)
        return which, path, result.metrics.wall_seconds
    tick = time.perf_counter()
    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent_results = list(pool.map(concurrent, (0, 1)))
    concurrent_wall = time.perf_counter()-tick
    concurrency = []
    for which, path, wall in concurrent_results:
        recovered = asr(recognizer, path)
        concurrency.append({'target': targets[which], 'asr': recovered, 'wall_seconds': wall,
            'wer': word_error_rate(targets[which], recovered['text']), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()})
    streaming = []
    for which in (0, 1):
        tick = time.perf_counter()
        chunks, first = [], None
        for chunk in backend.stream(targets[which], speaker):
            if first is None:
                first = time.perf_counter()-tick
            chunks.append(chunk)
        wall = time.perf_counter()-tick
        pcm = b''.join(chunks)
        path = OUT / f'stream_{which}.wav'
        sf.write(path, np.frombuffer(pcm, dtype='<i2'), 48000, subtype='PCM_16')
        recovered = asr(recognizer, path)
        seconds = len(pcm)/(48000*2)
        streaming.append({'target': targets[which], 'chunks': len(chunks), 'ttfa_seconds': first, 'wall_seconds': wall,
            'audio_seconds': seconds, 'rtf': wall/max(seconds, 1e-6), 'duplicate_chunks': len(chunks)-len(set(chunks)),
            'cumulative_chunks': sum(len(chunks[i]) > len(chunks[i-1]) and chunks[i].startswith(chunks[i-1]) for i in range(1,len(chunks))),
            'asr': recovered, 'wer': word_error_rate(targets[which], recovered['text'])})
    passed = rows[0]['sha256'] != rows[1]['sha256'] and all(r['wer'] <= .35 for r in rows+concurrency+streaming) and all(r['duplicate_chunks'] == r['cumulative_chunks'] == 0 for r in streaming)
    record('isolation.json', {'passed': passed, 'sequential_ABAB': rows, 'concurrency': concurrency, 'concurrent_wall_seconds': concurrent_wall,
        'streaming': streaming, 'cache_scope': 'Reference WAV only; official native API rebuilds conditioning per call. No waveform response cache.'})


def stream_reference_control():
    import numpy as np
    import soundfile as sf
    from engine.backends import NativeVoxCPMBackend
    from engine.contracts import SpeakerProfile
    from engine.qa import word_error_rate
    model, recognizer = models()
    ref = ROOT / 'temp/modal-e2e-1789503194/references/SPEAKER_00.wav'
    target = 'Bugün hava çok güzel, dışarıda yürüyüş yapıyoruz.'
    # Hold the default streaming seed and speaker WAV fixed. The sole changed
    # variable is omitting the long English continuation transcript.
    backend = NativeVoxCPMBackend(model=model, device='cuda')
    chunks = list(backend.stream(target, SpeakerProfile('SPEAKER_00', ref, '')))
    path = OUT / 'stream-reference-control.wav'
    sf.write(path, np.frombuffer(b''.join(chunks), dtype='<i2'), 48000, subtype='PCM_16')
    recovered = asr(recognizer, path)
    record('stream-reference-control.json', {'target': target, 'seed': 42, 'mode': 'reference_only', 'asr': recovered,
        'wer': word_error_rate(target, recovered['text']), 'chunks': len(chunks), 'duplicates': len(chunks)-len(set(chunks))})


if __name__ == '__main__':
    globals()[os.environ.get('REG_STAGE', 'prepare')]()
