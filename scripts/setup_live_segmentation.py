"""Explicit setup for the shared multilingual CPU boundary model.

The main VoxCPM/Whisper environment is never upgraded. Run --download only on
the inference computer. Merely launching the app/status/help downloads nothing.
"""
from pathlib import Path
import argparse
import json
import os
import shutil
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from live_dubbing.segmentation import MODEL_REVISION, installed, paths


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--install-runtime',action='store_true',help='Create an isolated wtpsplit 2.2.2 + ONNX CPU runtime')
    parser.add_argument('--download',action='store_true',help='Download ~442 MB model/tokenizer to this computer explicitly')
    args=parser.parse_args()
    python,model,tokenizer=paths()
    if args.install_runtime:
        environment=python.parent.parent
        uv=shutil.which('uv')
        if uv:
            if not python.is_file():
                subprocess.run([uv,'venv','--python',sys.executable,str(environment)],check=True)
            subprocess.run([uv,'pip','install','--python',str(python),
                'wtpsplit==2.2.2','onnxruntime>=1.20,<2'],check=True)
        else:
            if not python.is_file():
                venv.EnvBuilder(with_pip=True).create(environment)
            subprocess.run([str(python),'-m','pip','install',
                'wtpsplit==2.2.2','onnxruntime>=1.20,<2'],check=True)
    if args.download:
        # Execute downloads in the isolated environment, never install into the
        # main ASR/Vox stack and never choose a user's external model directory.
        if not python.is_file():
            parser.error('Install the isolated runtime first with --install-runtime')
        code='''import json,sys
from huggingface_hub import snapshot_download
model,tokenizer,revision=json.loads(sys.stdin.read())
snapshot_download('segment-any-text/sat-3l-sm',revision=revision,local_dir=model,allow_patterns=['config.json','model_optimized.onnx'])
snapshot_download('FacebookAI/xlm-roberta-base',revision='e73636d4f797dec63c3081bb6ed5c7b0bb3f2089',local_dir=tokenizer,allow_patterns=['config.json','tokenizer.json','tokenizer_config.json','special_tokens_map.json','sentencepiece.bpe.model'])
'''
        download_env = os.environ.copy()
        # --download is an explicit network request, even when a running
        # offline inference service set HF_HUB_OFFLINE for its own workers.
        download_env.pop('HF_HUB_OFFLINE', None)
        subprocess.run([str(python),'-c',code],
            input=json.dumps([str(model),str(tokenizer),MODEL_REVISION]),
            text=True,check=True,env=download_env)
    print(json.dumps({'installed':bool(installed()),'python':str(python),'model':str(model),
        'tokenizer':str(tokenizer),'model_revision':MODEL_REVISION,'implicit_downloads':False}))
    return 0


if __name__=='__main__':
    raise SystemExit(main())
