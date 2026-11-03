"""Full offline pipeline on a connected private Modal app with a reusable Volume."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import modal


ROOT = Path(__file__).resolve().parent
VOLUME_NAME = "video-dubbing-full-pipeline"
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
app = modal.App("video-dubbing-full-pipeline")
if modal.is_local():
    from compute_backends.source_bundle import bundle_source
    BUNDLE, _ = bundle_source(ROOT)
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .apt_install("ffmpeg", "libgomp1", "git")
        .pip_install_from_requirements(str(ROOT / "requirements-cloud.txt"))
        .add_local_file(str(ROOT / "requirements-hymt2.txt"), "/opt/hymt2-requirements.txt", copy=True)
        .run_commands("python -m venv --system-site-packages /opt/vdt-main",
                      "python -m venv --system-site-packages /opt/vdt-hymt2",
                      "/opt/vdt-hymt2/bin/pip install -r /opt/hymt2-requirements.txt")
        .add_local_file(str(BUNDLE), "/opt/vdt-source.tar.gz", copy=True)
    )
else:
    image = modal.Image.debian_slim(python_version="3.12")


def prepare_remote(model, *, gpu, hf_token="", diarization=False, lip_sync=False):
    import tarfile
    root = Path("/tmp/vdt-project")
    root.mkdir(exist_ok=True)
    # Bundle is generated from the explicit code allowlist, then validated again.
    with tarfile.open("/opt/vdt-source.tar.gz") as archive:
        for member in archive.getmembers():
            if not (root / member.name).resolve().is_relative_to(root) or not (member.isfile() or member.isdir()):
                raise ValueError("Unsafe source archive")
        archive.extractall(root, filter="data")
    sys.path.insert(0, str(root))
    from scripts.cloud_bootstrap import prepare
    (root / ".venv").symlink_to("/opt/vdt-main", target_is_directory=True) if not (root / ".venv").exists() else None
    (root / "third_party/hymt2").mkdir(parents=True, exist_ok=True)
    link = root / "third_party/hymt2/.venv"
    if not link.exists():
        link.symlink_to("/opt/vdt-hymt2", target_is_directory=True)
    (root / ".cloud-runtime-ready").write_text(hashlib.sha256((root / "requirements-cloud.txt").read_bytes()).hexdigest())
    if hf_token:
        os.environ["HF_TOKEN"] = hf_token
    try:
        return prepare({"project": str(root), "cache": "/storage/models", "archive": "/opt/vdt-source.tar.gz", "model": model,
                        "verify_gpu": gpu, "diarization": diarization, "lip_sync": lip_sync})
    finally:
        os.environ.pop("HF_TOKEN", None)


@app.function(image=image, volumes={"/storage": volume}, timeout=7200, max_containers=1, scaledown_window=2)
def prepare_models(model: str, hf_token: str = "", diarization: bool = False):
    volume.reload()
    try:
        return prepare_remote(model, gpu=False, hf_token=hf_token, diarization=diarization)
    finally:
        volume.commit()


@app.function(image=image, gpu=["A10G", "L4"], volumes={"/storage": volume}, timeout=86400,
              min_containers=1, max_containers=1, scaledown_window=60)
def run_video(job: str, filename: str, options: dict, hf_token: str = ""):
    if job == "_prepare":
        volume.reload()
        try:
            prepared = prepare_remote(options.get("translation_model", "hymt2-1.8b"), gpu=True)
            return {"ready": True, "project": prepared["project"]}
        finally:
            volume.commit()
    if len(job) != 32 or any(c not in "0123456789abcdef" for c in job):
        raise ValueError("Invalid job identifier")
    filename = Path(filename).name
    volume.reload()
    job_dir = Path("/storage/jobs") / job
    try:
        prepared = prepare_remote(options.get("translation_model", "hymt2-1.8b"), gpu=True, hf_token=hf_token,
                                  diarization=options.get("diarization", True), lip_sync=options.get("lip_sync", False))
        root = Path(prepared["project"])
        from scripts.colab_backend_remote import _runtime_environment
        from compute_backends.media import cloud_input
        working_video = cloud_input(job_dir / filename, root / "temp" / job)
        args = [prepared["python"], str(root / "scripts/run_pipeline.py"), str(working_video),
                "--source-language", options.get("source_language", "auto"), "--target-language", options["target_language"],
                "--translation-model", options.get("translation_model", "hymt2-1.8b"), "--translation-device", "auto"]
        if not options.get("diarization", True):
            args.append("--no-diarization")
        for field in ("num_speakers", "min_speakers", "max_speakers"):
            if options.get(field) is not None:
                args += ["--" + field.replace("_", "-"), str(options[field])]
        if options.get("glossary"):
            glossary = job_dir / "glossary.json"
            glossary.write_text(options["glossary"])
            args += ["--glossary", str(glossary)]
        if options.get("lip_sync"):
            args.append("--lip-sync")
        subprocess.run(args, cwd=root, env=_runtime_environment(root), check=True)
        source = root / "vocal-remover/final_video/final" / working_video.name
        if options.get("lip_sync"):
            source = source.with_name(source.stem + "_lipsync" + source.suffix)
        output = job_dir / ("translated" + source.suffix)
        shutil.copyfile(source, output)
        return str(output.relative_to("/storage"))
    finally:
        volume.commit()
