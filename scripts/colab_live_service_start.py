"""Start the consumer localhost service on the development GPU computer.

Development paths are environment overrides; the product has no Colab API,
cloud endpoint, public tunnel or credential dependency.
"""
from pathlib import Path
import builtins
import json
import os
import site
import subprocess
import sys
import time
import shutil
ROOT=Path('/content/video-dubbing-translator-main')
# Release only our file-fed development adapter. The browser will exercise the
# real session-scoped model worker through Start/Stop.
if hasattr(builtins,'_adaptive_live_models'):
 builtins._adaptive_live_models.close()
 del builtins._adaptive_live_models
libs=[str(Path(p)/'nvidia/cublas/lib') for p in site.getsitepackages() if (Path(p)/'nvidia/cublas/lib').is_dir()]
environment={**os.environ,'PYTHONPATH':str(ROOT),'HYMT2_MODELS_DIR':'/content','HYMT2_PYTHON':sys.executable,
 'DIARIZATION_PYTHON':sys.executable,'DIARIZATION_MODEL_DIR':str(ROOT/'third_party/diarization/community-1'),
 'LIVE_SPEAKER_PYTHON':sys.executable,'LIVE_SPEAKER_MODEL':str(ROOT/'third_party/diarization/community-1'),
 'LIVE_VOXCPM_PATH':'/content/voxcpm2','LIVE_WHISPER_MODEL':os.environ.get('LIVE_WHISPER_MODEL','/content/faster-whisper-small'),
 'HF_HUB_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','PYANNOTE_METRICS_ENABLED':'0',
 'LIVE_CAPTURE_DIAGNOSTICS':'1',
 'LIVE_SEGMENTATION_PYTHON':str(ROOT/'third_party/live_segmentation/.venv/bin/python'),
 'LIVE_SEGMENTATION_MODEL':str(ROOT/'third_party/live_segmentation/sat-3l-sm'),
 'LIVE_SEGMENTATION_TOKENIZER':str(ROOT/'third_party/live_segmentation/tokenizer'),
 'LD_LIBRARY_PATH':':'.join(libs+[os.environ.get('LD_LIBRARY_PATH','')]),'HYMT2_SEED':'42'}
boundary_backend=os.environ.get('LIVE_BOUNDARY_BACKEND','sat')
if boundary_backend not in ('sat','qwen3.5-27b'):
 raise ValueError('LIVE_BOUNDARY_BACKEND must be sat or qwen3.5-27b')
for name in ('LIVE_REPLAY_BOUNDARY','LIVE_REPLAY_MODE','LIVE_REPLAY_START_SECONDS',
             'LIVE_REPLAY_SECONDS','LIVE_TEST_MODE','LIVE_TEST_SECONDS',
             'LIVE_TEST_START_SECONDS','LIVE_REBUILD_ADAPTERS','LIVE_REFRESH_CODE',
             'LIVE_QWEN_MODEL'):
 environment.pop(name,None)
environment['LIVE_TRANSLATION_BACKEND']='hymt2-1.8b'
environment['LIVE_BOUNDARY_BACKEND']=boundary_backend
if boundary_backend == 'qwen3.5-27b':
 environment['LIVE_QWEN_MODEL']=str(ROOT/'third_party/live_translation/qwen35-27b')
environment.pop('LIVE_SYNTAX_MODEL',None)
logs=ROOT/'temp/browser-live';logs.mkdir(exist_ok=True,parents=True)
if (logs/'service.log').exists():
 shutil.copyfile(logs/'service.log',logs/f'service-previous-{int(time.time())}.log')
with (logs/'service.log').open('w') as stream:
 service=subprocess.Popen([sys.executable,'-m','live_dubbing.server'],cwd=ROOT,env=environment,
  stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
(logs/'service-process.json').write_text(json.dumps({'pid':service.pid,'port':8766,'host':'127.0.0.1'}))
# A deliberately tiny read-only fixture origin; never expose project/model dirs.
fixture=ROOT/'temp/browser-fixture';fixture.mkdir(exist_ok=True)
fixture_video=fixture/'test.mp4'
if fixture_video.is_symlink() and not fixture_video.exists():
 fixture_video.unlink()
if not fixture_video.exists() and (ROOT/'test_multispeaker.mp4').is_file():
 fixture_video.symlink_to(ROOT/'test_multispeaker.mp4')
(fixture/'index.html').write_text('''<!doctype html><title>Permitted multi-speaker test fixture</title>
<style>body{background:#121827;color:#fff;font:20px sans-serif;padding:24px}video{width:1100px;max-width:95vw}button{font-size:24px;padding:12px}</style>
<h1>Multi-speaker development fixture</h1><p>Existing approved project source. Play normally, then capture this tab.</p>
<video controls preload="metadata" src="test.mp4"></video>''')
fixture_pid=None
registry=logs/'fixture-process.json'
if registry.exists():
 import psutil
 previous=json.loads(registry.read_text())['pid']
 try:
  command=psutil.Process(previous).cmdline()
  if 'http.server' in command and str(fixture) in command:fixture_pid=previous
 except psutil.NoSuchProcess:pass
if fixture_pid is None:
 with (logs/'fixture.log').open('w') as stream:
  source=subprocess.Popen([sys.executable,'-m','http.server','8000','--bind','127.0.0.1','--directory',str(fixture)],
    stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
 fixture_pid=source.pid
 registry.write_text(json.dumps({'pid':fixture_pid,'port':8000,'host':'127.0.0.1'}))
print(json.dumps({'service_pid':service.pid,'fixture_pid':fixture_pid,
 'translation_backend':environment['LIVE_TRANSLATION_BACKEND'],
 'boundary_backend':environment['LIVE_BOUNDARY_BACKEND'],
 'models_loaded':False,'capture_started':False}))
