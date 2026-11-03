"""Bundle only application code; never include media, caches or credentials."""
from __future__ import annotations

import hashlib
from pathlib import Path
import tarfile
import tempfile

PACKAGES = ("compute_backends", "translation", "diarization", "dubbing", "engine", "live_dubbing", "browser_extension/live_dubbing")
SCRIPTS = ("run_pipeline.py", "download_model.py", "setup_models.py", "setup_hymt2.py", "manage_hymt2_models.py", "setup_diarization.py", "setup_latentsync.py", "setup_live_segmentation.py", "cloud_bootstrap.py", "colab_backend_remote.py", "colab_live_remote.py")


def source_files(root: Path) -> list[Path]:
    files = [root / "Video_Translator.py", root / "voxcpm_runtime.py"] + list(root.glob("requirements*.txt"))
    for package in PACKAGES:
        files += [p for p in (root / package).rglob("*") if p.suffix in (".py", ".js", ".html", ".css", ".json")]
    files += [root / "scripts" / name for name in SCRIPTS]
    files += [root / "vocal-remover" / "inference.py"]
    files += list((root / "vocal-remover" / "lib").glob("*.py"))
    return sorted({p for p in files if p.is_file() and not p.is_symlink() and "__pycache__" not in p.parts})


def bundle_source(root: Path) -> tuple[Path, str]:
    files = source_files(root)
    digest = hashlib.sha256()
    for path in files:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    fingerprint = digest.hexdigest()[:20]
    directory = Path(tempfile.gettempdir()) / "vdt-source-bundles"
    directory.mkdir(exist_ok=True)
    destination = directory / (fingerprint + ".tar.gz")
    if not destination.is_file():
        with tarfile.open(destination, "w:gz") as archive:
            for path in files:
                archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
    return destination, fingerprint
