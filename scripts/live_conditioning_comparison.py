"""Compare one ref-only render with saved real continuation candidates.

Uses one warm native model, fixed unchanged targets/references and 10 steps.
No translation correctness or browser acceptance claim is made.
"""
from pathlib import Path
import sys, os, json, time, hashlib, site, ctypes
from dataclasses import asdict
ROOT = Path('/content/video-dubbing-translator-main')
sys.path.insert(0, str(ROOT))
os.environ.update(LIVE_SPEAKER_PYTHON=sys.executable,
    LIVE_SPEAKER_MODEL=str(ROOT/'third_party/diarization/community-1'), HF_HUB_OFFLINE='1')
for library in ('libcublasLt.so.12', 'libcublas.so.12'):
    for location in site.getsitepackages():
        path = Path(location)/'nvidia/cublas/lib'/library
        if path.exists():
            ctypes.CDLL(str(path), mode=ctypes.RTLD_GLOBAL)
            break
import numpy as np
import soundfile as sf
import torch
from scipy.signal import resample_poly
from voxcpm import VoxCPM
from live_dubbing.models import LocalModels, SpeakerClient
from live_dubbing.speakers import SpeakerChoice
from live_dubbing.speech import IncrementalWhisper
from engine.qa import evaluate_tts

def pcm16(path):
    data, rate = sf.read(path, dtype='float32')
    if data.ndim > 1:
        data = data.mean(axis=1)
    from math import gcd
    common = gcd(rate, 16000)
    values = resample_poly(data, 16000//common, rate//common)
    return (np.clip(values, -1, 1)*32767).astype('<i2').tobytes()

def cosine(left, right):
    a,b = np.asarray(left),np.asarray(right)
    return float(np.dot(a,b)/max(1e-9,np.linalg.norm(a)*np.linalg.norm(b)))

cases = [
    ('number', 'bbed433e3c8c4450905f7e73e5992e6e', '3423678051ba44de9e1ae4fb5d42241e', 6.18),
    ('advice', '696e6f72e7ee44dda31af16bcae73f9c', 'ebe746cc285746879d49cc4c56c46792', 5.08),
    ('activism', '1023708ecf7040b2983efc84cd1924db', '47cf6285a4574574b4b140240dc14f10', 3.4),
]
output = ROOT/'temp'/f'live-conditioning-comparison-{int(time.time())}'
output.mkdir()
started = time.perf_counter()
runtime = object.__new__(LocalModels)
runtime.voxcpm = VoxCPM.from_pretrained('/content/voxcpm2', load_denoiser=False, optimize=False, device='cuda')
runtime._prompt_caches = {}
asr = IncrementalWhisper('/content/faster-whisper-medium', 'cuda')
speaker = SpeakerClient('cuda')
speaker.prepare()
load_seconds = time.perf_counter()-started
rows = []
try:
    for name, session_id, phrase_id, source_seconds in cases:
        directory = ROOT/'temp/live-dubbing'/session_id
        originals = [json.loads(p.read_text()) for p in directory.glob(phrase_id+'.*.json')]
        original = next(x for x in originals if x.get('seed') == 42 and 'quality' in x)
        reference = next(p for p in (directory/'references').glob('*.wav')
            if hashlib.sha256(p.read_bytes()).hexdigest() == original['reference_sha256'])
        choice = SpeakerChoice('same-speaker', reference, original['reference_transcript'],
            1, 'controlled_same_immutable_reference', original['reference_sha256'])
        chunks, last, first = [], b'', None
        started = time.perf_counter()
        for wave in runtime._stream(original['target_text'], choice, 42, reference_only=True):
            if first is None:
                first = time.perf_counter()-started
            if not np.all(np.isfinite(wave)):
                raise ValueError('nonfinite_pcm')
            chunk = (np.clip(wave,-1,1)*32767).astype('<i2').tobytes()
            if last and len(chunk)>len(last) and chunk.startswith(last):
                raise ValueError('cumulative_pcm')
            if chunk == last and np.max(np.abs(wave)) > .001:
                raise ValueError('duplicate_pcm')
            chunks.append(chunk)
            last = chunk
            if sum(map(len,chunks)) > 96000*30:
                raise ValueError('audio_exceeds_30_seconds')
        elapsed = time.perf_counter()-started
        samples = np.frombuffer(b''.join(chunks),dtype='<i2')
        path = output/(name+'-reference-only.wav')
        sf.write(path,samples,48000,subtype='PCM_16')
        recovered = asr.transcribe(pcm16(path))
        quality = evaluate_tts(path,original['target_text'],recovered_text=recovered.text,reference_wav=reference)
        embedding = speaker.analyze(pcm16(reference))['embedding']
        baseline_cosine = cosine(embedding,speaker.analyze(pcm16(original['wav']))['embedding'])
        new_cosine = cosine(embedding,speaker.analyze(pcm16(path))['embedding'])
        row = {'case':name,'target_unchanged':original['target_text'],'source_seconds':source_seconds,
            'reference_sha256':original['reference_sha256'],'baseline':original,
            'reference_only':{'wav':str(path),'ttfa_seconds':first,'tts_seconds':elapsed,
                'audio_seconds':len(samples)/48000,'rtf':elapsed/(len(samples)/48000),
                'chunks':len(chunks),'quality':asdict(quality),'language':recovered.language,
                'language_probability':recovered.language_probability,'conditioning':runtime._last_conditioning},
            'speaker_cosine':{'baseline':baseline_cosine,'reference_only':new_cosine,
                'backend':'community1-embedding','cross_language_threshold_calibrated':False},
            'semantic_review':'fixed existing translation; this checks audio/conditioning, not source meaning'}
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
finally:
    speaker.close()
result = {'load_seconds':load_seconds,'gpu':torch.cuda.get_device_name(0),'cases':rows}
(output/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
(ROOT/'temp/live-conditioning-comparison-latest.txt').write_text(str(output))
