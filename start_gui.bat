@echo off
cd /d "%~dp0"

if not exist ".venv\Scripts\activate.bat" (
  echo Virtual environment not found. Run the setup commands in README.md first.
  pause
  exit /b 1
)

echo Starting Video Dubbing Translator...
call ".venv\Scripts\activate.bat"
python web_gui.py
