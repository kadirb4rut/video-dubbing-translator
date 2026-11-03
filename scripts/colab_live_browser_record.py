"""Bounded recording and resource sampling of actual development playback.

Capture the visible local test player, not browser accounts or profiles. This
adds no source/audio simulation and does not touch media/player internals.
"""
from pathlib import Path
import json,os,subprocess,sys,time
root=Path('/content/video-dubbing-translator-main')
output=root/'temp'/f'browser-playback-{int(time.time())}'
output.mkdir()
os.chmod(output,0o777)
env={**os.environ,'DISPLAY':':99','XAUTHORITY':'/home/live-browser/.Xauthority',
 'XDG_RUNTIME_DIR':'/home/live-browser/runtime',
 'PULSE_SERVER':'unix:/home/live-browser/runtime/pulse/native'}
log=(output/'recording.log').open('w')
command=['runuser','-u','live-browser','--','ffmpeg','-nostdin','-y','-v','warning',
 '-f','x11grab','-video_size','1440x900','-framerate','15','-i',':99+0,100',
 '-f','pulse','-i','live_test.monitor','-t','70','-c:v','libx264','-preset','ultrafast',
 '-crf','28','-pix_fmt','yuv420p','-c:a','aac','-b:a','96k','-movflags','+faststart',str(output/'actual-playback.mp4')]
p=subprocess.Popen(command,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
(output/'process.json').write_text(json.dumps({'pid':p.pid,'bounded_seconds':70,'command':command,
 'source':'actual Xvfb video + actual PulseAudio monitor'}))
sampler=output/'sample.py'
sampler.write_text('''from pathlib import Path
import json,time,subprocess,psutil
root=Path(__file__).parent
start=time.perf_counter()
with (root/'resources.jsonl').open('w') as stream:
 while time.perf_counter()-start<75:
  gpu=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,memory.used','--format=csv,noheader,nounits'],text=True).strip()
  stream.write(json.dumps({'monotonic':time.perf_counter(),'cpu_percent':psutil.cpu_percent(),
   'system_used_memory_bytes':psutil.virtual_memory().used,'gpu_utilization_percent_and_memory_mib':gpu})+'\\n');stream.flush()
  time.sleep(1)
''')
with (output/'sampler.log').open('w') as stream:
 resource=subprocess.Popen([sys.executable,str(sampler)],stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
(output/'sampler-process.json').write_text(json.dumps({'pid':resource.pid}))
(root/'temp/browser-playback-latest.txt').write_text(str(output))
print(json.dumps({'recording_directory':str(output),'recording_pid':p.pid,'sampler_pid':resource.pid}))
