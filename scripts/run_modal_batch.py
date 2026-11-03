"""Submit one job to the Modal session held open by the GUI."""
import argparse
import json
from pathlib import Path
import signal
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compute_backends.client_tools import client_dir


def main():
    import modal
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(143))
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--options-json", required=True)
    args = parser.parse_args()
    session = json.loads((client_dir() / "modal-session.json").read_text())
    function = modal.Function.from_id(session["function_id"])
    volume = modal.Volume.from_name("video-dubbing-full-pipeline")
    video = Path(args.input).expanduser().resolve()
    destination = Path(args.output).expanduser().resolve()
    job = uuid.uuid4().hex
    call = None
    finished = False
    try:
        with volume.batch_upload() as upload:
            upload.put_file(str(video), f"/jobs/{job}/{video.name}")
        print("Video uploaded. Running on the connected Modal GPU…", flush=True)
        call = function.spawn(job, video.name, json.loads(args.options_json))
        print("Modal function call: " + call.object_id, flush=True)
        while True:
            try:
                result = call.get(timeout=15)
                break
            except modal.exception.FunctionTimeoutError:
                raise
            except TimeoutError:
                print("The connected Modal GPU is still processing this video…", flush=True)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        try:
            with partial.open("wb") as stream:
                for chunk in volume.read_file(result):
                    stream.write(chunk)
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)
        finished = True
        print("Dubbed video saved: " + str(destination), flush=True)
    finally:
        if call and not finished:
            # Cancel this input, leaving the user's connected app/GPU available.
            call.cancel()
        if finished:
            try:
                volume.remove_file(f"/jobs/{job}", recursive=True)
            except Exception:
                print("Video saved; temporary job files could not be removed.", flush=True)
        print("Modal remains connected. Use Disconnect GPU when finished.", flush=True)


if __name__ == "__main__":
    main()
