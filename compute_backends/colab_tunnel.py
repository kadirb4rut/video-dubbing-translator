"""Authenticated SSH forwarding to a user's selected Colab runtime.

The forwarded ports bind to loopback only. The launcher owns the selected
runtime for the duration of the browser session and releases it on disconnect.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import tempfile
import time
from typing import Iterable
from urllib.request import Request, urlopen


REMOTE_HOST = "root@colab-runtime"
LIVE_APP_ID = "video-dubbing-translator-live-v1"
SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


def validate_session_name(value: str) -> str:
    session = str(value or "").strip()
    if not SESSION_RE.fullmatch(session):
        raise ValueError(
            "Enter the Colab CLI session name (letters, numbers, dot, underscore, "
            "or dash; 1–63 characters)."
        )
    return session


def proxy_command(colab_executable: str, session: str, identity: str) -> str:
    session = validate_session_name(session)
    return shlex.join([
        colab_executable, "ssh", "--proxy-mode", "--session", session,
        "--identity", identity,
    ])


def stop_colab_session(colab_executable: str, session: str, *, timeout: float = 90.0) -> str:
    """Stop the explicitly selected Colab VM and return its CLI output."""
    result = subprocess.run(
        [colab_executable, "stop", "--session", validate_session_name(session)],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    output = ((result.stdout or "") + (result.stderr or "")).strip()
    if result.returncode != 0:
        raise RuntimeError(output or f"Colab CLI stop exited with code {result.returncode}.")
    return output


def build_tunnel_command(
    *,
    ssh_executable: str,
    colab_executable: str,
    session: str,
    identity: str,
    control_path: str,
    forwards: Iterable[tuple[int, int]] = ((8766, 8766),),
) -> list[str]:
    """Build an SSH multiplex master with loopback-only local forwards."""
    safe_session = validate_session_name(session)
    values = tuple((int(local), int(remote)) for local, remote in forwards)
    if not values:
        raise ValueError("At least one Colab port forward is required.")
    for local, remote in values:
        if not (1024 <= local <= 65535 and 1024 <= remote <= 65535):
            raise ValueError("Colab forwarded ports must be in the range 1024–65535.")
    proxy = proxy_command(colab_executable, safe_session, identity)
    command = [
        ssh_executable,
        "-N",
        "-M",
        "-S", control_path,
        "-i", identity,
        "-o", "IdentitiesOnly=yes",
        "-o", f"ProxyCommand={proxy}",
        "-o", "ControlMaster=yes",
        "-o", "ControlPersist=no",
        "-o", "ExitOnForwardFailure=yes",
        "-o", "ServerAliveInterval=30",
        "-o", "ServerAliveCountMax=3",
        # Colab's CLI intentionally uses an ephemeral abstract SSH host and
        # documents these settings for its own interactive SSH connection.
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
    ]
    for local, remote in values:
        command.extend(["-L", f"127.0.0.1:{local}:127.0.0.1:{remote}"])
    command.append(REMOTE_HOST)
    return command


def _port_available(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _service_status(port: int) -> dict | None:
    request = Request(
        f"http://127.0.0.1:{port}/api/status",
        headers={"Host": f"127.0.0.1:{port}"},
    )
    try:
        with urlopen(request, timeout=1.0) as response:
            value = json.loads(response.read())
    except (OSError, ValueError, TimeoutError):
        return None
    return value if value.get("app") == LIVE_APP_ID else None


class ColabLiveTunnel:
    """Own one local-to-Colab tunnel for a Live Dubbing browser process."""

    def __init__(
        self,
        session: str,
        *,
        local_port: int = 8766,
        remote_port: int = 8766,
        startup_timeout: float = 15.0,
        remote_project: str = "/content/video-dubbing-translator-main",
        log_path: str | Path | None = None,
    ) -> None:
        self.session = validate_session_name(session)
        self.local_port = int(local_port)
        self.remote_port = int(remote_port)
        if not (1024 <= self.local_port <= 65535 and 1024 <= self.remote_port <= 65535):
            raise ValueError("The Live Dubbing tunnel port must be in the range 1024–65535.")
        self.startup_timeout = max(1.0, float(startup_timeout))
        self.remote_project = str(remote_project)
        self.log_path = Path(log_path) if log_path else None
        self.process: subprocess.Popen | None = None
        self._colab_executable: str | None = None
        self._session_established = False
        self._temporary: tempfile.TemporaryDirectory | None = None
        self._log_handle = None

    def _tools(self) -> tuple[str, str, str]:
        if os.name == "nt":
            raise RuntimeError(
                "The Colab CLI currently supports macOS and Linux, not Windows. "
                "Choose this computer for Live Dubbing on Windows."
            )
        from .client_tools import client_bin
        colab = client_bin("colab")
        ssh = shutil.which("ssh")
        keygen = shutil.which("ssh-keygen")
        if not colab:
            raise RuntimeError(
                "Colab provider needs the authenticated Colab CLI. Install "
                "google-colab-cli and sign in with your Colab account."
            )
        if not ssh or not keygen:
            raise RuntimeError("Colab port forwarding needs OpenSSH and ssh-keygen on this computer.")
        return colab, ssh, keygen

    def _ensure_remote_service(self, colab: str) -> dict:
        project = Path(self.remote_project)
        if (not project.is_absolute() or project == Path('/content')
                or Path('/content') not in project.parents or '..' in project.parts):
            raise RuntimeError('The Colab Live Dubbing project must be a managed folder below /content.')
        # A persistent G4 may still be running a service from an older local
        # checkout. Refresh only this small server module; AI caches and the
        # VM are left untouched. The remote helper restarts it only when idle.
        remote_server = str(project / 'live_dubbing' / 'server.py')
        uploaded = subprocess.run(
            [colab, 'upload', str(Path(__file__).resolve().parents[1] / 'live_dubbing' / 'server.py'),
             remote_server, '--session', self.session],
            capture_output=True, text=True, timeout=60, check=False,
        )
        if uploaded.returncode != 0:
            detail = ((uploaded.stdout or '') + (uploaded.stderr or '')).strip()[-1000:]
            raise RuntimeError(
                'Could not update the small Live Dubbing service file on Colab. '
                'Check that the selected project exists and retry. ' + detail
            )
        helper = Path(__file__).resolve().parents[1] / "scripts" / "colab_live_remote.py"
        result = subprocess.run(
            [
                colab, "exec", "--session", self.session, "--timeout", "35",
                "--env", f"VDT_LIVE_PROJECT_ROOT={self.remote_project}",
                "--file", str(helper),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        output = (result.stdout or "") + (result.stderr or "")
        match = re.search(r"^VDT_REMOTE_LIVE_STATUS=(.+)$", output, re.MULTILINE)
        if result.returncode != 0 or not match:
            detail_match = re.search(r"^VDT_REMOTE_LIVE_ERROR=(.+)$", output, re.MULTILINE)
            detail = detail_match.group(1) if detail_match else output.strip()[-1800:]
            raise RuntimeError(
                "Could not start or reuse the Live Dubbing service in the selected Colab session. "
                "Check that its project and models are prepared. " + detail
            )
        try:
            status = json.loads(match.group(1))
        except ValueError as exc:
            raise RuntimeError("Colab returned an invalid Live Dubbing readiness status.") from exc
        if status.get("app") != LIVE_APP_ID:
            raise RuntimeError("The selected Colab session did not report this project's Live Dubbing service.")
        if status.get('pairing_api_version', 0) < 1:
            raise RuntimeError(
                'Colab is still serving an older Live Dubbing service. Close its Live Dubbing browser '
                'and retry so the idle service can be refreshed safely.'
            )
        return status

    def _prepare_key_and_socket(self, keygen: str) -> tuple[str, str]:
        # Keep the private key and SSH control socket out of the project tree.
        # The key is temporary and removed when this tunnel closes.
        parent = Path.home() / ".cache" / "vdt-colab"
        parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(parent, 0o700)
        self._temporary = tempfile.TemporaryDirectory(prefix="c-", dir=parent)
        try:
            directory = Path(self._temporary.name)
            key_path = directory / "k"
            result = subprocess.run(
                [keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            if result.returncode:
                raise RuntimeError("Could not create a temporary Colab SSH key: " + result.stderr.strip())
            return str(key_path), str(directory / "s")
        except Exception:
            self._temporary.cleanup()
            self._temporary = None
            raise

    def start(self) -> dict:
        if self.process is not None and self.process.poll() is None:
            status = _service_status(self.local_port)
            if status:
                return status
            raise RuntimeError("The existing Colab tunnel is open, but its Live Dubbing service is not responding.")
        colab, ssh, keygen = self._tools()
        self._colab_executable = colab
        if not _port_available(self.local_port):
            local_service = _service_status(self.local_port)
            if local_service:
                raise RuntimeError(
                    f"Local port {self.local_port} is already serving local Live Dubbing. "
                    "Close its browser/service before switching the live provider to Colab."
                )
            raise RuntimeError(
                f"Local port {self.local_port} is occupied. Close the application using it, then retry."
            )
        # From here the app has actively entered the user's selected runtime;
        # release it even if service setup or local browser startup subsequently fails.
        self._session_established = True
        remote_status = self._ensure_remote_service(colab)
        identity, control_path = self._prepare_key_and_socket(keygen)
        command = build_tunnel_command(
            ssh_executable=ssh,
            colab_executable=colab,
            session=self.session,
            identity=identity,
            control_path=control_path,
            forwards=((self.local_port, self.remote_port),),
        )
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = self.log_path.open("a", encoding="utf-8")
        else:
            self._log_handle = open(os.devnull, "w", encoding="utf-8")
        try:
            self.process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=self._log_handle,
                stderr=subprocess.STDOUT,
                start_new_session=(os.name != "nt"),
            )
            deadline = time.monotonic() + self.startup_timeout
            while time.monotonic() < deadline:
                if self.process.poll() is not None:
                    raise RuntimeError(self._startup_error())
                status = _service_status(self.local_port)
                if status:
                    return {**status, "remote_models": remote_status.get("models", {})}
                time.sleep(0.2)
            raise RuntimeError(
                "Connected to the Colab port forward, but the remote Live Dubbing service "
                "did not answer. Start it in the selected G4 session and retry."
            )
        except Exception:
            self.stop()
            raise

    def _startup_error(self) -> str:
        message = "Colab SSH tunnel closed during startup. Check the Colab CLI login, session name, and active runtime."
        if self.log_path and self.log_path.is_file():
            try:
                lines = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if lines:
                    message += "\n" + "\n".join(lines[-8:])
            except OSError:
                pass
        return message

    def stop(self, *, stop_session: bool = False) -> None:
        process, self.process = self.process, None
        stop_session = stop_session and self._session_established
        self._session_established = False
        if process is not None and process.poll() is None:
            try:
                if os.name != "nt":
                    import signal
                    os.killpg(process.pid, signal.SIGTERM)
                else:
                    process.terminate()
                process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=3)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        if self._log_handle is not None:
            self._log_handle.close()
            self._log_handle = None
        if self._temporary is not None:
            self._temporary.cleanup()
            self._temporary = None
        # Browser/tunnel closure preserves the VM; explicit Disconnect GPU owns release.
        if stop_session and self._colab_executable:
            try:
                output = stop_colab_session(self._colab_executable, self.session)
                print(output or f"Colab session {self.session} stopped.", flush=True)
            except Exception as exc:
                print(
                    f"Could not confirm Colab session shutdown for {self.session}: {exc}. "
                    "Use Disconnect GPU to retry releasing the Colab runtime.",
                    flush=True,
                )
