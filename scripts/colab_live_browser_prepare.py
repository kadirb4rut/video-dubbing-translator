"""Development-only Chrome/Xvfb preparation on the existing Colab VM.

No capture, browser launch, public listener or inference is started here.
Package installations execute in a separate process to leave the warm model
kernel untouched. All browser binaries remain on the Colab disk.
"""
from pathlib import Path
import json
import subprocess
import sys

ROOT = Path('/content/video-dubbing-translator-main')
script = ROOT / 'prepare-browser-worker.py'
script.write_text('''from pathlib import Path
import json,subprocess,sys,traceback,urllib.request
root=Path('/content/video-dubbing-translator-main')
def run(argv):
 subprocess.run(argv,check=True,timeout=600)
try:
 run(['apt-get','update','-qq'])
 run(['apt-get','install','-y','-qq','xvfb','x11vnc','novnc','pulseaudio','fonts-liberation'])
 chrome=Path('/content/google-chrome-stable.deb')
 urllib.request.urlretrieve('https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb',chrome)
 run(['apt-get','install','-y','-qq',str(chrome)])
 run([sys.executable,'-m','pip','install','-q','playwright','websockify'])
 version=subprocess.check_output(['google-chrome','--version'],text=True).strip()
 result={'passed':True,'chrome':version,'capture_started':False,'public_listener':False}
except Exception as error:
 result={'passed':False,'error':str(error),'traceback':traceback.format_exc()}
(root/'browser-prepare-result.json').write_text(json.dumps(result,indent=2))
''')
log = (ROOT / 'browser-prepare.log').open('w')
process = subprocess.Popen([sys.executable, str(script)], stdout=log, stderr=subprocess.STDOUT)
(ROOT / 'browser-prepare-pid.json').write_text(json.dumps({'pid': process.pid}))
print(json.dumps({'browser_prepare_pid': process.pid, 'log': str(ROOT / 'browser-prepare.log')}))
