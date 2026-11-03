"""Run the existing batch pipeline in an authenticated Colab session."""

from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .colab_tunnel import stop_colab_session, validate_session_name


def _cli_executable() -> str:
    if os.name == "nt":
        raise RuntimeError(
            "The Colab CLI currently supports macOS and Linux, not Windows. "
            "Choose this computer for offline dubbing on Windows."
        )
    from .client_tools import client_bin
    candidate = client_bin("colab")
    if candidate:
        return candidate
    executable_name = "colab.exe" if os.name == "nt" else "colab"
    candidate_path = Path(sys.executable).resolve().parent / executable_name
    if candidate_path.is_file():
        return str(candidate_path)
    raise RuntimeError(
        "Colab provider needs the Colab CLI in this Python environment. "
        "Install it with `python -m pip install -r requirements-colab.txt`, "
        "then authenticate with your Colab account."
    )


class ColabBatchRunner:
    """Upload a video and run the repository's existing pipeline on Colab."""

    def __init__(self, session: str, *, remote_project: str = "/content/video-dubbing-translator-main",
                 project_root: str | Path, poll_interval: float = 2.5, stop_after_job: bool = False) -> None:
        self.session = validate_session_name(session)
        self.remote_project = self._validate_remote_project(remote_project)
        self.project_root = Path(project_root).expanduser().resolve()
        self.poll_interval = max(1.0, float(poll_interval))
        self.colab = _cli_executable()
        self.job_id: str | None = None
        self.job_config: dict | None = None
        self._session_touched = False
        self.stop_after_job = stop_after_job
        self.workspace = "/content/.vdt-colab-backend/" + hashlib.sha256(self.session.encode()).hexdigest()[:12]

    @staticmethod
    def _validate_remote_project(value: str) -> str:
        path = Path(str(value)).as_posix()
        if not path.startswith("/content/") or ".." in Path(path).parts or path == "/content/":
            raise ValueError("Remote project directory must be a subdirectory of /content.")
        return path.rstrip("/")

    def _run_cli(self, args: list[str], *, timeout: float = 120.0) -> str:
        command = [self.colab, *args, "--session", self.session]
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, check=False)
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            raise RuntimeError(output.strip() or f"Colab CLI exited with code {result.returncode}.")
        return output

    def _remote_action(self, action: str, config: dict) -> dict:
        encoded = base64.b64encode(json.dumps(config, ensure_ascii=False).encode("utf-8")).decode("ascii")
        helper = self.project_root / "scripts" / "colab_backend_remote.py"
        output = self._run_cli([
            "exec", "--timeout", "60",
            "--env", f"VDT_COLAB_ACTION={action}",
            "--env", f"VDT_COLAB_CONFIG_B64={encoded}",
            "--file", str(helper),
        ], timeout=90)
        match = re.search(r"^VDT_COLAB_RESULT=(.+)$", output, re.MULTILINE)
        if not match:
            raise RuntimeError("Colab did not return a backend result. " + output[-2000:])
        result = json.loads(match.group(1))
        if "error" in result:
            raise RuntimeError(result["error"])
        return result

    def _prepare(self, job_id: str, options: dict) -> dict:
        config = {
            "project_root": self.remote_project,
            "workspace_dir": self.workspace,
            "job_dir": f"{self.workspace}/jobs/{job_id}",
            "translation_model": str(options.get("translation_model") or "hymt2-1.8b"),
        }
        return self._remote_action("prepare", config)

    def run(self, input_video: str | Path, output_video: str | Path, options: dict) -> Path:
        """Run a job while retaining its VM, unless an advanced caller opts in to release."""
        previous_sigterm = None
        if threading.current_thread() is threading.main_thread():
            previous_sigterm = signal.getsignal(signal.SIGTERM)

            def handle_sigterm(signum, _frame):
                self.cancel()
                raise SystemExit(128 + signum)

            signal.signal(signal.SIGTERM, handle_sigterm)
        try:
            return self._run_job(input_video, output_video, options)
        finally:
            try:
                if previous_sigterm is not None:
                    signal.signal(signal.SIGTERM, previous_sigterm)
            finally:
                if getattr(self, "stop_after_job", False):
                    self._stop_selected_session()

    def _stop_selected_session(self) -> None:
        if not self._session_touched:
            return
        print(
            f"Closing selected Colab session {self.session} to release its runtime. "
            "Files stored only on that VM will no longer be available.",
            flush=True,
        )
        try:
            output = stop_colab_session(self.colab, self.session)
            if output:
                print(output, flush=True)
            else:
                print(f"Colab session {self.session} stopped.", flush=True)
        except Exception as exc:
            # Preserve the pipeline result/error; the warning makes failed
            # resource cleanup visible and gives the user an actionable state.
            print(
                f"Could not confirm shutdown of Colab session {self.session}: {exc}. "
                "Run `colab stop --session " + self.session + "` to release it manually.",
                flush=True,
            )

    def _run_job(self, input_video: str | Path, output_video: str | Path, options: dict) -> Path:
        input_path = Path(input_video).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Input video not found: {input_path}")
        if not (self.project_root / "scripts" / "run_pipeline.py").is_file():
            raise RuntimeError("Local repository is missing scripts/run_pipeline.py.")
        self.job_id = uuid.uuid4().hex
        self._session_touched = True
        prepared = self._prepare(self.job_id, options)
        print(f"Connected to Colab session {self.session}; remote Python: {prepared['python']}", flush=True)
        print("Using the selected Colab project in place; repository files and models are not overwritten.", flush=True)

        job_dir = f"{self.workspace}/jobs/{self.job_id}"
        suffix = input_path.suffix.lower()
        if not suffix or not suffix.lstrip(".").isalnum():
            suffix = ".mp4"
        remote_input = f"{job_dir}/input{suffix}"
        self._run_cli(["upload", str(input_path), remote_input], timeout=1800)
        print("Input video uploaded to Colab over the authenticated Colab CLI.", flush=True)

        source = str(options.get("source_language") or "auto")
        target = str(options.get("target_language") or "en")
        translation_model = str(options.get("translation_model") or "hymt2-1.8b")
        translation_device = str(options.get("translation_device") or "auto")
        if translation_device == "mps":
            raise ValueError("Apple MPS is local-only. Select Auto or CUDA for a Colab run.")
        run_args = [
            remote_input,
            "--source-language", source,
            "--target-language", target,
            "--translation-model", translation_model,
            "--translation-device", translation_device,
            "--diarization-device", "auto",
        ]
        if not bool(options.get("diarization", True)):
            run_args.append("--no-diarization")
        for option, key in (("--num-speakers", "num_speakers"),
                            ("--min-speakers", "min_speakers"),
                            ("--max-speakers", "max_speakers")):
            value = options.get(key)
            if value not in (None, ""):
                run_args.extend([option, str(value)])
        glossary_text = str(options.get("glossary") or "").strip()
        if glossary_text:
            glossary_path = f"{job_dir}/glossary.json"
            local_glossary = Path(tempfile.gettempdir()) / f"vdt-colab-glossary-{self.job_id}.json"
            local_glossary.write_text(json.dumps(json.loads(glossary_text), ensure_ascii=False), encoding="utf-8")
            try:
                self._run_cli(["upload", str(local_glossary), glossary_path], timeout=120)
            finally:
                local_glossary.unlink(missing_ok=True)
            run_args.extend(["--glossary", glossary_path])
        if bool(options.get("lip_sync", False)):
            run_args.append("--lip-sync")

        remote_status = f"{job_dir}/status.json"
        remote_log = f"{job_dir}/worker.log"
        remote_worker = f"{job_dir}/colab_backend_remote.py"
        self._run_cli([
            "upload",
            str(self.project_root / "scripts" / "colab_backend_remote.py"),
            remote_worker,
        ], timeout=120)
        worker_python = prepared["python"]
        job_config = {
            "project_root": self.remote_project,
            "python": worker_python,
            "input_path": remote_input,
            "status_path": remote_status,
            "log_path": remote_log,
            "run_arguments": run_args,
            "lip_sync": bool(options.get("lip_sync", False)),
        }
        child_config_path = f"{job_dir}/worker-config.json"
        self._upload_json(job_config, child_config_path)
        pid_path = f"{job_dir}/worker.pid"
        self.job_config = {
            "project_root": self.remote_project,
            "worker_script": remote_worker,
            "worker_config": child_config_path,
            "worker_python": worker_python,
            "log_path": remote_log,
            "pid_path": pid_path,
            "status_path": remote_status,
        }
        started = self._remote_action("start", self.job_config)
        print(f"Started remote Colab job {self.job_id} (PID {started.get('pid')}).", flush=True)
        log_offset = 0
        while True:
            config = {"status_path": remote_status, "log_path": remote_log, "log_offset": log_offset}
            result = self._remote_action("poll", config)
            new_log = result.get("log", "")
            log_offset = int(result.get("log_offset", log_offset))
            if new_log:
                print(new_log, end="" if new_log.endswith("\n") else "\n", flush=True)
            status = result.get("status") or {}
            state = status.get("state")
            if state in {"finished", "failed", "cancelled"}:
                if state != "finished" or status.get("exit_code") != 0:
                    raise RuntimeError(
                        status.get("error") or f"Remote Colab pipeline {state}; exit code {status.get('exit_code')}."
                    )
                remote_output = status.get("output_path")
                if not remote_output:
                    raise RuntimeError("Colab pipeline finished without reporting its output file.")
                local_output = Path(output_video).expanduser().resolve()
                local_output.parent.mkdir(parents=True, exist_ok=True)
                self._run_cli(["download", remote_output, str(local_output)], timeout=1800)
                print(f"Output copied back for local preview: {local_output}", flush=True)
                return local_output
            time.sleep(self.poll_interval)

    def _upload_json(self, value: dict, remote_path: str) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
            json.dump(value, handle, ensure_ascii=False)
            local_path = Path(handle.name)
        try:
            self._run_cli(["upload", str(local_path), remote_path], timeout=120)
        finally:
            local_path.unlink(missing_ok=True)

    def cancel(self) -> None:
        if not self.job_config:
            return
        try:
            self._remote_action("cancel", self.job_config)
        except Exception as exc:
            print(f"Could not send remote cancel request: {exc}", flush=True)
