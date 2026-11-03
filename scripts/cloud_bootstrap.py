"""Prepare a NEW managed cloud directory; do not modify a user's existing runtime."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def progress(message):
    print("VDT_SETUP_EVENT=" + json.dumps({"stage": "preparing", "message": message}), flush=True)


def run(command, *, env=None, cwd=None):
    process = subprocess.Popen([str(p) for p in command], env=env, cwd=cwd,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    for line in process.stdout:
        print(line, end="", flush=True)
    if process.wait():
        raise RuntimeError("Cloud preparation command failed; see its output above.")


def unpack(source: Path, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(source) as archive:
        members = archive.getmembers()
        for item in members:
            target = (destination / item.name).resolve()
            if not target.is_relative_to(destination.resolve()) or not (item.isfile() or item.isdir()):
                raise ValueError("Unsafe application archive entry")
        archive.extractall(destination, members=members, filter="data")


def prepare(config: dict) -> dict:
    root = Path(config["project"])
    cache = Path(config["cache"])
    progress("Colab G4 connected. Unpacking the application and checking its cache…")
    unpack(Path(config["archive"]), root)
    cache.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "HF_HOME": str(cache / "huggingface"), "XDG_CACHE_HOME": str(cache), "MPLBACKEND": "Agg", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
    constraints = root / ".cloud-constraints.txt"
    constraints.write_text("torch==2.8.0\ntorchaudio==2.8.0\ntorchcodec==0.7.0\nnumpy==2.2.6\n")
    env["PIP_CONSTRAINT"] = str(constraints)
    # Model data can live on Drive/a Modal Volume; executable environments stay on the VM.
    for relative, target in (("vocal-remover/models", cache / "vocal-models"), ("third_party/hymt2/models", cache / "hymt2"), ("third_party/diarization/community-1", cache / "community-1")):
        link = root / relative
        target.mkdir(parents=True, exist_ok=True)
        link.parent.mkdir(parents=True, exist_ok=True)
        if not link.exists():
            link.symlink_to(target, target_is_directory=True)
    (root / ".vdt-runtime.json").write_text(json.dumps({k: env[k] for k in ("HF_HOME", "XDG_CACHE_HOME", "MPLBACKEND")}))
    if not shutil.which("ffmpeg"):
        run(["apt-get", "update", "-qq"])
        run(["apt-get", "install", "-y", "-qq", "ffmpeg", "libgomp1"])
    uv = shutil.which("uv")
    if not uv:
        run([sys.executable, "-m", "pip", "install", "uv==0.9.7"])
        uv = shutil.which("uv")
    python = root / ".venv/bin/python"
    if not python.is_file():
        run([uv, "venv", "--python", "3.12", "--seed", root / ".venv"])
    spec = (root / "requirements-cloud.txt").read_bytes()
    marker = root / ".cloud-runtime-ready"
    signature = hashlib.sha256(spec).hexdigest()
    if not marker.is_file() or marker.read_text() != signature:
        progress("Installing the GPU runtime and Python dependencies on Colab. Large packages may take several minutes…")
        print("Installing the isolated Linux GPU runtime…", flush=True)
        run([uv, "pip", "install", "--python", python, "-r", root / "requirements-cloud.txt"], env=env)
        progress("Preparing the Hy-MT2 translation runtime on Colab…")
        hy_dir = root / "third_party/hymt2/.venv"
        hy_python = hy_dir / "bin/python"
        # Colab's system Python can create an unseeded venv; use the managed
        # interpreter and uv for this NEW environment instead of system ensurepip.
        if not hy_python.is_file() or subprocess.run([str(hy_python), "-c", "import pip"], capture_output=True).returncode:
            run([uv, "venv", "--clear", "--python", python, "--seed", hy_dir])
        run([uv, "pip", "install", "--python", hy_python, "-r", root / "requirements-hymt2.txt", "-c", constraints], env=env)
        marker.write_text(signature)
    # These existing setup commands verify/reuse caches, downloading only missing files.
    progress("Checking speech and voice models; downloading missing files to the Colab VM…")
    run([python, root / "scripts/setup_models.py"], env=env, cwd=root)
    progress("Checking the selected translation model; downloading missing files if needed…")
    run([python, root / "scripts/manage_hymt2_models.py", "download", config.get("model", "hymt2-1.8b")], env=env, cwd=root)
    # The live service selects Hy-MT2 1.8B independently of the offline GUI's
    # model selector. Prepare both when the user's offline choice differs.
    if config.get("live") and config.get("model", "hymt2-1.8b") != "hymt2-1.8b":
        run([python, root / "scripts/manage_hymt2_models.py", "download", "hymt2-1.8b"], env=env, cwd=root)
    if config.get("diarization") and (os.environ.get("HF_TOKEN") or (cache / "community-1/config.yaml").is_file()):
        progress("Preparing the optional speaker recognition model…")
        command = [python, root / "scripts/setup_diarization.py"]
        if os.environ.get("HF_TOKEN"):
            command.append("--download-model")
        run(command, env=env, cwd=root)
    if config.get("live"):
        progress("Preparing the Live Dubbing speech-boundary model…")
        run([python, root / "scripts/setup_live_segmentation.py", "--install-runtime", "--download"], env=env, cwd=root)
        progress("Checking the Live Dubbing speech recognition model…")
        run([python, "-c", "from faster_whisper import download_model; download_model('small')"], env=env)
    if config.get("lip_sync"):
        progress("Preparing the optional lip-sync models…")
        run([python, root / "scripts/setup_latentsync.py"], env=env, cwd=root)
    if config.get("verify_gpu", True):
        progress("Verifying that the Colab GPU and application runtime are available…")
        run([python, "-c", "import torch,Video_Translator; assert torch.cuda.is_available(), 'Selected cloud GPU is unavailable'; print('GPU:',torch.cuda.get_device_name(0))"], env=env, cwd=root)
    return {"project": str(root), "python": str(python), "cache": str(cache)}


if __name__ == "__main__":
    import base64
    config = json.loads(base64.b64decode(os.environ["VDT_BOOTSTRAP_CONFIG"]))
    print("VDT_CLOUD_READY=" + json.dumps(prepare(config)), flush=True)
