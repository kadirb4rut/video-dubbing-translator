"""Start or reuse this repository's loopback Live Dubbing service on Colab."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from urllib.request import Request, urlopen


APP_ID = "video-dubbing-translator-live-v1"
PAIRING_API_VERSION = 1


def _content_path(value: str) -> Path:
    root = Path(value).resolve()
    content = Path("/content").resolve()
    if root == content or content not in root.parents:
        raise ValueError("Colab project folder must be below /content.")
    return root


def _status():
    request = Request(
        "http://127.0.0.1:8766/api/status",
        headers={"Host": "127.0.0.1:8766"},
    )
    try:
        with urlopen(request, timeout=1.0) as response:
            value = json.loads(response.read())
    except (OSError, ValueError, TimeoutError):
        return None
    return value if value.get("app") == APP_ID else None


def _port_open() -> bool:
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(("127.0.0.1", 8766)) == 0


def _stop_outdated_idle_service(root: Path, status: dict):
    """Restart only this project's idle service after its server file is refreshed."""
    if status.get("active") or status.get("initializing"):
        raise RuntimeError(
            "The existing Live Dubbing service is active and needs an update. "
            "Finish or stop that session before reopening the Live Dubbing Browser."
        )
    pid_file = root / "temp" / "colab-live-service" / "service.pid"
    try:
        pid = int(pid_file.read_text(encoding="ascii").strip())
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            "The older Live Dubbing service has no verifiable managed process id; "
            "it was left running. Stop it from its owning launcher, then retry."
        ) from exc
    proc = Path('/proc') / str(pid)
    try:
        cwd = Path(os.path.realpath(proc / 'cwd'))
        command = (proc / 'cmdline').read_bytes().split(b'\0')
    except OSError as exc:
        if not proc.exists():
            pid_file.unlink(missing_ok=True)
            return
        raise RuntimeError("Could not verify the old Live Dubbing service process; it was left running.") from exc
    if cwd != root or b'-m' not in command or b'live_dubbing.server' not in command:
        raise RuntimeError(
            "The recorded Colab process is not the managed Live Dubbing service; "
            "it was left running."
        )
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pid_file.unlink(missing_ok=True)
        return
    deadline = time.monotonic() + 12
    while time.monotonic() < deadline:
        if _status() is None and not _port_open():
            pid_file.unlink(missing_ok=True)
            return
        time.sleep(.2)
    raise RuntimeError("The idle Live Dubbing service did not stop for its safe update; it was left running.")


def ensure_service(project_root: str):
    root = _content_path(project_root)
    if not (root / "live_dubbing" / "server.py").is_file():
        raise FileNotFoundError(f"Live Dubbing server files were not found under {root}.")

    status = None
    for _ in range(8):
        status = _status()
        if status:
            break
        with socket.socket() as probe:
            probe.settimeout(.2)
            busy = probe.connect_ex(("127.0.0.1", 8766)) == 0
        if not busy:
            break
        time.sleep(.25)
    else:
        raise RuntimeError("Colab port 8766 is responding, but it is not this project's Live Dubbing service.")

    if status:
        if status.get("pairing_api_version", 0) < PAIRING_API_VERSION:
            _stop_outdated_idle_service(root, status)
        else:
            return status

    python = os.environ.get("LIVE_PYTHON")
    python_options = [Path(python).expanduser()] if python else []
    python_options.extend((
        root / ".venv" / "bin" / "python",
        root / ".venv" / "Scripts" / "python.exe",
        Path("/content/live-runtime/.venv/bin/python"),
        Path(sys.executable),
    ))
    environment = {
        **os.environ,
        "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    }
    config = root / ".vdt-runtime.json"
    if config.is_file():
        values = json.loads(config.read_text())
        for key in ("HF_HOME", "XDG_CACHE_HOME", "MPLBACKEND"):
            if isinstance(values.get(key), str):
                environment[key] = values[key]
    python_path = None
    checked = set()
    failures = []
    for candidate in python_options:
        # Preserve virtual-environment symlinks so their site-packages remain
        # visible when Chromium/Colab launches the server process.
        candidate = Path(os.path.abspath(os.path.expanduser(str(candidate))))
        if candidate in checked or not candidate.is_file():
            continue
        checked.add(candidate)
        probe = subprocess.run(
            [str(candidate), "-c", "import torch, aiohttp; import live_dubbing.server"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        if probe.returncode == 0:
            python_path = candidate
            break
        detail = (probe.stderr or probe.stdout).strip().splitlines()
        failures.append(f"{candidate}: {detail[-1] if detail else 'Live Dubbing imports failed'}")
    if python_path is None:
        raise RuntimeError(
            "No prepared Colab Python environment can import the Live Dubbing runtime. "
            "Install its existing project dependencies or set LIVE_PYTHON in the Colab "
            "runtime. Checked: " + "; ".join(failures)
        )

    logs = root / "temp" / "colab-live-service"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / "service.log"
    with log_path.open("ab") as log:
        process = subprocess.Popen(
            [str(python_path), "-m", "live_dubbing.server"],
            cwd=root,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    (logs / "service.pid").write_text(str(process.pid), encoding="ascii")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-2000:]
            raise RuntimeError(f"Colab Live Dubbing service exited during startup. {tail}")
        status = _status()
        if status and status.get("pairing_api_version", 0) >= PAIRING_API_VERSION:
            return status
        time.sleep(.25)
    raise RuntimeError(
        f"Colab Live Dubbing service did not become ready. See {log_path}; "
        "the launcher did not download or change any AI models."
    )


def prepare_live(project_root: str):
    """Add only Live-specific assets to an already-prepared Colab project."""
    root = _content_path(project_root)
    python = root / ".venv" / "bin" / "python"
    if not python.is_file():
        raise FileNotFoundError("The selected Colab project has no prepared GPU Python environment.")
    environment = {**os.environ, "PYTHONPATH": str(root) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    config_path = root / ".vdt-runtime.json"
    if config_path.is_file():
        config = json.loads(config_path.read_text())
        for key in ("HF_HOME", "XDG_CACHE_HOME", "MPLBACKEND"):
            if isinstance(config.get(key), str):
                environment[key] = config[key]
    subprocess.run(
        [str(python), str(root / "scripts" / "setup_live_segmentation.py"), "--install-runtime", "--download"],
        cwd=root, env=environment, check=True,
    )
    subprocess.run(
        [str(python), "-c", "from faster_whisper import download_model; download_model('small')"],
        cwd=root, env=environment, check=True,
    )
    return {"app": APP_ID, "project": str(root), "live": True}


if __name__ == "__main__":
    try:
        project_root = os.environ.get("VDT_LIVE_PROJECT_ROOT", "/content/video-dubbing-translator-main")
        if os.environ.get("VDT_PREPARE_LIVE") == "1":
            print("VDT_REMOTE_LIVE_PREPARED=" + json.dumps(prepare_live(project_root)), flush=True)
            raise SystemExit(0)
        print("VDT_REMOTE_LIVE_STATUS=" + json.dumps(ensure_service(project_root), ensure_ascii=False), flush=True)
    except Exception as exc:
        print("VDT_REMOTE_LIVE_ERROR=" + f"{type(exc).__name__}: {exc}", flush=True)
        raise
