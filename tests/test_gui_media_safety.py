import io
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import socket
import sys
import threading
import urllib.error
import urllib.request

import pytest


def test_atomic_upload_sanitizes_filename_and_publishes_complete_file(tmp_path):
    import web_gui

    target = web_gui.save_uploaded_file(io.BytesIO(b"complete video"), r"..\private\clip.mp4", tmp_path)
    assert target == tmp_path / "clip.mp4"
    assert target.read_bytes() == b"complete video"
    assert list(tmp_path.iterdir()) == [target]


def test_incomplete_upload_never_replaces_existing_file(tmp_path):
    import web_gui

    target = tmp_path / "clip.mp4"
    target.write_bytes(b"old complete video")
    with pytest.raises(ValueError, match="complete video"):
        web_gui.save_uploaded_file(io.BytesIO(b"partial"), "clip.mp4", tmp_path, expected_size=30)
    assert target.read_bytes() == b"old complete video"
    assert sorted(item.name for item in tmp_path.iterdir()) == ["clip.mp4"]


def test_headless_output_folder_returns_actionable_failure(tmp_path):
    import web_gui

    def unavailable(*_args, **_kwargs):
        raise FileNotFoundError("xdg-open")

    result = web_gui.open_output_folder(tmp_path / "output", platform_name="linux", opener_run=unavailable)
    assert result["ok"] is False
    assert result["code"] == "output_opener_unavailable"
    assert "file manager" in result["message"]


def test_headless_output_http_response_reaches_the_browser(gui, monkeypatch):
    module, port = gui
    failure = {
        "ok": False,
        "code": "output_opener_unavailable",
        "message": "The output was saved; open it from your file manager.",
    }
    monkeypatch.setattr(module, "open_output_folder", lambda _directory: failure)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/open-output",
        data=b"{}",
        headers={
            "X-Compute-Token": module.CLOUD_REQUEST_TOKEN,
            "Origin": f"http://127.0.0.1:{port}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 503
    assert json.loads(error.value.read()) == failure
    assert "data.error || data.message || 'Request failed'" in module.page()


@pytest.fixture
def gui(monkeypatch, tmp_path):
    import web_gui

    monkeypatch.setattr(web_gui, "OUTPUT_DIR", tmp_path / "outputs")
    with web_gui.state_lock:
        web_gui.state.update(output_video="", uploaded_video="", process=None, status="Ready")
    web_gui.revoke_output_access()
    server = web_gui.ReusableThreadingHTTPServer(("127.0.0.1", 0), web_gui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield web_gui, server.server_port
    server.shutdown()
    server.server_close()


def test_download_route_rejects_symlink_escape(gui, tmp_path):
    module, port = gui
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    module.OUTPUT_DIR.mkdir()
    link = module.OUTPUT_DIR / "escape.mp4"
    link.symlink_to(outside)
    module.state["output_video"] = str(link)
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/media/output-video?download=1")
    assert error.value.code == 403


def _authorize_output(module, port):
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/output-access",
        method="POST",
        headers={
            "X-Compute-Token": module.CLOUD_REQUEST_TOKEN,
            "Origin": f"http://127.0.0.1:{port}",
        },
    )
    with urllib.request.urlopen(request) as response:
        payload = json.load(response)
        cookie = SimpleCookie()
        cookie.load(response.headers["Set-Cookie"])
        return payload, cookie[module.OUTPUT_ACCESS_COOKIE].value


def test_output_video_requires_explicit_gui_authorization(gui, tmp_path):
    module, port = gui
    output = module.OUTPUT_DIR / "dubbed.mp4"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"private video")
    module.state["output_video"] = str(output)

    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/media/output-video")
    assert error.value.code == 403
    assert "Authorize" in error.value.read().decode()


def test_authorized_output_supports_range_playback_and_safe_download(gui, tmp_path):
    module, port = gui
    output = module.OUTPUT_DIR / "dubbed.mp4"
    output.parent.mkdir(parents=True)
    data = (b"0123456789" * 209716)[:2_097_152]
    output.write_bytes(data)
    module.state["output_video"] = str(output)

    payload, cookie_value = _authorize_output(module, port)
    assert payload["url"] == "/media/output-video"
    assert payload["filename"] == "dubbed.mp4"
    headers = {
        "Cookie": f"{module.OUTPUT_ACCESS_COOKIE}={cookie_value}",
        "Range": "bytes=2-5",
    }
    with urllib.request.urlopen(
        urllib.request.Request(f"http://127.0.0.1:{port}/media/output-video", headers=headers)
    ) as response:
        assert response.status == 206
        assert response.headers["Content-Range"] == f"bytes 2-5/{len(data)}"
        assert response.read() == b"2345"
        assert response.headers["Cache-Control"] == "private, no-store"
        assert response.headers["X-Content-Type-Options"] == "nosniff"

    with urllib.request.urlopen(
        urllib.request.Request(
            f"http://127.0.0.1:{port}/media/output-video?download=1",
            headers={"Cookie": f"{module.OUTPUT_ACCESS_COOKIE}={cookie_value}"},
        )
    ) as response:
        assert response.read() == data
        assert "attachment; filename=\"dubbed_video.mp4\"" in response.headers["Content-Disposition"]


