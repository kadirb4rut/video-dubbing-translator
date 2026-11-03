from pathlib import Path
import subprocess

import pytest

from scripts import open_live_dubbing_browser
from scripts.open_live_dubbing_browser import _ProfileLock, chrome_command


def test_chrome_command_uses_separate_profile_and_local_debugging():
    command = chrome_command(
        "/tmp/google-chrome",
        "/tmp/app-profile",
        "/tmp/live-dubbing-extension",
        "http://127.0.0.1:8766/", debug_port=43210,
    )

    assert command[0] == str(Path("/tmp/google-chrome").resolve())
    assert f"--user-data-dir={Path('/tmp/app-profile').resolve()}" in command
    assert "--remote-debugging-address=127.0.0.1" in command
    assert "--remote-debugging-port=43210" in command
    assert "http://127.0.0.1:8766/" in command
    assert not any(
        argument == "--enable-automation"
        or argument == "--headless"
        for argument in command
    )


def test_profile_lock_rejects_a_second_launcher(tmp_path):
    profile = tmp_path / "profile"
    profile.mkdir()

    with _ProfileLock(profile):
        with pytest.raises(RuntimeError, match="already in use"):
            with _ProfileLock(profile):
                pass


def test_colab_connection_loss_closes_live_browser_without_releasing_runtime(tmp_path, monkeypatch):
    profile = tmp_path / "profile"
    monkeypatch.setattr(open_live_dubbing_browser, "_profile_directory", lambda: profile)
    monkeypatch.setattr(open_live_dubbing_browser, "_status", lambda *_args: None)
    monkeypatch.setattr(open_live_dubbing_browser.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(open_live_dubbing_browser, "_load_extension_via_devtools", lambda *_args: "extension-id")

    class BrowserProcess:
        return_code = None
        terminated = False

        def poll(self):
            return self.return_code

        def terminate(self):
            self.terminated = True
            self.return_code = -15

        def wait(self, timeout=None):
            if self.return_code is not None:
                return self.return_code
            raise subprocess.TimeoutExpired("chrome", timeout)

    browser = BrowserProcess()
    monkeypatch.setattr(open_live_dubbing_browser.subprocess, "Popen", lambda *_args, **_kwargs: browser)

    class LostConnection:
        @staticmethod
        def poll():
            return 1

    with pytest.raises(RuntimeError, match="connection was lost.*runtime remains allocated"):
        open_live_dubbing_browser.launch_browser(
            "/tmp/google-chrome", connection_process=LostConnection(),
        )
    assert browser.terminated


def test_browser_crash_is_reported_instead_of_a_normal_close(tmp_path, monkeypatch):
    monkeypatch.setattr(open_live_dubbing_browser, '_profile_directory', lambda: tmp_path / 'profile')
    monkeypatch.setattr(open_live_dubbing_browser, '_status', lambda *_args: None)
    monkeypatch.setattr(open_live_dubbing_browser, '_load_extension_via_devtools', lambda *_args: 'extension-id')
    class CrashedBrowser:
        def poll(self):
            return -11
    monkeypatch.setattr(open_live_dubbing_browser.subprocess, 'Popen', lambda *_args, **_kwargs: CrashedBrowser())
    with pytest.raises(RuntimeError, match='exited unexpectedly.*Colab runtime remains allocated'):
        open_live_dubbing_browser.launch_browser('/tmp/google-chrome')
