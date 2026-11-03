"""Small per-user compute preferences; authentication secrets are never saved."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .providers import validate_provider


def settings_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys_platform() == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "VideoDubbingTranslator" / "compute.json"


def sys_platform() -> str:
    import sys

    return sys.platform


def load_compute_settings() -> dict[str, str]:
    defaults = {
        "batch_provider": "local",
        "live_provider": "local",
        "colab_session": "",
        "remote_project": "/content/video-dubbing-translator-main",
        "managed_colab": "no",
    }
    try:
        raw = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return defaults
    if not isinstance(raw, dict):
        return defaults
    try:
        defaults["batch_provider"] = validate_provider(raw.get("batch_provider", "local"))
        live_provider = validate_provider(raw.get("live_provider", "local"))
    except ValueError:
        return defaults
    if live_provider in ("modal", "modal-tts"):
        live_provider = "local"
    defaults["live_provider"] = live_provider
    for key in ("colab_session", "remote_project", "managed_colab"):
        value = raw.get(key)
        if isinstance(value, str):
            defaults[key] = value.strip()
    return defaults


def save_compute_settings(settings: dict[str, str]) -> None:
    current = load_compute_settings()
    current["batch_provider"] = validate_provider(settings.get("batch_provider", current["batch_provider"]))
    live_provider = validate_provider(settings.get("live_provider", current["live_provider"]))
    if live_provider in ("modal", "modal-tts"):
        raise ValueError("Modal VoxCPM2 is not currently supported for live streaming.")
    current["live_provider"] = live_provider
    for key in ("colab_session", "remote_project", "managed_colab"):
        value = settings.get(key)
        if value is not None:
            current[key] = str(value).strip()
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(current, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix="compute-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
