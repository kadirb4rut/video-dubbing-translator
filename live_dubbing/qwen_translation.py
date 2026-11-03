"""Explicit optional Live MT backend; offline Hy-MT2 selection is unchanged."""
from collections import deque
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import uuid

NAME = 'qwen3.5-27b'
REVISION = 'fc05daec18b0a78c049392ed2e771dde82bdf654'
ROOT = Path(__file__).resolve().parents[1]


class InvalidModelResponse(RuntimeError):
    """A completed model request whose output failed the strict contract."""


def selected_backend():
    value = os.environ.get('LIVE_TRANSLATION_BACKEND', 'hymt2-1.8b')
    if value not in ('hymt2-1.8b', NAME):
        raise ValueError('Unsupported LIVE_TRANSLATION_BACKEND')
    return value


def selected_boundary_backend(translation_backend=None):
    """Choose the optional contextual boundary proposer independently of MT.

    Existing Qwen-as-translator sessions retain their previous contextual
    boundary behavior. Hy-MT2 sessions stay on SaT unless the user explicitly
    opts into the local Qwen boundary proposer.
    """
    translation_backend = translation_backend or selected_backend()
    value = os.environ.get('LIVE_BOUNDARY_BACKEND')
    if value is None:
        return NAME if translation_backend == NAME else 'sat'
    if value not in ('sat', NAME):
        raise ValueError('Unsupported LIVE_BOUNDARY_BACKEND')
    return value


def model_path():
    return Path(os.environ.get('LIVE_QWEN_MODEL', str(ROOT / 'third_party/live_translation/qwen35-27b')))


def installed():
    """Filesystem readiness only: no inference, network or implicit download."""
    root = model_path()
    try:
        config = json.loads((root / 'config.json').read_text())
        index = json.loads((root / 'model.safetensors.index.json').read_text())
        shards = set(index['weight_map'].values())
        return bool(config['model_type'] == 'qwen3_5' and shards and
            (root / 'tokenizer.json').is_file() and all(
                isinstance(name, str) and Path(name).name == name and
                (root / name).is_file() and (root / name).stat().st_size > 0 for name in shards))
    except (OSError, ValueError, KeyError, TypeError):
        return False


class QwenTranslator:
    name = NAME

    def __init__(self, device='cuda'):
        if not installed():
            raise RuntimeError('Qwen Live MT is not installed at LIVE_QWEN_MODEL. Set it up explicitly; no files were downloaded.')
        self._statistics = threading.local()
        self.last_generation = {}
        self.responses = queue.Queue(maxsize=4)
        self.errors = deque(maxlen=12)
        self.lock = threading.Lock()
        python = os.environ.get('LIVE_TRANSLATION_PYTHON', sys.executable)
        self.process = subprocess.Popen([python, str(Path(__file__).with_name('qwen_translation_worker.py')),
            '--model', str(model_path()), '--device', device], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', bufsize=1,
            env={**os.environ, 'HF_HUB_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1'})
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    @property
    def last_generation(self):
        return getattr(self._statistics, 'value', {})

    @last_generation.setter
    def last_generation(self, value):
        self._statistics.value = value

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())

    def _request(self, operation, **payload):
        with self.lock:
            identifier = uuid.uuid4().hex
            if self.process.poll() is not None:
                raise RuntimeError('Qwen Live MT worker exited: ' + '\n'.join(self.errors))
            self.process.stdin.write(json.dumps({'id':identifier, 'operation':operation, **payload}, ensure_ascii=False)+'\n')
            self.process.stdin.flush()
            try:
                line = self.responses.get(timeout=90)
            except queue.Empty as exc:
                self.close()
                raise RuntimeError('Qwen Live MT request exceeded 90 seconds') from exc
            if line is None:
                raise RuntimeError('Qwen Live MT worker stopped: ' + '\n'.join(self.errors))
            value = json.loads(line)
            self.last_generation = value.get('stats', {})
            if value.get('id') != identifier or not value.get('ok'):
                if value.get('id') == identifier and value.get('error_code') == 'invalid_model_response':
                    self.last_generation = {**self.last_generation, 'rejected_response':{
                        'operation':operation, 'error':value.get('error'),
                        'decoded_response':value.get('decoded_response')}}
                    raise InvalidModelResponse(value.get('error', 'Invalid Live model response'))
                raise RuntimeError(value.get('error', 'Qwen Live MT response mismatch'))
            return value['text']

    def prepare(self):
        self._request('prepare')
        if not self.last_generation.get('prepared'):
            raise RuntimeError('Qwen Live MT did not confirm model preparation')

    def select_boundary(self, units, eligible_ends, preferred_end_unit):
        try:
            return json.loads(self._request('select_boundary', source_units=list(units),
                eligible_ends=list(eligible_ends), preferred_end_unit=preferred_end_unit))['end_unit']
        except InvalidModelResponse:
            # An optional proposal may be invalid without invalidating source
            # audio. Wait for new evidence; never coerce it into an endpoint.
            return 0

    def assess_source_boundary(self, prefix, continuation):
        try:
            return json.loads(self._request('assess_boundary', prefix=prefix,
                continuation=continuation))['crosses_dependency']
        except InvalidModelResponse:
            # Never treat an incomplete or malformed optional check as safe.
            return None

    def assess_simultaneous_candidate(self, prefix, continuation, target_text):
        """Independently check both cut safety and meaning for one exact chunk."""
        try:
            return json.loads(self._request('assess_candidate', prefix=prefix,
                continuation=continuation, target_text=target_text))
        except InvalidModelResponse:
            return None

    def assess_translation_support(self, source_text, target_text):
        """Check whether one complete translation is grounded in its exact owner."""
        try:
            return json.loads(self._request('assess_translation_support',
                source_text=source_text, target_text=target_text))
        except InvalidModelResponse:
            return None


    def assess_qa_difference(self, expected, recovered):
        minor = json.loads(self._request('assess_qa', expected_text=expected,
            recovered_text=recovered))['minor']
        return {'minor':minor, 'generation':dict(self.last_generation)}

    def translate(self, text, source_language, target_language, *, context=None, glossary=None):
        from translation.backends import LANGUAGE_NAMES
        return self._request('translate', source_text=text,
            source_language=LANGUAGE_NAMES.get(source_language, source_language or 'Automatic'),
            target_language=LANGUAGE_NAMES.get(target_language, target_language),
            previous_source=(getattr(context, 'previous_text', None) or '')[-800:],
            # Lookahead selects source boundaries; it is not owned content
            # for this translation request and must not be copied into speech.
            following_source='',
            preferred_duration_seconds=getattr(context, 'preferred_duration_seconds', None),
            glossary=dict(glossary or {}))

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        for stream in (self.process.stdin, self.process.stdout, self.process.stderr):
            stream.close()
