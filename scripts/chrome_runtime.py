"""Locate Google Chrome and install it for the current user on macOS if approved."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import urllib.request


class InstallationDeclined(RuntimeError):
    pass


def find_chrome() -> Path | None:
    candidates = []
    if sys.platform == "darwin":
        candidates.extend(Path(p) / "Contents/MacOS/Google Chrome" for p in (
            "/Applications/Google Chrome.app",
            str(Path.home() / "Applications/Google Chrome.app"),
            str(Path.home() / "Library/Application Support/VideoDubbingTranslator/Google Chrome.app"),
        ))
    elif os.name == "nt":
        for key in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            if os.environ.get(key):
                candidates.append(Path(os.environ[key]) / "Google/Chrome/Application/chrome.exe")
    else:
        for name in ("google-chrome-stable", "google-chrome"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((p.resolve() for p in candidates if p.is_file()), None)


def ensure_chrome() -> Path:
    found = find_chrome()
    if found:
        return found
    print("Google Chrome is required. It was not found on this computer.", flush=True)
    if sys.platform != "darwin":
        raise RuntimeError("Install Google Chrome from https://www.google.com/chrome/ and run the launcher again.")
    try:
        answer = input("Download and install Google Chrome for this user? [y/N]: ").strip().lower()
    except EOFError:
        answer = ""
    if answer not in ("y", "yes"):
        raise InstallationDeclined("Chrome installation was declined; the GUI was not opened.")
    target = Path.home() / "Library/Application Support/VideoDubbingTranslator"
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vdt-chrome-install-") as tmp:
        dmg = Path(tmp) / "chrome.dmg"
        print("Downloading Google Chrome from Google; AI models are not downloaded.", flush=True)
        urllib.request.urlretrieve("https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg", dmg)
        mount = Path(tmp) / "mounted"
        mount.mkdir()
        subprocess.run(["hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", str(mount), str(dmg)], check=True, stdout=subprocess.DEVNULL)
        try:
            app = mount / "Google Chrome.app"
            subprocess.run(["codesign", "--verify", "--deep", "--strict", str(app)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            signature = subprocess.run(["codesign", "-dv", "--verbose=4", str(app)], check=True, capture_output=True, text=True)
            if "TeamIdentifier=EQHXZ8M8AV" not in signature.stderr:
                raise RuntimeError("The downloaded Chrome application is not signed by Google LLC.")
            subprocess.run(["ditto", str(app), str(target / "Google Chrome.app")], check=True)
        finally:
            subprocess.run(["hdiutil", "detach", str(mount)], check=False, stdout=subprocess.DEVNULL)
    found = find_chrome()
    if not found:
        raise RuntimeError("Chrome installation did not produce the expected executable.")
    return found
