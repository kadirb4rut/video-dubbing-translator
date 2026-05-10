#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Virtual environment not found. Run the setup commands in README.md first."
  exit 1
fi

PYTHON=".venv/bin/python"

if [ ! -x "$PYTHON" ]; then
  echo "Python executable not found at $PYTHON. Recreate the virtual environment from README.md."
  exit 1
fi

echo "Starting Video Dubbing Translator..."
"$PYTHON" web_gui.py
