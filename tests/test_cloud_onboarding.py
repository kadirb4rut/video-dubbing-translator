import io
import json
from pathlib import Path
import tarfile

import pytest

from compute_backends.source_bundle import bundle_source, source_files
from compute_backends.onboarding import CloudConnection
from scripts.cloud_bootstrap import unpack
from scripts.colab_backend_remote import _runtime_environment


def test_application_upload_never_includes_user_media_models_or_credentials(tmp_path):
    for name in ("Video_Translator.py", "voxcpm_runtime.py", "secret.py", ".env", "uploads/personal.mp4", "vocal-remover/models/baseline.pth", "compute_backends/real.py", "compute_backends/__pycache__/secret.py"):
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("sensitive" if "secret" in name else "code")
    archive, _ = bundle_source(tmp_path)
    with tarfile.open(archive) as bundle:
        assert set(bundle.getnames()) == {"Video_Translator.py", "voxcpm_runtime.py", "compute_backends/real.py"}


def test_application_bundle_includes_live_browser_assets(tmp_path):
    assets = tmp_path / "browser_extension" / "live_dubbing"
    assets.mkdir(parents=True)
    for name in ("player.html", "client.js", "style.css", "manifest.json"):
        (assets / name).write_text("test")
    (assets / "private.mp4").write_bytes(b"media")
    archive, _ = bundle_source(tmp_path)
    with tarfile.open(archive) as bundle:
        assert set(bundle.getnames()) == {
            "browser_extension/live_dubbing/player.html",
            "browser_extension/live_dubbing/client.js",
            "browser_extension/live_dubbing/style.css",
            "browser_extension/live_dubbing/manifest.json",
        }


@pytest.mark.parametrize("name,link", [("../escape", False), ("/tmp/escape", False), ("link", True)])
def test_cloud_archive_rejects_escape_and_link_entries(tmp_path, name, link):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as bundle:
        item = tarfile.TarInfo(name)
        if link:
            item.type = tarfile.SYMTYPE
            item.linkname = "/tmp/escape"
        bundle.addfile(item, io.BytesIO())
    with pytest.raises(ValueError, match="Unsafe"):
        unpack(archive, tmp_path / "app")


def test_authentication_link_is_in_memory_and_not_in_connection_log():
    connection = CloudConnection()
    connection._line("Open https://accounts.google.com/o/oauth2/auth?state=SECRET_STATE")
    state = connection.status()
    assert state["authUrl"].endswith("state=SECRET_STATE")
    assert "SECRET_STATE" not in "".join(state["logs"])
    connection._line("https://untrusted.example/auth?state=other")
    assert "untrusted.example" not in connection.status()["authUrl"]
    connection._line('VDT_SETUP_EVENT={"stage":"ready","message":"Ready"}')
    assert not connection.status()["authUrl"]


def test_remote_cache_environment_reaches_pipeline_without_overriding_arbitrary_variables(tmp_path, monkeypatch):
    (tmp_path / ".vdt-runtime.json").write_text(json.dumps({"HF_HOME": "/storage/models/hf", "LD_PRELOAD": "bad"}))
    monkeypatch.delenv("LD_PRELOAD", raising=False)
    env = _runtime_environment(tmp_path)
    assert env["HF_HOME"] == "/storage/models/hf"
    assert "LD_PRELOAD" not in env


def test_full_modal_is_separate_from_legacy_speech_only_provider():
    from compute_backends.providers import provider_description
    assert provider_description("modal")["batch"] == "full"
    assert provider_description("modal-tts")["batch"] == "tts-only"


def test_modal_live_request_is_rejected_before_any_cloud_setup():
    connection = CloudConnection()
    with pytest.raises(ValueError, match="Modal Live Dubbing is not available"):
        connection.start("modal", live=True)
    assert connection.process is None
    assert connection.status()["stage"] == "disconnected"


def test_colab_zero_exit_with_kernel_traceback_cannot_report_ready():
    import sys
    from scripts.prepare_compute import run
    with pytest.raises(RuntimeError, match="did not confirm"):
        run([sys.executable, "-c", "print('ValueError: mount failed')"], expected_marker="VDT_CLOUD_READY=")


