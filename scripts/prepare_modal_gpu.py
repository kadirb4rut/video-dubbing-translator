"""Prepare the authenticated deployed app, without owning its lifetime."""
import argparse
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compute_backends.client_tools import client_dir


def main():
    import modal
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-id", required=True)
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    function = modal.Function.from_name("video-dubbing-full-pipeline", "run_video")
    function.hydrate()
    session_path = client_dir() / "modal-session.json"
    session = {"app_id": args.app_id, "function_id": function.object_id, "model": args.model, "ready": False}
    temporary = session_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(session))
    temporary.replace(session_path)
    prepare = modal.Function.from_name("video-dubbing-full-pipeline", "prepare_models")
    token = os.environ.get("HF_TOKEN", "")
    prepare.remote(args.model, token, bool(token))
    print('VDT_SETUP_EVENT='+json.dumps({"stage":"preparing", "message":"Models cached. Waiting for GPU allocation and checking the Modal runtime…"}), flush=True)
    function.remote("_prepare", "", {"translation_model": args.model})
    session["ready"] = True
    temporary.write_text(json.dumps(session))
    temporary.replace(session_path)
    print('VDT_SETUP_EVENT='+json.dumps({"stage":"ready", "provider":"modal", "model":args.model,
          "message":"Modal GPU is ready. It remains connected until Disconnect GPU."}), flush=True)


if __name__ == "__main__":
    main()
