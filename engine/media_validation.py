"""Early, user-facing validation for supported dubbing inputs."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import shutil
import subprocess


MIN_VIDEO_DURATION_SECONDS = 1.0


class MediaValidationError(ValueError):
    """A media input cannot be processed by the dubbing pipeline."""


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_audio: bool
    has_video: bool


def inspect_media(path: str | Path, *, ffprobe: str | None = None) -> MediaInfo:
    media_path = Path(path).expanduser()
    label = media_path.name or "input"
    if not media_path.is_file():
        raise MediaValidationError(f"Input validation failed for {label}: file not found.")
    executable = ffprobe or shutil.which("ffprobe")
    if not executable:
        raise MediaValidationError(
            f"Input validation failed for {label}: ffprobe is unavailable. Install FFmpeg and retry."
        )
    result = subprocess.run(
        [
            executable,
            "-v", "error",
            "-show_entries", "stream=codec_type:format=duration",
            "-of", "json",
            str(media_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.strip().splitlines()[-1:] or ["media could not be decoded"]
        raise MediaValidationError(
            f"Input validation failed for {label}: FFprobe could not inspect the file ({detail[0]}). "
            "Check that the upload is complete and the file is a supported media format."
        )
    try:
        payload = json.loads(result.stdout)
        duration = float(payload["format"]["duration"])
        streams = payload.get("streams", [])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise MediaValidationError(
            f"Input validation failed for {label}: FFprobe returned incomplete media metadata."
        ) from exc
    if not math.isfinite(duration) or duration <= 0:
        raise MediaValidationError(f"Input validation failed for {label}: duration is not positive.")
    return MediaInfo(
        duration=duration,
        has_audio=any(item.get("codec_type") == "audio" for item in streams),
        has_video=any(item.get("codec_type") == "video" for item in streams),
    )


def validate_dubbing_input(path: str | Path, *, ffprobe: str | None = None) -> MediaInfo:
    """Reject unsupported stream layouts before model setup or inference."""

    info = inspect_media(path, ffprobe=ffprobe)
    name = Path(path).name or "input"
    if not info.has_video:
        raise MediaValidationError(
            f"Input validation failed for {name}: no video stream was found. "
            "Choose a video file; audio-only inputs are not supported."
        )
    if not info.has_audio:
        raise MediaValidationError(
            f"Input validation failed for {name}: no audio stream was found. "
            "Choose a video with dialogue or other audio to dub."
        )
    if info.duration < MIN_VIDEO_DURATION_SECONDS:
        raise MediaValidationError(
            f"Input validation failed for {name}: duration is {info.duration:.3f} seconds. "
            f"Videos shorter than {MIN_VIDEO_DURATION_SECONDS:.1f} second are not supported."
        )
    return info
