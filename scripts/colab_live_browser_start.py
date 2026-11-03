"""Development desktop on Colab: sandboxed Chrome, private X11, loopback VNC.

Access is through an authenticated SSH tunnel. WebSocket requests additionally
require the exact viewer Origin, preventing cross-site WebSocket access.
No browser security or protected-media restrictions are disabled.
"""
from pathlib import Path
import json
import os
import pwd
import secrets
import subprocess
import time

ROOT=Path('/content/video-dubbing-translator-main')
probe=json.loads((ROOT/'browser-sandbox-probe.json').read_text())
if not probe['passed'] or probe['sandbox_disabled']:
    raise RuntimeError('Sandboxed Chrome probe must pass first')
user=pwd.getpwnam('live-browser')
home=Path(user.pw_dir)
runtime=home/'runtime'
runtime.mkdir(mode=0o700,exist_ok=True)
os.chown(runtime,user.pw_uid,user.pw_gid)
xauth=home/'.Xauthority'
subprocess.run(['runuser','-u','live-browser','--','xauth','-f',str(xauth),
                'add',':99','.',secrets.token_hex(16)],check=True,capture_output=True)
environment={**os.environ,'DISPLAY':':99','XAUTHORITY':str(xauth),
             'XDG_RUNTIME_DIR':str(runtime),'PULSE_SINK':'live_test'}
logs=ROOT/'temp/browser-desktop'
logs.mkdir(parents=True,exist_ok=True)
processes={}
def start(name,args,as_user=True):
    log=(logs/(name+'.log')).open('w')
    command=(['runuser','-u','live-browser','--'] if as_user else [])+args
    process=subprocess.Popen(command,env=environment,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    processes[name]={'pid':process.pid,'command':command}
    return process

xvfb=start('xvfb',['Xvfb',':99','-screen','0','1440x1000x24','-nolisten','tcp','-auth',str(xauth)])
deadline=time.monotonic()+10
while not Path('/tmp/.X11-unix/X99').exists():
    if xvfb.poll() is not None or time.monotonic()>deadline:
        raise RuntimeError('Private Xvfb failed; see desktop log')
    time.sleep(.1)
subprocess.run(['runuser','-u','live-browser','--','pulseaudio','--start','--exit-idle-time=-1'],env=environment,check=True,capture_output=True)
subprocess.run(['runuser','-u','live-browser','--','pactl','load-module','module-null-sink',
                'sink_name=live_test','sink_properties=device.description=Live_Dubbing_Test'],
               env=environment,check=True,capture_output=True)
start('vnc',['x11vnc','-localhost','-display',':99','-auth',str(xauth),
             '-forever','-shared','-rfbport','5900','-nopw'])
auth=ROOT/'loopback_vnc_auth.py'
auth.write_text('''from urllib.parse import urlparse
from websockify.auth_plugins import AuthenticationError
class ViewerOrigin:
 def __init__(self,src=None):self.origin=src
 def authenticate(self,headers,target_host,target_port):
  if headers.get('Host')!=urlparse(self.origin).netloc:raise AuthenticationError(response_code=403)
  if headers.get('Upgrade','').lower()=='websocket':
   if headers.get('Origin')!=self.origin:raise AuthenticationError(response_code=403)
  elif headers.get('Sec-Fetch-Site') not in (None,'none','same-origin') and not (
   headers.get('Sec-Fetch-Mode')=='navigate' and headers.get('Sec-Fetch-Dest')=='document'):
   raise AuthenticationError(response_code=403)
''')
environment['PYTHONPATH']=str(ROOT)
start('novnc',['python','-m','websockify','--web','/usr/share/novnc','--web-auth',
    '--auth-plugin','loopback_vnc_auth.ViewerOrigin','--auth-source','http://127.0.0.1:16080',
    '127.0.0.1:6080','127.0.0.1:5900'],as_user=False)
extension=ROOT/'browser_extension/live_dubbing'
if not (extension/'manifest.json').is_file():
    raise RuntimeError('The local live-dubbing extension is missing')
chrome=start('chrome',['google-chrome','--no-first-run','--no-default-browser-check',
    '--disable-dev-shm-usage','--user-data-dir=/home/live-browser/live-profile',
    f'--disable-extensions-except={extension}',f'--load-extension={extension}',
    '--remote-debugging-address=127.0.0.1','--remote-debugging-port=9222',
    '--window-size=1440,1000','about:blank'])
time.sleep(2)
if chrome.poll() is not None:
    raise RuntimeError('Chrome exited; no sandbox-disabling fallback will be used')
(logs/'processes.json').write_text(json.dumps(processes,indent=2))
print(json.dumps({'desktop':'started','processes':processes,
    'viewer_remote_bind':'127.0.0.1:6080','viewer_local_port':16080,
    'sandbox_disabled':False,'capture_started':False}))