def test_colab_setup_uses_vm_cache_without_drive_mount(monkeypatch):
    import base64
    import sys
    from scripts import prepare_compute

    commands = []
    monkeypatch.setattr(sys, "argv", ["prepare_compute.py", "colab", "--live"])
    monkeypatch.setattr(prepare_compute, "ensure_client", lambda provider: "colab")
    monkeypatch.setattr(prepare_compute, "report_colab_usage", lambda tool: None)
    monkeypatch.setattr(prepare_compute, "bundle_source", lambda root: ("app.tar.gz", "fingerprint"))
    monkeypatch.setattr(prepare_compute, "load_compute_settings", lambda: {})
    monkeypatch.setattr(prepare_compute, "save_compute_settings", lambda value: None)

    def fake_run(command, **kwargs):
        commands.append(command)
        return "[video-dubbing-g4]" if command[1] == "sessions" else ""

    monkeypatch.setattr(prepare_compute, "run", fake_run)
    prepare_compute.main()
    assert not any("drivemount" in command for command in commands)
    bootstrap = next(command for command in commands if "--file" in command)
    config_arg = next(arg for arg in bootstrap if str(arg).startswith("VDT_BOOTSTRAP_CONFIG="))
    config = json.loads(base64.b64decode(config_arg.split("=", 1)[1]))
    assert config["cache"] == "/content/.vdt-managed/cache"
    assert config["live"] is True


def test_gui_forwards_authorization_response_through_a_real_controlling_terminal(tmp_path, monkeypatch):
    import os
    import shutil
    import time
    from compute_backends import onboarding
    from compute_backends import client_tools, settings
    if os.name == "nt":
        pytest.skip("POSIX controlling terminal")
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    shutil.copyfile(onboarding.ROOT / "scripts/compute_terminal.py", scripts / "compute_terminal.py")
    (scripts / "prepare_compute.py").write_text('''import json
print("https://accounts.google.com/example?state=test", flush=True)
print("Enter authorization code:", flush=True)
with open('/dev/tty') as terminal:
    answer=terminal.readline().strip()
assert answer == 'TEST_PRIVATE_CODE'
print('VDT_SETUP_EVENT='+json.dumps({'stage':'ready','message':'Authorized'}), flush=True)
''')
    monkeypatch.setattr(onboarding, "ROOT", tmp_path)
    monkeypatch.setattr(client_tools, "client_dir", lambda: tmp_path / "client")
    monkeypatch.setattr(settings, "settings_path", lambda: tmp_path / "settings.json")
    connection = CloudConnection()
    try:
        connection.start("modal")
        deadline = time.monotonic() + 6
        while not connection.status()["needsInput"] and time.monotonic() < deadline:
            time.sleep(.02)
        connection.reply("TEST_PRIVATE_CODE")
        while connection.process is not None and time.monotonic() < deadline:
            time.sleep(.02)
        assert connection.status()["stage"] == "ready"
        assert "TEST_PRIVATE_CODE" not in "".join(connection.status()["logs"])
    finally:
        connection.disconnect()


@pytest.mark.parametrize("failure", [False, True])
def test_job_completion_and_failure_leave_colab_connected_by_default(failure):
    from compute_backends.colab_batch import ColabBatchRunner
    runner = object.__new__(ColabBatchRunner)
    closed = []
    runner._stop_selected_session = lambda: closed.append(True)
    def job(*args):
        if failure:
            raise RuntimeError("job failed")
        return Path("result.mp4")
    runner._run_job = job
    if failure:
        with pytest.raises(RuntimeError):
            runner.run("source.mp4", "result.mp4", {})
    else:
        runner.run("source.mp4", "result.mp4", {})
    assert not closed


def test_gui_completion_does_not_disconnect_provider_or_register_shutdown_watchers(monkeypatch):
    from compute_backends import onboarding
    connection = CloudConnection()
    connection.data.update(provider="colab", stage="ready")
    closed = []
    monkeypatch.setattr(connection, "disconnect", lambda: closed.append(True))
    connection.complete()
    assert connection.status()["stage"] == "ready"
    assert "Disconnect GPU" in connection.status()["message"]
    assert not closed
