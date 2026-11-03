#!/usr/bin/env python3
"""Run offline dubbing with a selected local, Colab, or Modal compute path."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute_backends.colab_batch import ColabBatchRunner
from compute_backends.settings import load_compute_settings


def parse_args(argv=None):
    preferences = load_compute_settings()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", help="Video file to dub")
    parser.add_argument(
        "--compute", choices=("local", "colab", "modal", "modal-tts"),
        default=preferences["batch_provider"],
    )
    parser.add_argument(
        "--session", default=preferences["colab_session"],
        help="Existing authenticated Colab CLI session",
    )
    parser.add_argument("--remote-project", default=preferences["remote_project"])
    parser.add_argument("--output", help="Local destination for the returned Colab output")
    parser.add_argument("--source-language", default="auto")
    parser.add_argument("--target-language", required=True)
    parser.add_argument("--translation-model", default="hymt2-1.8b")
    parser.add_argument("--translation-device", default="auto")
    parser.add_argument("--no-diarization", action="store_false", dest="diarization")
    parser.set_defaults(diarization=True)
    parser.add_argument("--num-speakers", type=int)
    parser.add_argument("--min-speakers", type=int)
    parser.add_argument("--max-speakers", type=int)
    parser.add_argument("--glossary", help="Optional JSON glossary file")
    parser.add_argument("--lip-sync", action="store_true")
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    video = Path(args.input).expanduser().resolve()
    if not video.is_file():
        raise FileNotFoundError(f"Input video was not found: {video}")
    glossary = ""
    if args.glossary:
        glossary_value = json.loads(Path(args.glossary).expanduser().read_text(encoding="utf-8"))
        if not isinstance(glossary_value, dict):
            raise ValueError("Glossary JSON must contain an object of source-to-target terms.")
        glossary = json.dumps(glossary_value, ensure_ascii=False)

    options = {
        "source_language": args.source_language,
        "target_language": args.target_language,
        "translation_model": args.translation_model,
        "translation_device": args.translation_device,
        "diarization": args.diarization,
        "num_speakers": args.num_speakers,
        "min_speakers": args.min_speakers,
        "max_speakers": args.max_speakers,
        "glossary": glossary,
        "lip_sync": args.lip_sync,
    }
    if args.compute in ("colab", "modal"):
        if args.compute == "colab" and not args.session:
            raise ValueError("Choose an existing authenticated Colab CLI session with --session.")
        destination = Path(args.output).expanduser().resolve() if args.output else (
            ROOT / "vocal-remover" / "final_video" / "final" / video.with_suffix(".mp4").name
        )
        if args.compute == "modal":
            from compute_backends.modal_batch import run_modal_video
            return run_modal_video(ROOT, video, destination, options)
        runner = ColabBatchRunner(
            args.session, remote_project=args.remote_project, project_root=ROOT,
        )
        runner.run(video, destination, options)
        return 0

    command = [
        sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), str(video),
        "--source-language", args.source_language,
        "--target-language", args.target_language,
        "--translation-model", args.translation_model,
        "--translation-device", args.translation_device,
        "--tts-backend", "modal" if args.compute == "modal-tts" else "native",
    ]
    if not args.diarization:
        command.append("--no-diarization")
    for flag, value in (("--num-speakers", args.num_speakers),
                        ("--min-speakers", args.min_speakers),
                        ("--max-speakers", args.max_speakers),
                        ("--glossary", args.glossary)):
        if value is not None:
            command.extend([flag, str(value)])
    if args.lip_sync:
        command.append("--lip-sync")
    return subprocess.run(command, cwd=ROOT, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
