"""Session-scoped local inference process, including bounded cancellation.

The HTTP process never owns CUDA model tensors. Stopping a session exits its
model process so CUDA contexts and the isolated translation/speaker children
are actually released rather than leaving a resident GPU context behind.
The pipe is inherited privately; no pickle endpoint is exposed on the network.
"""
from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import multiprocessing as mp
import os
from pathlib import Path
import signal
import threading
import time
import traceback
import uuid


def _model_main(connection, stopped, directory, options):
    if os.name == 'posix':
        os.setsid()
    models = None
    writer = threading.Lock()
    native_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-native-owner')
    asr_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-asr-priority')
    source_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix='live-source')
    def send(value):
        with writer:
            connection.send(value)
    try:
        import torch
        from .models import LocalModels
        device = options.pop('device', 'auto')
        if device == 'auto':
            device = 'cuda' if torch.cuda.is_available() else 'cpu'
        # CUDA Graph caches are thread-local. Construction/warm-up and every
        # native render must share one persistent owner, including across RPCs.
        models = native_pool.submit(LocalModels, Path(directory), device=device, **options).result()
        send({'ready': True, 'load_seconds': models.load_seconds,
              'contextual_chunking':models.contextual_chunking,
              'live_boundary_backend':getattr(models, 'live_boundary_backend', 'sat'),
              'live_translation_backend':getattr(models, 'live_translation_backend', 'hymt2-1.8b')})
        def cancellation():
            stopped.wait()
            models.closed = True
        threading.Thread(target=cancellation, daemon=True).start()
        def call(request, worker_received_at):
            identifier = request['id']
            try:
                worker_started_at = time.perf_counter()
                operation = request['operation']
                if operation == 'recognize':
                    result = models.recognize(*request['args'])
                elif operation == 'render':
                    result = models.render(**request['kwargs'])
                elif operation == 'translate_draft':
                    result = models.translate_draft(**request['kwargs'])
                elif operation == 'select_boundary':
                    result = models.select_boundary(**request['kwargs'])
                elif operation == 'assess_source_boundary':
                    result = models.assess_source_boundary(**request['kwargs'])
                elif operation == 'assess_simultaneous_candidate':
                    result = models.assess_simultaneous_candidate(**request['kwargs'])
                elif operation == 'propose_simultaneous':
                    result = models.propose_simultaneous(**request['kwargs'])
                else:
                    raise ValueError('Unsupported local model operation')
                worker_completed_at = time.perf_counter()
                send({'id': identifier, 'ok': True, 'result': result,
                      'worker_timing': {'pool_wait_seconds': max(0.0, worker_started_at-worker_received_at),
                          'execution_seconds': max(0.0, worker_completed_at-worker_started_at),
                          'worker_started_at': worker_started_at,
                          'worker_completed_at': worker_completed_at}})
            except Exception as exc:
                send({'id': identifier, 'ok': False, 'error': f'{type(exc).__name__}: {exc}',
                      'traceback': traceback.format_exc()})
        while not stopped.is_set():
            if connection.poll(.1):
                request = connection.recv()
                worker_received_at = time.perf_counter()
                if request.get('operation') == 'stop':
                    stopped.set()
                    break
                operation = request.get('operation')
                if operation == 'render':
                    pool = native_pool
                elif operation == 'recognize':
                    # ASR must not sit behind a slow contextual boundary or
                    # translation request. The live decoder coalesces source
                    # windows, so this single-worker lane stays bounded.
                    pool = asr_pool
                else:
                    pool = source_pool
                pool.submit(call, request, worker_received_at)
    except (EOFError, BrokenPipeError):
        stopped.set()
    except Exception as exc:
        try:
            send({'ready': False, 'error': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc()})
        except (EOFError, BrokenPipeError):
            pass
    finally:
        try:
            if models is not None:
                native_pool.submit(models.close).result(timeout=5)
        except FutureTimeoutError:
            # The HTTP owner enforces bounded process-group termination
            # if a native kernel or compilation cannot finish on Stop.
            pass
        finally:
            native_pool.shutdown(wait=False, cancel_futures=True)
            asr_pool.shutdown(wait=False, cancel_futures=True)
            source_pool.shutdown(wait=False, cancel_futures=True)
            connection.close()


