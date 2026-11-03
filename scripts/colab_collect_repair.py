"""Collect only small regression evidence, never model files."""
import json
from pathlib import Path
import tarfile
import torch

OUT = Path('/content/regression-evidence')
run = Path(json.loads((OUT/'full-validation.json').read_text())['run_root'])
model_metadata = {}
for name, folder in [('VoxCPM2','/content/voxcpm2'), ('Hy-MT2-1.8B','/content/hymt2-1.8b')]:
    folder = Path(folder)
    metadata = folder/'.cache/huggingface/download/model.safetensors.metadata'
    model_metadata[name] = {'path':str(folder), 'weights_bytes':(folder/'model.safetensors').stat().st_size,
        'download_metadata':metadata.read_text().splitlines()[:2] if metadata.exists() else None}
(OUT/'colab-readiness.json').write_text(json.dumps({'passed':torch.cuda.is_available(), 'gpu':torch.cuda.get_device_name(0),
    'models':model_metadata, 'real_inference_validation':str(OUT/'full-validation.json')}, indent=2))
with tarfile.open(OUT/'final-small-evidence.tar.gz','w:gz') as archive:
    for path in OUT.iterdir():
        if path.is_file() and path.suffix in ('.json','.log','.png'):
            archive.add(path, arcname='evidence/'+path.name)
with tarfile.open(OUT/'selected-audio.tar.gz','w:gz') as archive:
    for folder in ('wav','references'):
        for path in (run/folder).iterdir():
            if path.is_file():
                archive.add(path, arcname='repaired/'+folder+'/'+path.name)
    for name in ('qa-initial.wav','qa-regenerated.wav','bad_group_8.wav','good_group_8.wav','repaired_group_8.wav'):
        archive.add(OUT/name, arcname='evidence/'+name)
print(json.dumps({'passed':True,'small_evidence_bytes':(OUT/'final-small-evidence.tar.gz').stat().st_size,
    'selected_audio_bytes':(OUT/'selected-audio.tar.gz').stat().st_size, 'model_weights_downloaded_to_client':False}))
