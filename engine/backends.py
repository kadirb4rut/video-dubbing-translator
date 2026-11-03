from __future__ import annotations

import base64
import io
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence

import numpy as np

from .contracts import (
    AudioResult,
    BackendUnavailable,
    InferenceMetrics,
    ProsodyProfile,
    SpeakerProfile,
    TTSBackend,
    TranslationBackend,
)


class NativeVoxCPMBackend(TTSBackend):
    """Native CUDA/CPU VoxCPM2 backend with lazy model loading.

    The model is loaded once per process and the lock is held only during the
    non-thread-safe generation call.  This makes it safe to put behind the
    concurrent worker pool while avoiding duplicate multi-gigabyte loads.
    """

    name = "voxcpm2-native"

    def __init__(self, model=None, device: Optional[str] = None) -> None:
        import threading

        self._model = model
        self.device = device
        self._lock = threading.Lock()

    @property
    def model(self):
        if self._model is None:
            from voxcpm_runtime import load_model

            self._model = load_model(self.device)
        return self._model

    def synthesize(
        self,
        text: str,
        speaker: SpeakerProfile,
        *,
        prosody: Optional[ProsodyProfile] = None,
        style: Optional[str] = None,
        output_wav: Optional[Path] = None,
        seed: int = 42,
    ) -> AudioResult:
        if not text or not text.strip():
            raise ValueError("TTS text must not be empty")
        if output_wav is None:
            raise ValueError("NativeVoxCPMBackend requires output_wav for batch synthesis")
        from voxcpm_runtime import synthesize_cloned_speech

        output_wav = Path(output_wav).expanduser().resolve()
        started = time.perf_counter()
        with self._lock:
            synthesize_cloned_speech(
                model=self.model,
                text=text,
                reference_wav_path=speaker.reference_wav,
                output_wav_path=output_wav,
                prompt_text=speaker.transcript or None,
                seed=seed,
                style=style,
                prosody=prosody,
            )
        wall = time.perf_counter() - started
        try:
            import soundfile as sf

            info = sf.info(output_wav)
            audio_seconds = float(info.frames) / float(info.samplerate)
            sample_rate = int(info.samplerate)
            channels = int(info.channels)
        except Exception:
            audio_seconds = None
            sample_rate, channels = self.sample_rate, 1
        return AudioResult(
            wav_path=output_wav,
            sample_rate=sample_rate,
            channels=channels,
            metrics=InferenceMetrics(
                backend=self.name,
                wall_seconds=wall,
                audio_seconds=audio_seconds,
                request_id=uuid.uuid4().hex,
            ),
            backend=self.name,
        )

    def stream(
        self,
        text: str,
        speaker: SpeakerProfile,
        *,
        prosody: Optional[ProsodyProfile] = None,
        style: Optional[str] = None,
    ) -> Iterator[bytes]:
        from voxcpm_runtime import stream_cloned_speech

        import soundfile as sf

        with self._lock:
            for chunk in stream_cloned_speech(
                self.model,
                text,
                speaker.reference_wav,
                prompt_text=speaker.transcript or None,
                style=style,
            ):
                array = np.asarray(chunk)
                # Native chunks are float32 in [-1, 1].  Convert to the PCM16
                # contract used by the HTTP/vLLM and live playback paths.
                if np.issubdtype(array.dtype, np.floating):
                    array = np.clip(array, -1.0, 1.0)
                    array = (array * 32767.0).astype(np.int16)
                elif array.dtype != np.int16:
                    array = array.astype(np.int16)
                yield array.tobytes()


