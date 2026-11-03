#!/usr/bin/env python3
"""Open the Live Dubbing extension in an isolated Google Chrome profile."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import traceback
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from compute_backends.settings import load_compute_settings
from scripts.chrome_runtime import ensure_chrome
EXTENSION_DIR = ROOT / "browser_extension" / "live_dubbing"
STATUS_FILE = Path(os.environ.get(
    "LIVE_BROWSER_STATUS_PATH", ROOT / "temp" / "live-dubbing-browser-status.json"
))
SERVICE_URL = "http://127.0.0.1:8766/"
BROWSER_START_URL = "https://www.youtube.com/"


def _project_python():
    if os.name == "nt":
        return ROOT / ".venv" / "Scripts" / "python.exe"
    return ROOT / ".venv" / "bin" / "python"


def _relaunch_in_project_environment():
    project_python = _project_python()
    if not project_python.is_file():
        raise RuntimeError(
            "The project environment is missing. Run install.sh (macOS/Linux) or "
            "install.ps1 (Windows) first."
        )
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        os.execv(str(project_python), [str(project_python), str(Path(__file__).resolve()), *sys.argv[1:]])


def _status(state, message):
    STATUS_FILE.parent.mkdir(parents=True, exist_ok=True)
    temporary = STATUS_FILE.with_suffix(".tmp")
    temporary.write_text(
        json.dumps({"state": state, "message": message, "updated": time.time()}),
        encoding="utf-8",
    )
    temporary.replace(STATUS_FILE)
    print(message, flush=True)


def _ensure_browser_client():
    try:
        from websockets.sync.client import connect  # noqa: F401
    except ImportError:
        _status("installing", "Installing the small Chrome connection component; no AI models are downloaded.")
        subprocess.run([sys.executable, "-m", "pip", "install", "websockets>=12,<17"], check=True)
        os.execv(sys.executable, [sys.executable, str(Path(__file__).resolve()), *sys.argv[1:]])


def _profile_directory():
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "VideoDubbingTranslator" / "LiveChromeProfile"


def chrome_command(executable, profile, extension_dir, service_url=SERVICE_URL, *, debug_port=None):
    """Build a Chrome command using a separate application profile."""
    executable = Path(executable).resolve()
    profile = Path(profile).resolve()
    extension_dir = Path(extension_dir).resolve()
    command = [
        str(executable),
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if sys.platform == "darwin":
        command.append("--use-mock-keychain")
    if debug_port is not None:
        command.extend(["--remote-debugging-address=127.0.0.1", f"--remote-debugging-port={debug_port}"])
    command.append(service_url)
    return command


def _free_local_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _load_extension_via_devtools(port, process, extension_dir):
    try:
        from websockets.sync.client import connect
    except ImportError as exc:
        raise RuntimeError("The browser connection package is missing. Run the project installer again.") from exc
    deadline = time.monotonic() + 20
    address = f"http://127.0.0.1:{port}/json/version"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("Chrome exited before the extension could be loaded.")
        try:
            with urllib.request.urlopen(address, timeout=1) as response:
                info = json.load(response)
            endpoint = info["webSocketDebuggerUrl"]
            if not endpoint.startswith(f"ws://127.0.0.1:{port}/"):
                raise RuntimeError("Chrome returned an unexpected debugging endpoint.")
            with connect(endpoint, open_timeout=5, close_timeout=2, max_size=1024 * 1024) as ws:
                ws.send(json.dumps({"id": 1, "method": "Extensions.loadUnpacked", "params": {"path": str(Path(extension_dir).resolve())}}))
                while True:
                    result = json.loads(ws.recv(timeout=10))
                    if result.get("id") == 1:
                        if result.get("error") or not result.get("result", {}).get("id"):
                            raise RuntimeError("Chrome could not load the Live Dubbing extension. Update Chrome and retry.")
                        return result["result"]["id"]
        except (OSError, ValueError, KeyError, TimeoutError):
            time.sleep(.2)
    raise RuntimeError("Chrome did not become ready for extension setup. Update Chrome and retry.")


class _ProfileLock:
    """Prevent two launcher processes from racing on Chrome's app profile."""

    def __init__(self, profile):
        self.path = Path(profile) / ".live-dubbing-launcher.lock"
        self.file = None

    def __enter__(self):
        self.file = self.path.open("a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"\0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as exc:
            self.file.close()
            self.file = None
            raise RuntimeError(
                "The separate Live Dubbing browser profile is already in use. "
                "Close the other Live Dubbing Browser window and retry."
            ) from exc
        return self

    def __exit__(self, *_exc):
        if self.file is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_UN)
        finally:
            self.file.close()
            self.file = None


def live_browser_profile_in_use():
    """Return whether another Live Dubbing launcher owns the app profile."""
    profile = _profile_directory()
    try:
        with _ProfileLock(profile):
            return False
    except RuntimeError:
        return True


