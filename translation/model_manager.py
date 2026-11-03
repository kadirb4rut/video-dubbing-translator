from __future__ import annotations

import os
import shutil
from dataclasses import asdict
from pathlib import Path
from typing import Callable, Optional

from .registry import MODELS, HyMT2Model, get_model


BASE_DIR = Path(__file__).resolve().parents[1]
RUNTIME_DIR = BASE_DIR / "third_party" / "hymt2"
MODELS_DIR = Path(os.environ.get("HYMT2_MODELS_DIR", RUNTIME_DIR / "models")).expanduser()


def runtime_python() -> Path:
    configured = os.environ.get("HYMT2_PYTHON")
    if configured:
        return Path(configured).expanduser()
    candidates = (
        RUNTIME_DIR / ".venv" / "bin" / "python",
        RUNTIME_DIR / ".venv" / "Scripts" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def model_path(model: HyMT2Model | str) -> Path:
    model_info = get_model(model) if isinstance(model, str) else model
    return MODELS_DIR / model_info.key


def is_installed(model: HyMT2Model | str) -> bool:
    path = model_path(model)
    has_weights = any(path.glob("*.safetensors"))
    return (
        (path / "config.json").is_file()
        and (path / "tokenizer_config.json").is_file()
        and has_weights
    )


def model_status() -> list[dict[str, object]]:
    runtime_ready = runtime_python().is_file()
    return [
        {
            **asdict(model),
            "installed": is_installed(model),
            "path": str(model_path(model).resolve()),
            "runtime_ready": runtime_ready,
        }
        for model in MODELS
    ]


def download_model(
    key: str,
    *,
    token: Optional[str] = None,
    snapshot_download_fn: Optional[Callable[..., str]] = None,
) -> Path:
    """Download exactly one explicitly selected model into the ignored local store."""

    model = get_model(key)
    destination = model_path(model)
    destination.mkdir(parents=True, exist_ok=True)
    if snapshot_download_fn is None:
        from huggingface_hub import snapshot_download

        snapshot_download_fn = snapshot_download
    snapshot_download_fn(
        repo_id=model.model_id,
        local_dir=destination,
        token=token or os.environ.get("HF_TOKEN"),
    )
    if not is_installed(model):
        raise RuntimeError(
            f"Download finished but {model.display_name} is incomplete at {destination}."
        )
    return destination


def remove_model(key: str) -> None:
    path = model_path(key).resolve()
    root = MODELS_DIR.resolve()
    if path == root or root not in path.parents:
        raise ValueError(f"Refusing to remove unexpected model path: {path}")
    if path.exists():
        shutil.rmtree(path)
