from __future__ import annotations

import pytest

from app.media import validate_upload


@pytest.mark.parametrize("filename", ["../escape.wav", r"..\\escape.wav", ""])
def test_upload_names_cannot_escape_object_namespace(filename: str):
    with pytest.raises(ValueError):
        validate_upload(filename, "audio/wav", 10)
