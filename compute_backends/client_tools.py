"""Optional cloud CLIs live in their own environment, never the AI environment."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def client_dir() -> Path:
    base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".cache"))
    return base / "VideoDubbingTranslator" / "cloud-client"


def client_bin(name: str) -> str | None:
    override = os.environ.get("VDT_" + name.upper() + "_CLI")
    candidates = [override, shutil.which(name), str(client_dir() / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else "")))]
    return next((str(p) for p in candidates if p and Path(p).is_file()), None)


def client_python() -> str:
    path = client_dir() / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    return str(path) if path.is_file() else sys.executable


def tool_python(name: str) -> str:
    tool = client_bin(name)
    if tool:
        try:
            first = Path(tool).read_text().splitlines()[0]
            if first.startswith("#!/") and Path(first[2:]).is_file():
                return first[2:]
        except (OSError, UnicodeError):
            pass
    return client_python()


def ensure_client(provider: str) -> str:
    name = "colab" if provider == "colab" else "modal"
    existing = client_bin(name)
    if existing:
        return existing
    print("Installing the optional cloud connection tools in a separate environment…", flush=True)
    uv = shutil.which("uv")
    if not uv:
        bootstrap = client_dir().with_name("cloud-installer")
        subprocess.run([sys.executable, "-m", "venv", str(bootstrap)], check=True)
        python = bootstrap / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        subprocess.run([str(python), "-m", "pip", "install", "uv==0.9.7"], check=True)
        uv = str(bootstrap / ("Scripts/uv.exe" if os.name == "nt" else "bin/uv"))
    if not Path(client_python()).is_relative_to(client_dir()):
        subprocess.run([uv, "venv", "--python", "3.12", "--seed", str(client_dir())], check=True)
    package = "google-colab-cli==0.7.5" if name == "colab" else "modal>=1.5,<2"
    subprocess.run([uv, "pip", "install", "--python", client_python(), package], check=True)
    result = client_bin(name)
    if not result:
        raise RuntimeError("Cloud client installation did not produce " + name)
    return result
