"""Check the real development Chrome sandbox without disabling protections."""
from pathlib import Path
import json
import os
import pwd
import signal
import subprocess
import time

root=Path('/content/video-dubbing-translator-main')
try:
    user=pwd.getpwnam('live-browser')
except KeyError:
    subprocess.run(['useradd','--create-home','--shell','/bin/bash','live-browser'],check=True)
    user=pwd.getpwnam('live-browser')
command=['runuser','-u','live-browser','--','google-chrome','--headless',
    '--no-first-run','--disable-dev-shm-usage',
    '--user-data-dir=/home/live-browser/sandbox-probe','--dump-dom','about:blank']
log=(root/'browser-sandbox-probe.log').open('w')
started=time.perf_counter()
process=subprocess.Popen(command,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
try:
    code=process.wait(timeout=25)
except subprocess.TimeoutExpired:
    os.killpg(process.pid,signal.SIGTERM)
    code=process.wait(timeout=5)
log.close()
text=(root/'browser-sandbox-probe.log').read_text()
result={'passed':code==0 and '<html' in text,'exit_code':code,
        'wall_seconds':time.perf_counter()-started,'command':command,
        'sandbox_disabled':False,'stdout_stderr':text[-7000:]}
(root/'browser-sandbox-probe.json').write_text(json.dumps(result,indent=2))
print(json.dumps(result))
