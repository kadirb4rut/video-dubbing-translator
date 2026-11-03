"""Real saved-audio check of the approved offline ideal tempo ceiling.

One ASR/speaker instance; no new native generation or translation is performed.
"""
from pathlib import Path
import os,sys,site,ctypes,json,time,subprocess
ROOT=Path('/content/video-dubbing-translator-main')
sys.path.insert(0,str(ROOT))
os.environ.update(LIVE_SPEAKER_PYTHON=sys.executable,
    LIVE_SPEAKER_MODEL=str(ROOT/'third_party/diarization/community-1'),HF_HUB_OFFLINE='1')
for name in ('libcublasLt.so.12','libcublas.so.12'):
    for location in site.getsitepackages():
        path=Path(location)/'nvidia/cublas/lib'/name
        if path.exists():
            ctypes.CDLL(str(path),mode=ctypes.RTLD_GLOBAL)
            break
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly
from live_dubbing.speech import IncrementalWhisper
from live_dubbing.models import SpeakerClient
from engine.qa import evaluate_tts
from dataclasses import asdict

def pcm16(path):
    samples,rate=sf.read(path,dtype='float32')
    from math import gcd
    divisor=gcd(rate,16000)
    samples=resample_poly(samples,16000//divisor,rate//divisor)
    return (np.clip(samples,-1,1)*32767).astype('<i2').tobytes()

def cosine(a,b):
    a,b=np.asarray(a),np.asarray(b)
    return float(np.dot(a,b)/max(1e-9,np.linalg.norm(a)*np.linalg.norm(b)))

source=Path((ROOT/'temp/live-conditioning-comparison-latest.txt').read_text())/'result.json'
cases=json.loads(source.read_text())['cases']
output=ROOT/'temp'/f'live-tempo-comparison-{int(time.time())}'
output.mkdir()
asr=IncrementalWhisper('/content/faster-whisper-medium','cuda')
speaker=SpeakerClient('cuda')
speaker.prepare()
rows=[]
try:
    for case in cases:
        original=case['baseline']
        path=output/(case['case']+'-tempo-1.10.wav')
        started=time.perf_counter()
        subprocess.run(['ffmpeg','-nostdin','-y','-v','error','-i',original['wav'],
            '-af','atempo=1.10','-ar','48000','-ac','1','-c:a','pcm_s16le',str(path)],
            check=True,timeout=10)
        transform_seconds=time.perf_counter()-started
        pcm=pcm16(path)
        recovered=asr.transcribe(pcm)
        quality=evaluate_tts(path,original['target_text'],recovered_text=recovered.text)
        # Same source reference used in the existing fair conditioning comparison.
        old_wav=Path(original['wav'])
        import hashlib
        reference=next(p for p in (old_wav.parent/'references').glob('*.wav')
            if hashlib.sha256(p.read_bytes()).hexdigest()==original['reference_sha256'])
        vector=speaker.analyze(pcm16(reference))['embedding']
        similarity=cosine(vector,speaker.analyze(pcm)['embedding'])
        row={'case':case['case'],'target_unchanged':original['target_text'],'source_seconds':case['source_seconds'],
            'original_wav':original['wav'],'tempo_wav':str(path),'tempo':1.10,
            'original_seconds':original['audio_seconds'],'tempo_seconds':sf.info(path).duration,
            'transform_seconds':transform_seconds,'quality':asdict(quality),
            'auto_language':recovered.language,'auto_probability':recovered.language_probability,
            'speaker_cosine_before':case['speaker_cosine']['baseline'],'speaker_cosine_after':similarity,
            'speaker_backend':'community1-embedding','cross_language_threshold_calibrated':False,
            'native_regenerated':False,'human_naturalness_review':'pending; ASR and embedding do not prove subjective quality'}
        rows.append(row)
        print(json.dumps(row,ensure_ascii=False),flush=True)
finally:
    speaker.close()
(output/'result.json').write_text(json.dumps({'source':str(source),'cases':rows},ensure_ascii=False,indent=2))
(ROOT/'temp/live-tempo-comparison-latest.txt').write_text(str(output))
