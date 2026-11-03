#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -x ".venv/bin/python" ]; then
  echo "Project Python environment not found. Run ./install.sh first."
  exit 1
fi

exec .venv/bin/python scripts/open_live_dubbing_browser.py "$@"
