"""Remote-side helpers executed inside the selected Colab kernel.

This file uses only the Python standard library so a prepared Colab session can
prepare and poll jobs without installing an extra remote control service.
"""

from __future__ import annotations

import json
import os
import base64
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback


def _config() -> tuple[str, dict]:
    action = os.environ.get("VDT_COLAB_ACTION", "").strip()
    if action not in {"prepare", "start", "poll", "cancel"}:
        raise ValueError("Invalid Colab backend action.")
    encoded = os.environ.get("VDT_COLAB_CONFIG_B64", "")
    if encoded:
        payload = json.loads(base64.b64decode(encoded, validate=True))
    else:
        config_path = Path(os.environ.get("VDT_COLAB_CONFIG", ""))
        if not config_path.is_absolute():
            raise ValueError("The Colab job config path must be absolute.")
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("The Colab job config must be a JSON object.")
    return action, payload


def _content_path(value: str) -> Path:
    path = Path(value).resolve()
    content_root = Path("/content").resolve()
    if path == content_root or content_root not in path.parents:
        raise ValueError("Colab backend paths must stay below /content.")
    return path


def _python_interpreter_path(value: str | Path) -> Path:
    """Return an absolute interpreter path without following venv symlinks."""
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def _runtime_environment(root: Path) -> dict:
    environment = os.environ.copy()
    config = root / ".vdt-runtime.json"
    if config.is_file():
        values = json.loads(config.read_text())
        for key in ("HF_HOME", "XDG_CACHE_HOME", "MPLBACKEND"):
            if isinstance(values.get(key), str):
                environment[key] = values[key]
    return environment


