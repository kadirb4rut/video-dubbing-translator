"""Explicit development ASR cache upgrade after real 'tasks' -> 'TARS' errors.

Only Colab disk is used. This does not rerun completed inference gates or
change the consumer's installed model without explicit setup.
"""
from pathlib import Path
import subprocess,sys
root=Path('/content/video-dubbing-translator-main')
script=root/'prepare-accuracy-asr.py'
script.write_text('''from pathlib import Path
import json,traceback
root=Path('/content/video-dubbing-translator-main')
try:
 from huggingface_hub import snapshot_download,model_info
 repo='Systran/faster-whisper-medium'
 revision=model_info(repo).sha
 snapshot_download(repo,revision=revision,local_dir='/content/faster-whisper-medium',allow_patterns=['model.bin','*.json','*.txt'])
 result={'passed':True,'repo':repo,'revision':revision,'path':'/content/faster-whisper-medium','downloads_on':'Colab disk only'}
except Exception as exc:
 result={'passed':False,'error':str(exc),'traceback':traceback.format_exc()}
(root/'temp/asr-accuracy-setup.json').write_text(json.dumps(result,indent=2))
''')
with (root/'temp/asr-accuracy-setup.log').open('w') as log:
 p=subprocess.Popen([sys.executable,str(script)],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
print({'download_pid':p.pid,'model':'faster-whisper-medium','location':'Colab only'})
