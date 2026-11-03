import contextlib
import json
import threading
import urllib.error
import urllib.request

import pytest


@pytest.fixture
def gui(monkeypatch, tmp_path):
    import web_gui
    from compute_backends import settings
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "compute.json")
    monkeypatch.setitem(web_gui.state, "uploaded_video", str(tmp_path / "source.webm"))
    server = web_gui.ReusableThreadingHTTPServer(("127.0.0.1", 0), web_gui.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield web_gui, f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def post(url, payload, headers=None):
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    return urllib.request.urlopen(request)


def test_cloud_account_controls_reject_cross_origin_and_missing_token(gui):
    module, base = gui
    for headers in ({}, {"X-Compute-Token": module.CLOUD_REQUEST_TOKEN, "Origin": "https://untrusted.example"}):
        with pytest.raises(urllib.error.HTTPError) as response:
            post(base + "/api/cloud-input", {"value": "secret"}, headers)
        assert response.value.code == 403


def test_modal_full_pipeline_does_not_require_ai_models_on_the_mac(gui, monkeypatch, tmp_path):
    module, base = gui
    monkeypatch.setattr(module, "MODEL_PATH", tmp_path / "absent-model")
    monkeypatch.setattr(module, "hymt2_model_status", lambda: [])
    monkeypatch.setattr(module.cloud_connection, "status", lambda: {"stage": "ready", "provider": "modal", "model": "hymt2-1.8b"})
    commands = []
    monkeypatch.setattr(module, "run_process", lambda command, *args, **kwargs: (commands.append((command, kwargs)) or (True, "Started")))
    response = post(base + "/api/run", {"batchProvider": "modal", "source": "en", "target": "tr", "diarization": False}, {"X-Compute-Token": module.CLOUD_REQUEST_TOKEN})
    assert json.load(response)["ok"]
    command, options = commands[0]
    assert command[command.index("--compute") + 1] == "modal"
    assert command[command.index("--target-language") + 1] == "tr"
    assert command[command.index("--output") + 1].endswith("source.mp4")
    assert "--no-diarization" in command


def test_remote_job_cannot_start_with_failed_preparation(gui, monkeypatch):
    module, base = gui
    monkeypatch.setattr(module.cloud_connection, "status", lambda: {"stage": "error", "provider": "colab", "model": "hymt2-1.8b"})
    with pytest.raises(urllib.error.HTTPError) as response:
        post(base + "/api/run", {"batchProvider": "colab", "source": "en", "target": "tr"}, {"X-Compute-Token": module.CLOUD_REQUEST_TOKEN})
    assert response.value.code == 409


def test_completed_video_download_and_suffix_seeking_use_the_local_file(gui, monkeypatch, tmp_path):
    module, base = gui
    video = tmp_path / "Turkce video.mp4"
    data = bytes(range(256)) * 20
    video.write_bytes(data)
    monkeypatch.setattr(module, "OUTPUT_DIR", tmp_path)
    monkeypatch.setitem(module.state, "output_video", str(video))
    response = urllib.request.urlopen(base + "/media/output-video?download=1")
    assert "attachment" in response.headers["Content-Disposition"]
    assert response.read() == data
    response = urllib.request.urlopen(urllib.request.Request(base + "/media/output-video", headers={"Range": "bytes=-1024"}))
    assert response.status == 206
    assert response.read() == data[-1024:]
    assert response.headers["Content-Range"] == f"bytes {len(data)-1024}-{len(data)-1}/{len(data)}"
