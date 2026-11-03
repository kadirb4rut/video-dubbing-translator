"""Offline, isolated CPU sentence-boundary worker. No implicit downloads."""
from __future__ import annotations

from collections import deque
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import uuid

ROOT = Path(__file__).resolve().parents[1]
MODEL_REVISION = '137da054051ad9f1eac42025f758db4ac9f22535'


def paths():
    base = ROOT / 'third_party' / 'live_segmentation'
    return (Path(os.environ.get('LIVE_SEGMENTATION_PYTHON', str(base / '.venv' /
            ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')))),
            Path(os.environ.get('LIVE_SEGMENTATION_MODEL', str(base / 'sat-3l-sm'))),
            Path(os.environ.get('LIVE_SEGMENTATION_TOKENIZER', str(base / 'tokenizer'))))


def installed():
    python, model, tokenizer = paths()
    return (python.is_file() and (model / 'model_optimized.onnx').is_file()
            and (model / 'config.json').is_file() and (tokenizer / 'tokenizer.json').is_file())


class BoundaryClient:
    def __init__(self):
        if not installed():
            raise RuntimeError('Live segmentation setup required: run scripts/setup_live_segmentation.py explicitly. No files were downloaded.')
        python, model, tokenizer = paths()
        self.process = subprocess.Popen([str(python), str(Path(__file__).with_name('segmentation_worker.py')),
            '--model', str(model), '--tokenizer', str(tokenizer)], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1,
            env={**os.environ, 'HF_HUB_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                 'TOKENIZERS_PARALLELISM': 'false'})
        self.responses = queue.Queue(maxsize=4)
        self.errors = deque(maxlen=8)
        self.lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()
        try:
            self.predict('')  # Load before user capture starts.
        except Exception:
            self.close()
            raise

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())

    def predict(self, text):
        if not isinstance(text, str) or len(text) > 8000:
            raise ValueError('Invalid bounded segmentation input')
        with self.lock:
            identifier = uuid.uuid4().hex
            self.process.stdin.write(json.dumps({'id': identifier, 'text': text}, ensure_ascii=False) + '\n')
            self.process.stdin.flush()
            try:
                line = self.responses.get(timeout=20)
            except queue.Empty as exc:
                self.close()
                raise RuntimeError('Local segmentation worker timed out') from exc
            if line is None:
                raise RuntimeError('Local segmentation worker exited: ' + '\n'.join(self.errors))
            value = json.loads(line)
            scores = value.get('scores')
            if (value.get('id') != identifier or not value.get('ok') or not isinstance(scores, list)
                    or len(scores) != len(text) or any(type(p) not in (float, int)
                    or not math.isfinite(p) or not 0 <= p <= 1 for p in scores)):
                raise RuntimeError('Invalid local segmentation response')
            return tuple(scores)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
