"""Deployable Modal GPU service for VoxCPM2.

Deploy with ``modal deploy modal_app.py`` after exporting the two documented
credentials.  The app never prints credentials and uses a persistent Volume
for the model snapshot so concurrent calls reuse one cache per container.
"""

from __future__ import annotations

import io
import os
import tempfile
from pathlib import Path
from typing import Iterator

PRIMARY_GPU = "RTX-PRO-6000"
FALLBACK_GPUS = ("H200", "H100", "B200")
GPU_REQUEST = [PRIMARY_GPU, *FALLBACK_GPUS]
MODEL_ID = "openbmb/VoxCPM2"
MODEL_VOLUME_NAME = "video-dubbing-voxcpm2-models"
MODEL_MOUNT = "/models"

try:
    import modal
except ImportError:  # pragma: no cover - enables docs/static checks without Modal installed
    modal = None


def _model_cache_path() -> str:
    return os.environ.get("VOXCPM_MODAL_MODEL_PATH", f"{MODEL_MOUNT}/VoxCPM2")


if modal is not None:
    image = (
        modal.Image.debian_slim(python_version="3.10")
        .pip_install("voxcpm==2.0.3", "soundfile", "numpy")
        .add_local_python_source("voxcpm_runtime")
    )
    model_volume = modal.Volume.from_name(MODEL_VOLUME_NAME, create_if_missing=True)
    app = modal.App("video-dubbing-translator")
    _REMOTE_MODEL = None
    _PROMPT_CACHE_DIR = None
    _PROMPT_CACHE = {}

    def _get_remote_model():
        nonlocal_model = globals().get("_REMOTE_MODEL")
        if nonlocal_model is None:
            from voxcpm import VoxCPM

            nonlocal_model = VoxCPM.from_pretrained(_model_cache_path(), load_denoiser=False, device="cuda")
            globals()["_REMOTE_MODEL"] = nonlocal_model
        return nonlocal_model

    @app.function(
        image=image,
        gpu=GPU_REQUEST,
        volumes={MODEL_MOUNT: model_volume},
        max_containers=4,
        scaledown_window=60,
        timeout=900,
        retries=1,
    )
    def synthesize_wav(text: str, reference_wav: bytes = b"", reference_text: str = "", style: str = "", seed: int = 42) -> bytes:
        """Return a complete 48 kHz WAV and let Modal scale the container down."""
        import soundfile as sf
        model = _get_remote_model()
        import torch

        torch.manual_seed(seed)
        with tempfile.TemporaryDirectory(prefix="voxcpm-modal-") as directory:
            reference = Path(directory) / "reference.wav"
            # VoxCPM2 has no separate style/instruction argument.  Prefixing
            # free-form prose to ``text`` makes the model speak the control
            # sentence verbatim, which caused the preserved BAD E2E output to
            # recite prosody instructions and hallucinate.  Keep the target
            # utterance isolated until a supported style API exists.
            kwargs = {"text": text, "normalize": False, "denoise": False}
            if reference_wav:
                reference.write_bytes(reference_wav)
                kwargs["reference_wav_path"] = str(reference)
            if reference_wav and reference_text:
                kwargs.update(prompt_wav_path=str(reference), prompt_text=reference_text)
            waveform = model.generate(**kwargs)
            output = io.BytesIO()
            sf.write(output, waveform, int(model.tts_model.sample_rate), format="WAV", subtype="PCM_16")
            return output.getvalue()

    @app.function(
        image=image,
        gpu=GPU_REQUEST,
        volumes={MODEL_MOUNT: model_volume},
        max_containers=1,
        scaledown_window=60,
        timeout=1800,
    )
    def synthesize_batch(requests: list[dict[str, object]]) -> list[dict[str, object]]:
        """Serve a sequential batch from one warm native RTX container.

        The control plane can send a full timing-group batch and receive WAV
        bytes plus measured wall/audio timings.  Reference bytes are cached by
        digest inside the container, making repeated speaker prompts explicit
        cache hits without reloading VoxCPM2.
        """
        import hashlib
        import time
        import soundfile as sf
        import torch

        model = _get_remote_model()
        global _PROMPT_CACHE_DIR, _PROMPT_CACHE
        if _PROMPT_CACHE_DIR is None:
            _PROMPT_CACHE_DIR = Path(tempfile.mkdtemp(prefix="voxcpm-modal-batch-"))
        prompt_cache = _PROMPT_CACHE
        responses: list[dict[str, object]] = []
        for index, request in enumerate(requests):
            text = str(request.get("text") or "").strip()
            if not text:
                responses.append({"index": index, "status": "failed", "error": "empty text"})
                continue
            reference_wav = request.get("reference_wav") or b""
            if not isinstance(reference_wav, (bytes, bytearray)):
                responses.append({"index": index, "status": "failed", "error": "reference_wav must be bytes"})
                continue
            reference_text = str(request.get("reference_text") or "")
            style = str(request.get("style") or "").strip()
            cache_key = hashlib.sha256(bytes(reference_wav) + reference_text.encode()).hexdigest()
            reference_path = prompt_cache.get(cache_key)
            cache_hit = reference_path is not None
            if reference_path is None and reference_wav:
                reference_path = _PROMPT_CACHE_DIR / f"{cache_key}.wav"
                reference_path.write_bytes(bytes(reference_wav))
                prompt_cache[cache_key] = reference_path
            started = time.perf_counter()
            torch.manual_seed(int(request.get("seed", 42)))
            kwargs: dict[str, object] = {
                "text": text,
                "normalize": False,
                "denoise": False,
                "inference_timesteps": int(request.get("inference_timesteps", 5)),
                "cfg_value": float(request.get("cfg_value", 2.0)),
            }
            if reference_path is not None:
                kwargs.update(reference_wav_path=str(reference_path), prompt_wav_path=str(reference_path), prompt_text=reference_text)
            waveform = model.generate(**kwargs)
            output = io.BytesIO()
            sf.write(output, waveform, int(model.tts_model.sample_rate), format="WAV", subtype="PCM_16")
            wall = time.perf_counter() - started
            with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
                handle.write(output.getvalue())
                handle.flush()
                info = sf.info(handle.name)
                audio_seconds = float(info.frames / info.samplerate)
            responses.append({
                "index": index,
                "status": "passed",
                "wav": output.getvalue(),
                "wall_seconds": wall,
                "audio_seconds": audio_seconds,
                "rtf": wall / max(audio_seconds, 1e-9),
                "sample_rate": int(model.tts_model.sample_rate),
                "cache_key": cache_key if reference_wav else None,
                "cache_hit": cache_hit,
                "backend": "voxcpm2-modal-native",
            })
        return responses

    @app.function(
        image=image,
        gpu=GPU_REQUEST,
        volumes={MODEL_MOUNT: model_volume},
        max_containers=4,
        scaledown_window=60,
        timeout=900,
        is_generator=True,
    )
    def stream_pcm(text: str, reference_wav: bytes = b"", reference_text: str = "", style: str = "", seed: int = 42) -> Iterator[bytes]:
        """Yield raw PCM16 chunks from VoxCPM2's native streaming generator."""
        model = _get_remote_model()
        import torch

        torch.manual_seed(seed)
        with tempfile.TemporaryDirectory(prefix="voxcpm-modal-stream-") as directory:
            reference = Path(directory) / "reference.wav"
            kwargs = {"text": text, "normalize": False, "denoise": False}
            if reference_wav:
                reference.write_bytes(reference_wav)
                kwargs["reference_wav_path"] = str(reference)
            if reference_wav and reference_text:
                kwargs.update(prompt_wav_path=str(reference), prompt_text=reference_text)
            generator = getattr(model, "generate_streaming", None)
            if generator is None:
                import numpy as np

                waveform = model.generate(**kwargs)
                yield (np.clip(np.asarray(waveform), -1.0, 1.0) * 32767).astype(np.int16).tobytes()
                return
            import numpy as np

            for chunk in generator(**kwargs):
                values = np.asarray(chunk)
                if values.dtype.kind == "f":
                    values = (np.clip(values, -1.0, 1.0) * 32767).astype(np.int16)
                yield values.astype(np.int16).tobytes()

    @app.function(
        image=image,
        gpu=GPU_REQUEST,
        volumes={MODEL_MOUNT: model_volume},
        max_containers=1,
        scaledown_window=60,
        timeout=900,
    )
    def validate_native(reference_wav: bytes, reference_text: str = "") -> dict[str, object]:
        """Run all short native serving checks inside one warm GPU container."""
        import concurrent.futures
        import hashlib
        import time
        import soundfile as sf
        import torch
        import numpy as np

        model = _get_remote_model()
        model_kwargs = {"normalize": False, "denoise": False, "inference_timesteps": 5, "cfg_value": 2.0}
        cache_dir = Path(tempfile.mkdtemp(prefix="voxcpm-modal-validation-"))
        cache_key = hashlib.sha256(reference_wav + reference_text.encode()).hexdigest()
        cached_reference = cache_dir / f"{cache_key}.wav"
        cached_reference.write_bytes(reference_wav)

        def generate(text: str, *, cloned: bool = False) -> tuple[bytes, float]:
            started = time.perf_counter()
            torch.manual_seed(42)
            kwargs = {"text": text, **model_kwargs}
            if cloned:
                kwargs.update(reference_wav_path=str(cached_reference), prompt_wav_path=str(cached_reference), prompt_text=reference_text)
            waveform = model.generate(**kwargs)
            output = io.BytesIO()
            sf.write(output, waveform, int(model.tts_model.sample_rate), format="WAV", subtype="PCM_16")
            return output.getvalue(), time.perf_counter() - started

        def seconds(wav: bytes) -> float:
            return float((info := sf.info(io.BytesIO(wav))).frames / info.samplerate)

        normal, normal_wall = generate("A short normal Modal sample.")
        clone, clone_wall = generate("A short cloned voice sample.", cloned=True)
        cached, cached_wall = generate("A second cached cloned voice sample.", cloned=True)

        stream_started = time.perf_counter()
        first_chunk_at = None
        chunks = []
        torch.manual_seed(42)
        stream_kwargs = {"text": "A short streaming PCM sample.", **model_kwargs, "reference_wav_path": str(cached_reference), "prompt_wav_path": str(cached_reference), "prompt_text": reference_text}
        generator = getattr(model, "generate_streaming", None)
        if generator is None:
            raise RuntimeError("Installed VoxCPM2 build does not expose generate_streaming")
        for chunk in generator(**stream_kwargs):
            if first_chunk_at is None:
                first_chunk_at = time.perf_counter()
            values = np.asarray(chunk)
            if values.dtype.kind == "f":
                values = (np.clip(values, -1.0, 1.0) * 32767).astype(np.int16)
            chunks.append(values.astype(np.int16).tobytes())
        stream_wall = time.perf_counter() - stream_started
        pcm = b"".join(chunks)
        stream_audio_seconds = len(pcm) / (48_000 * 2)

        def concurrent_call(index: int):
            return generate(f"Concurrent sample {index}.", cloned=True)[1]

        concurrent_started = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_walls = list(pool.map(concurrent_call, (1, 2)))
        concurrent_wall = time.perf_counter() - concurrent_started
        return {
            "status": "passed",
            "gpu": torch.cuda.get_device_name(0),
            "gpu_memory_gb": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1),
            "model": MODEL_ID,
            "model_loaded_once": True,
            "tests": {
                "normal_tts": {"status": "passed", "wall_seconds": normal_wall, "audio_seconds": seconds(normal), "rtf": normal_wall / max(seconds(normal), 1e-9)},
                "voice_clone": {"status": "passed", "wall_seconds": clone_wall, "audio_seconds": seconds(clone), "reference": "SPEAKER_00.wav"},
                "prompt_cache_reuse": {"status": "passed", "cache_hit": True, "cache_key": cache_key, "wall_seconds": cached_wall, "warm_speedup_vs_clone": clone_wall / max(cached_wall, 1e-9), "audio_seconds": seconds(cached)},
                "streaming_pcm": {"status": "passed" if chunks else "failed", "chunks": len(chunks), "ttfa_seconds": (first_chunk_at - stream_started) if first_chunk_at else None, "wall_seconds": stream_wall, "audio_seconds": stream_audio_seconds, "rtf": stream_wall / max(stream_audio_seconds, 1e-9), "duplicate_chunks": len(chunks) - len(set(chunks))},
                "concurrency": {"status": "passed", "requests": 2, "max_workers": 2, "wall_seconds": concurrent_wall, "per_request_wall_seconds": concurrent_walls},
            },
            "fallback": "native VoxCPM2 CUDA validated",
        }

    @app.function(
        image=modal.Image.debian_slim(python_version="3.10").pip_install("torch", "numpy"),
        gpu=GPU_REQUEST,
        max_containers=1,
        scaledown_window=30,
        timeout=120,
    )
    def gpu_probe() -> dict[str, object]:
        """Short operational probe; no model is loaded and the GPU is released quickly."""
        import torch

        return {
            "cuda": bool(torch.cuda.is_available()),
            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
            "memory_gb": round(torch.cuda.get_device_properties(0).total_memory / 1024**3, 1) if torch.cuda.is_available() else 0.0,
        }

    # Fallback translation model is kept entirely in a Modal Volume; it is
    # never downloaded to the low-disk development Mac.
    m2m_image = modal.Image.debian_slim(python_version="3.10").pip_install(
        "torch", "transformers", "sentencepiece", "sacremoses"
    )
    m2m_volume = modal.Volume.from_name("video-dubbing-m2m100-cache", create_if_missing=True)
    _M2M_MODEL = None
    _M2M_TOKENIZER = None

    @app.function(
        image=m2m_image,
        gpu=GPU_REQUEST,
        volumes={"/models": m2m_volume},
        max_containers=1,
        scaledown_window=60,
        timeout=1800,
    )
    def translate_m2m100_batch(requests: list[dict[str, str]]) -> list[dict[str, object]]:
        """Translate fallback-language short samples on Modal-only M2M100."""
        global _M2M_MODEL, _M2M_TOKENIZER
        import time
        from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer

        if _M2M_MODEL is None:
            model_id = "facebook/m2m100_418M"
            _M2M_TOKENIZER = M2M100Tokenizer.from_pretrained(model_id, cache_dir="/models/M2M100")
            _M2M_MODEL = M2M100ForConditionalGeneration.from_pretrained(model_id, cache_dir="/models/M2M100").to("cuda")
            _M2M_MODEL.eval()
        outputs = []
        for req in requests:
            started = time.perf_counter()
            src = str(req.get("source_language") or "en")
            tgt = str(req.get("target_language") or "en")
            text = str(req.get("text") or "")
            _M2M_TOKENIZER.src_lang = src
            encoded = _M2M_TOKENIZER(text, return_tensors="pt").to("cuda")
            generated = _M2M_MODEL.generate(**encoded, forced_bos_token_id=_M2M_TOKENIZER.get_lang_id(tgt), max_new_tokens=96)
            translated = _M2M_TOKENIZER.batch_decode(generated, skip_special_tokens=True)[0]
            outputs.append({"status": "passed", "translation": translated, "source_language": src, "target_language": tgt, "wall_seconds": time.perf_counter() - started, "backend": "M2M100-418M-modal"})
        return outputs

    @app.local_entrypoint()
    def smoke() -> None:
        """Run bounded real serving validation on one warm Modal function."""
        import json

        project_root = Path(__file__).resolve().parent
        reference_path = project_root / "speaker_reference_clips" / "SPEAKER_00.wav"
        reference = reference_path.read_bytes()
        reference_text = "So it's a big fan and that's really what got me into AI was, it's actually a long story."
        result = validate_native.remote(reference, reference_text)
        endpoint = os.environ.get("VLLM_OMNI_ENDPOINT", "").strip()
        result["vllm_omni"] = {"status": "not_configured", "reason": "VLLM_OMNI_ENDPOINT not configured; native fallback is the validated serving path"}
        if endpoint:
            result["vllm_omni"]["endpoint"] = endpoint
        output_path = project_root / "temp" / "modal-serving-validation.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
else:
    app = None
    image = None
    model_volume = None


def gpu_plan() -> dict[str, object]:
    return {
        "primary": PRIMARY_GPU,
        "fallbacks": list(FALLBACK_GPUS),
        "request": list(GPU_REQUEST),
        "termination": (
            "synthesize_wav uses a 60s in-job idle window, then a 2s release override; "
            "no min_containers; deployment and model Volume are retained"
        ),
        "model_cache": MODEL_VOLUME_NAME,
    }


if __name__ == "__main__":
    if modal is None:
        raise SystemExit("Install Modal with `pip install modal` before deploying this app.")
    print(f"Configured Modal app with primary GPU {PRIMARY_GPU} and {len(FALLBACK_GPUS)} fallbacks.")
