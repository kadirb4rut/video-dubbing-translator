#!/usr/bin/env python3
"""Start the local GUI in an isolated Google Chrome profile."""
from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import json
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
PORT = 8765
GUI_URL = f"http://127.0.0.1:{PORT}/"


def _port_is_open() -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", PORT)) == 0


def _start_gui_server() -> subprocess.Popen | None:
    if _port_is_open():
        print(f"GUI server is already running at {GUI_URL}", flush=True)
        return None

    server = subprocess.Popen(
        [sys.executable, str(ROOT / "web_gui.py")],
        cwd=ROOT,
        start_new_session=(os.name != "nt"),
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError("The local GUI server stopped before it was ready.")
        if _port_is_open():
            print(f"GUI server is ready at {GUI_URL}", flush=True)
            return server
        time.sleep(0.2)
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
    raise RuntimeError("The local GUI server did not become ready on port 8765.")


def _reset_dashboard_view() -> None:
    request = urllib.request.Request("http://127.0.0.1:8765/api/new-ui-session")
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise RuntimeError(
                "The GUI server is an older version. Stop its terminal process with Ctrl+C, "
                "then run ./start_gui.sh again."
            ) from exc
        raise RuntimeError("Could not prepare a fresh dashboard view.") from exc
    if result.get("ok"):
        print("Previous Final Video display cleared for this launch.", flush=True)
    else:
        print(result.get("message", "A running job is active; its view was kept."), flush=True)


def _profile_directory() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "VideoDubbingTranslator" / "ChromeAppProfile"


def _chrome_command(executable: Path, profile: Path) -> list[str]:
    command = [
        str(executable.resolve()),
        f"--user-data-dir={profile.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
    ]
    if sys.platform == "darwin":
        command.append("--use-mock-keychain")
    command.append(GUI_URL)
    return command


def _stop_process(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def main() -> int:
    print("Video Dubbing Translator opens in Google Chrome with a separate app profile.", flush=True)
    if sys.platform == "darwin":
        print("The isolated browser will not request access to macOS Keychain items.", flush=True)

    from scripts.chrome_runtime import InstallationDeclined, ensure_chrome

    server = None
    browser = None
    try:
        executable = ensure_chrome()
        server = _start_gui_server()
        profile = _profile_directory()
        profile.mkdir(parents=True, exist_ok=True)
        _reset_dashboard_view()
        browser = subprocess.Popen(_chrome_command(executable, profile), cwd=ROOT)
        time.sleep(1.0)
        if browser.poll() not in (None, 0):
            raise RuntimeError(
                "Chrome did not start successfully. On Linux, install Chrome's required "
                "system libraries with your distribution's package manager, then retry."
            )
        print("The Video Dubbing Translator panel is opening in Google Chrome.", flush=True)

        if server is None:
            # An existing GUI server is not owned by this invocation.
            return browser.wait()

        # Keep the terminal command attached to the GUI server, as before.
        while server.poll() is None:
            time.sleep(0.5)
        return server.returncode or 0
    except KeyboardInterrupt:
        print("\nStopping the Video Dubbing Translator GUI server.", flush=True)
        return 130
    except InstallationDeclined as exc:
        print(str(exc), file=sys.stderr, flush=True)
        return 1
    except Exception as exc:
        print(f"Could not start Video Dubbing Translator in Chrome: {exc}", file=sys.stderr, flush=True)
        return 1
    finally:
        _stop_process(server)
        # The browser has its own app profile; close the process started here on
        # startup failure or terminal interruption, but not after normal return.
        if browser is not None and browser.poll() is None and (server is None or server.poll() is not None):
            _stop_process(browser)


if __name__ == "__main__":
    raise SystemExit(main())
