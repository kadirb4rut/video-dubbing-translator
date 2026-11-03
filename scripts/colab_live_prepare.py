"""Development only: prepare one G4 session. Never used by the consumer UI."""
from pathlib import Path
import json
import subprocess
import sys
import tarfile

root = Path('/content/video-dubbing-translator-main')
root.mkdir(exist_ok=True)
with tarfile.open('/content/live-source.tar.gz') as archive:
    for member in archive.getmembers():
        target = (root / member.name).resolve()
        if not target.is_relative_to(root) or member.issym() or member.islnk():
            raise ValueError('Unsafe source archive entry')
    archive.extractall(root)
result = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
    'voxcpm==2.0.3', 'faster-whisper==1.2.1', 'pyannote.audio==4.0.7',
    'transformers==5.20.2', 'aiohttp>=3.10,<4', 'soundfile', 'sentencepiece'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
(root / 'dependency-install.log').write_text(result.stdout)
if result.returncode:
    raise RuntimeError('Colab dependency installation failed; inspect dependency-install.log')
# Colab can leave NumPy 2.1.3 import files beside NumPy 2.2.6 metadata after
# dependency resolution. That mixed install breaks librosa -> SciPy imports
# used by VoxCPM2 prompt-cache setup. Reinstall the verified wheel coherently.
numpy_fix = subprocess.run([sys.executable, '-m', 'pip', 'install', '-q',
    '--force-reinstall', '--no-deps', 'numpy==2.2.6'], stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, text=True, timeout=300)
with (root / 'dependency-install.log').open('a') as stream:
    stream.write('\nNumPy consistency repair:\n' + numpy_fix.stdout)
if numpy_fix.returncode:
    raise RuntimeError('Colab NumPy consistency repair failed; inspect dependency-install.log')
audio_probe = subprocess.run([sys.executable, '-c',
    'import numpy, scipy.signal, librosa; assert numpy.__version__ == "2.2.6"'],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=60)
if audio_probe.returncode:
    with (root / 'dependency-install.log').open('a') as stream:
        stream.write('\nNumPy/audio import probe failed:\n' + audio_probe.stdout)
    raise RuntimeError('Colab NumPy/librosa/SciPy probe failed; inspect dependency-install.log')
from huggingface_hub import snapshot_download
from concurrent.futures import ThreadPoolExecutor
requests = [
    ('openbmb/VoxCPM2', '32279effe8c19989596f05d353d1447f51d9e915', '/content/voxcpm2',
     ['audiovae.pth', 'config.json', 'model.safetensors', '*.json', 'tokenization_voxcpm2.py']),
    ('tencent/Hy-MT2-1.8B', '9a341cd1b679d3efd23b46e847b01745a71ed792', '/content/hymt2-1.8b',
     ['*.safetensors', '*.json', '*.jinja']),
    ('Systran/faster-whisper-small', None, '/content/faster-whisper-small',
     ['model.bin', '*.json', '*.txt']),
]
def download(item):
    repo, revision, local, patterns = item
    snapshot_download(repo, revision=revision, local_dir=local, allow_patterns=patterns)
    return {'repo': repo, 'revision': revision, 'path': local}
with ThreadPoolExecutor(max_workers=3) as pool:
    models = list(pool.map(download, requests))
(root / 'live-model-setup.json').write_text(json.dumps({'models': models, 'downloads_on': 'Colab disk only'}, indent=2))
print(json.dumps({'prepared': True, 'root': str(root), 'models': models}))
