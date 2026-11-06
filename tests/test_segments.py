import math

import pytest

from dubbing.segments import from_legacy_lists


def test_transcript_segments_are_clamped_to_media_duration_without_losing_text():
    segments = from_legacy_lists(
        ["starts before zero", "ends after video", "past end"],
        [-0.4, 14.7, 15.1],
        [0.5, 15.94, 16.0],
        media_duration=15.0,
    )
    assert [(item.start, item.end, item.text) for item in segments] == [
        (0.0, 0.5, "starts before zero"),
        (14.7, 15.0, "ends after video"),
    ]


def test_transcript_segments_outside_or_with_invalid_intervals_are_removed():
    segments = from_legacy_lists(
        ["before", "after", "inverted", "nan", "valid"],
        [-2, 15.94, 3, 1, 2],
        [-1, 16, 2, math.inf, 2.5],
        media_duration=15,
    )
    assert [(item.index, item.text) for item in segments] == [(4, "valid")]


def test_media_duration_must_be_finite_and_positive():
    with pytest.raises(ValueError, match="finite and positive"):
        from_legacy_lists(["speech"], [0], [1], media_duration=math.nan)
