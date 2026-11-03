"""Summarize preserved real CLI evidence without rerunning any model."""
from pathlib import Path
import hashlib,json,math
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'temp/live-validation-20261025'
OUT.mkdir(exist_ok=True)
def objects(path):
 text=path.read_text()
 decoder=json.JSONDecoder();position=0
 while True:
  start=text.find('{',position)
  if start<0:return
  try:value,end=decoder.raw_decode(text,start)
  except json.JSONDecodeError:position=start+1;continue
  position=end
  if isinstance(value,dict):yield value

def stats(values):
 values=sorted(value for value in values if isinstance(value,(int,float)) and math.isfinite(value))
 if not values:return {'n':0,'p50':None,'p95':None,'max':None}
 def percentile(q):
  n=(len(values)-1)*q;a=int(n);b=min(a+1,len(values)-1)
  return values[a]+(values[b]-values[a])*(n-a)
 return {'n':len(values),'p50':percentile(.5),'p95':percentile(.95),'max':max(values)}
runs=[]
for filename in ('colab-live-three-modes-v3.log','colab-live-low-latency-v5.log'):
 path=ROOT/'temp'/filename;events=[]
 for item in objects(path):
  if 'type' in item:events.append(item)
  if 'result' in item and item['result'].get('mode'):
   result=item['result']
   if result.get('passed'):
    ready=[e['metrics'] for e in events if e['type']=='phrase_ready']
    fields=set().union(*(m.keys() for m in ready)) if ready else set()
    runs.append({'mode':result['mode'],'source_log':filename,'source_log_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
      'scope':'real models and wall-clock file-fed arrival; no browser playback',
      'result':result,'commits':[e for e in events if e['type']=='committed'],
      'phrase_metrics':ready,'statistics':{key:stats([m.get(key) for m in ready]) for key in sorted(fields)
       if any(isinstance(m.get(key),(int,float)) for m in ready)}})
   events=[]
cache=list(objects(ROOT/'temp/colab-live-cache-auto-asr.log'))
speaker=list(objects(ROOT/'temp/colab-live-speakers.log'))
evidence={'schema_version':1,'current_status':'in progress; browser and YouTube gates open',
 'prior_vm_terminated':True,'raw_prior_vm_only_artifacts_available':False,
 'retained_evidence':'Local CLI stdout logs; no inference was rerun to recreate lost raw files',
 'runs':runs,'native_cache_probe':cache[-1] if cache else None,'speaker_probe':speaker[-1] if speaker else None}
(OUT/'real-evidence-preserved.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False))
print({'runs':[r['mode'] for r in runs],'output':str(OUT/'real-evidence-preserved.json')})
