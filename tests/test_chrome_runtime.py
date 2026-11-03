from pathlib import Path

import pytest

from scripts import chrome_runtime


def test_existing_chrome_is_reused_without_download(monkeypatch):
    chrome = Path("/tmp/Google Chrome")
    monkeypatch.setattr(chrome_runtime, "find_chrome", lambda: chrome)
    monkeypatch.setattr("builtins.input", lambda *_: pytest.fail("Unexpected installation prompt"))
    assert chrome_runtime.ensure_chrome() == chrome


def test_missing_chrome_is_not_downloaded_without_consent(monkeypatch):
    monkeypatch.setattr(chrome_runtime, "find_chrome", lambda: None)
    monkeypatch.setattr(chrome_runtime.sys, "platform", "darwin")
    monkeypatch.setattr("builtins.input", lambda *_: "n")
    monkeypatch.setattr(chrome_runtime.urllib.request, "urlretrieve", lambda *_: pytest.fail("Unexpected download"))
    with pytest.raises(chrome_runtime.InstallationDeclined):
        chrome_runtime.ensure_chrome()
