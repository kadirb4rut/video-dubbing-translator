"""Run a selected offline dubbing job on an existing Colab CLI session."""

from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compute_backends.colab_batch import ColabBatchRunner


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", required=True)
    parser.add_argument("--remote-project", default="/content/video-dubbing-translator-main")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--options-file")
    parser.add_argument("--options-json")
    args = parser.parse_args(argv)
    if bool(args.options_file) == bool(args.options_json):
        parser.error("Provide exactly one of --options-file or --options-json.")
    return args


def main(argv=None):
    args = parse_args(argv)
    options = (
        json.loads(Path(args.options_file).read_text(encoding="utf-8"))
        if args.options_file
        else json.loads(args.options_json)
    )
    runner = ColabBatchRunner(args.session, remote_project=args.remote_project, project_root=ROOT)
    signal.signal(signal.SIGTERM, lambda *_: (runner.cancel(), sys.exit(143)))
    signal.signal(signal.SIGINT, lambda *_: (runner.cancel(), sys.exit(130)))
    runner.run(args.input, args.output, options)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
