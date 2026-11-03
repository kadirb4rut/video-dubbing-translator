import argparse
import json
import os
import sys
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))

from translation.model_manager import (  # noqa: E402
    download_model,
    model_status,
    remove_model,
)
from translation.registry import MODEL_BY_KEY  # noqa: E402


def parse_args():
    parser = argparse.ArgumentParser(
        description="Inspect, download, or remove one local Hy-MT2 model."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    list_parser = subparsers.add_parser("list", help="Show local model status.")
    list_parser.add_argument("--json", action="store_true")
    download_parser = subparsers.add_parser(
        "download", help="Download exactly one selected model."
    )
    download_parser.add_argument("model", choices=MODEL_BY_KEY)
    remove_parser = subparsers.add_parser("remove", help="Remove one downloaded model.")
    remove_parser.add_argument("model", choices=MODEL_BY_KEY)
    return parser.parse_args()


def main():
    args = parse_args()
    if args.command == "list":
        statuses = model_status()
        if args.json:
            print(json.dumps(statuses, indent=2))
        else:
            for item in statuses:
                state = "Installed" if item["installed"] else "Not installed"
                print(
                    f"{item['display_name']}: {state} | approximately "
                    f"{item['approximate_download_gb']} GB | {item['path']}"
                )
                print(f"  {item['hardware_note']}")
        return 0
    if args.command == "download":
        print(
            f"Downloading only {MODEL_BY_KEY[args.model].display_name}. "
            "No other Hy-MT2 model will be downloaded.",
            flush=True,
        )
        path = download_model(args.model, token=os.environ.get("HF_TOKEN"))
        print(f"Model ready: {path}")
        return 0
    remove_model(args.model)
    print(f"Removed local model: {args.model}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
