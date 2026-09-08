from __future__ import annotations

import json
import mimetypes
import subprocess
from pathlib import Path

from .config import settings

ALLOWED_MIME_PREFIXES = ("video/", "audio/")


def validate_upload(filename: str, content_type: str | None, size_bytes: int) -> None:
    normalized_name = filename.replace("\\", "/")
    safe_name = Path(normalized_name).name
    if not safe_name or safe_name != normalized_name or "\x00" in filename:
        raise ValueError("Invalid filename")
    if size_bytes <= 0 or size_bytes > settings.max_upload_bytes:
        raise ValueError("File exceeds the configured upload limit")
    guessed = content_type or mimetypes.guess_type(filename)[0] or ""
    if not guessed.startswith(ALLOWED_MIME_PREFIXES):
        raise ValueError("Only audio and video uploads are supported")


def _ffprobe_input(source: str, *, timeout: int = 120) -> dict:
    command = ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", source]
    try:
        result = subprocess.run(command, check=True, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("FFprobe is required to inspect media") from exc
    return json.loads(result.stdout)


def ffprobe(path: Path) -> dict:
    return _ffprobe_input(str(path))


def inspect_media_url(url: str) -> dict:
    """Inspect a private S3 object through a short-lived signed URL.

    This is used by the Lambda control plane so a multi-gigabyte upload does not
    have to be downloaded into a 30-second API invocation. FFprobe can issue
    HTTP range requests for container metadata; the worker still validates the
    complete media file before processing it.
    """
    return _inspect_metadata(_ffprobe_input(url, timeout=25))


def inspect_media(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(path)
    return _inspect_metadata(ffprobe(path))


def _inspect_metadata(metadata: dict) -> dict:
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((metadata.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("Media has no readable duration")
    fps = None
    if video and video.get("r_frame_rate") and video["r_frame_rate"] != "0/0":
        numerator, denominator = video["r_frame_rate"].split("/")
        fps = round(float(numerator) / float(denominator), 3)
    return {
        "duration_seconds": duration,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": fps,
        "sample_rate": int(audio["sample_rate"]) if audio and audio.get("sample_rate") else None,
        "channels": audio.get("channels") if audio else None,
        "media_kind": "video" if video else "audio",
        "streams": [{"codec_type": s.get("codec_type"), "codec_name": s.get("codec_name")} for s in streams],
    }


def validate_output(
    path: Path,
    expected_kind: str,
    *,
    minimum_duration_seconds: float = 0.01,
    expected_duration_seconds: float | None = None,
    duration_tolerance_seconds: float = 1.5,
) -> dict:
    metadata = inspect_media(path)
    if metadata["media_kind"] != expected_kind:
        raise ValueError(f"Expected {expected_kind} output, got {metadata['media_kind']}")
    if metadata["duration_seconds"] < minimum_duration_seconds:
        raise ValueError("Output media has no usable duration")
    if expected_duration_seconds is not None and abs(metadata["duration_seconds"] - expected_duration_seconds) > duration_tolerance_seconds:
        raise ValueError(
            f"Output duration {metadata['duration_seconds']:.2f}s is outside the expected "
            f"{expected_duration_seconds:.2f}s ± {duration_tolerance_seconds:.2f}s"
        )
    return metadata
