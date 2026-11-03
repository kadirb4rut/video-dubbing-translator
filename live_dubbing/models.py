"""Local model lifecycle and pre-playback streaming QA. No network downloads."""
from __future__ import annotations

import base64
from collections import deque
from dataclasses import asdict, dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import sys
import threading
import time
import uuid

import numpy as np

from .speech import IncrementalWhisper, SileroStream
from .speakers import (SpeakerChoice, SpeakerTracker, bounded_speaker_windows,
                       combine_speaker_window_results, segment_reference_fallback)
from .inference_scheduler import PriorityInferenceScheduler

RUNTIME_SOURCE_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def reference_leak_terms(reference_text, target_text, recovered_text):
    """Find extra recovered words also present in the speaker transcript.

    This is a narrow signal of reference continuation, not semantic proof.
    Decisions using it also require a failed first candidate and a successful
    bounded reference-only regeneration of the same source and speaker.
    """
    import re
    import unicodedata
    from collections import Counter
    def tokens(text):
        text = unicodedata.normalize('NFC', text or '').casefold()
        return [word for word in re.findall(r'[^\W\d_]+', text) if len(word) >= 4]
    reference = set(tokens(reference_text))
    extra = Counter(tokens(recovered_text)) - Counter(tokens(target_text))
    return sorted(word for word in extra if word in reference)


def live_source_entities(text):
    """Avoid treating a caption-style uppercase sentence as a list of acronyms."""
    import re
    from engine.context import extract_entities
    words = re.findall(r"[^\W\d_]+", text)
    uppercase_sentence = len(words) >= 8 and all(word.isupper() for word in words)
    entities = extract_entities(text)
    if uppercase_sentence:
        entities = [(kind, value) for kind, value in entities if kind != 'acronym']
    return entities, uppercase_sentence


class SpeakerClient:
    def __init__(self, device='cpu', *, inference_scheduler=None):
        from diarization.model_manager import runtime_python, MODEL_DIR
        python = os.environ.get('LIVE_SPEAKER_PYTHON') or str(runtime_python())
        model = os.environ.get('LIVE_SPEAKER_MODEL') or str(MODEL_DIR)
        self.inference_scheduler = inference_scheduler
        self.process = subprocess.Popen([python, str(Path(__file__).with_name('speaker_worker.py')),
            '--model', model, '--device', device], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1,
            env={**os.environ, 'HF_HUB_OFFLINE':'1', 'HF_HUB_DISABLE_TELEMETRY':'1',
                 'PYANNOTE_METRICS_ENABLED':'0'})
        self.responses = queue.Queue(maxsize=4)
        self.errors = deque(maxlen=8)
        threading.Thread(target=self._read, daemon=True).start()
        threading.Thread(target=self._stderr, daemon=True).start()

    def _read(self):
        for line in self.process.stdout:
            self.responses.put(line)
        self.responses.put(None)

    def _stderr(self):
        for line in self.process.stderr:
            self.errors.append(line.rstrip())

    def _request(self, payload):
        identifier = uuid.uuid4().hex
        self.process.stdin.write(json.dumps({'id': identifier, **payload}) + '\n')
        self.process.stdin.flush()
        try:
            line = self.responses.get(timeout=90)
            if line is None:
                raise RuntimeError('Speaker worker exited: ' + '\n'.join(self.errors))
            result = json.loads(line)
        except queue.Empty as exc:
            self.close()
            raise RuntimeError('Online speaker worker timed out; no speech was committed') from exc
        if result.get('id') != identifier or not result.get('ok'):
            raise RuntimeError(result.get('error', 'Speaker worker response mismatch'))
        return result

    def analyze(self, pcm):
        windows = bounded_speaker_windows(pcm)
        results = []
        for window in windows:
            payload = {'pcm': base64.b64encode(window).decode()}
            if self.inference_scheduler is None:
                results.append(self._request(payload))
            else:
                # Re-enter the shared GPU scheduler for each bounded window,
                # allowing urgent source ASR and approved TTS between windows.
                with self.inference_scheduler.lease('qa'):
                    results.append(self._request(payload))
        if len(results) == 1:
            return results[0]
        combined = combine_speaker_window_results(results, len(pcm) / 32000)
        combined['analysis_window_overlap_seconds'] = 1.0
        return combined

    def prepare(self):
        return self._request({'operation': 'prepare'})

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


@dataclass
class RenderedPhrase:
    identifier: str
    source_start: float
    source_end: float
    source_text: str
    target_text: str
    speaker: dict
    pcm: bytes
    chunk_sizes: list[int]
    metrics: dict
    candidates: list[dict]


def native_compile_requested(device):
    value = os.environ.get('LIVE_VOXCPM_COMPILE', '0')
    if value not in ('0', '1'):
        raise ValueError('LIVE_VOXCPM_COMPILE must be 0 or 1')
    if value == '1' and not device.startswith('cuda'):
        raise ValueError('Live native compilation requires an explicit CUDA device')
    return value == '1'


