"""Normalize cloud inputs for the existing H.264/AAC reconstruction pipeline."""
from pathlib import Path
import subprocess


def cloud_input(video: Path, directory: Path) -> Path:
    if video.suffix.lower() == ".mp4":
        return video
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / "video.mp4"
    print("Preparing an MP4 working copy of the uploaded video…", flush=True)
    subprocess.run(["ffmpeg", "-nostdin", "-y", "-i", str(video), "-map", "0:v:0", "-map", "0:a:0?",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-c:a", "aac", str(output)], check=True)
    return output
