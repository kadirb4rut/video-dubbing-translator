"""Persistent JSON-lines speaker worker in the isolated Community-1 environment."""
from __future__ import annotations

import argparse
import base64
import contextlib
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--model', required=True)
    parser.add_argument('--device', default='cpu')
    args = parser.parse_args()
    model = None
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if request.get('operation') == 'shutdown':
                break
            with contextlib.redirect_stdout(sys.stderr):
                from live_dubbing.speakers import PyannoteOnlineModels
                if model is None:
                    model = PyannoteOnlineModels(args.model, args.device)
                if request.get('operation') == 'prepare':
                    result = {'prepared': True}
                else:
                    pcm = base64.b64decode(request['pcm'], validate=True)
                    if not pcm or len(pcm) > 32_000 * 20 or len(pcm) % 2:
                        raise ValueError('Invalid bounded speaker audio')
                    result = model.analyze(pcm)
            response = {'id': request.get('id'), 'ok': True, **result}
        except Exception as exc:
            response = {'id': request.get('id'), 'ok': False, 'error': f'{type(exc).__name__}: {exc}'}
        print(json.dumps(response), flush=True)


if __name__ == '__main__':
    main()