class LocalModels:
    """One ASR, translation worker and VoxCPM instance per explicit live session."""

    def __init__(self, directory: Path, *, device='cpu', source_language=None,
                 target_language='tr', whisper_model='small', glossary=None,
                 voxcpm_model=None, asr_model=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.native_compile = native_compile_requested(device)
        if self.native_compile and voxcpm_model is not None:
            raise ValueError('Live native compilation requires a model loaded by this session')
        self.source_language = None if source_language in (None, 'auto') else source_language
        self.target_language = target_language
        from .qwen_translation import selected_backend, selected_boundary_backend
        self.live_translation_backend = selected_backend()
        self.live_boundary_backend = selected_boundary_backend(self.live_translation_backend)
        self.contextual_chunking = self.live_boundary_backend == 'qwen3.5-27b'
        self.whisper_model = whisper_model
        from .security import validated_glossary
        self.glossary = validated_glossary(glossary)
        self.asr = self.translator = self.boundary_selector = self.voxcpm = self.speaker_models = self.memory = None
        self.speaker_model_warning = None
        self.lock = threading.RLock()
        self.asr_lock = threading.Lock()
        scheduler_setting = os.environ.get('LIVE_GPU_INFERENCE_SCHEDULER', '1')
        if scheduler_setting not in ('0', '1'):
            raise ValueError('LIVE_GPU_INFERENCE_SCHEDULER must be 0 or 1')
        self.inference_scheduler = PriorityInferenceScheduler(enabled=scheduler_setting == '1')
        self.closed = False
        self._prompt_caches = {}
        self._reference_only_after_leak = set()
        self._last_conditioning = {}
        started = time.perf_counter()
        try:
            from translation.backends import CapabilityRouter, HyMT2Backend, M2M100Backend
            from .memory import LiveContextMemory
            from voxcpm_runtime import load_model
            self.vad = SileroStream()
            self.asr = IncrementalWhisper(whisper_model, os.environ.get('LIVE_ASR_DEVICE', device), model_instance=asr_model)
            if self.live_translation_backend == 'qwen3.5-27b':
                from .qwen_translation import QwenTranslator
                self.translator = QwenTranslator(device)
            else:
                self.translator = CapabilityRouter(HyMT2Backend(device=device), M2M100Backend(device=device))
            if self.live_boundary_backend == 'qwen3.5-27b':
                if self.live_translation_backend == 'qwen3.5-27b':
                    self.boundary_selector = self.translator
                else:
                    from .qwen_translation import QwenTranslator
                    self.boundary_selector = QwenTranslator(device)
            self.memory = LiveContextMemory(self.directory / 'context.sqlite3')
            self.speakers = SpeakerTracker(self.directory / 'references')
            speaker_model_dir = Path(os.environ.get('LIVE_SPEAKER_MODEL',
                os.environ.get('DIARIZATION_MODEL_DIR', 'third_party/diarization/community-1')))
            required_speaker_weights = (speaker_model_dir / 'embedding/pytorch_model.bin',
                                        speaker_model_dir / 'segmentation/pytorch_model.bin')
            missing_speaker_weights = [str(path) for path in required_speaker_weights if not path.is_file()]
            require_speaker_models = os.environ.get('LIVE_REQUIRE_SPEAKER_MODELS', '0')
            if require_speaker_models not in ('0', '1'):
                raise ValueError('LIVE_REQUIRE_SPEAKER_MODELS must be 0 or 1')
            if missing_speaker_weights:
                self.speaker_model_warning = ('Community-1 checkpoints unavailable; stable speaker identity '
                    'and overlap analysis are disabled. Missing: ' + ', '.join(missing_speaker_weights))
                if require_speaker_models == '1':
                    raise FileNotFoundError(self.speaker_model_warning)
            else:
                try:
                    self.speaker_models = SpeakerClient(device,
                        inference_scheduler=self.inference_scheduler)
                    self.speaker_models.prepare()
                except (ImportError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
                    if self.speaker_models is not None:
                        self.speaker_models.close()
                        self.speaker_models = None
                    self.speaker_model_warning = (f'Community-1 speaker analysis unavailable: '
                        f'{type(exc).__name__}: {str(exc)[:400]}')
                    if require_speaker_models == '1':
                        raise RuntimeError(self.speaker_model_warning) from exc
            path = os.environ.get('LIVE_VOXCPM_PATH')
            if voxcpm_model is not None:
                self.voxcpm = voxcpm_model
            elif path or self.native_compile:
                from voxcpm import VoxCPM
                if not path:
                    from voxcpm_runtime import resolve_model_path
                    path = str(resolve_model_path(local_files_only=True))
                self.voxcpm = VoxCPM.from_pretrained(path, load_denoiser=False,
                    optimize=self.native_compile, device=device, local_files_only=True)
            else:
                self.voxcpm = load_model(device)
            if self.native_compile:
                model = self.voxcpm.tts_model
                if not (hasattr(model.base_lm.forward_step, '_torchdynamo_orig_callable')
                        and hasattr(model.residual_lm.forward_step, '_torchdynamo_orig_callable')
                        and hasattr(model.feat_encoder, '_orig_mod')
                        and hasattr(model.feat_decoder.estimator, '_orig_mod')):
                    raise RuntimeError('Native CUDA compilation was not enabled; use LIVE_VOXCPM_COMPILE=0 for eager fallback')
            # Ready must mean ready to decode, not merely that a lazy object
            # exists. The first real stream previously overflowed its queue
            # while CUDA ASR initialized. No source content is pre-processed.
            if self.live_translation_backend == 'qwen3.5-27b':
                self.translator.prepare()
            else:
                self.translator.hymt2.prepare()
            if self.boundary_selector is not None and self.boundary_selector is not self.translator:
                self.boundary_selector.prepare()
            self.asr.transcribe(bytes(16000 * 2), language=self.source_language or 'en')
            # Warm reference loading/resampling/encoding on known silence, not
            # on future source speech. Never put these features in a speaker cache.
            import soundfile as sf
            warmup = self.directory / '.reference-encoder-warmup.wav'
            try:
                sf.write(warmup, np.zeros(16000, dtype=np.float32), 16000, subtype='PCM_16')
                self.voxcpm.tts_model.build_prompt_cache(reference_wav_path=str(warmup))
            finally:
                warmup.unlink(missing_ok=True)
        except Exception:
            self.close()
            raise
        self.load_seconds = time.perf_counter() - started

    def recognize(self, pcm, start=0.0, language=None):
        from dataclasses import replace
        lock_requested = time.perf_counter()
        with self.asr_lock, self.inference_scheduler.lease('asr') as lease:
            lock_acquired = time.perf_counter()
            if self.closed:
                raise RuntimeError('Live models stopped')
            inference_started = time.perf_counter()
            transcript = self.asr.transcribe(pcm, start, language or self.source_language)
            inference_completed = time.perf_counter()
        return replace(transcript, runtime_metrics={**dict(transcript.runtime_metrics or {}),
            'asr_lock_wait_seconds': max(0.0, lock_acquired-lock_requested),
            'asr_inference_seconds': max(0.0, inference_completed-inference_started),
            'inference_scheduler_wait_seconds': lease.wait_seconds})

    def select_boundary(self, *, units, eligible_ends, preferred_end_unit):
        if not self.contextual_chunking:
            raise RuntimeError('Selected translation backend does not propose contextual chunks')
        selector = self.boundary_selector or self.translator
        with self.inference_scheduler.lease('boundary') as lease:
            end = selector.select_boundary(units, eligible_ends, preferred_end_unit)
        return {'end_unit':end, 'metrics':{**dict(selector.last_generation),
            'inference_scheduler_wait_seconds':lease.wait_seconds}}

    def assess_source_boundary(self, *, prefix, continuation):
        if not self.contextual_chunking:
            raise RuntimeError('Selected translation backend does not check contextual chunks')
        selector = self.boundary_selector or self.translator
        with self.inference_scheduler.lease('boundary') as lease:
            crosses = selector.assess_source_boundary(prefix, continuation)
        return {'crosses_dependency':crosses, 'metrics':{**dict(selector.last_generation),
            'inference_scheduler_wait_seconds':lease.wait_seconds}}

    def assess_simultaneous_candidate(self, *, prefix, continuation, target_text):
        """Use the separate local Qwen model to check one Hy-MT2 early chunk."""
        if (self.live_translation_backend != 'hymt2-1.8b'
                or self.live_boundary_backend != 'qwen3.5-27b'
                or self.boundary_selector is None
                or self.boundary_selector is self.translator):
            raise RuntimeError('Incremental translation requires an independent local candidate checker')
        values = (prefix, continuation, target_text)
        if any(not isinstance(value, str) or not value.strip() or len(value) > 8000
               for value in values):
            raise ValueError('Invalid bounded READ/WAIT/WRITE candidate')
        with self.inference_scheduler.lease('qa') as lease:
            result = self.boundary_selector.assess_simultaneous_candidate(
                prefix, continuation, target_text)
        if (not isinstance(result, dict)
                or type(result.get('crosses_dependency')) is not bool
                or type(result.get('supported')) is not bool):
            result = None
        return {'result':result, 'metrics':{**dict(self.boundary_selector.last_generation),
            'inference_scheduler_wait_seconds':lease.wait_seconds,
            'provider':'qwen3.5-27b_independent_of_hymt2'}}

    def assess_translation_support(self, *, source_text, target_text):
        """Check an anomalously expansive Hy-MT draft against its exact source owner."""
        if (self.live_translation_backend != 'hymt2-1.8b'
                or self.live_boundary_backend != 'qwen3.5-27b'
                or self.boundary_selector is None
                or self.boundary_selector is self.translator):
            return None
        values = (source_text, target_text)
        if any(not isinstance(value, str) or not value.strip() or len(value) > 8000
               for value in values):
            raise ValueError('Invalid bounded translation support comparison')
        with self.inference_scheduler.lease('qa') as lease:
            try:
                result = self.boundary_selector.assess_translation_support(
                    source_text, target_text)
            except Exception as exc:
                result = None
                error_type = type(exc).__name__
            else:
                error_type = None
        supported = (result.get('supported') if isinstance(result, dict)
                     and type(result.get('supported')) is bool else None)
        return {'supported':supported, 'error_type':error_type,
            'metrics':{**dict(self.boundary_selector.last_generation),
                'inference_scheduler_wait_seconds':lease.wait_seconds,
                'provider':'qwen3.5-27b_independent_of_hymt2'}}

    def propose_simultaneous(self, *, words, source_language, eligible_ends,
                             preferred_end_unit, previous_source='',
                             previous_translation='', approved_end_unit=None,
                             approved_following_text=None,
                             approved_source_text=None,
                             approved_duration_seconds=None):
        """Get one local Hy-MT2 READ/WAIT/WRITE proposal without telemetry text."""
        from translation.base import glossary_preservation
        words = tuple(words)
        if not words or len(words) > 256 or sum(
                len(word.surface if word.surface is not None else word.text)
                for word in words) > 8000:
            raise ValueError('Invalid bounded READ/WAIT/WRITE ASR snapshot')
        if self.live_translation_backend != 'hymt2-1.8b':
            raise RuntimeError('READ/WAIT/WRITE prototype currently requires the local Hy-MT2 backend')
        from translation.backends import CapabilityRouter
        if not isinstance(self.translator, CapabilityRouter):
            raise RuntimeError('READ/WAIT/WRITE requires the local Hy-MT2 capability router')
        if approved_end_unit is not None:
            if (type(approved_end_unit) is not int
                    or approved_end_unit not in eligible_ends
                    or approved_end_unit <= 0
                    or approved_end_unit > len(words)-2):
                raise ValueError('A guarded source cut must remain eligible in the current snapshot')
            if self._translation_backend_name(source_language) != 'hymt2-1.8b':
                raise RuntimeError('Guarded incremental translation requires the local Hy-MT2 route')
            def spoken_source(items):
                return ''.join(item.surface if item.surface is not None else
                    ((' ' if index else '') + item.text)
                    for index, item in enumerate(items))
            prefix = words[:approved_end_unit]
            continuation = words[approved_end_unit:]
            source_prefix = spoken_source(prefix)
            if approved_source_text is not None:
                if (not isinstance(approved_source_text, str)
                        or not approved_source_text.strip()
                        or len(approved_source_text) > 8000):
                    raise ValueError('Invalid frozen source text for a guarded incremental translation')
                source_prefix = approved_source_text
            following_source = (approved_following_text if approved_following_text is not None
                                else spoken_source(continuation))
            if not isinstance(following_source, str) or len(following_source) > 8000:
                raise ValueError('Invalid stable context for a guarded incremental translation')
            duration = max(.2, prefix[-1].end-prefix[0].start)
            if approved_duration_seconds is not None:
                try:
                    duration = float(approved_duration_seconds)
                except (TypeError, ValueError) as exc:
                    raise ValueError('Invalid frozen duration for a guarded incremental translation') from exc
                if not math.isfinite(duration) or duration < .2:
                    raise ValueError('Invalid frozen duration for a guarded incremental translation')
            started = time.perf_counter()
            draft = self.translate_draft(source_text=source_prefix,
                source_language=source_language,
                following_text=following_source, previous_text=previous_source,
                previous_translation=previous_translation,
                duration=duration,
                include_following_context=True, deterministic_translation=True)
            completed = time.perf_counter()
            generation = dict(self.translator.hymt2._translator.last_generation)
            generation.pop('prompt', None)
            return {'raw':json.dumps({'status':'ready',
                    'end_unit':approved_end_unit, 'translation':draft['text']},
                    ensure_ascii=False, separators=(',', ':')),
                'metrics':{
                    'proposal_started_at':started,
                    'proposal_completed_at':completed,
                    'proposal_seconds':max(0., completed-started),
                    # Keep the exact Hy-MT2 stage timings with the proposal.
                    # The accepted RWW chunk enters the regular render path,
                    # which consumes these fields for translation timing.
                    'translation_draft_metrics':dict(draft.get('metrics', {})),
                    'inference_scheduler_wait_seconds':draft['metrics'].get(
                        'inference_scheduler_wait_seconds', 0.),
                    'translation_generation':generation,
                    'proposal_strategy':'qwen_guarded_cut_deterministic_hymt2_exact_prefix',
                    'translation_prompt_context_policy':(
                        'frozen_full_approved_cut_snapshot'
                        if approved_source_text is not None and
                           approved_duration_seconds is not None and
                           approved_following_text is not None
                        else 'current_approved_cut_context'),
                    'right_context_policy':('frozen_first_eligible_stable_snapshot'
                        if approved_following_text is not None else 'current_stable_snapshot'),
                    'translation_backend':'hymt2-1.8b',
                    'translation_correctness_certified':False,
                }}
        source_text = ''.join(word.surface if word.surface is not None else
                              ((' ' if index else '') + word.text)
                              for index, word in enumerate(words))
        relevant = {key:self.glossary[key] for key in
                    glossary_preservation(source_text, '', self.glossary)}
        started = time.perf_counter()
        with self.inference_scheduler.lease('translation') as lease:
            raw = self.translator.propose_simultaneous(
                [{'id':index+1, 'text':word.surface if word.surface is not None else
                  ((' ' if index else '') + word.text)}
                 for index, word in enumerate(words)],
                source_language=source_language,
                target_language=self.target_language,
                eligible_ends=list(eligible_ends),
                preferred_end_unit=preferred_end_unit,
                previous_source=previous_source[-800:],
                previous_translation=previous_translation[-800:],
                glossary=relevant,
            )
        completed = time.perf_counter()
        generation = dict(self.translator.hymt2._translator.last_generation)
        generation.pop('prompt', None)
        return {'raw':raw, 'metrics':{
            'proposal_started_at':started,
            'proposal_completed_at':completed,
            'proposal_seconds':max(0.0, completed-started),
            'inference_scheduler_wait_seconds':lease.wait_seconds,
            'generation':generation,
            'translation_backend':'hymt2-1.8b',
            'translation_correctness_certified':False,
        }}

    def _stream(self, text, choice: SpeakerChoice, seed, *, reference_only=False):
        import torch
        with self.inference_scheduler.lease('tts'):
            torch.manual_seed(seed)
        conditioning_started = time.perf_counter()
        cache = None
        self._last_conditioning = {'cache_hit': False, 'scope': 'no_reference_temporary_base_voice'}
        if choice.reference:
            from voxcpm_runtime import continuation_prompt, VOXCPM_MODEL_REVISION
            digest = hashlib.sha256(choice.reference.read_bytes()).hexdigest()
            if choice.reference_sha256 and digest != choice.reference_sha256:
                raise ValueError('Speaker reference changed after identity selection')
            prompt = None if reference_only else continuation_prompt(text, choice.transcript)
            key = (VOXCPM_MODEL_REVISION, digest, prompt)
            hit = key in self._prompt_caches
            if hit:
                # Dict insertion order tracks the most recently used reference.
                self._prompt_caches[key] = self._prompt_caches.pop(key)
            else:
                options = {'reference_wav_path': str(choice.reference)}
                if prompt:
                    options.update(prompt_wav_path=str(choice.reference), prompt_text=prompt)
                with self.inference_scheduler.lease('tts'):
                    built = self.voxcpm.tts_model.build_prompt_cache(**options)
                    if set(built) - {'mode', 'prompt_text', 'audio_feat', 'ref_audio_feat'}:
                        raise ValueError('Unexpected mutable generation state in native conditioning cache')
                    # Only immutable source conditioning is retained. Give the
                    # native decoder fresh tensors/dict on every request so even
                    # an in-place decoder change cannot contaminate the next text.
                    immutable = {name: value.detach().clone() if torch.is_tensor(value) else value
                                 for name, value in built.items()}
                    if len(self._prompt_caches) >= 32:
                        del self._prompt_caches[next(iter(self._prompt_caches))]
                    self._prompt_caches[key] = immutable
            with self.inference_scheduler.lease('tts'):
                cache = {name: value.clone() if torch.is_tensor(value) else value
                         for name, value in self._prompt_caches[key].items()}
            self._last_conditioning = {'cache_hit': hit, 'scope': 'encoded_source_conditioning_only',
                                       'mode': cache['mode'], 'reference_sha256': digest}
        conditioning_completed = time.perf_counter()
        self._last_conditioning.update(
            conditioning_cache_seconds=max(0.0, conditioning_completed-conditioning_started),
            conditioning_started_at=conditioning_started,
            conditioning_completed_at=conditioning_completed)
        generator = self.voxcpm.tts_model.generate_with_prompt_cache_streaming
        stream = iter(generator(target_text=text, prompt_cache=cache, inference_timesteps=10,
                                cfg_value=2.0, retry_badcase=False))
        while True:
            with self.inference_scheduler.lease('tts'):
                try:
                    waveform, _text_tokens, _new_audio_features = next(stream)
                except StopIteration:
                    return
                array = waveform.detach().float().cpu().numpy().reshape(-1)
            yield array

    def translate_draft(self, *, source_text, source_language, following_text='', previous_text='',
                        previous_translation='', duration=1., include_following_context=False,
                        deterministic_translation=False):
        """Translate an owned source unit with source-only surrounding memory.

        Following text is normally retained for endpoint metrics only. The
        simultaneous prefetch path may explicitly supply stable ASR-confirmed
        right context; the prompt still asks Hy-MT2 to translate only the
        exact owned source unit.
        """
        from translation.base import TranslationContext, glossary_preservation
        context_started = time.perf_counter()
        relevant = {key:self.glossary[key] for key in glossary_preservation(source_text, '', self.glossary)}
        memories=self.memory.relevant(source_text,limit=6)
        # Reserve room for recent conversation; a long retrieval must not
        # replace it. Add whole non-duplicate earlier items within the budget.
        parts = [previous_text[-500:]] if previous_text else []
        used_memories = 0
        for item in memories:
            if (item.kind == 'live_committed' and item.source not in previous_text
                    and item.source not in parts and len(' '.join([*parts, item.source])) <= 800):
                parts.append(item.source)
                used_memories += 1
        actual_previous=' '.join(parts)
        next_context = following_text[-800:] if include_following_context else None
        if deterministic_translation and self._translation_backend_name(source_language) != 'hymt2-1.8b':
            raise RuntimeError('Deterministic streaming drafts require local Hy-MT2')
        context = TranslationContext(previous_text=actual_previous or None,
            previous_translation=previous_translation[-500:] or None,
            next_text=next_context or None,
            preferred_duration_seconds=duration)
        context_completed = time.perf_counter()
        started = time.perf_counter()
        with self.inference_scheduler.lease('translation') as lease:
            translate_kwargs = {'context':context, 'glossary':relevant}
            if deterministic_translation:
                translate_kwargs['deterministic'] = True
            target = self.translator.translate(source_text, source_language,
                self.target_language, **translate_kwargs)
        inference_completed = time.perf_counter()
        if not isinstance(target, str) or not target.strip() or len(target) > 8000:
            raise ValueError('Invalid bounded translation draft')
        validation_started = time.perf_counter()
        preservation = glossary_preservation(source_text, target, relevant)
        missing = [term for term, valid in preservation.items() if not valid]
        if missing:
            raise ValueError('explicit_glossary_not_preserved: ' + ', '.join(missing))
        completed = time.perf_counter()
        return {'text':target,'metrics':{'translation_seconds':completed-started,
            'context_retrieval_seconds':max(0.0, context_completed-context_started),
            'translator_inference_seconds':max(0.0, inference_completed-started),
            'translation_validation_seconds':max(0.0, completed-validation_started),
            'context_retrieval_started_at':context_started,
            'context_retrieval_completed_at':context_completed,
            'translation_started_at':started, 'translation_completed_at':completed,
            'translation_validation_started_at':validation_started,
            'translation_validation_completed_at':completed,
            'inference_scheduler_wait_seconds':lease.wait_seconds,
            'previous_source':actual_previous,'following_source':following_text[:800],
            'previous_translation':previous_translation[-500:],
            'following_source_usage':('translation_background_only' if next_context
                                      else 'endpoint_only_not_sent_to_mt'),
            'used_retrieved_source_items':used_memories,
            'retrieved_context_items':len(memories),'retrieval':'unicode_ngrams_fts5_bm25',
            'glossary':relevant,'glossary_preservation':preservation,
            'translation_backend':self._translation_backend_name(source_language),
            'generation':getattr(self.translator, 'last_generation', {})
                if getattr(self, 'live_translation_backend', '') == 'qwen3.5-27b' else {},
            'agreement_is_not_semantic_correctness':True}}

    def _translation_backend_name(self, source_language):
        from translation.backends import M2M100_FALLBACK_LANGUAGES
        if getattr(self, 'live_translation_backend', '') == 'qwen3.5-27b':
            return 'qwen3.5-27b'
        return ('m2m100-418m' if self.target_language in M2M100_FALLBACK_LANGUAGES or
                source_language in M2M100_FALLBACK_LANGUAGES else 'hymt2-1.8b')

    def _assess_tts_presentation(self, pcm, path, *, target, choice, attempt, native_signal):
        """Apply the same signal, language and text checks to the audio to play."""
        from engine.qa import evaluate_tts
        from scipy.signal import resample_poly
        qa_audio = resample_poly(np.frombuffer(pcm, dtype='<i2').astype(np.float32), 1, 3)
        qa_pcm = np.clip(qa_audio, -32768, 32767).astype('<i2').tobytes()
        lock_requested = time.perf_counter()
        with self.asr_lock, self.inference_scheduler.lease('asr') as scheduler_lease:
            lock_acquired = time.perf_counter()
            asr_started = time.perf_counter()
            recovered = self.asr.transcribe(qa_pcm)
            asr_completed = time.perf_counter()
        text_started = time.perf_counter()
        quality = evaluate_tts(path, target, recovered_text=recovered.text,
                               reference_wav=choice.reference, retry_count=attempt)
        comparison = None
        if (getattr(self, 'live_translation_backend', '') == 'qwen3.5-27b' and recovered.language == self.target_language
                and quality.cer is not None and 0 < quality.cer <= .5
                and not (set(quality.hard_failures)-{'high_wer'})):
            try:
                with self.inference_scheduler.lease('qa'):
                    comparison = self.translator.assess_qa_difference(target, recovered.text)
            except (RuntimeError, ValueError) as exc:
                comparison = {'error':str(exc), 'minor':None}
            from .qa import tolerate_surface_difference
            quality = tolerate_surface_difference(quality, assessment=comparison)
        failures = list(dict.fromkeys((*native_signal.hard_failures, *quality.hard_failures)))
        if recovered.language != self.target_language and recovered.language_probability >= .85 and len(target.split()) >= 4:
            failures.append('wrong_target_language')
        text_completed = time.perf_counter()
        return {'qa_pcm':qa_pcm, 'quality':quality, 'recovered':recovered,
            'text_comparison':comparison, 'failures':failures,
            'qa_asr_word_timing_alignment_fallback': recovered.runtime_metrics.get(
                'word_timing_alignment_fallback'),
            'qa_asr_lock_requested_at':lock_requested, 'qa_asr_lock_acquired_at':lock_acquired,
            'qa_asr_started_at':asr_started, 'qa_asr_completed_at':asr_completed,
            'qa_asr_lock_wait_seconds':max(0.0, lock_acquired-lock_requested),
            'qa_asr_inference_seconds':max(0.0, asr_completed-asr_started),
            'qa_asr_scheduler_wait_seconds':scheduler_lease.wait_seconds,
            'qa_text_evaluation_started_at':text_started, 'qa_text_evaluation_completed_at':text_completed,
            'qa_text_evaluation_seconds':max(0.0, text_completed-text_started)}

    def render(self, *, pcm, source_text, source_start, source_end, source_language,
               identifier=None, translation_draft=None, translation_request=None,
               maximum_presentation_tempo=1.20, presentation_speed_factor=1.0) -> RenderedPhrase:
        from engine.context import ContextItem
        from translation.base import TranslationContext
        import soundfile as sf
        identifier = identifier or uuid.uuid4().hex
        render_started = time.perf_counter()
        self.inference_scheduler.reset_thread_metrics()
        metrics = {'model_adapter_source_sha256': RUNTIME_SOURCE_SHA256,
                   'tts_optimization':'torch_compile' if getattr(self, 'native_compile', False) else 'eager',
                   'presentation_speed_factor_requested':presentation_speed_factor}
        candidates = []
        with self.lock:
            if self.closed:
                raise RuntimeError('Live models stopped')
            started = time.perf_counter()
            metrics['speaker_started_at'] = started
            if self.speaker_models is not None:
                acoustic = self.speaker_models.analyze(pcm)
                choice = self.speakers.select(pcm, source_text, acoustic['embedding'],
                    overlap=acoustic['overlap'], mixed_turn=acoustic['mixed_turn'])
                metrics['speaker_analysis'] = {key: value for key, value in acoustic.items() if key != 'embedding'}
                if acoustic['overlap'] or acoustic['mixed_turn']:
                    metrics['speaker_warning'] = 'Uncertain/overlapping speaker context: neutral voice; dominant stable transcript only. Simultaneous speech is not separated.'
            else:
                choice = segment_reference_fallback(pcm, source_text, self.speakers.directory, identifier,
                    min_seconds=self.speakers.min_reference_seconds)
                metrics['speaker_analysis'] = {'available': False, 'backend': 'community1-unavailable',
                    'identity_confirmed': False, 'overlap': 'unknown',
                    'reason': self.speaker_model_warning}
                if choice.reference is not None:
                    metrics['speaker_warning'] = ('Stable speaker identity/overlap analysis unavailable; '
                        'using only this source phrase as its temporary cloning reference.')
                else:
                    metrics['speaker_warning'] = ('Stable speaker identity/overlap analysis unavailable; '
                        'this phrase is too short or unclean for a safe reference, so a temporary base voice is used.')
            metrics['speaker_seconds'] = time.perf_counter() - started
            metrics['speaker_completed_at'] = time.perf_counter()
            metrics['speaker_min_reference_seconds'] = self.speakers.min_reference_seconds
            # Stable ASR is still the content gate. Uncertain identity must
            # neither borrow another person's voice nor poison a profile.
            context_retrieval_started = time.perf_counter()
            metrics['context_retrieval_started_at'] = context_retrieval_started
            relevant = {key: value for key, value in self.glossary.items() if key.casefold() in source_text.casefold()}
            entities, uppercase_sentence = live_source_entities(source_text)
            protected = tuple(value for _, value in entities)
            memories = self.memory.relevant(source_text, speaker_id=choice.speaker_id, limit=6, distant=True)
            # Supply relevant *source* context only. Previously translated
            # answers must never replace the current utterance or act as
            # whole-sentence glossary constraints.
            previous_source = ' '.join(item.source for item in memories
                                       if item.kind == 'live_committed')[:800] or None
            metrics['context_memory'] = {'retrieved': len(memories),
                'previous_source': previous_source, 'glossary': relevant,
                'protected_tokens': list(protected),
                'uppercase_sentence_acronyms_ambiguous': uppercase_sentence}
            presentation_duration = max(.2, source_end - source_start)
            duration = presentation_duration
            context = TranslationContext(speaker=choice.speaker_id,
                previous_text=previous_source,
                preferred_duration_seconds=duration, target_spoken_units=max(3, duration * 4.8),
                protected_tokens=protected)
            metrics['context_retrieval_seconds'] = time.perf_counter() - context_retrieval_started
            metrics['context_retrieval_completed_at'] = time.perf_counter()
            started = time.perf_counter()
            if translation_request is not None:
                if translation_draft is not None or any((
                    translation_request.get('source_text') != source_text,
                    translation_request.get('source_language') != source_language,
                    abs(translation_request.get('duration', -1) - duration) > 1e-6,
                )):
                    raise ValueError('translation_request_does_not_match_owned_source')
                translation_draft = self.translate_draft(**translation_request)
            metrics['render_to_translation_start_seconds'] = started - render_started
            if translation_draft is None:
                # Direct diagnostic callers retain their single-shot interface;
                # the actual browser/session path supplies source-supported text.
                with self.inference_scheduler.lease('translation') as lease:
                    target = self.translator.translate(source_text, source_language, self.target_language,
                                                        context=context, glossary=relevant)
                metrics['inference_scheduler_translation_wait_seconds'] = lease.wait_seconds
                metrics['translation_seconds'] = time.perf_counter() - started
            else:
                target = translation_draft['text']
                metrics['translation_seconds'] = translation_draft['metrics']['translation_seconds']
                source_boundary_evidence = translation_draft['metrics'].get(
                    'source_boundary_evidence')
                metrics['translation_commitment'] = {
                    'source_support':'confirmed_source_boundary',
                    'selection':('confirmed_source_boundary_prefetch' if source_boundary_evidence
                                 else 'latest_complete_contextual_candidate'),
                    'translation_equality_required':False,
                    'semantic_correctness_certified':False,
                    'source_boundary_evidence':source_boundary_evidence,
                    'draft_context':translation_draft['metrics'], 'scope':'source_unit_before_tts'}
            translated_at = (translation_draft['metrics']['translation_completed_at']
                             if translation_draft else time.perf_counter())
            from translation.backends import M2M100_FALLBACK_LANGUAGES
            from voxcpm_runtime import VOXCPM_MODEL_REVISION
            metrics.update(tts_backend='native-voxcpm2', tts_model_revision=VOXCPM_MODEL_REVISION,
                translation_backend=self._translation_backend_name(source_language),
                source_seconds=presentation_duration)
            from engine.context import ConsistencyIssue
            import re
            # Do not turn previous full-sentence translations into glossary
            # constraints. Only explicit terms and exact numeric values apply.
            issues = [ConsistencyIssue('glossary', key, value, target, 'error')
                      for key, value in relevant.items() if value.casefold() not in target.casefold()]
            issues += [ConsistencyIssue('number', number, number, target, 'error')
                       for number in re.findall(r'\b\d+(?:[.,]\d+)?\b', source_text)
                       if number not in target]
            hard_preservation = [asdict(issue) for issue in issues if issue.severity == 'error']
            if not target.strip() or hard_preservation:
                raise ValueError(f'translation_preservation_failed: {json.dumps(hard_preservation, ensure_ascii=False)}')
            if source_language != self.target_language and source_text.strip().casefold() == target.strip().casefold() and len(source_text.split()) > 3:
                raise ValueError('translation_returned_source_sentence')
            # Run15 had exactly one extreme expansion (38 Turkish words from
            # 3.26 s / 7 Welsh ASR words); the other 35 voiced phrases were at
            # or below 5.08 target words/s and 1.34x target/source word ratio.
            # Only this joint outlier is screened, and an independent local
            # Qwen result is required before any extra generation or caption
            # fallback. Duration alone never rejects a translation.
            target_word_count = len(target.split())
            source_word_count = len(source_text.split())
            target_words_per_source_second = target_word_count / max(presentation_duration, .2)
            target_to_source_word_ratio = target_word_count / max(source_word_count, 1)
            support_checker_available = (
                self.live_translation_backend == 'hymt2-1.8b'
                and self.live_boundary_backend == 'qwen3.5-27b'
                and self.boundary_selector is not None
                and self.boundary_selector is not self.translator)
            if (support_checker_available and target_words_per_source_second > 8.0
                    and target_to_source_word_ratio >= 4.5):
                outlier_started = time.perf_counter()
                first_check = self.assess_translation_support(
                    source_text=source_text, target_text=target)
                guard = {'trigger':'joint_translation_expansion_outlier',
                    'target_words':target_word_count, 'source_words':source_word_count,
                    'source_seconds':presentation_duration,
                    'target_words_per_source_second':round(target_words_per_source_second, 3),
                    'target_to_source_word_ratio':round(target_to_source_word_ratio, 3),
                    'first_check':first_check, 'source_only_retry':False}
                if not first_check or first_check.get('supported') is not True:
                    if first_check and first_check.get('supported') is False:
                        retry_started = time.perf_counter()
                        retry_context = TranslationContext(speaker=choice.speaker_id,
                            preferred_duration_seconds=duration,
                            target_spoken_units=max(3, duration * 4.8),
                            protected_tokens=protected)
                        with self.inference_scheduler.lease('translation') as lease:
                            retry_target = self.translator.translate(source_text,
                                source_language, self.target_language,
                                context=retry_context, glossary=relevant)
                        retry_seconds = max(0.0, time.perf_counter()-retry_started)
                        retry_check = self.assess_translation_support(
                            source_text=source_text, target_text=retry_target)
                        guard.update(source_only_retry=True,
                            source_only_retry_seconds=retry_seconds,
                            source_only_translation_scheduler_wait_seconds=lease.wait_seconds,
                            retry_target_words=len(retry_target.split()),
                            retry_check=retry_check)
                        retry_preservation = [
                            (key, key.casefold() in retry_target.casefold())
                            for key in relevant]
                        retry_numbers = [(number, number in retry_target)
                            for number in re.findall(r'\b\d+(?:[.,]\d+)?\b', source_text)]
                        guard['source_only_retry_preservation'] = {
                            'glossary':retry_preservation, 'numbers':retry_numbers}
                        if all(ok for _, ok in retry_preservation+retry_numbers):
                            target = retry_target
                            if metrics.get('translation_commitment'):
                                metrics['translation_commitment'].update(
                                    selection='source_only_semantic_retry',
                                    semantic_correctness_certified=False)
                            if retry_check and retry_check.get('supported') is True:
                                guard['action'] = 'speak_source_only_retry'
                            else:
                                guard['action'] = 'caption_source_only_retry_unsupported'
                        else:
                            guard['action'] = 'caption_source_only_retry_preservation_failed'
                    else:
                        guard['action'] = 'caption_translation_support_unavailable'
                    guard['completed_seconds'] = round(time.perf_counter()-outlier_started, 3)
                    metrics['translation_outlier_guard'] = guard
                    if guard['action'] != 'speak_source_only_retry':
                        marker = ('translation_semantic_support_unavailable'
                            if guard['action'] == 'caption_translation_support_unavailable'
                            else 'translation_semantic_support_failed')
                        raise ValueError(marker + ': ' + json.dumps(guard, ensure_ascii=False))
                else:
                    guard.update(action='speak_original',
                        completed_seconds=round(time.perf_counter()-outlier_started, 3))
                    metrics['translation_outlier_guard'] = guard
            played = None
            adaptive_ref_only = bool(choice.reference_sha256 and
                choice.reference_sha256 in self._reference_only_after_leak)
            metrics['conditioning_policy'] = ('reference_only_after_verified_continuation_leak'
                if adaptive_ref_only else 'standard_conditioning')
            for attempt in range(2):
                attempt_id = uuid.uuid4().hex
                started = time.perf_counter()
                dispatch_delay = started - translated_at
                if attempt == 0:
                    metrics['translation_to_tts_request_seconds'] = dispatch_delay
                chunks, first_at = [], None
                last = b''
                # A new seed alone cannot correct a bad cross-language
                # continuation. The one permitted retry keeps the same
                # immutable speaker WAV but omits its source-language text.
                reference_only = bool(choice.reference) and (adaptive_ref_only or (attempt > 0 and any(
                    failure in ('high_wer', 'wrong_target_language', 'tts_text_meaning_mismatch')
                    for failure in candidates[-1]['failures'])))
                # Persist the exact request before inference as well: a bounded
                # Stop/backpressure may interrupt generation before a candidate
                # WAV/quality JSON exists. This is an attempted request, not a
                # claim that usable audio was generated or played.
                with (self.directory / 'tts_requests.jsonl').open('a') as audit:
                    audit.write(json.dumps({'type': 'tts_request_started',
                        'phrase_id': identifier, 'request_id': attempt_id,
                        'source_start': source_start, 'source_end': source_end,
                        'source_text': source_text, 'target_text': target,
                        'target_language': self.target_language,
                        'reference_sha256': choice.reference_sha256,
                        'reference_transcript': choice.transcript,
                        'seed': 42 + 1000*attempt, 'reference_only_retry': reference_only,
                        'model_revision': VOXCPM_MODEL_REVISION}, ensure_ascii=False) + '\n')
                for chunk in self._stream(target, choice, 42 + 1000 * attempt,
                                          reference_only=reference_only):
                    if self.closed:
                        raise RuntimeError('Live session stopped before playback')
                    array = np.asarray(chunk).reshape(-1)
                    if array.size == 0:
                        continue
                    if not np.all(np.isfinite(array)):
                        raise ValueError('nonfinite_native_pcm')
                    data = (np.clip(array, -1, 1) * 32767).astype('<i2').tobytes()
                    if last and len(data) > len(last) and data.startswith(last):
                        raise ValueError('cumulative_native_pcm_chunk')
                    if data == last and np.max(np.abs(array)) > .001:
                        raise ValueError('duplicate_non_silent_native_pcm_chunk')
                    if first_at is None:
                        first_at = time.perf_counter()
                    chunks.append(data)
                    last = data
                    if sum(map(len, chunks)) > 48_000 * 2 * 30:
                        raise ValueError('native_audio_exceeded_bounded_phrase_duration')
                finished = time.perf_counter()
                waveform = b''.join(chunks)
                if not waveform:
                    raise ValueError('empty_native_stream')
                path = self.directory / f'{identifier}.{attempt_id}.wav'
                sf.write(path, np.frombuffer(waveform, dtype='<i2'), 48000, subtype='PCM_16')
                qa_started = time.perf_counter()
                from engine.qa import inspect_signal
                from .timing import presentation_pcm
                signal_qa_started = time.perf_counter()
                native_signal = inspect_signal(path)
                signal_qa_completed = time.perf_counter()
                presentation_started = time.perf_counter()
                presentation, qa_path, timing = presentation_pcm(path, presentation_duration,
                    maximum_tempo=maximum_presentation_tempo,
                    speed_factor=presentation_speed_factor)
                presentation_completed = time.perf_counter()
                def assess_audio(audio, audio_path):
                    return self._assess_tts_presentation(audio, audio_path, target=target,
                        choice=choice, attempt=attempt, native_signal=native_signal)
                assessment = assess_audio(presentation, qa_path)
                assessments = [assessment]
                from .qa import retry_conservative_presentation, retry_verified_presentation
                conservative_started = time.perf_counter()
                fallback_tempo = (min(maximum_presentation_tempo, 1.30)
                                  if maximum_presentation_tempo > 1.30 else 1.20)
                conservative = retry_conservative_presentation(path, presentation_duration,
                    original=assessment, timing=timing, assess=assess_audio,
                    maximum_tempo=fallback_tempo)
                conservative_completed = time.perf_counter()
                conservative_audit = None
                if conservative is not None:
                    if conservative.get('conservative') is not None:
                        assessments.append(conservative['conservative'])
                    conservative_audit = {'reason':conservative['reason'],
                        'selected':conservative['selected'], 'started_at':conservative_started,
                        'completed_at':conservative_completed, 'error':conservative.get('error'),
                        'conservative_timing':conservative.get('conservative_timing')}
                    if conservative.get('selected'):
                        presentation, qa_path = conservative['pcm'], conservative['path']
                        assessment = conservative['conservative']
                        timing = {**conservative['conservative_timing'],
                            'fallback_from_maximum_tempo':maximum_presentation_tempo}
                fallback_started = time.perf_counter()
                fallback = retry_verified_presentation(path, waveform, presentation_duration,
                    original=assessment, timing=timing, assess=assess_audio)
                fallback_completed = time.perf_counter()
                fallback_audit = None
                if fallback is not None:
                    for name in ('native', 'alternate'):
                        if fallback.get(name) is not None:
                            assessments.append(fallback[name])
                    def compact_assessment(value):
                        return {'failures':value['failures'], 'recovered_text':value['recovered'].text,
                            'wer':value['quality'].wer, 'cer':value['quality'].cer,
                            'language':value['recovered'].language}
                    fallback_audit = {'reason':fallback['reason'], 'selected':fallback['selected'],
                        'started_at':fallback_started, 'completed_at':fallback_completed,
                        'original':compact_assessment(fallback['original']),
                        'native':(compact_assessment(fallback['native'])
                                  if fallback.get('native') is not None else None),
                        'alternate':(compact_assessment(fallback['alternate'])
                                     if 'alternate' in fallback else None),
                        'error':fallback.get('error')}
                    if fallback.get('alternate_timing'):
                        fallback_audit['alternate_timing'] = fallback['alternate_timing']
                    if fallback['selected']:
                        first_seconds = len(presentation)/96000
                        presentation, qa_path = fallback['pcm'], fallback['path']
                        assessment = fallback['alternate']
                        timing = {**fallback['alternate_timing'],
                            'fallback_from_method':'atempo',
                            'fallback_extra_presentation_seconds':len(presentation)/96000-first_seconds}
                qa_pcm, quality = assessment['qa_pcm'], assessment['quality']
                recovered, text_comparison = assessment['recovered'], assessment['text_comparison']
                failures = assessment['failures']
                qa_lock_requested = assessments[0]['qa_asr_lock_requested_at']
                qa_lock_acquired = assessments[0]['qa_asr_lock_acquired_at']
                qa_asr_started = assessments[0]['qa_asr_started_at']
                qa_asr_completed = assessments[-1]['qa_asr_completed_at']
                qa_text_started = assessments[0]['qa_text_evaluation_started_at']
                qa_text_completed = assessments[-1]['qa_text_evaluation_completed_at']
                candidate = {'request_id': attempt_id, 'target_text': target, 'seed': 42 + 1000 * attempt,
                    'reference_sha256': choice.reference_sha256, 'reference_transcript': choice.transcript,
                    'reference_mode': 'repaired_native_conditioning' if choice.reference else 'temporary_model_base_voice',
                    'conditioning_cache': dict(self._last_conditioning),
                    'retry_strategy': 'same_speaker_reference_only' if reference_only else 'normal_conditioning',
                    'translation_to_request_seconds': dispatch_delay,
                    'tts_started_at':started,
                    'quality': asdict(quality), 'asr_language': recovered.language,
                    'asr_language_probability': recovered.language_probability, 'failures': failures,
                    'qa_asr_word_timing_alignment_fallback_count':sum(
                        a.get('qa_asr_word_timing_alignment_fallback') is not None
                        for a in assessments),
                    'tts_first_chunk_seconds': first_at - started,
                    'tts_first_chunk_at':first_at,
                    'tts_completed_at':finished,
                    'conditioning_cache_seconds':self._last_conditioning.get('conditioning_cache_seconds', 0.0),
                    'conditioning_started_at':self._last_conditioning.get('conditioning_started_at'),
                    'conditioning_completed_at':self._last_conditioning.get('conditioning_completed_at'),
                    'tts_seconds': finished - started, 'audio_seconds': len(waveform) / 96000,
                    'tts_rtf': (finished - started) / (len(waveform) / 96000),
                    'tts_generation_after_first_chunk_seconds': max(0.0, finished-first_at),
                    'qa_signal_seconds': max(0.0, signal_qa_completed-signal_qa_started),
                    'qa_signal_started_at':signal_qa_started,
                    'qa_signal_completed_at':signal_qa_completed,
                    'presentation_timing_seconds': max(0.0, presentation_completed-presentation_started)
                        + (conservative.get('conservative_timing', {}).get('processing_seconds', 0.0)
                           if conservative else 0.0)
                        + (fallback.get('alternate_timing', {}).get('processing_seconds', 0.0) if fallback else 0.0),
                    'presentation_timing_started_at':presentation_started,
                    'presentation_timing_completed_at':presentation_completed,
                    'qa_asr_lock_wait_seconds': sum(a['qa_asr_lock_wait_seconds'] for a in assessments),
                    'qa_asr_lock_requested_at':qa_lock_requested,
                    'qa_asr_lock_acquired_at':qa_lock_acquired,
                    'qa_asr_inference_seconds': sum(a['qa_asr_inference_seconds'] for a in assessments),
                    'qa_asr_started_at':qa_asr_started,
                    'qa_asr_completed_at':qa_asr_completed,
                    'qa_text_evaluation_seconds': sum(a['qa_text_evaluation_seconds'] for a in assessments),
                    'qa_text_evaluation_started_at':qa_text_started,
                    'qa_text_evaluation_completed_at':qa_text_completed,
                    'presentation_audio_seconds':len(presentation)/96000,
                    'timing_control':timing, 'native_signal':asdict(native_signal),
                    'text_comparison':text_comparison, 'presentation_fallback':fallback_audit,
                    'conservative_timing_fallback':conservative_audit,
                    'qa_started_at':qa_started,
                    'qa_seconds': time.perf_counter() - qa_started, 'chunks': len(chunks),
                    'sha256': hashlib.sha256(qa_path.read_bytes()).hexdigest(), 'wav': str(qa_path),
                    'native_wav':str(path)}
                if choice.reference and self.speaker_models is not None:
                    speaker_qa_started = time.perf_counter()
                    candidate['speaker_similarity_started_at'] = speaker_qa_started
                    generated_voice = self.speaker_models.analyze(qa_pcm)
                    reference_profile = next(profile for profile in self.speakers.profiles
                                             if profile.identifier == choice.speaker_id)
                    vector = np.asarray(generated_voice['embedding'], dtype=np.float32)
                    cosine = float(np.dot(vector, reference_profile.embedding) /
                                   max(1e-9, np.linalg.norm(vector)))
                    candidate['speaker_embedding_similarity'] = {
                        'cosine': cosine, 'backend': 'community1-embedding',
                        'cross_language_threshold_calibrated': False}
                    candidate['speaker_similarity_seconds'] = max(0.0, time.perf_counter()-speaker_qa_started)
                    candidate['speaker_similarity_completed_at'] = time.perf_counter()
                elif choice.reference:
                    candidate['speaker_embedding_similarity'] = None
                    candidate['speaker_similarity_unavailable_reason'] = self.speaker_model_warning
                    candidate['speaker_similarity_seconds'] = None
                candidate.setdefault('speaker_similarity_seconds', None)
                candidate['qa_seconds'] = time.perf_counter() - qa_started
                candidate['qa_completed_at'] = time.perf_counter()
                candidates.append(candidate)
                (self.directory / f'{identifier}.{attempt_id}.json').write_text(
                    json.dumps(candidate, indent=2, ensure_ascii=False, default=str))
                if not failures:
                    if attempt > 0 and reference_only and choice.reference_sha256:
                        first = candidates[0]
                        leak = reference_leak_terms(choice.transcript, target,
                            first['quality']['recovered_text'])
                        if (leak and 'tts_text_meaning_mismatch' in first['failures'] and
                                first['conditioning_cache'].get('mode') == 'ref_continuation'):
                            self._reference_only_after_leak.add(choice.reference_sha256)
                            metrics['verified_reference_leak'] = {
                                'terms': leak, 'scope': 'same_reference_this_session',
                                'first_candidate_rejected': True, 'regenerated_candidate_passed': True,
                                'semantic_correctness_certified': False}
                    played = presentation
                    output_chunk_sizes = [len(played[i:i+15360]) for i in range(0,len(played),15360)]
                    metrics.update({k: candidate[k] for k in ('tts_first_chunk_seconds', 'tts_seconds', 'tts_rtf', 'qa_seconds', 'audio_seconds',
                        'tts_generation_after_first_chunk_seconds', 'qa_signal_seconds', 'presentation_timing_seconds',
                        'qa_asr_lock_wait_seconds', 'qa_asr_inference_seconds', 'qa_text_evaluation_seconds',
                        'qa_asr_word_timing_alignment_fallback_count',
                        'speaker_similarity_seconds', 'conditioning_cache', 'conditioning_cache_seconds')})
                    metrics['conditioning_cache'] = dict(candidate['conditioning_cache'])
                    metrics['retry_count'] = attempt
                    metrics['translation_to_selected_tts_request_seconds'] = dispatch_delay
                    metrics['initial_tts_first_chunk_seconds'] = candidates[0]['tts_first_chunk_seconds']
                    metrics['total_tts_seconds'] = sum(c['tts_seconds'] for c in candidates)
                    metrics['total_qa_seconds'] = sum(c['qa_seconds'] for c in candidates)
                    metrics['tts_to_source_duration_ratio'] = candidate['audio_seconds'] / duration
                    metrics['presentation_audio_seconds'] = candidate['presentation_audio_seconds']
                    metrics['presentation_to_source_duration_ratio'] = (
                        candidate['presentation_audio_seconds']/presentation_duration)
                    metrics['timing_control'] = candidate['timing_control']
                    metrics['presentation_fallback'] = candidate['presentation_fallback']
                    metrics['conservative_timing_fallback'] = candidate['conservative_timing_fallback']
                    metrics['speaker_embedding_similarity'] = candidate.get('speaker_embedding_similarity')
                    metrics['preservation_diagnostics'] = [asdict(issue) for issue in issues]
                    break
            if played is None:
                # Keep the faithful translation available without presenting
                # rejected PCM as speech. The controlled player shows this
                # phrase's text on its source timeline, then continues.
                played, output_chunk_sizes = b'', []
                metrics.update(presentation_mode='caption_only', speech_generated=False,
                    tts_backend='unavailable', attempted_tts_backend='native-voxcpm2',
                    fallback_reason='bounded_tts_qa_failed', retry_count=len(candidates)-1,
                    tts_first_chunk_seconds=None, tts_rtf=None, audio_seconds=0.,
                    tts_seconds=sum(c['tts_seconds'] for c in candidates),
                    qa_seconds=sum(c['qa_seconds'] for c in candidates),
                    total_tts_seconds=sum(c['tts_seconds'] for c in candidates),
                    total_qa_seconds=sum(c['qa_seconds'] for c in candidates),
                    presentation_audio_seconds=0., speaker_embedding_similarity=None)
            else:
                metrics.update(presentation_mode='speech', speech_generated=True)
            metrics['qa_asr_word_timing_alignment_fallback_count'] = sum(
                int(candidate.get('qa_asr_word_timing_alignment_fallback_count', 0))
                for candidate in candidates)
            metrics['tts_candidate_timings'] = [{
                **{key: candidate.get(key) for key in (
                    'conditioning_cache_seconds', 'conditioning_started_at',
                    'conditioning_completed_at', 'tts_started_at', 'tts_first_chunk_at',
                    'tts_completed_at', 'qa_started_at', 'qa_completed_at',
                    'speaker_similarity_started_at', 'speaker_similarity_completed_at',
                    'speaker_similarity_seconds', 'tts_first_chunk_seconds', 'tts_seconds',
                    'tts_rtf', 'qa_seconds', 'qa_signal_seconds',
                    'qa_signal_started_at', 'qa_signal_completed_at',
                    'presentation_timing_seconds', 'qa_asr_lock_wait_seconds',
                    'presentation_timing_started_at', 'presentation_timing_completed_at',
                    'qa_asr_lock_requested_at', 'qa_asr_lock_acquired_at',
                    'qa_asr_started_at', 'qa_asr_completed_at',
                    'qa_asr_inference_seconds', 'qa_text_evaluation_seconds',
                    'qa_asr_word_timing_alignment_fallback_count',
                    'qa_text_evaluation_started_at', 'qa_text_evaluation_completed_at', 'failures')},
                'attempt': index,
                'retry_strategy': candidate.get('retry_strategy'),
                'presentation_fallback': candidate.get('presentation_fallback'),
                'conditioning_cache_hit': (candidate.get('conditioning_cache') or {}).get('cache_hit'),
            } for index, candidate in enumerate(candidates)]
            self.memory.remember(ContextItem('live_committed', source_text, target, choice.speaker_id,
                                        metadata={'phrase_id': identifier}))
            # Index the full source turn once. Bare entity duplicates used to
            # outrank the sentence supplying their context and distort corpus
            # frequencies; explicit glossary mappings remain separate.
            rendered = RenderedPhrase(identifier, source_start, source_end, source_text, target,
                                      asdict(choice), played, output_chunk_sizes, metrics, candidates)
            metrics['render_total_seconds'] = max(0.0, time.perf_counter()-render_started)
            scheduler_waits = self.inference_scheduler.thread_metrics()
            metrics['inference_scheduler_wait_seconds_by_stage'] = scheduler_waits
            metrics['inference_scheduler_total_wait_seconds'] = round(sum(scheduler_waits.values()), 6)
            metrics['inference_scheduler_enabled'] = self.inference_scheduler.enabled
            trace = asdict(rendered)
            trace.pop('pcm')
            trace['speaker']['reference'] = str(choice.reference) if choice.reference else None
            (self.directory / f'{identifier}.json').write_text(json.dumps(trace, indent=2, ensure_ascii=False, default=str))
            return rendered

    def close(self):
        self.closed = True
        with self.lock:
            closed = set()
            for attribute in ('translator', 'boundary_selector', 'speaker_models', 'memory'):
                value = getattr(self, attribute, None)
                if value is not None:
                    if id(value) not in closed:
                        value.close()
                        closed.add(id(value))
                    setattr(self, attribute, None)
            self.voxcpm = self.asr = None
            self._prompt_caches.clear()
            if 'torch' in sys.modules:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
