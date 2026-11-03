"""Run one matched three-mode real-time fixture comparison on warm G4 models."""
from pathlib import Path
import base64
import builtins
import json
import os
import subprocess
import threading
import time

ROOT=Path('/content/video-dubbing-translator-main')
os.environ['HYMT2_SEED']='42'
from translation.backends import HyMT2Backend
models=builtins._adaptive_live_models
# Reload only the isolated translator worker once so its sampling seed is the
# same for all modes. Native VoxCPM2 / ASR / speaker weights stay warm.
models.translator.hymt2.close()
models.translator.hymt2=HyMT2Backend(device='cuda')
models.translator.hymt2.prepare()
stop=threading.Event()
output=ROOT/'temp'/f'live-three-mode-comparison-{int(time.time())}'
output.mkdir()
def sample():
    import psutil
    process=psutil.Process()
    process.cpu_percent()
    with (output/'resources.jsonl').open('w') as stream:
        while not stop.wait(1):
            children=process.children(recursive=True)
            rss=process.memory_info().rss
            for child in children:
                try:rss+=child.memory_info().rss
                except psutil.Error:pass
            stream.write(json.dumps({'monotonic':time.perf_counter(),'summed_rss_bytes':rss,
                'parent_cpu_percent_of_one_core':process.cpu_percent(),
                'child_count':len(children)})+'\n')
            stream.flush()
thread=threading.Thread(target=sample,daemon=True)
thread.start()
gpu_log=(output/'gpu.csv').open('w')
gpu=subprocess.Popen(['nvidia-smi','--query-gpu=timestamp,utilization.gpu,memory.used',
    '--format=csv','-l','1'],stdout=gpu_log,stderr=subprocess.STDOUT)
rows=[]
try:
    source=base64.b64decode(os.environ.pop('LIVE_GATE_SOURCE_BASE64')).decode()
    for mode in ('quality','balanced','low_latency'):
        os.environ.update(LIVE_TEST_MODE=mode,LIVE_TEST_SECONDS='12.4',LIVE_REFRESH_CODE='1')
        exec(compile(source,'real_live_gate.py','exec'),globals())
        path=Path('/content/live-gate-latest.txt').read_text()
        rows.append({'mode':mode,'directory':path,**json.loads((Path(path)/'gate-result.json').read_text())})
        if not rows[-1]['passed']:
            break
finally:
    stop.set()
    thread.join(timeout=3)
    gpu.terminate()
    gpu.wait(timeout=5)
    gpu_log.close()
result={'passed':len(rows)==3 and all(row['passed'] for row in rows),
        'scope':'Real models + real-time file arrival; actual browser playback and YouTube not tested here',
        'translation_seed':42,'runs':rows}
(output/'comparison.json').write_text(json.dumps(result,indent=2,ensure_ascii=False))
Path('/content/live-mode-comparison-latest.txt').write_text(str(output))
print(json.dumps({'output':str(output),'passed':result['passed'],'runs':[
    {key:row[key] for key in ('mode','passed','directory','phrases')} for row in rows]}))