def _terminate_browser(process):
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def launch_browser(executable, service_url=SERVICE_URL, *, connection_process=None):
    if not (EXTENSION_DIR / "manifest.json").is_file():
        raise RuntimeError(f"The bundled Live Dubbing extension is missing: {EXTENSION_DIR}")

    profile = _profile_directory()
    profile.mkdir(parents=True, exist_ok=True)
    debug_port = _free_local_port()
    command = chrome_command(executable, profile, EXTENSION_DIR, service_url, debug_port=debug_port)
    with _ProfileLock(profile):
        # Chrome prints the DevTools WebSocket URL at startup. It grants local
        # control of this isolated profile, so never place it in GUI logs.
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            _load_extension_via_devtools(debug_port, process, EXTENSION_DIR)
            _status(
                "ready",
                "Google Chrome is open on YouTube with the bundled Live Dubbing extension and a separate app profile. "
                "Choose a video, then open Local Adaptive Live Dubbing from Chrome's Extensions menu. "
                "Capture starts only after your confirmation. "
                f"Profile: {profile}",
            )
            while process.poll() is None:
                if connection_process is not None and connection_process.poll() is not None:
                    raise RuntimeError(
                        "The Colab connection was lost. The browser is closing. "
                        "The Colab runtime remains allocated until Disconnect GPU."
                    )
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    continue
            exit_code = process.poll()
            if exit_code != 0:
                raise RuntimeError(
                    f"Live Dubbing Chrome exited unexpectedly (status {exit_code}). "
                    "Open Live Dubbing Browser to retry. The Colab runtime remains allocated until Disconnect GPU."
                )
        except KeyboardInterrupt:
            _status("closing", "Closing the Live Dubbing Browser.")
            _terminate_browser(process)
            raise
        finally:
            _terminate_browser(process)


def _main():
    preferences = load_compute_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backend",
        choices=("local", "colab", "modal-tts"),
        default=os.environ.get("LIVE_COMPUTE_PROVIDER", preferences["live_provider"]),
        help="Where Live Dubbing models run (default: local).",
    )
    parser.add_argument(
        "--colab-session",
        default=os.environ.get("COLAB_SESSION_NAME", preferences["colab_session"]),
        help="Existing authenticated Colab CLI session name for --backend colab.",
    )
    parser.add_argument(
        "--remote-project",
        default=os.environ.get("COLAB_REMOTE_PROJECT", preferences["remote_project"]),
        help="Repository directory on the selected Colab runtime.",
    )
    parser.add_argument(
        "--check", action="store_true",
        help="ensure Google Chrome is installed and exit without opening a browser",
    )
    args = parser.parse_args()

    try:
        _relaunch_in_project_environment()
        _status("starting", "Checking for Google Chrome.")
        executable = ensure_chrome()
        _ensure_browser_client()
        if args.check:
            _status("ready", "Google Chrome is ready. No AI models were loaded or downloaded.")
            return 0

        tunnel = None
        managed_local_service = False
        try:
            if args.backend == "colab":
                if not args.colab_session:
                    raise RuntimeError(
                        "Select an existing Colab CLI session before opening Live Dubbing on Colab."
                    )
                from compute_backends.colab_tunnel import ColabLiveTunnel

                _status("connecting", f"Opening an authenticated tunnel to Colab session {args.colab_session}.")
                tunnel = ColabLiveTunnel(
                    args.colab_session,
                    remote_project=args.remote_project,
                    log_path=ROOT / "temp" / "colab-live-tunnel.log",
                )
                remote_status = tunnel.start()
                models = remote_status.get("models", {})
                missing = sorted(key for key, ready in models.items()
                                 if isinstance(ready, bool) and not ready
                                 and key != "speaker_models")
                if missing:
                    raise RuntimeError(
                        "The Colab Live Dubbing service is reachable, but its model "
                        "readiness check reports missing setup: " + ", ".join(missing) + "."
                    )
                _status(
                    "opening",
                    "Colab G4 is ready. Opening local Chrome; the selected tab's "
                    "media will be sent through the authenticated tunnel for processing.",
                )
                launch_browser(executable, BROWSER_START_URL, connection_process=tunnel.process)
            elif args.backend == "modal-tts":
                raise RuntimeError(
                    "Modal currently supports offline VoxCPM2 speech generation only; "
                    "Live Dubbing requires the local or Colab G4 provider."
                )
            else:
                from live_dubbing.service_launcher import ensure_live_service, stop_managed_service
                ensure_live_service()
                managed_local_service = True
                _status("opening", "Local Live Dubbing service is ready. Opening Google Chrome.")
                launch_browser(executable, BROWSER_START_URL)
            _status("closed", "Live Dubbing Browser was closed.")
        finally:
            if tunnel is not None:
                tunnel.stop()
            if managed_local_service:
                from live_dubbing.service_launcher import stop_managed_service
                stop_managed_service()
        return 0
    except KeyboardInterrupt:
        _status("closed", "Live Dubbing Browser startup was cancelled.")
        return 130
    except Exception as exc:
        _status("error", f"Could not open Live Dubbing Browser: {exc}")
        traceback.print_exc()
        return 1


def main():
    previous_sigterm = signal.getsignal(signal.SIGTERM)

    def handle_sigterm(_signum, _frame):
        # Convert termination into normal Python unwinding so browser, tunnel,
        # and the selected Colab runtime reach their cleanup paths.
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, handle_sigterm)
    try:
        return _main()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm)


if __name__ == "__main__":
    raise SystemExit(main())
