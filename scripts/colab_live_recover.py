"""Recover only ephemeral development infrastructure after VM termination.

No completed inference/acceptance benchmark is rerun. All large downloads stay
on the Colab disk. The Jupyter kernel never imports the changing AI stack.
"""
from pathlib import Path
import json
import subprocess
import sys
import tarfile
root=Path('/content/video-dubbing-translator-main')
root.mkdir(exist_ok=True)
with tarfile.open('/content/live-source.tar.gz') as archive:
 for member in archive.getmembers():
  target=(root/member.name).resolve()
  if not target.is_relative_to(root) or member.issym() or member.islnk():
   raise ValueError('Unsafe source archive entry')
 archive.extractall(root)
worker=root/'recover-model-environment.py'
worker.write_text('''from pathlib import Path
import json,os,subprocess,sys,traceback
root=Path('/content/video-dubbing-translator-main')
os.environ.update(HF_HUB_DISABLE_TELEMETRY='1',PYANNOTE_METRICS_ENABLED='0')
try:
 subprocess.run([sys.executable,'-m','pip','install','-q','voxcpm==2.0.3','faster-whisper==1.2.1',
  'pyannote.audio==4.0.7','transformers==5.20.2','torch==2.11.0','torchaudio==2.11.0',
  'aiohttp>=3.10,<4','soundfile','sentencepiece','nvidia-cublas-cu12','fsorter==2.4','moviepy==1.0.3'],
  check=True,timeout=900)
 from huggingface_hub import snapshot_download
 from concurrent.futures import ThreadPoolExecutor
 requests=[('openbmb/VoxCPM2','32279effe8c19989596f05d353d1447f51d9e915','/content/voxcpm2',
  ['audiovae.pth','config.json','model.safetensors','*.json','tokenization_voxcpm2.py']),
  ('tencent/Hy-MT2-1.8B','9a341cd1b679d3efd23b46e847b01745a71ed792','/content/hymt2-1.8b',
   ['*.safetensors','*.json','*.jinja']),
  ('Systran/faster-whisper-small',None,'/content/faster-whisper-small',['model.bin','*.json','*.txt'])]
 def download(item):
  repo,revision,local,patterns=item
  snapshot_download(repo,revision=revision,local_dir=local,allow_patterns=patterns)
  return {'repo':repo,'revision':revision,'path':local}
 with ThreadPoolExecutor(max_workers=3) as pool:models=list(pool.map(download,requests))
 result={'passed':True,'models':models,'inference_rerun':False,'large_download_location':'Colab only'}
except Exception as exc:
 result={'passed':False,'error':str(exc),'traceback':traceback.format_exc()}
(root/'recovery-model-result.json').write_text(json.dumps(result,indent=2))
''')
log=(root/'recovery-model.log').open('w')
p=subprocess.Popen([sys.executable,str(worker)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(root/'recovery-model-process.json').write_text(json.dumps({'pid':p.pid}))
print({'recovery_model_pid':p.pid,'inference_rerun':False})
