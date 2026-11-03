"""Versioned machine-readable and human-readable quality reports."""

from __future__ import annotations

import csv
import html
import json
import platform
import time
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional


SCHEMA_VERSION = "1.0"


def _jsonable(value):
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


class QualityReport:
    def __init__(self, *, run_id: str, source: Optional[str] = None, target: Optional[str] = None, metadata: Optional[Mapping[str, object]] = None):
        self.run_id = run_id
        self.source_language = source
        self.target_language = target
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.groups: list[dict[str, object]] = []
        self.performance: dict[str, object] = dict(metadata or {})

    def add_group(self, group_index: int, *, quality=None, prosody_distance: Optional[float] = None, translation_backend: Optional[str] = None, tts_backend: Optional[str] = None, extra: Optional[Mapping[str, object]] = None) -> None:
        row: dict[str, object] = {"group_index": int(group_index), "prosody_distance": prosody_distance, "translation_backend": translation_backend, "tts_backend": tts_backend}
        if quality is not None:
            row["quality"] = _jsonable(quality)
        row.update(extra or {})
        self.groups.append(row)

    def finish(self, *, duration_seconds: Optional[float] = None) -> dict[str, object]:
        self.performance.setdefault("host", platform.platform())
        self.performance.setdefault("python", platform.python_version())
        if duration_seconds is not None:
            self.performance["total_wall_seconds"] = float(duration_seconds)
        qualities = [row.get("quality", {}) for row in self.groups]
        self.summary = {
            "groups": len(self.groups),
            "passed": sum(not item.get("hard_failures") for item in qualities if isinstance(item, Mapping)),
            "hard_failures": sum(len(item.get("hard_failures", [])) for item in qualities if isinstance(item, Mapping)),
            "warnings": sum(len(item.get("warnings", [])) for item in qualities if isinstance(item, Mapping)),
            "mean_wer": _mean([item.get("wer") for item in qualities if isinstance(item, Mapping)]),
            "mean_cer": _mean([item.get("cer") for item in qualities if isinstance(item, Mapping)]),
            "mean_speaker_similarity": _mean([((item.get("speaker_similarity") or {}).get("cosine")) for item in qualities if isinstance(item, Mapping)]),
            "mean_prosody_distance": _mean([row.get("prosody_distance") for row in self.groups]),
        }
        return self.payload()

    def payload(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "run_id": self.run_id,
            "created_at": self.started_at,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "summary": getattr(self, "summary", {"groups": len(self.groups)}),
            "groups": _jsonable(self.groups),
            "performance": _jsonable(self.performance),
        }

    def write(self, directory: str | Path, *, csv_output: bool = True) -> dict[str, Path]:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        payload = self.payload()
        json_path = directory / "quality_report.json"
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        html_path = directory / "quality_report.html"
        html_path.write_text(render_html(payload), encoding="utf-8")
        paths = {"json": json_path, "html": html_path}
        if csv_output:
            csv_path = directory / "quality_report.csv"
            fields = ["group_index", "wer", "cer", "normalized_similarity", "speaker_cosine", "prosody_distance", "hard_failures", "warnings", "translation_backend", "tts_backend"]
            with csv_path.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields)
                writer.writeheader()
                for row in self.groups:
                    quality = row.get("quality") or {}
                    speaker = quality.get("speaker_similarity") or {}
                    writer.writerow({
                        "group_index": row.get("group_index"), "wer": quality.get("wer"), "cer": quality.get("cer"), "normalized_similarity": quality.get("normalized_similarity"), "speaker_cosine": speaker.get("cosine"), "prosody_distance": row.get("prosody_distance"), "hard_failures": ";".join(quality.get("hard_failures") or []), "warnings": ";".join(quality.get("warnings") or []), "translation_backend": row.get("translation_backend"), "tts_backend": row.get("tts_backend"),
                    })
            paths["csv"] = csv_path
        return paths


def _mean(values) -> Optional[float]:
    clean = [float(value) for value in values if value is not None]
    return sum(clean) / len(clean) if clean else None


def render_html(payload: Mapping[str, object]) -> str:
    summary = payload.get("summary") or {}
    rows = payload.get("groups") or []
    body, details = [], []
    for row in rows:
        quality = row.get("quality") or {}
        failures = quality.get("hard_failures") or []
        body.append("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in (
            row.get("original_group_index", row.get("group_index")), row.get("speaker", "—"),
            row.get("tts_backend"), row.get("final_duration_seconds", (quality.get("signal") or {}).get("duration_seconds")),
            row.get("applied_tempo", "—"), quality.get("wer"), row.get("prosody_distance"),
            "; ".join(failures) or "passed", quality.get("retry_count", 0),
        )) + "</tr>")
        details.append(
            f"<details><summary>Group {html.escape(str(row.get('original_group_index', row.get('group_index'))))} · text and measurements</summary>"
            f"<p>Expected: {html.escape(str(quality.get('expected_text') or '—'))}</p>"
            f"<p>Recovered: {html.escape(str(quality.get('recovered_text') or 'not measured'))}</p>"
            f"<pre>{html.escape(json.dumps(row, ensure_ascii=False, indent=2))}</pre></details>"
        )
    return f"""<!doctype html><meta charset='utf-8'><title>Quality report {html.escape(str(payload.get('run_id')))}</title>
<style>body{{font:14px system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem}} table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:.4rem;text-align:left}}pre{{white-space:pre-wrap;overflow-wrap:anywhere}}details{{margin:1rem 0}} .bad{{color:#a00}}</style>
<h1>Video dubbing quality report</h1><p>Schema {html.escape(SCHEMA_VERSION)} · Run {html.escape(str(payload.get('run_id')))}</p>
<p>{html.escape(str(payload.get('source_language') or 'unspecified'))} → {html.escape(str(payload.get('target_language') or 'unspecified'))}</p>
<pre>{html.escape(json.dumps(summary, ensure_ascii=False, indent=2))}</pre>
<table><thead><tr><th>Group</th><th>Speaker</th><th>TTS backend</th><th>Audio seconds</th><th>Tempo</th><th>WER</th><th>Prosody distance</th><th>QA</th><th>Retries</th></tr></thead><tbody>{''.join(body)}</tbody></table>
{''.join(details)}"""
