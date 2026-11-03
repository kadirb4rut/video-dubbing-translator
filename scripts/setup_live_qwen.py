"""Explicit optional Live Qwen model setup. Status never downloads or loads AI."""
import argparse
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live_dubbing.qwen_translation import REVISION, installed, model_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--model-dir', type=Path, default=model_path())
    args = parser.parse_args()
    os.environ['LIVE_QWEN_MODEL'] = str(args.model_dir)
    if args.download:
        from huggingface_hub import HfApi, snapshot_download
        args.model_dir.mkdir(parents=True, exist_ok=True)
        info = HfApi().model_info('Qwen/Qwen3.5-27B', revision=REVISION, files_metadata=True)
        size = sum(item.size or 0 for item in info.siblings if item.rfilename.endswith('.safetensors')
            and (not (args.model_dir / item.rfilename).is_file() or
                 (args.model_dir / item.rfilename).stat().st_size != item.size))
        if shutil.disk_usage(args.model_dir).free < size + 10*1024**3:
            raise RuntimeError('Qwen Live setup needs its 56 GB weights plus 10 GiB free reserve')
        snapshot_download(info.id, revision=REVISION, local_dir=str(args.model_dir),
            allow_patterns=['*.safetensors', '*.json', '*.jinja', '*.txt', 'LICENSE', 'README.md'],
            max_workers=2)
        (args.model_dir / 'live_model_manifest.json').write_text(json.dumps({
            'repo':info.id, 'revision':REVISION, 'license':'apache-2.0'}))
    print(json.dumps({'installed':installed(), 'model_path':str(args.model_dir),
        'explicit_download_requested':args.download,
        'runtime':'Select a prepared Transformers 5.20+ environment with LIVE_TRANSLATION_PYTHON; main Vox environment is not modified.'}))


if __name__ == '__main__':
    main()
