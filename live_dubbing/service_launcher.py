"""Start or reuse the loopback Live Dubbing service without loading models."""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
SERVICE_URL = "http://127.0.0.1:8766/"
_service_process = None
_service_lock = threading.Lock()


def _health_check():
    try:
        with urllib.request.urlopen(SERVICE_URL + "api/status", timeout=.2) as response:
            value = json.loads(response.read())
        return value if value.get("app") == "video-dubbing-translator-live-v1" else None
    except (OSError, ValueError):
        return None


def _port_is_open():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(.2)
        return sock.connect_ex(("127.0.0.1", 8766)) == 0


def ensure_live_service():
    """Return the service URL, reusing the GUI's existing verified startup path."""
    global _service_process
    with _service_lock:
        if _health_check():
            return SERVICE_URL
        if _port_is_open():
            raise RuntimeError("Port 8766 is occupied by an unresponsive or different local service.")
        if _service_process is None or _service_process.poll() is not None:
            python = os.environ.get("LIVE_PYTHON", sys.executable)
            (ROOT / "temp").mkdir(exist_ok=True)
            with (ROOT / "temp/live-dubbing-service.log").open("a") as log:
                _service_process = subprocess.Popen(
                    [python, "-m", "live_dubbing.server"], cwd=ROOT,
                    stdout=log, stderr=subprocess.STDOUT,
                )
        for _ in range(40):
            if _service_process.poll() is not None:
                raise RuntimeError(
                    "Live Dubbing service could not start. Install its optional runtime; "
                    "see temp/live-dubbing-service.log. The offline workflow is unchanged."
                )
            if _health_check():
                return SERVICE_URL
            time.sleep(.05)
        raise RuntimeError(
            "Live Dubbing service did not become ready; the offline workflow remains available."
        )


def stop_managed_service():
    """Stop only the service process this Python process started."""
    global _service_process
    with _service_lock:
        process = _service_process
        _service_process = None
    if process is not None and process.poll() is None:
        process.terminate()
