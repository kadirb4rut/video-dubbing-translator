import pytest
from pathlib import Path
import sys

from compute_backends.colab_tunnel import (
    ColabLiveTunnel,
    build_tunnel_command,
    stop_colab_session,
    validate_session_name,
)
from compute_backends.colab_batch import ColabBatchRunner
from compute_backends.modal_tts import create_modal_synthesizer
from compute_backends.providers import provider_description, validate_provider
from compute_backends import settings
from scripts.colab_backend_remote import _python_interpreter_path
from scripts import run_dubbing


def test_provider_capabilities_are_explicit():
    assert validate_provider("local") == "local"
    assert provider_description("colab")["batch"] == "full"
    modal = provider_description("modal-tts")
    assert modal["batch"] == "tts-only"
    assert modal["live"] == "unsupported"
    with pytest.raises(ValueError):
        validate_provider("unknown")


def test_colab_ssh_tunnel_is_loopback_only_and_uses_proxy_command():
    command = build_tunnel_command(
        ssh_executable="/usr/bin/ssh",
        colab_executable="/opt/bin/colab",
        session="my-g4-session",
        identity="/tmp/private key",
        control_path="/tmp/vdt-control",
        forwards=((8766, 8766), (18765, 8765)),
    )
    assert "ProxyCommand=/opt/bin/colab ssh --proxy-mode --session my-g4-session --identity '/tmp/private key'" in command
    assert "127.0.0.1:8766:127.0.0.1:8766" in command
    assert "127.0.0.1:18765:127.0.0.1:8765" in command
    assert "0.0.0.0:8766:127.0.0.1:8766" not in command
    assert command[-1] == "root@colab-runtime"


