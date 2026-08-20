[CmdletBinding()]
param(
    [switch]$SkipModels
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Get-Command py -ErrorAction SilentlyContinue
if ($null -eq $python) {
    throw "Python Launcher (py) was not found. Install Python 3.10 and rerun this script."
}

$version = (& py -3.10 -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')").Trim()
if ($version -ne "3.10") {
    throw "Python 3.10 is required. The Python Launcher could not select it."
}

foreach ($command in @("ffmpeg", "ffprobe")) {
    if ($null -eq (Get-Command $command -ErrorAction SilentlyContinue)) {
        throw "$command was not found on PATH. Install FFmpeg with your normal system installer, then rerun this script."
    }
}

if (-not (Test-Path ".venv\Scripts\python.exe")) {
    Write-Host "Creating .venv with Python 3.10"
    & py -3.10 -m venv .venv
} else {
    Write-Host "Reusing existing .venv"
}

$venvPython = ".venv\Scripts\python.exe"
Write-Host "Installing pinned Python dependencies"
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

if ($SkipModels) {
    Write-Host "Skipping model downloads (-SkipModels). Run .\install.ps1 again before dubbing."
    exit 0
}

Write-Host "Downloading and verifying required models"
& $venvPython scripts\setup_models.py
Write-Host "Running setup preflight"
& $venvPython scripts\check_setup.py
Write-Host "Installation complete. Start the app with .\start_gui.bat"
