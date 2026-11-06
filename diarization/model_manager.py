from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
MODEL_ID = "pyannote/speaker-diarization-community-1"
MODEL_DIR = Path(
    os.environ.get(
        "DIARIZATION_MODEL_DIR", BASE_DIR / "third_party" / "diarization" / "community-1"
    )
).expanduser()
RUNTIME_DIR = BASE_DIR / "third_party" / "diarization"


@dataclass(frozen=True)
class DiarizationStatus:
    installed: bool
    model_dir: Path
    runtime_ready: bool
    detail: str


def runtime_python() -> Path:
    configured = os.environ.get("DIARIZATION_PYTHON")
    if configured:
        return Path(configured).expanduser()
    candidates = (
        RUNTIME_DIR / ".venv" / "bin" / "python",
        RUNTIME_DIR / ".venv" / "Scripts" / "python.exe",
    )
    return next((path for path in candidates if path.is_file()), candidates[0])


def model_installed(model_dir: Path = MODEL_DIR) -> bool:
    return not missing_model_files(model_dir)


def missing_model_files(model_dir: Path = MODEL_DIR) -> tuple[str, ...]:
    """List required Community-1 pipeline files absent from the local snapshot."""
    missing = []
    if not (model_dir / "config.yaml").is_file():
        missing.append("config.yaml")
    def has_weights(component: str) -> bool:
        directory = model_dir / component
        return any(directory.glob("*.safetensors")) or any(directory.glob("*.bin"))

    # Community-1 currently ships PyTorch .bin files, but accepting safetensors
    # keeps this check valid if the upstream packaging changes.
    for component in ("embedding", "segmentation"):
        if not has_weights(component):
            missing.append(f"{component}/*.(bin|safetensors)")
    # VBx clustering loads both PLDA transforms from config.yaml at pipeline
    # construction. Without them, pyannote fails only after processing starts.
    for name in ("plda/xvec_transform.npz", "plda/plda.npz"):
        if not (model_dir / name).is_file():
            missing.append(name)
    return tuple(missing)


def status(model_dir: Path = MODEL_DIR) -> DiarizationStatus:
    installed = model_installed(model_dir)
    runtime_ready = runtime_python().is_file()
    if installed and runtime_ready:
        detail = "Community-1 runtime and local model are ready."
    elif not runtime_ready:
        detail = "Diarization runtime is not installed; run scripts/setup_diarization.py."
    else:
        missing = ", ".join(missing_model_files(model_dir))
        detail = (
            f"Community-1 checkpoint is incomplete (missing: {missing}). "
            "Accept its Hugging Face terms, set HF_TOKEN, then run "
            "scripts/setup_diarization.py --download-model."
        )
    return DiarizationStatus(installed, model_dir, runtime_ready, detail)


def remove_model(model_dir: Path = MODEL_DIR) -> None:
    resolved = model_dir.expanduser().resolve()
    allowed_parent = (BASE_DIR / "third_party" / "diarization").resolve()
    if resolved == allowed_parent or allowed_parent not in resolved.parents:
        raise ValueError(f"Refusing to remove unexpected diarization path: {resolved}")
    if resolved.exists():
        shutil.rmtree(resolved)