def test_colab_stop_targets_only_the_selected_validated_session(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "Session terminated."
        stderr = ""

    monkeypatch.setattr(
        "compute_backends.colab_tunnel.subprocess.run",
        lambda command, **kwargs: calls.append((command, kwargs)) or Result(),
    )
    assert stop_colab_session("/opt/bin/colab", "my-g4") == "Session terminated."
    assert calls[0][0] == ["/opt/bin/colab", "stop", "--session", "my-g4"]
    assert calls[0][1]["timeout"] == 90.0
    with pytest.raises(ValueError):
        stop_colab_session("/opt/bin/colab", "../../other")


def test_colab_live_tunnel_stops_runtime_only_after_successful_connection(monkeypatch):
    calls = []
    monkeypatch.setattr(
        "compute_backends.colab_tunnel.stop_colab_session",
        lambda executable, session: calls.append((executable, session)) or "stopped",
    )
    tunnel = object.__new__(ColabLiveTunnel)
    tunnel.session = "my-g4"
    tunnel.process = None
    tunnel._colab_executable = "/opt/bin/colab"
    tunnel._session_established = True
    tunnel._log_handle = None
    tunnel._temporary = None
    tunnel.stop(stop_session=True)
    assert calls == [("/opt/bin/colab", "my-g4")]

    tunnel._session_established = False
    tunnel.stop(stop_session=True)
    assert calls == [("/opt/bin/colab", "my-g4")]


@pytest.mark.parametrize("raises", [False, True])
def test_colab_batch_runner_closes_selected_session_after_job(monkeypatch, raises):
    runner = object.__new__(ColabBatchRunner)
    runner.stop_after_job = True
    stop_calls = []
    runner._stop_selected_session = lambda: stop_calls.append(True)

    def run_job(*_args):
        if raises:
            raise RuntimeError("job failed")
        return Path("finished.mp4")

    runner._run_job = run_job
    if raises:
        with pytest.raises(RuntimeError, match="job failed"):
            runner.run("input.mp4", "output.mp4", {})
    else:
        assert runner.run("input.mp4", "output.mp4", {}) == Path("finished.mp4")
    assert stop_calls == [True]


@pytest.mark.parametrize("value", ["", "g4 session", "../other", "a" * 64])
def test_colab_session_name_rejects_shell_input(value):
    with pytest.raises(ValueError):
        validate_session_name(value)


@pytest.mark.parametrize("value", ["/tmp/project", "/content/", "/content/../etc/passwd"])
def test_colab_batch_rejects_project_paths_outside_content(value):
    with pytest.raises(ValueError):
        ColabBatchRunner._validate_remote_project(value)


def test_colab_python_path_preserves_virtual_environment_symlink(tmp_path):
    executable = tmp_path / ".venv" / "bin" / "python"
    executable.parent.mkdir(parents=True)
    executable.symlink_to(sys.executable)
    assert _python_interpreter_path(executable) == executable


def test_simple_cli_defaults_local_and_accepts_each_compute_choice(monkeypatch):
    defaults = {
        "batch_provider": "local", "live_provider": "local",
        "colab_session": "", "remote_project": "/content/video-dubbing-translator-main",
    }
    monkeypatch.setattr(run_dubbing, "load_compute_settings", lambda: defaults.copy())
    assert run_dubbing.parse_args(["input.mp4", "--target-language", "tr"]).compute == "local"
    colab = run_dubbing.parse_args([
        "input.mp4", "--target-language", "tr", "--compute", "colab", "--session", "my-g4",
    ])
    assert (colab.compute, colab.session) == ("colab", "my-g4")
    modal = run_dubbing.parse_args([
        "input.mp4", "--target-language", "tr", "--compute", "modal-tts",
    ])
    assert modal.compute == "modal-tts"


def test_colab_prepare_passes_selected_translation_model():
    runner = object.__new__(ColabBatchRunner)
    runner.remote_project = "/content/project"
    runner.workspace = "/content/jobs"
    calls = []
    runner._remote_action = lambda action, config: calls.append((action, config)) or {"ok": True}
    runner._prepare("job-1", {"translation_model": "hymt2-7b"})
    assert calls[0][0] == "prepare"
    assert calls[0][1]["translation_model"] == "hymt2-7b"


def test_compute_settings_persist_provider_choices_without_auth_secrets(tmp_path, monkeypatch):
    path = tmp_path / "compute.json"
    monkeypatch.setattr(settings, "settings_path", lambda: path)
    settings.save_compute_settings({
        "batch_provider": "modal-tts",
        "live_provider": "colab",
        "colab_session": "my-g4-session",
        "remote_project": "/content/vdt",
        "managed_colab": "no",
    })
    assert settings.load_compute_settings() == {
        "batch_provider": "modal-tts",
        "live_provider": "colab",
        "colab_session": "my-g4-session",
        "remote_project": "/content/vdt",
        "managed_colab": "no",
    }
    assert "token" not in path.read_text(encoding="utf-8").lower()


def test_modal_synthesizer_uses_deployed_function_and_writes_wav(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference wav")

    class FakeFunction:
        def __init__(self):
            self.call = None
            self.window_updates = []
            self.autoscaler = self

        def update(self, **kwargs):
            self.window_updates.append(kwargs)

        def remote(self, *args):
            self.call = args
            return b"RIFFfake-wav-response"

    function = FakeFunction()

    class FakeFunctions:
        @staticmethod
        def from_name(app_name, function_name):
            assert (app_name, function_name) == ("video-dubbing-translator", "synthesize_wav")
            return function

    class FakeModal:
        Function = FakeFunctions

    class Reference:
        wav_path = reference
        transcript = "Speaker prompt"

    output = tmp_path / "out.wav"
    synthesize = create_modal_synthesizer(modal_module=FakeModal)
    synthesize("Hello", Reference(), output, seed=123, style="calm")
    synthesize.close()

    assert output.read_bytes() == b"RIFFfake-wav-response"
    assert function.call == ("Hello", b"reference wav", "Speaker prompt", "calm", 123)
    assert function.window_updates == [{"scaledown_window": 60}, {"scaledown_window": 2}]


def test_modal_synthesizer_supports_modal_15_autoscaler_api(tmp_path):
    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference wav")
    updates = []

    class LegacyFunction:
        def update_autoscaler(self, **kwargs):
            updates.append(kwargs)

        def remote(self, *_args):
            return b"RIFFfake-wav-response"

    class FakeFunctions:
        @staticmethod
        def from_name(_app_name, _function_name):
            return LegacyFunction()

    class FakeModal:
        Function = FakeFunctions

    class Reference:
        wav_path = reference
        transcript = "Speaker prompt"

    synthesize = create_modal_synthesizer(modal_module=FakeModal)
    synthesize("Hello", Reference(), tmp_path / "out.wav")
    synthesize.close()
    assert updates == [{"scaledown_window": 60}, {"scaledown_window": 2}]
