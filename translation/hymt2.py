from __future__ import annotations

import json
import subprocess
import sys
import threading
import uuid
from collections import deque
from pathlib import Path
from typing import Mapping, Optional

from .base import TranslationContext, Translator
from .model_manager import BASE_DIR, is_installed, model_path, runtime_python
from .prompts import (duration_prompt, sanitize_translation_output,
                      simultaneous_proposal_prompt, translation_prompt)
from .registry import get_model


class HyMT2ProtocolError(RuntimeError):
    pass


class HyMT2Translator(Translator):
    """Persistent local Hy-MT2 subprocess using a strict JSON-lines protocol."""

    def __init__(self, model_key: str = "hymt2-1.8b", device: str = "auto") -> None:
        self.model = get_model(model_key)
        self.device = validate_device(device)
        if not runtime_python().is_file():
            raise RuntimeError(
                "Hy-MT2 runtime is missing. Run `python scripts/setup_hymt2.py`."
            )
        if not is_installed(self.model):
            raise RuntimeError(
                f"{self.model.display_name} is not installed. Run `python "
                f"scripts/manage_hymt2_models.py download {self.model.key}`."
            )
        self._lock = threading.Lock()
        self.last_generation: dict[str, object] = {}
        self._process = subprocess.Popen(
            [
                str(runtime_python()),
                str(BASE_DIR / "translation" / "hymt2_worker.py"),
                "--model-path",
                str(model_path(self.model).resolve()),
                "--device",
                self.device,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self._stderr_lines = deque(maxlen=100)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, daemon=True)
        self._stderr_thread.start()

    def _drain_stderr(self) -> None:
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            self._stderr_lines.append(line.rstrip())
            print(f"[Hy-MT2] {line}", end="", file=sys.stderr, flush=True)

    def _stderr_detail(self) -> str:
        return "\n".join(self._stderr_lines).strip()

    def _request(self, operation: str, prompt: str, *, deterministic: bool = False) -> str:
        request_id = uuid.uuid4().hex
        request = {"id": request_id, "operation": operation, "prompt": prompt}
        if deterministic:
            request["deterministic"] = True
        with self._lock:
            if self._process.poll() is not None:
                raise HyMT2ProtocolError(
                    f"Hy-MT2 worker exited unexpectedly: {self._stderr_detail()}"
                )
            assert self._process.stdin is not None
            assert self._process.stdout is not None
            self._process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            self._process.stdin.flush()
            response_line = self._process.stdout.readline()
        if not response_line:
            raise HyMT2ProtocolError(
                f"Hy-MT2 worker returned no response: {self._stderr_detail()}"
            )
        try:
            response = json.loads(response_line)
        except json.JSONDecodeError as exc:
            raise HyMT2ProtocolError("Hy-MT2 worker returned invalid JSON.") from exc
        if response.get("id") != request_id:
            raise HyMT2ProtocolError("Hy-MT2 response ID did not match the request.")
        if not response.get("ok"):
            raise RuntimeError(str(response.get("error") or "Hy-MT2 inference failed."))
        self.last_generation = {
            "operation": operation,
            "prompt": prompt,
            **(response.get("stats") or {}),
        }
        if operation == "prepare":
            if not (response.get("stats") or {}).get("prepared"):
                raise HyMT2ProtocolError("Hy-MT2 did not confirm model preparation.")
            return ""
        text = str(response.get("text", ""))
        return text.strip() if operation == "simultaneous_proposal" else sanitize_translation_output(text)

    def propose_simultaneous(
        self,
        source_units: list[dict[str, object]],
        source_language: Optional[str],
        target_language: str,
        *,
        eligible_ends: list[int],
        preferred_end_unit: int,
        previous_source: str = "",
        previous_translation: str = "",
        glossary: Optional[Mapping[str, str]] = None,
    ) -> str:
        """Return a deterministic bounded READ/WAIT/WRITE proposal as JSON."""
        prompt = simultaneous_proposal_prompt(
            source_units, source_language, target_language,
            eligible_ends=eligible_ends,
            preferred_end_unit=preferred_end_unit,
            previous_source=previous_source,
            previous_translation=previous_translation,
            glossary=glossary,
        )
        return self._request("simultaneous_proposal", prompt, deterministic=True)

    def prepare(self) -> None:
        """Optional explicit preload; the offline lazy default is unchanged."""
        self._request("prepare", "")

    def translate(
        self,
        source_text: str,
        source_language: Optional[str],
        target_language: str,
        *,
        context: Optional[TranslationContext] = None,
        glossary: Optional[Mapping[str, str]] = None,
        deterministic: bool = False,
    ) -> str:
        return self._request(
            "translate",
            translation_prompt(
                source_text,
                source_language,
                target_language,
                context=context,
                glossary=glossary,
            ),
            deterministic=deterministic,
        )

    def rewrite_for_duration(
        self,
        source_text: str,
        current_translation: str,
        source_language: Optional[str],
        target_language: str,
        target_duration_seconds: float,
        actual_duration_seconds: float,
        *,
        context: Optional[TranslationContext] = None,
        glossary: Optional[Mapping[str, str]] = None,
    ) -> str:
        return self._request(
            "rewrite_for_duration",
            duration_prompt(
                source_text,
                current_translation,
                source_language,
                target_language,
                target_duration_seconds,
                actual_duration_seconds,
                context=context,
                glossary=glossary,
            ),
        )

    def close(self) -> None:
        process = getattr(self, "_process", None)
        if process is None or process.poll() is not None:
            return
        try:
            assert process.stdin is not None
            process.stdin.write(json.dumps({"operation": "shutdown"}) + "\n")
            process.stdin.flush()
            process.wait(timeout=5)
        except (BrokenPipeError, subprocess.TimeoutExpired):
            process.terminate()
        finally:
            self._process = None


def validate_device(device: str) -> str:
    normalized = device.strip().lower()
    if normalized in {"auto", "cpu", "mps", "cuda"}:
        return normalized
    if normalized.startswith("cuda:") and normalized[5:].isdigit():
        return normalized
    raise ValueError("Translation device must be auto, cpu, mps, cuda, or cuda:N.")