class ModelProcess:
    def __init__(self, directory, *, source_language=None, target_language='tr',
                 whisper_model='small', device='auto', startup_cancel=None, glossary=None):
        self.source_language = None if source_language in (None, 'auto') else source_language
        self.whisper_model = whisper_model
        self.closed = self._disposed = False
        self._writer = threading.Lock()
        self._lock = threading.Lock()
        self._futures = {}
        self._errors_path = Path(directory) / 'model-inference-error.txt'
        context = mp.get_context('spawn')
        self._connection, child = context.Pipe()
        self._stopped = context.Event()
        self.process = context.Process(target=_model_main, args=(child, self._stopped,
            str(directory), {'source_language': self.source_language, 'target_language': target_language,
            'whisper_model': whisper_model, 'device': device, 'glossary': glossary}))
        started = time.perf_counter()
        self.process.start()
        child.close()
        try:
            while not self._connection.poll(.1):
                if not self.process.is_alive():
                    raise RuntimeError(f'Live model process exited during startup ({self.process.exitcode})')
                if startup_cancel is not None and startup_cancel.is_set():
                    raise RuntimeError('Live startup cancelled before capture')
                if time.perf_counter()-started > 120:
                    raise RuntimeError('Local model preparation exceeded the bounded startup deadline')
            result = self._connection.recv()
            if not result.get('ready'):
                (Path(directory) / 'model-startup-error.json').write_text(__import__('json').dumps(result, indent=2))
                raise RuntimeError(result.get('error', 'Local model startup failed'))
            from .speech import SileroStream
            self.vad = SileroStream()  # CPU only, no CUDA model in the HTTP process
            self.load_seconds = time.perf_counter()-started
            self.child_load_seconds = result['load_seconds']
            self.contextual_chunking = result.get('contextual_chunking', False)
            self.live_boundary_backend = result.get('live_boundary_backend', 'sat')
            self.live_translation_backend = result.get('live_translation_backend', 'hymt2-1.8b')
            threading.Thread(target=self._read, daemon=True).start()
        except Exception:
            self.close()
            raise

    def _read(self):
        try:
            while not self._disposed:
                response = self._connection.recv()
                with self._lock:
                    future = self._futures.pop(response.get('id'), None)
                if future is not None:
                    if response.get('ok'):
                        future.set_result((response['result'], response.get('worker_timing', {})))
                    else:
                        if response.get('traceback'):
                            self._errors_path.write_text(response['traceback'])
                        future.set_exception(RuntimeError(response.get('error', 'Local inference failed')))
        except (EOFError, OSError):
            pass
        finally:
            with self._lock:
                waiting, self._futures = self._futures, {}
            for future in waiting.values():
                future.set_exception(RuntimeError('Live model process stopped'))

    def _request(self, operation, *, args=(), kwargs=None):
        if self.closed:
            raise RuntimeError('Live model session stopped')
        identifier, future = uuid.uuid4().hex, Future()
        with self._lock:
            self._futures[identifier] = future
        dispatched_at = time.perf_counter()
        with self._writer:
            self._connection.send({'id': identifier, 'operation': operation,
                                   'args': args, 'kwargs': kwargs or {},
                                   'client_dispatched_at': dispatched_at})
        try:
            timeout = (12.0 if operation == 'assess_simultaneous_candidate' else
                       8.0 if operation in ('select_boundary', 'assess_source_boundary')
                       else 90.0)
            result, worker_timing = future.result(timeout=timeout)
            completed_at = time.perf_counter()
            return result, {**worker_timing,
                'rpc_roundtrip_seconds': max(0.0, completed_at-dispatched_at),
                'client_dispatched_at': dispatched_at,
                'client_completed_at': completed_at}
        finally:
            with self._lock:
                self._futures.pop(identifier, None)

    def recognize(self, pcm, start=0.0, language=None):
        result, timing = self._request('recognize', args=(pcm, start, language))
        from dataclasses import replace
        return replace(result, worker_timing=timing)

    def render(self, **kwargs):
        result, timing = self._request('render', kwargs=kwargs)
        result.metrics.update({f'model_worker_{key}': value for key, value in timing.items()})
        return result

    def translate_draft(self, **kwargs):
        result, timing = self._request('translate_draft', kwargs=kwargs)
        result.setdefault('metrics', {}).update({f'model_worker_{key}': value for key, value in timing.items()})
        return result

    def select_boundary(self, **kwargs):
        result, timing = self._request('select_boundary', kwargs=kwargs)
        result.setdefault('metrics', {}).update({f'model_worker_{key}': value for key, value in timing.items()})
        return result

    def assess_source_boundary(self, **kwargs):
        result, timing = self._request('assess_source_boundary', kwargs=kwargs)
        result.setdefault('metrics', {}).update({f'model_worker_{key}': value for key, value in timing.items()})
        return result

    def assess_simultaneous_candidate(self, **kwargs):
        result, timing = self._request('assess_simultaneous_candidate', kwargs=kwargs)
        result.setdefault('metrics', {}).update({f'model_worker_{key}': value for key, value in timing.items()})
        return result


    def propose_simultaneous(self, **kwargs):
        result, timing = self._request('propose_simultaneous', kwargs=kwargs)
        result.setdefault('metrics', {}).update({f'model_worker_{key}': value for key, value in timing.items()})
        return result

    def close(self):
        if self._disposed:
            return
        self.closed = self._disposed = True
        self._stopped.set()
        try:
            with self._writer:
                self._connection.send({'operation': 'stop'})
        except (OSError, BrokenPipeError):
            pass
        self.process.join(timeout=5)
        if self.process.is_alive():
            if os.name == 'posix':
                # The group is created in this dedicated child, never the app's
                # or user's process group. It owns only this session's workers.
                try:
                    if os.getpgid(self.process.pid) == self.process.pid:
                        os.killpg(self.process.pid, signal.SIGTERM)
                    else:
                        self.process.terminate()
                except ProcessLookupError:
                    pass
            else:
                self.process.terminate()
            self.process.join(timeout=5)
        if self.process.is_alive():
            self.process.kill()
            self.process.join()
        self._connection.close()
