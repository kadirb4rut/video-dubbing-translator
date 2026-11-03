#!/usr/bin/env python3
"""Locate or install an unbranded Chromium build for the application launchers."""
from __future__ import annotations

import os
from pathlib import Path
import platform
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile


SNAPSHOT_URL = "https://storage.googleapis.com/chromium-browser-snapshots"
APP_NAME = "VideoDubbingTranslator"
MAX_DOWNLOAD_BYTES = 1_500_000_000


class InstallationDeclined(RuntimeError):
    pass


def _data_directory() -> Path:
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    elif os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / APP_NAME / "Chromium"


def _platform_artifact() -> tuple[str, str, tuple[str, ...]]:
    system = platform.system()
    machine = platform.machine().lower()
    if system == "Darwin" and machine in ("arm64", "aarch64"):
        return "Mac_Arm", "chrome-mac.zip", (
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        )
    if system == "Darwin" and machine in ("x86_64", "amd64"):
        return "Mac", "chrome-mac.zip", (
            "chrome-mac/Chromium.app/Contents/MacOS/Chromium",
        )
    if system == "Linux" and machine in ("x86_64", "amd64"):
        return "Linux_x64", "chrome-linux.zip", ("chrome-linux/chrome",)
    if system == "Windows" and machine in ("x86_64", "amd64"):
        return "Win_x64", "chrome-win.zip", ("chrome-win/chrome.exe",)
    raise RuntimeError(
        f"Automatic Chromium setup is not configured for {system} {machine}. "
        "Install an unbranded Chromium build and make its executable available as `chromium`."
    )


def _system_chromium() -> Path | None:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            Path(app) / "Contents" / "MacOS" / "Chromium"
            for app in (
                "/Applications/Chromium.app",
                str(Path.home() / "Applications/Chromium.app"),
            )
        )
    names = ("chromium.exe",) if os.name == "nt" else ("chromium", "chromium-browser")
    for name in names:
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    return next((path.resolve() for path in candidates if path.is_file()), None)


def _managed_executable(root: Path, platform_key: str, build: str) -> Path | None:
    _, _, candidates = _platform_artifact()
    base = root / platform_key / build
    return next((base / rel for rel in candidates if (base / rel).is_file()), None)


def _existing_managed(root: Path) -> Path | None:
    try:
        platform_key, _, _ = _platform_artifact()
    except RuntimeError:
        return None
    platform_root = root / platform_key
    if not platform_root.is_dir():
        return None
    builds = sorted(
        (entry for entry in platform_root.iterdir() if entry.is_dir() and entry.name.isdigit()),
        key=lambda entry: int(entry.name),
        reverse=True,
    )
    for build in builds:
        executable = _managed_executable(root, platform_key, build.name)
        if executable:
            _repair_executable_permissions(executable)
            return executable.resolve()
    return None


def _repair_executable_permissions(executable: Path) -> None:
    """Restore executable bits stripped by zipfile on Chromium helper binaries."""
    if sys.platform == "darwin":
        bundle = executable.parents[2]
        candidates = [
            *bundle.rglob("MacOS/*"),
            *bundle.rglob("chrome_crashpad_handler"),
        ]
        for path in candidates:
            if path.is_file():
                path.chmod(path.stat().st_mode | 0o111)


def find_chromium() -> Path | None:
    return _system_chromium() or _existing_managed(_data_directory())


def _latest_build(platform_key: str) -> str:
    request = urllib.request.Request(
        f"{SNAPSHOT_URL}/{platform_key}/LAST_CHANGE",
        headers={"User-Agent": "VideoDubbingTranslator/ChromiumInstaller"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        build = response.read(64).decode("ascii", errors="strict").strip()
    if not build.isdigit():
        raise RuntimeError("The official Chromium snapshot service returned an invalid build number.")
    return build


def _download(url: str, destination: Path) -> None:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "VideoDubbingTranslator/ChromiumInstaller"},
    )
    with urllib.request.urlopen(request, timeout=60) as response, destination.open("wb") as output:
        length = int(response.headers.get("Content-Length", "0") or 0)
        if length > MAX_DOWNLOAD_BYTES:
            raise RuntimeError("The Chromium download is larger than the permitted installer limit.")
        total = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_DOWNLOAD_BYTES:
                raise RuntimeError("The Chromium download exceeded the permitted installer limit.")
            output.write(chunk)
            if length:
                print(f"Downloading Chromium: {min(100, total * 100 // length)}%", flush=True)
    if destination.stat().st_size == 0:
        raise RuntimeError("The Chromium download was empty.")


def _extract_safely(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for member in archive.infolist():
            member_path = Path(member.filename)
            if member_path.is_absolute() or ".." in member_path.parts:
                raise RuntimeError("The Chromium archive contains an unsafe file path.")
            target = (destination / member_path).resolve()
            try:
                target.relative_to(destination.resolve())
            except ValueError as exc:
                raise RuntimeError("The Chromium archive contains an unsafe file path.") from exc
        archive.extractall(destination)
        if os.name != "nt":
            for member in archive.infolist():
                target = destination / Path(member.filename)
                mode = (member.external_attr >> 16) & 0o777
                if mode and target.exists():
                    target.chmod(mode)


def install_chromium() -> Path:
    platform_key, archive_name, _ = _platform_artifact()
    build = _latest_build(platform_key)
    root = _data_directory()
    existing = _managed_executable(root, platform_key, build)
    if existing:
        return existing.resolve()

    platform_root = root / platform_key
    platform_root.mkdir(parents=True, exist_ok=True)
    staging_parent = Path(tempfile.mkdtemp(prefix="chromium-install-", dir=platform_root))
    final_dir = platform_root / build
    try:
        archive_path = staging_parent / archive_name
        extract_dir = staging_parent / "unpacked"
        extract_dir.mkdir()
        download_url = f"{SNAPSHOT_URL}/{platform_key}/{build}/{archive_name}"
        print(f"Installing official Chromium snapshot {build} for {platform_key}.", flush=True)
        _download(download_url, archive_path)
        _extract_safely(archive_path, extract_dir)
        executable = next(
            (extract_dir / rel for rel in _platform_artifact()[2] if (extract_dir / rel).is_file()),
            None,
        )
        if executable is None:
            raise RuntimeError("The official Chromium archive did not contain its browser executable.")
        final_dir.parent.mkdir(parents=True, exist_ok=True)
        os.replace(extract_dir, final_dir)
        installed = next(
            (final_dir / rel for rel in _platform_artifact()[2] if (final_dir / rel).is_file()),
            None,
        )
        if installed is None:
            raise RuntimeError("Chromium installation did not produce its browser executable.")
        if os.name != "nt":
            installed.chmod(installed.stat().st_mode | 0o111)
            _repair_executable_permissions(installed)
        return installed.resolve()
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as exc:
        raise RuntimeError(f"Chromium could not be downloaded or installed: {exc}") from exc
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)


def ensure_chromium(*, ask: bool = True) -> Path:
    available = find_chromium()
    if available:
        return available
    _platform_artifact()
    if ask:
        print(
            "This project requires Chromium. It was not found on this computer.\n"
            "Installing downloads an official Chromium build to the project's separate app-data folder.\n"
            "It does not install Google Chrome, change your Chrome profile, or download AI models.",
            flush=True,
        )
        try:
            answer = input("Download and install Chromium now? [y/N]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer not in ("y", "yes"):
            raise InstallationDeclined("Chromium installation was declined; the GUI was not opened.")
    return install_chromium()
