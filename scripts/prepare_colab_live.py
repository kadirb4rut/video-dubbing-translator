"""Prepare Live-specific files on an already-connected Colab CLI session."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compute_backends.client_tools import client_bin
from compute_backends.colab_tunnel import validate_session_name
from scripts.prepare_compute import event, report_colab_usage


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    session = validate_session_name(args.session)
    project = args.project
    if not re.fullmatch(r"/content/[A-Za-z0-9_./-]+", project) or ".." in Path(project).parts:
        raise ValueError("The saved Colab project path is invalid.")
    tool = client_bin("colab")
    if not tool:
        raise RuntimeError("Install and sign in to the Colab CLI, then retry Live preparation.")
    report_colab_usage(tool, stage="preparing")
    event("preparing", "Adding Live Dubbing models to the connected Colab runtime; its existing cache is reused.")
    helper = ROOT / "scripts" / "colab_live_remote.py"
    command = [
        tool, "exec", "--session", session, "--timeout", "7200",
        "--env", "VDT_PREPARE_LIVE=1",
        "--env", f"VDT_LIVE_PROJECT_ROOT={project}",
        "--file", str(helper),
    ]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, bufsize=1)
    output_lines = []
    try:
        for line in process.stdout:
            print(line, end="", flush=True)
            output_lines.append(line.rstrip())
        return_code = process.wait(timeout=7500)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("Colab Live preparation timed out. The G4 session remains connected.")
    output = "\n".join(output_lines)
    for line in output.splitlines():
        if line.startswith("VDT_REMOTE_LIVE_PREPARED="):
            try:
                prepared = json.loads(line.split("=", 1)[1])
            except ValueError as exc:
                raise RuntimeError("Colab returned an invalid Live readiness response.") from exc
            event("ready", "Colab G4 is ready for Live Dubbing. The existing VM remains connected until Disconnect GPU.",
                  provider="colab", live=True, session=session, project=project,
                  model=args.model, app=prepared.get("app"))
            return 0
    detail = output.strip()[-1800:] or f"Colab CLI exited with code {return_code}."
    raise RuntimeError("Could not prepare Live Dubbing on the selected Colab runtime. " + detail)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        event("error", f"Colab Live preparation failed ({type(exc).__name__}). The GPU session was left running.")
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
