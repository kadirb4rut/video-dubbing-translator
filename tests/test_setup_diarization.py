from pathlib import Path

import scripts.setup_diarization as setup_diarization


def test_incomplete_diarization_environment_is_recreated_with_uv(monkeypatch, tmp_path):
    target_python = tmp_path / "third_party" / "diarization" / ".venv" / "bin" / "python"
    base_python = tmp_path / ".venv" / "bin" / "python"
    target_python.parent.mkdir(parents=True)
    target_python.touch()
    commands = []
    available = iter((False, True))

    monkeypatch.setattr(setup_diarization, "VENV_DIR", target_python.parent.parent)
    monkeypatch.setattr(setup_diarization, "python_in_venv", lambda: target_python)
    monkeypatch.setattr(setup_diarization, "find_python", lambda: base_python)
    monkeypatch.setattr(setup_diarization, "runtime_has_pip", lambda _python: next(available))
    monkeypatch.setattr(setup_diarization.shutil, "which", lambda name: "/usr/bin/uv" if name == "uv" else None)
    monkeypatch.setattr(setup_diarization, "run", lambda command: commands.append(command))

    result = setup_diarization.ensure_runtime()

    assert result == target_python
    assert commands == [[
        "/usr/bin/uv", "venv", "--clear", "--python", base_python, "--seed", target_python.parent.parent
    ]]


def test_runtime_has_pip_reports_missing_ensurepip_as_unavailable(monkeypatch, tmp_path):
    import subprocess

    def no_pip(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 1, stdout="", stderr="No module named ensurepip")

    monkeypatch.setattr(setup_diarization.subprocess, "run", no_pip)
    assert setup_diarization.runtime_has_pip(Path("/path/to/python")) is False
