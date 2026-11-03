"""One-shot vLLM-Omni VoxCPM2 smoke test on Modal.

This intentionally lives apart from the native app so a failed vLLM image or
server cannot affect the known-good native service. It preflights the runtime,
then sends one normal WAV request, one reference-audio clone request, and one
streaming-PCM request using the official OpenAI-compatible speech fields.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import base64
from pathlib import Path

import modal

GPU_REQUEST = ["RTX-PRO-6000", "H200", "H100", "B200"]
MODEL_PATH = "/models/VoxCPM2"
VOLUME_NAME = "video-dubbing-voxcpm2-models"

image = (
    # vLLM-Omni imports enum.StrEnum at CLI startup; use the upstream
    # quickstart's Python 3.12 runtime instead of Python 3.10 (which fails
    # before the model server can even be launched).
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("vllm==0.28.0", "vllm-omni==0.28.0", "httpx", "soundfile")
)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=False)
app = modal.App("video-dubbing-vllm-omni-smoke")


@app.function(image=image, gpu=GPU_REQUEST, volumes={"/models": volume}, timeout=900, max_containers=1, scaledown_window=60)
def run_smoke(reference_wav: bytes = b"", reference_text: str = "") -> dict[str, object]:
    import soundfile as sf
    import importlib
    import importlib.metadata

    # Keep the preflight evidence separate from server/model evidence. This
    # makes a packaging/import failure actionable instead of looking like an
    # opaque model-startup timeout.
    checks: dict[str, object] = {
        "python": sys.version,
        "python_major_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }
    try:
        from enum import StrEnum  # noqa: F401 - explicit runtime contract
        checks["enum_strenum"] = True
    except Exception as exc:  # pragma: no cover - exercised remotely
        checks["enum_strenum"] = False
        checks["enum_strenum_error"] = f"{type(exc).__name__}: {exc}"
    versions: dict[str, str] = {}
    for package in ("vllm", "vllm-omni", "torch"):
        try:
            versions[package] = importlib.metadata.version(package)
        except Exception as exc:  # pragma: no cover - diagnostic only
            versions[package] = f"unavailable ({type(exc).__name__}: {exc})"
    checks["versions"] = versions
    import_errors: dict[str, str] = {}
    for module_name in ("vllm", "vllm_omni"):
        try:
            importlib.import_module(module_name)
            checks[f"import_{module_name}"] = True
        except Exception as exc:  # pragma: no cover - exercised remotely
            checks[f"import_{module_name}"] = False
            import_errors[module_name] = f"{type(exc).__name__}: {exc}"
    try:
        # This is the same serving import used by the `vllm serve --omni`
        # entrypoint and therefore checks the VoxCPM2-capable serving stack.
        from vllm_omni.entrypoints.openai.api_server import omni_run_server  # noqa: F401
        checks["voxcpm2_serving_import"] = True
    except Exception as exc:  # pragma: no cover - exercised remotely
        checks["voxcpm2_serving_import"] = False
        import_errors["voxcpm2_serving"] = f"{type(exc).__name__}: {exc}"
    if import_errors or checks.get("enum_strenum") is not True:
        return {
            "status": "failed",
            "stage": "preflight_imports",
            "error": "vLLM-Omni preflight failed",
            "checks": checks,
            "import_errors": import_errors,
            "requests_issued": [],
            "fallback": "native VoxCPM2 CUDA service (modal_app.py)",
        }

    model_log = Path(tempfile.mkstemp(prefix="vllm-omni-")[1])
    requests_issued: list[str] = []
    command = [
        "vllm", "serve", MODEL_PATH, "--omni", "--host", "127.0.0.1", "--port", "8000",
        "--gpu-memory-utilization", "0.80", "--enforce-eager", "--max-model-len", "2048",
    ]
    started = time.perf_counter()
    with model_log.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 240
        while time.time() < deadline:
            if process.poll() is not None:
                detail = model_log.read_text(encoding="utf-8", errors="replace")
                raise RuntimeError(f"vLLM-Omni server exited with code {process.returncode} before health check:\n{detail[-16000:]}")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=2) as response:
                    if response.status == 200:
                        break
            except Exception:
                time.sleep(2)
        else:
            raise RuntimeError("vLLM-Omni server did not become healthy within 240 seconds")

        checks["model_server_started"] = True

        def post(payload):
            request = urllib.request.Request("http://127.0.0.1:8000/v1/audio/speech", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(request, timeout=180) as response:
                return response.read()

        normal_started = time.perf_counter()
        requests_issued.append("normal_tts")
        normal = post({"model": "voxcpm2", "input": "A short vLLM Omni sample.", "voice": "default", "response_format": "wav"})
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            handle.write(normal); handle.flush(); info = sf.info(handle.name)
            normal_seconds = float(info.frames / info.samplerate)
        normal_wall = time.perf_counter() - normal_started

        clone_started = time.perf_counter()
        reference_data = "data:audio/wav;base64," + base64.b64encode(reference_wav).decode("ascii")
        requests_issued.append("voice_clone")
        clone = post({
            "model": "voxcpm2", "input": "A short vLLM cloned-voice sample.", "voice": "default",
            "ref_audio": reference_data, "ref_text": reference_text, "response_format": "wav",
        })
        with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
            handle.write(clone); handle.flush(); info = sf.info(handle.name)
            clone_seconds = float(info.frames / info.samplerate)
        clone_wall = time.perf_counter() - clone_started

        stream_started = time.perf_counter()
        requests_issued.append("streaming_pcm")
        stream_request = urllib.request.Request("http://127.0.0.1:8000/v1/audio/speech", data=json.dumps({"model": "voxcpm2", "input": "A short vLLM streaming sample.", "voice": "default", "ref_audio": reference_data, "ref_text": reference_text, "stream": True, "stream_format": "audio", "response_format": "pcm"}).encode(), headers={"Content-Type": "application/json"}, method="POST")
        chunks = []
        first_chunk = None
        with urllib.request.urlopen(stream_request, timeout=180) as response:
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                first_chunk = first_chunk or time.perf_counter()
                chunks.append(chunk)
        stream_wall = time.perf_counter() - stream_started
        stream_seconds = sum(len(chunk) for chunk in chunks) / (48_000 * 2)
        duplicate_chunks = len(chunks) - len({bytes(chunk) for chunk in chunks})
        cumulative_repeats = sum(
            1 for index, chunk in enumerate(chunks)
            if any(bytes(chunk) == b"".join(chunks[:prior + 1]) for prior in range(index))
        )
        return {
            "status": "passed", "checks": checks, "server_start_seconds": time.perf_counter() - started,
            "normal": {"wall_seconds": normal_wall, "audio_seconds": normal_seconds, "rtf": normal_wall / max(normal_seconds, 1e-9)},
            "voice_clone": {"wall_seconds": clone_wall, "audio_seconds": clone_seconds, "reference": "SPEAKER_00.wav"},
            "streaming": {"chunks": len(chunks), "ttfa_seconds": first_chunk - stream_started if first_chunk else None, "wall_seconds": stream_wall, "audio_seconds": stream_seconds, "rtf": stream_wall / max(stream_seconds, 1e-9), "duplicate_chunks": duplicate_chunks, "cumulative_chunk_repeats": cumulative_repeats, "pcm_integrity": duplicate_chunks == 0 and cumulative_repeats == 0},
            "request_shape": {"stream": True, "stream_format": "audio", "response_format": "pcm", "clone_ref_audio": True},
        }
    except Exception as exc:
        tail = model_log.read_text(encoding="utf-8", errors="replace")[-16000:]
        return {"status": "failed", "stage": "serving", "error": f"{type(exc).__name__}: {exc}", "checks": checks, "requests_issued": requests_issued, "server_log_tail": tail, "fallback": "native VoxCPM2 CUDA service (modal_app.py)"}
    finally:
        process.terminate()
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            process.kill()


@app.local_entrypoint()
def main() -> None:
    reference_path = Path(__file__).resolve().parent / "speaker_reference_clips" / "SPEAKER_00.wav"
    reference_text = "So it's a big fan and that's really what got me into AI was, it's actually a long story."
    result = run_smoke.remote(reference_path.read_bytes(), reference_text)
    output = Path(__file__).resolve().parent / "temp" / "modal-vllm-omni-validation.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
