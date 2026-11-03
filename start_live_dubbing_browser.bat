@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  echo Project Python environment not found. Run install.ps1 first.
  exit /b 1
)
".venv\Scripts\python.exe" "scripts\open_live_dubbing_browser.py" %*
