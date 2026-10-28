import re
from pathlib import Path


def parse_srt_time(value):
    hours, minutes, seconds = value.strip().split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds.replace(",", "."))


def parse_vtt_time(value):
    parts = value.strip().split(":")
    if len(parts) == 2:
        minutes, seconds = parts
        return int(minutes) * 60 + float(seconds)
    hours, minutes, seconds = parts
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def load_subtitles(path):
    """Parse an .srt or .vtt file into (start, end, text) cues."""
    path = Path(path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Subtitle file not found: {path}")
    raw = path.read_text(encoding="utf-8-sig")
    parse_time = parse_vtt_time if path.suffix.lower() == ".vtt" else parse_srt_time
    cues = []
    for block in re.split(r"\n\s*\n", raw.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            continue
        start_raw, end_raw = (part.strip() for part in lines[timing_idx].split("-->"))
        end_raw = end_raw.split()[0]
        try:
            start = parse_time(start_raw)
            end = parse_time(end_raw)
        except (ValueError, IndexError):
            continue
        text = " ".join(lines[timing_idx + 1:])
        text = re.sub(r"<[^>]+>", " ", text)
        text = text.replace("\u200b", "").replace("\xa0", " ")
        text = re.sub(r"\s+", " ", text).strip()
        if end > start and re.search(r"\w", text):
            cues.append((start, end, text))
    return cues
