import json
from pathlib import Path
import subprocess

import pytest

from engine.media_validation import MediaValidationError, validate_dubbing_input


def _probe(monkeypatch, *, streams, duration="2.0", returncode=0):
    def fake_run(command, **kwargs):
        payload = {"streams": streams, "format": {"duration": duration}}
        return subprocess.CompletedProcess(
            command,
            returncode,
            stdout=json.dumps(payload),
            stderr="invalid data" if returncode else "",
        )

    monkeypatch.setattr("engine.media_validation.subprocess.run", fake_run)


def test_rejects_video_without_audio_before_processing(tmp_path: Path, monkeypatch):
    media = tmp_path / "silent.mp4"
    media.touch()
    _probe(monkeypatch, streams=[{"codec_type": "video"}])
    with pytest.raises(MediaValidationError, match="no audio stream"):
        validate_dubbing_input(media, ffprobe="ffprobe")


def test_rejects_audio_only_input_before_processing(tmp_path: Path, monkeypatch):
    media = tmp_path / "speech.wav"
    media.touch()
    _probe(monkeypatch, streams=[{"codec_type": "audio"}])
    with pytest.raises(MediaValidationError, match="audio-only inputs are not supported"):
        validate_dubbing_input(media, ffprobe="ffprobe")


def test_rejects_subsecond_video_with_actionable_diagnostic(tmp_path: Path, monkeypatch):
    media = tmp_path / "tiny.mp4"
    media.touch()
    _probe(monkeypatch, streams=[{"codec_type": "video"}, {"codec_type": "audio"}], duration="0.6")
    with pytest.raises(MediaValidationError, match="0.600 seconds"):
        validate_dubbing_input(media, ffprobe="ffprobe")


def test_accepts_video_with_audio_and_valid_duration(tmp_path: Path, monkeypatch):
    media = tmp_path / "valid.mp4"
    media.touch()
    _probe(monkeypatch, streams=[{"codec_type": "video"}, {"codec_type": "audio"}], duration="2.2")
    info = validate_dubbing_input(media, ffprobe="ffprobe")
    assert info.duration == 2.2
    assert info.has_audio and info.has_video
