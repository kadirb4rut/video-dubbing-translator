from __future__ import annotations

from pathlib import Path

import pytest

from app.providers_real import ProviderUnavailable, validate_segments, write_srt, write_txt, write_vtt


def test_subtitle_exports_keep_segment_order(tmp_path: Path):
    segments = [{"start": 0.2, "end": 1.4, "text": "Hello"}, {"start": 2.0, "end": 3.2, "text": "World"}]
    srt = write_srt(segments, tmp_path / "captions.srt").read_text()
    vtt = write_vtt(segments, tmp_path / "captions.vtt").read_text()
    txt = write_txt(segments, tmp_path / "captions.txt").read_text()
    assert "00:00:00,200 --> 00:00:01,400" in srt
    assert "00:00:02.000 --> 00:00:03.200" in vtt
    assert vtt.startswith("WEBVTT")
    assert txt.splitlines() == ["Hello", "World"]


def test_invalid_transcript_timestamps_fail_loudly():
    with pytest.raises(ProviderUnavailable):
        validate_segments([{"start": 2.0, "end": 1.0, "text": "Backwards"}])
    with pytest.raises(ProviderUnavailable):
        validate_segments([{"start": 0.0, "end": 1.0, "text": "First"}, {"start": -1.0, "end": 2.0, "text": "Second"}])
    with pytest.raises(ProviderUnavailable):
        validate_segments([{"start": float("nan"), "end": 1.0, "text": "Not finite"}])