def _worker(config_path: Path) -> int:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    root = _content_path(config["project_root"])
    # Preserve virtual-environment symlinks. Resolving them can point at the
    # system interpreter and silently discard that environment's site-packages.
    python = _python_interpreter_path(config["python"])
    input_path = _content_path(config["input_path"])
    status_path = _content_path(config["status_path"])
    log_path = _content_path(config["log_path"])
    if not python.is_file():
        raise FileNotFoundError(f"Colab Python environment is missing: {python}")
    if not (root / "scripts" / "run_pipeline.py").is_file():
        raise FileNotFoundError(f"Remote project is missing scripts/run_pipeline.py: {root}")
    if not input_path.is_file():
        raise FileNotFoundError(f"Uploaded input video is missing: {input_path}")
    args = config.get("run_arguments")
    if not isinstance(args, list) or not all(isinstance(item, str) for item in args):
        raise ValueError("Invalid remote run arguments.")
    status_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(status_path, {"state": "running", "started": time.time(), "pid": os.getpid()})
    try:
        with log_path.open("ab") as log:
            # Keep WebM/other source formats out of the stable MP4 writer.
            # The original upload remains intact; only this job's working copy changes.
            normalize = [str(python), "-c", (
                "from pathlib import Path; from compute_backends.media import cloud_input; "
                "import sys; print('VDT_INPUT='+str(cloud_input(Path(sys.argv[1]),Path(sys.argv[2]))))"
            ), str(input_path), str(status_path.parent / "normalized")]
            normalized = subprocess.run(normalize, cwd=root, env=_runtime_environment(root), capture_output=True, text=True)
            log.write((normalized.stdout + normalized.stderr).encode())
            if normalized.returncode:
                raise RuntimeError("Preparing the uploaded video failed; see worker log.")
            result = next((line.split("=", 1)[1] for line in normalized.stdout.splitlines() if line.startswith("VDT_INPUT=")), "")
            input_path = _content_path(result)
            args = [str(input_path), *args[1:]]
            output_path = root / "vocal-remover/final_video/final" / input_path.name
            if config.get("lip_sync"):
                output_path = output_path.with_name(output_path.stem + "_lipsync" + output_path.suffix)
            process = subprocess.Popen(
                [str(python), str(root / "scripts" / "run_pipeline.py"), *args],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=False,
                env=_runtime_environment(root),
            )
            return_code = process.wait()
        final_path = output_path if output_path.is_file() else None
        _write_json(status_path, {
            "state": "finished" if return_code == 0 else "failed",
            "exit_code": return_code,
            "output_path": str(final_path) if final_path else "",
            "finished": time.time(),
        })
        return return_code
    except BaseException as exc:
        _write_json(status_path, {
            "state": "failed", "exit_code": 1,
            "error": f"{type(exc).__name__}: {exc}", "finished": time.time(),
        })
        raise


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _run_action(action: str, config: dict) -> dict:
    if action == "prepare":
        root = _content_path(config["project_root"])
        if not root.is_dir():
            raise FileNotFoundError(
                f"Colab project folder does not exist: {root}. Clone/install this repository "
                "in that Colab runtime and configure its models before selecting it."
            )
        if not (root / "scripts" / "run_pipeline.py").is_file():
            raise FileNotFoundError(f"Colab project is missing scripts/run_pipeline.py: {root}")
        if not (root / "Video_Translator.py").is_file():
            raise FileNotFoundError(f"Colab project is missing Video_Translator.py: {root}")
        for key in ("job_dir", "workspace_dir"):
            _content_path(config[key]).mkdir(parents=True, exist_ok=True)
        python_options = []
        configured_python = os.environ.get("VDT_BATCH_PYTHON", "").strip()
        if configured_python:
            python_options.append(Path(configured_python).expanduser())
        python_options.extend((
            root / ".venv" / "bin" / "python",
            root / ".venv" / "Scripts" / "python.exe",
            Path("/content/live-runtime/.venv/bin/python"),
            Path(sys.executable),
        ))
        checked = set()
        failures = []
        for python in python_options:
            # Keep the venv's executable path intact; Path.resolve() follows the
            # symlink to the base interpreter and can make a healthy venv appear
            # to be missing torch and the project's installed dependencies.
            python = _python_interpreter_path(python)
            if python in checked or not python.is_file():
                continue
            checked.add(python)
            environment = {
                **_runtime_environment(root),
                "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
                "HF_HUB_OFFLINE": "1",
                "HF_HUB_DISABLE_TELEMETRY": "1",
            }
            try:
                probe = subprocess.run(
                    [str(python), "-c", "import torch, soundfile; import Video_Translator"],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                failures.append(f"{python}: {exc}")
                continue
            if probe.returncode == 0:
                vocal_model = root / "vocal-remover" / "models" / "baseline.pth"
                if not vocal_model.is_file():
                    raise RuntimeError(
                        "The selected Colab project is missing the required vocal-removal model: "
                        f"{vocal_model}. Set up that model in the selected project, then retry."
                    )
                requested_model = str(config.get("translation_model") or "hymt2-1.8b")
                model_probe = subprocess.run(
                    [str(python), "-c", (
                        "import json, sys; from translation.model_manager import is_installed; "
                        "print(json.dumps({'installed': is_installed(sys.argv[1])}))"
                    ), requested_model],
                    cwd=root,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                try:
                    translation_ready = json.loads(model_probe.stdout.strip().splitlines()[-1])["installed"]
                except (IndexError, KeyError, ValueError):
                    translation_ready = False
                if model_probe.returncode != 0 or not translation_ready:
                    raise RuntimeError(
                        f"The selected Colab project is missing its {requested_model} translation model. "
                        "Install that model in the selected project, then retry."
                    )
                tts_probe = subprocess.run(
                    [str(python), "-c", (
                        "import json; from voxcpm_runtime import model_ready; "
                        "ready, path = model_ready(); print(json.dumps({'ready': bool(ready)}))"
                    )],
                    cwd=root,
                    env={**environment, "HF_HUB_OFFLINE": "1"},
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                try:
                    tts_ready = json.loads(tts_probe.stdout.strip().splitlines()[-1])["ready"]
                except (IndexError, KeyError, ValueError):
                    tts_ready = False
                if tts_probe.returncode != 0 or not tts_ready:
                    raise RuntimeError(
                        "The selected Colab project is missing the pinned VoxCPM2 model. "
                        "Install it in the selected project, then retry."
                    )
                return {
                    "ok": True, "project_root": str(root), "python": str(python),
                    "kernel_python": sys.executable,
                }
            detail = (probe.stderr or probe.stdout).strip().splitlines()
            failures.append(f"{python}: {detail[-1] if detail else 'pipeline imports failed'}")
        raise RuntimeError(
            "No prepared Colab Python environment can import the existing dubbing pipeline. "
            "Prepare its normal project environment or set VDT_BATCH_PYTHON in the Colab "
            "runtime, then retry. Checked: " + "; ".join(failures)
        )

    if action == "start":
        script = _content_path(config["worker_script"])
        child_config = _content_path(config["worker_config"])
        if not script.is_file() or not child_config.is_file():
            raise FileNotFoundError("Colab job worker files are missing.")
        worker_python = Path(config["worker_python"]).resolve()
        log_path = _content_path(config["log_path"])
        pid_path = _content_path(config["pid_path"])
        status_path = _content_path(config["status_path"])
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # Publish the initial state before the child starts: a quick failure or
        # completion must never be overwritten by a late "queued" update.
        _write_json(status_path, {"state": "queued", "started": time.time()})
        with log_path.open("ab") as log:
            try:
                process = subprocess.Popen(
                    [str(worker_python), str(script), "--worker", str(child_config)],
                    cwd=str(_content_path(config["project_root"])),
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
            except OSError as exc:
                _write_json(status_path, {
                    "state": "failed", "exit_code": 1,
                    "error": f"Could not start Colab worker: {exc}", "finished": time.time(),
                })
                raise
        pid_path.write_text(str(process.pid), encoding="ascii")
        return {"ok": True, "pid": process.pid, "state": "queued"}

    if action == "poll":
        status_path = _content_path(config["status_path"])
        log_path = _content_path(config["log_path"])
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.is_file() else {"state": "missing"}
        offset = max(0, int(config.get("log_offset", 0)))
        log_text = ""
        next_offset = offset
        if log_path.is_file():
            with log_path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read(256 * 1024)
                next_offset = handle.tell()
            log_text = chunk.decode("utf-8", errors="replace")
        return {"status": status, "log": log_text, "log_offset": next_offset}

    if action == "cancel":
        pid_path = _content_path(config["pid_path"])
        status_path = _content_path(config["status_path"])
        if not pid_path.is_file():
            return {"ok": True, "state": "not-running"}
        pid = int(pid_path.read_text(encoding="ascii").strip())
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        _write_json(status_path, {"state": "cancelled", "finished": time.time()})
        return {"ok": True, "state": "cancelled"}

    raise ValueError("Unsupported Colab backend action.")


def _exec_remote_action() -> None:
    try:
        action, config = _config()
        result = _run_action(action, config)
        print("VDT_COLAB_RESULT=" + json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as exc:
        error = {"error": f"{type(exc).__name__}: {exc}"}
        print("VDT_COLAB_RESULT=" + json.dumps(error, ensure_ascii=False), flush=True)
        raise


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--worker":
        try:
            raise SystemExit(_worker(Path(sys.argv[2])))
        except SystemExit:
            raise
        except Exception:
            traceback.print_exc()
            raise SystemExit(1)
    _exec_remote_action()
