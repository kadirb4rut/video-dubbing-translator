"""Keep full Modal jobs outside the local AI environment."""
from pathlib import Path
import json
import subprocess

from .client_tools import client_bin, client_dir, tool_python


def run_modal_video(root: Path, video: Path, output: Path, options: dict) -> int:
    tool = client_bin("modal")
    if not tool:
        raise RuntimeError("Select Modal and click Connect & prepare GPU first.")
    if not (client_dir() / "modal-session.json").is_file():
        raise RuntimeError("Connect Modal in the web GUI before starting a job. It stays open until Disconnect GPU.")
    command = [tool_python("modal"), str(root / "scripts/run_modal_batch.py"), "--input", str(video), "--output", str(output), "--options-json", json.dumps(options)]
    return subprocess.run(command, cwd=root, check=False).returncode