class ModalVoxCPMBackend(TTSBackend):
    """OpenAI-compatible client for the deployed Modal VoxCPM service."""

    name = "voxcpm2-modal"

    def __init__(self, endpoint: str, *, token_id: Optional[str] = None, token_secret: Optional[str] = None, timeout: float = 900.0):
        self.endpoint = endpoint.rstrip("/")
        self.token_id = token_id or os.environ.get("MODAL_TOKEN_ID")
        self.token_secret = token_secret or os.environ.get("MODAL_TOKEN_SECRET")
        self.timeout = timeout
        if not self.endpoint.startswith(("http://", "https://")):
            raise ValueError("Modal endpoint must be an http(s) URL")

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "Accept": "audio/wav"}
        if self.token_id and self.token_secret:
            headers["Authorization"] = f"Bearer {self.token_id}.{self.token_secret}"
        return headers

    @staticmethod
    def _reference_data_uri(path: Path) -> str:
        payload = base64.b64encode(Path(path).read_bytes()).decode("ascii")
        return "data:audio/wav;base64," + payload

    def _payload(self, text: str, speaker: SpeakerProfile, *, style: Optional[str], stream: bool) -> dict[str, object]:
        payload: dict[str, object] = {
            "model": "voxcpm2",
            "input": text,
            "voice": speaker.speaker_id,
            "ref_audio": self._reference_data_uri(speaker.reference_wav),
            "ref_text": speaker.transcript or None,
            "response_format": "pcm" if stream else "wav",
        }
        if style:
            payload["instructions"] = style
        if stream:
            payload.update(stream=True, stream_format="audio")
        return payload

    def synthesize(self, text: str, speaker: SpeakerProfile, *, prosody: Optional[ProsodyProfile] = None, style: Optional[str] = None, output_wav: Optional[Path] = None, seed: int = 42) -> AudioResult:
        if output_wav is None:
            raise ValueError("ModalVoxCPMBackend requires output_wav for batch synthesis")
        started = time.perf_counter()
        request = urllib.request.Request(
            self.endpoint + "/v1/audio/speech",
            data=json.dumps(self._payload(text, speaker, style=style, stream=False)).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                data = response.read()
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BackendUnavailable(f"Modal VoxCPM endpoint unavailable: {exc}") from exc
        output_wav = Path(output_wav).expanduser().resolve()
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        output_wav.write_bytes(data)
        audio_seconds = None
        sample_rate = 48_000
        channels = 1
        try:
            import soundfile as sf

            info = sf.info(output_wav)
            sample_rate, channels = int(info.samplerate), int(info.channels)
            audio_seconds = info.frames / info.samplerate
        except Exception:
            pass
        return AudioResult(
            wav_path=output_wav,
            sample_rate=sample_rate,
            channels=channels,
            metrics=InferenceMetrics(self.name, time.perf_counter() - started, audio_seconds, request_id=uuid.uuid4().hex),
            backend=self.name,
        )

    def stream(self, text: str, speaker: SpeakerProfile, *, prosody: Optional[ProsodyProfile] = None, style: Optional[str] = None) -> Iterator[bytes]:
        request = urllib.request.Request(
            self.endpoint + "/v1/audio/speech",
            data=json.dumps(self._payload(text, speaker, style=style, stream=True)).encode(),
            headers=self._headers(),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    yield chunk
        except (urllib.error.URLError, TimeoutError) as exc:
            raise BackendUnavailable(f"Modal VoxCPM stream unavailable: {exc}") from exc


class VLLMOmniBackend(ModalVoxCPMBackend):
    """vLLM-Omni-compatible HTTP backend.

    The request shape follows the official speech endpoint: normal WAV calls
    use ``response_format=wav``; raw streaming uses ``stream=true``,
    ``stream_format=audio``, and ``response_format=pcm``.
    """

    name = "voxcpm2-vllm-omni"


class BackendRouter(TTSBackend):
    """Try vLLM/Modal first, then native local CUDA/CPU as an explicit fallback."""

    name = "tts-router"

    def __init__(self, primary: TTSBackend, fallback: TTSBackend):
        self.primary, self.fallback = primary, fallback
        self.last_backend: Optional[str] = None

    def synthesize(self, text: str, speaker: SpeakerProfile, **kwargs) -> AudioResult:
        errors = []
        for backend in (self.primary, self.fallback):
            try:
                result = backend.synthesize(text, speaker, **kwargs)
                self.last_backend = backend.name
                return result
            except (BackendUnavailable, ConnectionError, TimeoutError, OSError) as exc:
                errors.append(f"{backend.name}: {exc}")
        raise BackendUnavailable("All TTS backends failed: " + " | ".join(errors))

    def stream(self, text: str, speaker: SpeakerProfile, **kwargs) -> Iterator[bytes]:
        for backend in (self.primary, self.fallback):
            try:
                iterator = backend.stream(text, speaker, **kwargs)
                first = next(iterator)
                self.last_backend = backend.name
                yield first
                yield from iterator
                return
            except (BackendUnavailable, ConnectionError, TimeoutError, OSError):
                continue
        raise BackendUnavailable("All TTS streaming backends failed")


class ConcurrentInferencePool:
    """Bounded concurrent inference helper for independent phrase groups."""

    def __init__(self, backend: TTSBackend, max_workers: int = 4):
        self.backend = backend
        self.max_workers = max(1, int(max_workers))

    def map(self, requests: Iterable[tuple[str, SpeakerProfile, Mapping[str, object]]]) -> list[AudioResult]:
        def run(item):
            text, speaker, kwargs = item
            return self.backend.synthesize(text, speaker, **dict(kwargs))

        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="dubbing-infer") as pool:
            return list(pool.map(run, requests))
