#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3.10 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3.10)"
  elif command -v python3 >/dev/null 2>&1 && [[ "$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" == "3.10" ]]; then
    PYTHON_BIN="$(command -v python3)"
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.10 is required. Install it or set PYTHON_BIN=/path/to/python3.10." >&2
  exit 1
fi

if [[ "$($PYTHON_BIN -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')" != "3.10" ]]; then
  echo "The selected interpreter must be Python 3.10: $PYTHON_BIN" >&2
  exit 1
fi

missing=()
for command in ffmpeg ffprobe; do
  if ! command -v "$command" >/dev/null 2>&1; then
    missing+=("$command")
  fi
done
if (( ${#missing[@]} > 0 )); then
  echo "Missing required command(s): ${missing[*]}" >&2
  echo "Install FFmpeg with your normal system package manager, then rerun this script." >&2
  echo "This installer does not use sudo or change system packages automatically." >&2
  exit 1
fi

if [[ ! -x ".venv/bin/python" ]]; then
  echo "Creating .venv with $PYTHON_BIN"
  "$PYTHON_BIN" -m venv .venv
else
  echo "Reusing existing .venv"
fi

PYTHON=".venv/bin/python"
echo "Installing pinned Python dependencies"
"$PYTHON" -m pip install --upgrade pip
"$PYTHON" -m pip install -r requirements.txt

if [[ "${SKIP_MODELS:-0}" == "1" ]]; then
  echo "Skipping model downloads (SKIP_MODELS=1). Run ./install.sh again before dubbing."
  exit 0
fi

echo "Downloading and verifying required models"
"$PYTHON" scripts/setup_models.py
echo "Running setup preflight"
"$PYTHON" scripts/check_setup.py
echo "Installation complete. Start the app with ./start_gui.sh"
