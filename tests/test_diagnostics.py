import os

from engine.diagnostics import create_private_diagnostic_log, sanitize_for_display


def test_browser_diagnostic_text_redacts_tokens_secrets_and_local_paths(monkeypatch):
    monkeypatch.setenv("VOXADUB_TEST_SECRET", "sensitive-test-value-123")
    fake_hf_token = "hf_" + "a" * 24
    message = (
        "failed /Users/private-user/uploads/clip.mp4 with "
        + fake_hf_token
        + " and "
        "api_key=sensitive-test-value-123"
    )
    safe = sanitize_for_display(message)
    assert "/Users/private-user" not in safe
    assert "sensitive-test-value-123" not in safe
    assert fake_hf_token not in safe
    assert "clip.mp4" in safe
    assert "[redacted]" in safe


def test_private_diagnostic_log_keeps_stage_and_basename_with_restricted_mode(tmp_path):
    path = create_private_diagnostic_log(
        tmp_path / "diagnostics",
        stage="Translating video",
        input_name="/private/user/uploads/clip.mp4",
    )
    contents = path.read_text(encoding="utf-8")
    assert "Stage: Translating video" in contents
    assert "Input: clip.mp4" in contents
    assert "/private/user" not in contents
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert path.parent.stat().st_mode & 0o777 == 0o700
