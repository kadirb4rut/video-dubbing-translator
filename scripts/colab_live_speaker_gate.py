"""Actual Community-1 online identity probe on approved conversation excerpts.

Labels come from the existing offline annotation and require human review;
constructed simultaneous speech is explicitly distinguished from the source.
This tests the online identity adapter, not browser playback.
"""
from pathlib import Path
import builtins
from dataclasses import asdict
import importlib
import json
import subprocess
import time
import numpy as np
from live_dubbing.speakers import SpeakerTracker
ROOT=Path('/content/video-dubbing-translator-main')
output=ROOT/'temp'/f'live-speaker-gate-{int(time.time())}'
output.mkdir()
tracker=SpeakerTracker(output/'references')
models=builtins._adaptive_live_models
fixtures=[('A_first',.98,6.04,'A'),('B_first',12.502,15.52,'B'),
          ('A_return',16.26,21.422,'A'),('B_distant_return',60.237,65.5,'B')]
rows=[]
clips=[]
for name,start,end,label in fixtures:
 pcm=subprocess.check_output(['ffmpeg','-v','error','-ss',str(start),'-i',str(ROOT/'test_multispeaker.mp4'),
   '-t',str(end-start),'-f','s16le','-ac','1','-ar','16000','pipe:1'])
 clips.append(pcm)
 asr=models.recognize(pcm,language='en')
 started=time.perf_counter()
 acoustic=models.speaker_models.analyze(pcm)
 choice=tracker.select(pcm,asr.text,acoustic['embedding'],overlap=acoustic['overlap'],mixed_turn=acoustic['mixed_turn'])
 rows.append({'fixture':name,'source_start':start,'source_end':end,'reference_annotation':label,
   'actual_asr_text':asr.text,'analysis_seconds':time.perf_counter()-started,
   'acoustic':acoustic,'choice':asdict(choice)})
short=clips[0][:16000]
a=models.speaker_models.analyze(short)
s=tracker.select(short,'Short source excerpt.',a['embedding'],overlap=a['overlap'],mixed_turn=a['mixed_turn'])
length=min(map(len,clips[:2]))//2
mixed=np.clip((np.frombuffer(clips[0][:length*2],dtype='<i2').astype(np.float32)+
    np.frombuffer(clips[1][:length*2],dtype='<i2').astype(np.float32))*.7,-32768,32767).astype('<i2').tobytes()
a=models.speaker_models.analyze(mixed)
m=tracker.select(mixed,'Constructed simultaneous source speech.',a['embedding'],overlap=a['overlap'],mixed_turn=a['mixed_turn'])
labels=[r['choice']['speaker_id'] for r in rows]
result={'gate':'C-online-identity-probe','same_speaker_return':labels[0]==labels[2] and labels[1]==labels[3],
 'different_speakers':labels[0]!=labels[1],'rows':rows,'short_interjection':asdict(s),
 'constructed_overlap':{'provenance':'sum of two actual source speakers, not a natural source event',
   'acoustic':a,'choice':asdict(m)},'browser_playback':False}
(output/'result.json').write_text(json.dumps(result,indent=2,ensure_ascii=False,default=str))
print(json.dumps({'output':str(output),'same_speaker_return':result['same_speaker_return'],
 'different_speakers':result['different_speakers'],'labels':labels,
 'constructed_overlap':{k:a[k] for k in ('overlap','overlap_fraction','mixed_turn')},
 'short_choice':s.reason},ensure_ascii=False))
