"""Bounded real A/B/A/B encoded-conditioning isolation on the warm native model."""
import builtins
from dataclasses import asdict
import importlib
import json
import numpy as np
from pathlib import Path
import sys
import time

ROOT=Path('/content/video-dubbing-translator-main')
sys.path.insert(0,str(ROOT))
import live_dubbing.models as module
importlib.reload(module)
from live_dubbing.speakers import SpeakerChoice
from engine.qa import word_error_rate
import soundfile as sf
from scipy.signal import resample_poly

models=builtins._adaptive_live_models
models.__class__=module.LocalModels
if not hasattr(models,'_prompt_caches'):
    models._prompt_caches={}
reference=models.speakers.profiles[0]
choice=SpeakerChoice(reference.identifier,reference.path,reference.text,1.0,'real_controlled_reference',reference.sha256)
output=ROOT/'temp'/f'live-cache-gate-{int(time.time())}'
output.mkdir()
targets=['Bugün hava çok güzel, dışarıda yürüyüş yapıyoruz.',
         'Yarın sabah yeni bir kitap okumaya başlayacağım.']
rows=[]
for index,target in enumerate(targets*2):
    started=time.perf_counter()
    first=None
    chunks=[]
    for chunk in models._stream(target,choice,42):
        if first is None:first=time.perf_counter()
        chunks.append((np.clip(chunk,-1,1)*32767).astype('<i2').tobytes())
    finished=time.perf_counter()
    pcm=b''.join(chunks)
    path=output/f'{index}.wav'
    sf.write(path,np.frombuffer(pcm,dtype='<i2'),48000,subtype='PCM_16')
    down=resample_poly(np.frombuffer(pcm,dtype='<i2').astype(np.float32),1,3)
    recovered=models.asr.transcribe(np.clip(down,-32768,32767).astype('<i2').tobytes())
    rows.append({'target':target,'recovered':recovered.text,'language':recovered.language,
        'wer':word_error_rate(target,recovered.text),'ttfa_seconds':first-started,
        'rtf':(finished-started)/(len(pcm)/96000),'chunks':len(chunks),
        'cache':dict(models._last_conditioning),'wav':str(path),
        'duplicate_non_silent_chunks':sum(a==b and any(a) for a,b in zip(chunks,chunks[1:]))})
result={'passed':all(row['wer']<=.15 and row['language']=='tr' and not row['duplicate_non_silent_chunks'] for row in rows),
        'rows':rows,'scope':'encoded source features only; cloned tensors per request; no target/KV/waveform cache'}
(output/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
print(json.dumps({'output':str(output),**result},ensure_ascii=False))
