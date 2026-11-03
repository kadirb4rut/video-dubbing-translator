"""GUI-owned cloud setup. Human account consent stays in the browser."""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compute_backends.client_tools import ensure_client, client_dir, tool_python
from compute_backends.settings import load_compute_settings, save_compute_settings
from compute_backends.source_bundle import bundle_source


def event(stage, message, **kwargs):
    print("VDT_SETUP_EVENT=" + json.dumps({"stage": stage, "message": message, **kwargs}), flush=True)


def run(command, *, capture=False, timeout=None, env=None, expected_marker=None):
    if expected_marker:
        child = subprocess.Popen([str(p) for p in command], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
        found = False
        for line in child.stdout:
            print(line, end="", flush=True)
            if line.startswith(expected_marker):
                found = True
        if child.wait() or not found:
            raise RuntimeError("Colab setup did not confirm completion; the CLI may return zero even when the kernel fails.")
        return ""
    result = subprocess.run([str(p) for p in command], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, env=env, timeout=timeout)
    return result.stdout or ""


def check_colab_usage(tool):
    """Read the CLI's reported balance; failure must not block connection."""
    try:
        result = subprocess.run([tool, "usage"], capture_output=True, text=True, timeout=20, check=True)
        match = re.search(r"Current balance:\s*([0-9]+(?:\.[0-9]+)?)\s+compute units", result.stdout, re.IGNORECASE)
        return float(match.group(1)) if match else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def report_colab_usage(tool, stage="connecting"):
    event(stage, "Checking your Colab Compute Unit balance…", cu_status="checking", cu_balance=None)
    balance = check_colab_usage(tool)
    if balance is None:
        event(stage, "Colab balance could not be checked; continuing with connection.", cu_status="unknown", cu_balance=None)
    else:
        status = "empty" if balance <= 0 else "low" if balance < 1 else "available"
        event(stage, f"Colab balance: {balance:g} CU. Checking G4 availability…", cu_status=status, cu_balance=balance)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("provider", choices=("colab", "modal"))
    parser.add_argument("--model", default="hymt2-1.8b")
    # Retain the old flag for callers from earlier releases; cache is always local to the VM.
    parser.add_argument("--temporary-cache", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--diarization", action="store_true")
    parser.add_argument("--lip-sync", action="store_true")
    args = parser.parse_args()
    if args.provider == "modal" and args.live:
        raise ValueError("Modal Live Dubbing is not available yet. Choose Colab CLI or this computer.")
    event("connecting", "Installing/checking cloud connection tools…")
    tool = ensure_client(args.provider)
    if args.provider == "colab":
        report_colab_usage(tool)
    archive, fingerprint = bundle_source(ROOT)
    if args.provider == "modal":
        event("connecting", "Connect your Modal account if prompted.")
        try:
            run([tool, "app", "list", "--json"], capture=True)
        except subprocess.CalledProcessError:
            run([tool, "token", "new"])
        event("preparing", "Building your private GPU app. It stays running until Disconnect GPU.")
        run([tool, "deploy", ROOT / "modal_batch_app.py"])
        apps = json.loads(run([tool, "app", "list", "--json"], capture=True))
        apps = [row for row in apps if row.get("description") == "video-dubbing-full-pipeline" and row.get("state") == "deployed"]
        if not apps:
            raise RuntimeError("Modal deployment was not found after preparation.")
        selected = max(apps, key=lambda row: row.get("created_at") or "")
        session = {"app_id": selected["app_id"], "model": args.model}
        path = client_dir() / "modal-session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session))
        run([tool_python("modal"), ROOT / "scripts/prepare_modal_gpu.py", "--app-id", selected["app_id"], "--model", args.model], env=os.environ.copy())
        return
    session = "video-dubbing-g4"
    event("connecting", "Connecting to the dedicated Colab G4 session…")
    # Existing dedicated session is reused; unrelated notebooks are never selected.
    listing = run([tool, "sessions"], capture=True)
    if not re.search(r"\[" + re.escape(session) + r"\]", listing):
        run([tool, "new", "--session", session, "--gpu", "G4"])
    preferences = load_compute_settings()
    preferences.update(colab_session=session, managed_colab="yes")
    save_compute_settings(preferences)
    event("preparing", "G4 connected. Preparing the application and model cache…", session=session)
    cache = "/content/.vdt-managed/cache"
    project = "/content/.vdt-managed/project"
    remote_archive = "/content/vdt-application.tar.gz"
    run([tool, "upload", archive, remote_archive, "--session", session])
    config = {"archive": remote_archive, "project": project, "cache": cache, "model": args.model,
              "live": args.live, "diarization": args.diarization, "lip_sync": args.lip_sync}
    encoded = base64.b64encode(json.dumps(config).encode()).decode()
    event("preparing", "Installing the isolated GPU environment and downloading only missing models…")
    command = [tool, "exec", "--session", session, "--timeout", "7200", "--env", "VDT_BOOTSTRAP_CONFIG=" + encoded]
    # HF_TOKEN is optional and is kept in memory, never in preferences or archives.
    if os.environ.get("HF_TOKEN"):
        command += ["--env", "HF_TOKEN=" + os.environ["HF_TOKEN"]]
    command += ["--file", ROOT / "scripts/cloud_bootstrap.py"]
    run(command, timeout=7500, expected_marker="VDT_CLOUD_READY=")
    preferences = load_compute_settings()
    preferences.update(colab_session=session, remote_project=project, managed_colab="yes")
    save_compute_settings(preferences)
    message = ("Colab G4 is ready for Live Dubbing. Open the Live Dubbing Browser."
               if args.live else "Colab G4 is ready. Select your video and press Start Dubbing.")
    event("ready", message, provider="colab", session=session, project=project)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        event("error", f"Cloud setup failed ({type(exc).__name__}). Check the connection log and retry.")
        raise SystemExit(1)