def test_output_authorization_refuses_symlink_escape(gui, tmp_path):
    module, port = gui
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(b"private")
    module.OUTPUT_DIR.mkdir()
    link = module.OUTPUT_DIR / "escape.mp4"
    link.symlink_to(outside)
    module.state["output_video"] = str(link)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/output-access",
        method="POST",
        headers={
            "X-Compute-Token": module.CLOUD_REQUEST_TOKEN,
            "Origin": f"http://127.0.0.1:{port}",
        },
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


def test_output_authorization_rejects_path_traversal(gui, tmp_path):
    module, port = gui
    module.OUTPUT_DIR.mkdir(parents=True)
    outside = tmp_path / "private.mp4"
    outside.write_bytes(b"private")
    module.state["output_video"] = str(module.OUTPUT_DIR / ".." / outside.name)
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/output-access",
        data=b"{}",
        headers={
            "X-Compute-Token": module.CLOUD_REQUEST_TOKEN,
            "Origin": f"http://127.0.0.1:{port}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403


def test_stale_compute_token_has_machine_code_and_one_time_page_refresh(gui):
    module, port = gui
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/cancel",
        data=b"{}",
        headers={
            "X-Compute-Token": "expired-compute-token",
            "Origin": f"http://127.0.0.1:{port}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 403
    assert json.loads(error.value.read()) == {
        "error": "Refresh this local GUI before continuing.",
        "code": "stale_ui_session",
    }

    page = module.page()
    assert "data.code === 'stale_ui_session'" in page
    assert "sessionStorage.getItem(key) !== '" + module.CLOUD_REQUEST_TOKEN + "'" in page
    assert "sessionStorage.setItem(key, '" + module.CLOUD_REQUEST_TOKEN + "')" in page
    assert "window.location.replace('/')" in page


def test_failed_worker_keeps_raw_diagnostics_private_and_sanitizes_browser_log(
    gui, tmp_path, monkeypatch
):
    module, _port = gui
    monkeypatch.setattr(module, "BASE_DIR", tmp_path)
    monkeypatch.setenv("VOXADUB_WORKER_SECRET", "worker-secret-value-987")
    with module.state_lock:
        module.state["logs"] = []

    diagnostic_output = (
        "import sys; "
        "print('Traceback (most recent call last):'); "
        "print('  File /Users/private-user/stage.py, line 1'); "
        "print('api_key=worker-secret-value-987', file=sys.stderr); "
        "raise SystemExit(2)"
    )
    module.worker(
        [sys.executable, "-c", diagnostic_output],
        stage="Translation stage",
        input_name="source.mp4",
    )

    with module.state_lock:
        browser_log = "".join(module.state["logs"])
        status = module.state["status"]
    assert status == "Failed"
    assert "Translation stage failed for source.mp4" in browser_log
    assert "/Users/private-user" not in browser_log
    assert "worker-secret-value-987" not in browser_log
    assert "private diagnostic log was saved" in browser_log

    diagnostic_files = list((tmp_path / "temp" / "diagnostics").glob("voxadub-diagnostic-*.log"))
    assert len(diagnostic_files) == 1
    private_log = diagnostic_files[0].read_text(encoding="utf-8")
    assert "/Users/private-user/stage.py" in private_log
    assert "worker-secret-value-987" in private_log
    if os.name != "nt":
        assert diagnostic_files[0].stat().st_mode & 0o777 == 0o600


def test_interrupted_multipart_upload_does_not_create_final_file(gui, tmp_path):
    module, port = gui
    uploads = tmp_path / "uploads"
    module.UPLOAD_DIR = uploads
    boundary = "voxadub-test-boundary"
    partial = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"video\"; filename=\"clip.mp4\"\r\n"
        "Content-Type: video/mp4\r\n\r\n"
    ).encode() + b"partial video bytes"
    declared_length = len(partial) + 100
    sock = socket.create_connection(("127.0.0.1", port), timeout=2)
    request = (
        f"POST /api/upload HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Type: multipart/form-data; boundary={boundary}\r\n"
        f"Content-Length: {declared_length}\r\n"
        f"X-Compute-Token: {module.CLOUD_REQUEST_TOKEN}\r\nConnection: close\r\n\r\n"
    ).encode() + partial
    sock.sendall(request)
    sock.shutdown(socket.SHUT_WR)
    response = b""
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk
    sock.close()
    assert b"400" in response.split(b"\r\n", 1)[0]
    assert not (uploads / "clip.mp4").exists()
    assert not list(uploads.glob(".upload-*.part")) if uploads.exists() else True
