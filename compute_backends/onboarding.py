"""One asynchronous GUI connection, with account prompts forwarded over a PTY."""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
URL = re.compile(r"https://[^\s\x1b<>]+")
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")


class CloudConnection:
    def __init__(self):
        self.lock = threading.RLock()
        self.process = None
        self.master = None
        self.last_seen = time.monotonic()
        self.cancel_job = lambda: None
        self.data = {"stage": "disconnected", "message": "Choose a GPU provider to connect.", "provider": "local", "logs": [], "authUrl": "", "needsInput": False}

    def touch(self):
        self.last_seen = time.monotonic()

    def complete(self):
        with self.lock:
            if self.data["stage"] == "ready":
                self.data.update(stage="ready", message="Task ended. GPU remains connected until you press Disconnect GPU.")

    def status(self):
        with self.lock:
            self.touch()
            return {**self.data, "logs": list(self.data["logs"])}

    def start(self, provider, *, model="hymt2-1.8b", temporary=False, live=False, diarization=False, lip_sync=False, hf_token=""):
        if provider not in ("colab", "modal"):
            raise ValueError("Choose Colab CLI or Modal.")
        if provider == "modal" and live:
            raise ValueError("Modal Live Dubbing is not available yet. Choose Colab CLI or this computer for live dubbing.")
        if provider == "colab" and os.name == "nt":
            raise ValueError("The upstream Colab CLI currently supports macOS/Linux.")
        if provider == "colab" and self.data.get("stage") == "ready" and self.data.get("provider") == "colab":
            # A Colab runtime may be stopped outside this GUI (for example after
            # a browser/tunnel crash). Do not trust the in-memory "ready" state
            # without checking the selected CLI session before reusing it.
            from .client_tools import client_bin

            tool = client_bin("colab")
            session = self.data.get("session", "")
            if not tool or not session:
                with self.lock:
                    self.data.update(stage="error", message="Could not verify the saved Colab session. Reconnect Colab and retry.")
                raise ValueError("Could not verify the saved Colab session. Reconnect Colab and retry.")
            try:
                listing = subprocess.run(
                    [tool, "sessions"], capture_output=True, text=True,
                    timeout=30, check=True,
                ).stdout
            except (OSError, subprocess.SubprocessError) as exc:
                with self.lock:
                    self.data.update(stage="error", message="Could not verify the Colab session. Check the Colab CLI connection and retry.")
                raise ValueError("Could not verify the Colab session. Check the Colab CLI connection and retry.") from exc
            if not re.search(r"\[" + re.escape(session) + r"\]", listing):
                with self.lock:
                    self.data.update(
                        stage="disconnected",
                        message="The saved Colab session is no longer active. Reconnecting…",
                    )
            elif (self.data.get("model") == model and (not live or self.data.get("live"))):
                from scripts.prepare_compute import check_colab_usage
                balance = check_colab_usage(tool)
                with self.lock:
                    self.data.update(cu_balance=balance, cu_status=(
                        "unknown" if balance is None else "empty" if balance <= 0
                        else "low" if balance < 1 else "available"))
                return
        from translation.registry import MODEL_BY_KEY
        if model not in MODEL_BY_KEY:
            raise ValueError("Unknown translation model")
        if provider == "modal":
            from .client_tools import client_bin, client_dir
            saved = client_dir() / "modal-session.json"
            tool = client_bin("modal")
            if saved.is_file() and tool and self.process is None:
                session = json.loads(saved.read_text())
                listing = subprocess.run([tool, "app", "list", "--json"], capture_output=True, text=True, timeout=30, check=True)
                if session.get("function_id") and session.get("ready") and session.get("model") == model and any(row.get("app_id") == session["app_id"] and row.get("state") != "stopped" for row in json.loads(listing.stdout)):
                    with self.lock:
                        self.data.update(stage="ready", provider="modal", model=model, message="Existing Modal GPU connection recovered. Use Disconnect GPU when finished.")
                    return
        if self.data["provider"] not in ("local", provider) and self.data["stage"] != "disconnected":
            raise ValueError("Press Disconnect GPU before switching cloud providers.")
        upgrade_live = (
            provider == "colab" and live and self.data.get("stage") == "ready"
            and self.data.get("provider") == "colab" and self.data.get("model") == model
            and not self.data.get("live")
        )
        existing_session = self.data.get("session", "")
        existing_project = self.data.get("project", "")
        with self.lock:
            self.touch()
            if self.process is not None:
                if self.data["stage"] == "ready" and self.data["provider"] == provider and self.data.get("model") == model:
                    return
                raise ValueError("A cloud connection is already open. Disconnect it before preparing another.")
            self.data = {"stage": "connecting", "message": "Connecting…", "provider": provider, "model": model, "live": live, "logs": [], "authUrl": "", "needsInput": False}
            if upgrade_live:
                command = [sys.executable, "-u", str(ROOT / "scripts/prepare_colab_live.py"),
                           "--session", existing_session, "--project", existing_project,
                           "--model", model]
            else:
                command = [sys.executable, "-u", str(ROOT / "scripts/prepare_compute.py"), provider, "--model", model]
            for enabled, flag in (() if upgrade_live else ((temporary, "--temporary-cache"), (live, "--live"), (diarization, "--diarization"), (lip_sync, "--lip-sync"))):
                if enabled:
                    command.append(flag)
            environment = {**os.environ, "PYTHONUNBUFFERED": "1", "TERM": "dumb", "NO_COLOR": "1"}
            if hf_token:
                environment["HF_TOKEN"] = hf_token
            if os.name != "nt":
                import pty
                import termios
                self.master, slave = pty.openpty()
                flags = termios.tcgetattr(slave)
                flags[3] &= ~termios.ECHO
                termios.tcsetattr(slave, termios.TCSANOW, flags)
                try:
                    terminal_command = [sys.executable, str(ROOT / "scripts/compute_terminal.py"), *command]
                    self.process = subprocess.Popen(terminal_command, cwd=ROOT, stdin=slave, stdout=slave, stderr=slave, start_new_session=True, env=environment)
                except Exception:
                    os.close(self.master)
                    self.master = None
                    self.data.update(stage="error", message="Could not start the cloud connection process.")
                    raise
                finally:
                    os.close(slave)
            else:
                self.process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=environment)
            threading.Thread(target=self._read, daemon=True).start()

    def _line(self, text):
        text = ANSI.sub("", text).strip()
        if not text:
            return
        with self.lock:
            if "VDT_SETUP_EVENT=" in text:
                try:
                    event = json.loads(text.split("VDT_SETUP_EVENT=", 1)[1])
                    self.data.update(event)
                    if event["stage"] not in ("authorizing", "connecting"):
                        self.data.update(authUrl="", needsInput=False)
                    return
                except ValueError:
                    pass
            for match in URL.findall(text):
                parsed = urllib.parse.urlparse(match)
                host = parsed.hostname or ""
                if host == "accounts.google.com" or (host in ("modal.com", "www.modal.com") and parsed.path.startswith(("/token-flow/", "/login"))):
                    self.data.update(authUrl=match, needsInput=True)
            lowered = text.lower()
            if "press enter" in lowered or "authorization code" in lowered or "verification code" in lowered:
                self.data["needsInput"] = True
            # OAuth URLs and tokens are never retained in the connection log.
            safe = URL.sub("[browser link]", text)
            safe = re.sub(r"(?:hf_|ya29\.|glrt-|ak-)[A-Za-z0-9_\-.]+", "[redacted]", safe)
            self.data["logs"] = (self.data["logs"] + [safe])[-100:]

    def _read(self):
        process = self.process
        buffer = ""
        try:
            while True:
                try:
                    chunk = os.read(self.master, 4096) if self.master is not None else process.stdout.read1(4096)
                except OSError:
                    break
                if not chunk:
                    break
                buffer += chunk.decode("utf-8", errors="replace")
                # Prompts may not end in a newline. Surface their state immediately.
                if "Press Enter" in buffer or "authorization code:" in buffer.lower():
                    with self.lock:
                        self.data["needsInput"] = True
                while "\n" in buffer or "\r" in buffer:
                    line, buffer = re.split(r"[\r\n]", buffer, maxsplit=1)
                    self._line(line)
            self._line(buffer)
            code = process.wait()
            with self.lock:
                if code != 0 and self.data["stage"] not in ("cache_failed", "disconnected"):
                    logs = "\n".join(self.data.get("logs", [])).lower()
                    if (self.data.get("provider") == "colab" and "creating session" in logs
                            and "service unavailable" in logs):
                        message = "Colab could not provide the G4 session (service unavailable). Check your CU balance and retry later; GPU availability can also vary."
                    else:
                        message = self.data.get("message") if self.data.get("stage") == "error" else None
                        message = message or "Connection/setup failed. Read the log, then retry or disconnect."
                    self.data.update(stage="error", message=message)
                self.data.update(authUrl="", needsInput=False)
        finally:
            with self.lock:
                if self.master is not None:
                    os.close(self.master)
                    self.master = None
                self.process = None

    def reply(self, value):
        if not isinstance(value, str) or len(value) > 8192 or "\n" in value or "\r" in value:
            raise ValueError("Enter one authorization code or press Continue.")
        with self.lock:
            if self.process is None or not self.data["needsInput"]:
                raise ValueError("No account confirmation is pending.")
            payload = (value + "\n").encode()
            if self.master is not None:
                os.write(self.master, payload)
            else:
                self.process.stdin.write(payload)
                self.process.stdin.flush()
            self.data.update(needsInput=False, authUrl="")

    def disconnect(self):
        from .client_tools import client_bin, client_dir
        modal_session_path = client_dir() / "modal-session.json"
        modal_session = json.loads(modal_session_path.read_text()) if modal_session_path.is_file() else None
        with self.lock:
            process = self.process
            self.data.update(stage="disconnecting", message="Releasing cloud resources…")
        if process and process.poll() is None:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            try:
                process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                if os.name != "nt":
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
        from .settings import load_compute_settings
        preferences = load_compute_settings()
        if preferences.get("managed_colab") == "yes" and preferences["colab_session"]:
            tool = client_bin("colab")
            if tool:
                stopped = subprocess.run([tool, "stop", "--session", preferences["colab_session"]], capture_output=True, timeout=90)
                if stopped.returncode:
                    listing = subprocess.run([tool, "sessions"], capture_output=True, text=True, check=True, timeout=30).stdout
                    if "[" + preferences["colab_session"] + "]" in listing:
                        raise RuntimeError("Colab session is still active")
        if modal_session:
            tool = client_bin("modal")
            if not tool:
                raise RuntimeError("Modal client is required to disconnect the existing GPU app.")
            for app_id in [modal_session["app_id"], *modal_session.get("extra_apps", [])]:
                stopped = subprocess.run([tool, "app", "stop", "--yes", app_id], capture_output=True, timeout=90)
                if stopped.returncode:
                    listing = subprocess.run([tool, "app", "list", "--json"], capture_output=True, text=True, check=True, timeout=30)
                    if any(row.get("app_id") == app_id and row.get("state") != "stopped" for row in json.loads(listing.stdout)):
                        raise RuntimeError("Modal app is still active")
            modal_session_path.unlink(missing_ok=True)
        with self.lock:
            self.data.update(stage="disconnected", message="Disconnected. Cloud processing is stopped.", authUrl="", needsInput=False)


connection = CloudConnection()
