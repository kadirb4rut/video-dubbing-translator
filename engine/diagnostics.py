"""Private process diagnostics and conservative browser-facing redaction."""

from __future__ import annotations

from datetime import datetime, timezone
import os
from pathlib import Path
import re
import tempfile


_TOKEN_PATTERNS = (
    re.compile(r"\b(?:hf_[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{16,}|sk-[A-Za-z0-9_-]{16,})\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*"),
    re.compile(r"(?i)(\b(?:hf[_-]?token|api[_-]?key|access[_-]?token|authorization|password|secret)\b\s*[:=]\s*)([^\s,;]+)"),
)
_PRIVATE_PATH = re.compile(r"(?<![\w])(?:/Users|/home|/private/var|/tmp|/content)/[^\s\"'<>]+")
_WINDOWS_USER_PATH = re.compile(r"(?i)\b[A-Z]:\\Users\\[^\s\"'<>]+")
_TRAILING_PUNCTUATION = ",;:.)]}>"


def _safe_basename(path_text: str) -> str:
    trimmed = path_text.rstrip(_TRAILING_PUNCTUATION)
    suffix = path_text[len(trimmed) :]
    basename = Path(trimmed.replace("\\", "/")).name or "file"
    return f"[local path]/{basename}{suffix}"


def sanitize_for_display(text: str) -> str:
    """Redact common credentials and local paths before sending output to a browser."""
    result = str(text)
    for pattern in _TOKEN_PATTERNS:
        result = pattern.sub(lambda match: (match.group(1) + "[redacted]") if match.lastindex else "[redacted]", result)

    secret_env_names = {
        name
        for name in os.environ
        if any(part in name.upper() for part in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY", "CREDENTIAL"))
        or name.upper() in {"HF_TOKEN", "HUGGING_FACE_HUB_TOKEN", "GITHUB_TOKEN", "AUTHORIZATION"}
    }
    for name in secret_env_names:
        value = os.environ.get(name, "")
        if len(value) >= 6:
            result = result.replace(value, "[redacted]")

    result = _PRIVATE_PATH.sub(lambda match: _safe_basename(match.group(0)), result)
    result = _WINDOWS_USER_PATH.sub(lambda match: _safe_basename(match.group(0)), result)
    home = str(Path.home())
    if home and home != "/":
        result = result.replace(home, "[home]")
    return result


def create_private_diagnostic_log(
    directory: str | Path,
    *,
    stage: str,
    input_name: str = "",
) -> Path:
    """Create a mode-0600 log for raw subprocess output and tracebacks."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        directory.chmod(0o700)
    fd, name = tempfile.mkstemp(prefix="voxadub-diagnostic-", suffix=".log", dir=directory)
    path = Path(name)
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="replace") as log:
            log.write(f"Started: {datetime.now(timezone.utc).isoformat()}\n")
            log.write(f"Stage: {sanitize_for_display(stage)}\n")
            if input_name:
                log.write(f"Input: {Path(input_name).name}\n")
            log.write("Raw subprocess output and diagnostics follow.\n\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        path.unlink(missing_ok=True)
        raise
    return path
