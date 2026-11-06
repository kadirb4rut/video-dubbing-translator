from pathlib import Path

import pytest

from dubbing.references import AudioQuality, SpeakerReferenceManager
from dubbing.segments import SpeechSegment


def test_short_segment_reuses_a_safe_same_speaker_reference(tmp_path: Path):
    segments = [
        SpeechSegment(0, 0, 0.04, "yes", "SPEAKER_00"),
        SpeechSegment(1, 1, 3, "a complete phrase", "SPEAKER_00"),
    ]
    quality = {1: AudioQuality(0.1, 0.5, 0.0)}
    manager = SpeakerReferenceManager(segments, quality, lambda *_: None, tmp_path)
    manager.prepare()
    reference = manager.reference_for(segments[0])
    assert reference.source_segment_index == 1
    assert reference.speaker == "SPEAKER_00"


def test_short_segment_without_same_speaker_reference_fails_explicitly(tmp_path: Path):
    segment = SpeechSegment(0, 0, 0.04, "yes", "SPEAKER_00")
    manager = SpeakerReferenceManager(
        [segment], {0: AudioQuality(0.1, 0.5, 0.0)}, lambda *_: None, tmp_path
    )
    manager.prepare()
    with pytest.raises(ValueError, match="No safe voice reference"):
        manager.reference_for(segment)


def test_ambiguous_reference_fails_without_borrowing_a_voice(tmp_path: Path):
    segment = SpeechSegment(0, 0, 1.5, "uncertain speaker", None, speaker_ambiguous=True)
    writes = []
    manager = SpeakerReferenceManager(
        [segment], {0: AudioQuality(0.1, 0.5, 0.0)}, lambda *args: writes.append(args), tmp_path
    )
    manager.prepare()
    with pytest.raises(ValueError, match="speaker identity is ambiguous"):
        manager.reference_for(segment)
    assert writes == []
