"""SQLite-backed long-video context, glossary, and consistency checks."""

from __future__ import annotations

import re
import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


def _associative_terms(text: str) -> set[str]:
    # Function words cannot establish topical relevance ("the" even matched
    # the substring in "weather"). Match complete lexical tokens instead.
    stop = {'the', 'and', 'but', 'for', 'with', 'that', 'this', 'these', 'those',
            'from', 'into', 'are', 'was', 'were', 'has', 'have', 'had', 'not',
            'does', 'still', 'only', 'then', 'there', 'their', 'they', 'you', 'your'}
    return set(re.findall(r"[\wÀ-ž-]{3,}", text.casefold())) - stop


def _context_overlap(source: str, terms_json: str) -> int:
    return len(_associative_terms(source).intersection(json.loads(terms_json)))


@dataclass(frozen=True)
class ContextItem:
    kind: str
    source: str
    target: Optional[str] = None
    speaker_id: Optional[str] = None
    group_index: Optional[int] = None
    metadata: Mapping[str, object] = None


@dataclass(frozen=True)
class ConsistencyIssue:
    kind: str
    value: str
    expected: Optional[str]
    actual: Optional[str]
    severity: str = "warning"


class ContextMemory:
    """Small persistent memory with bounded relevant-context retrieval."""

    def __init__(self, path: str | Path = ":memory:") -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.create_function("context_overlap", 2, _context_overlap, deterministic=True)
        with self._db:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS context_items (
                    id INTEGER PRIMARY KEY,
                    kind TEXT NOT NULL,
                    source TEXT NOT NULL,
                    target TEXT,
                    speaker_id TEXT,
                    group_index INTEGER,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_context_kind_source ON context_items(kind, source);
                CREATE INDEX IF NOT EXISTS idx_context_speaker ON context_items(speaker_id);
                """
            )

    def remember(self, item: ContextItem | None = None, *, kind: str = "phrase", source: str = "", target: Optional[str] = None, speaker_id: Optional[str] = None, group_index: Optional[int] = None, metadata: Optional[Mapping[str, object]] = None) -> int:
        if item is not None:
            kind, source, target, speaker_id, group_index, metadata = item.kind, item.source, item.target, item.speaker_id, item.group_index, item.metadata
        if not source.strip():
            raise ValueError("context source must not be empty")
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO context_items(kind, source, target, speaker_id, group_index, created_at, metadata_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (kind, source, target, speaker_id, group_index, datetime.now(timezone.utc).isoformat(), json.dumps(metadata or {}, ensure_ascii=False)),
            )
            return int(cursor.lastrowid)

    def remember_translation(self, source: str, target: str, *, speaker_id: Optional[str] = None, group_index: Optional[int] = None) -> int:
        return self.remember(kind="phrase", source=source, target=target, speaker_id=speaker_id, group_index=group_index)

    def remember_entities(self, text: str, *, speaker_id: Optional[str] = None, group_index: Optional[int] = None) -> list[int]:
        ids = []
        for kind, value in extract_entities(text):
            ids.append(self.remember(kind=kind, source=value, speaker_id=speaker_id, group_index=group_index))
        return ids

    def relevant(self, text: str = "", *, speaker_id: Optional[str] = None, limit: int = 12, distant: bool = False) -> list[ContextItem]:
        terms = {term.casefold() for term in re.findall(r"[\wÀ-ž-]{3,}", text or "")}
        if distant:
            terms = _associative_terms(text or '')
        query = "SELECT kind, source, target, speaker_id, group_index, metadata_json FROM context_items"
        parameters = []
        if distant and terms:
            # Live sessions may last hours. Filter before the bounded fetch so
            # a recurring name remains discoverable after unrelated turns.
            # Keep the existing offline retrieval behavior unless requested.
            query += " WHERE context_overlap(source, ?) > 0"
            parameters.append(json.dumps(sorted(terms)))
            if speaker_id:
                query += " AND (speaker_id IS NULL OR speaker_id = ?)"
                parameters.append(speaker_id)
        if distant and terms:
            query += " ORDER BY context_overlap(source, ?) DESC, id DESC LIMIT ?"
            parameters.append(json.dumps(sorted(terms)))
        else:
            query += " ORDER BY id DESC LIMIT ?"
        parameters.append(max(1, int(limit) * 6))
        with self._lock:
            rows = self._db.execute(query, parameters).fetchall()
        selected = []
        seen = set()
        for row in rows:
            if speaker_id and row["speaker_id"] not in (None, speaker_id):
                continue
            score = (len(terms.intersection(_associative_terms(row['source']))) if distant else
                     int(row["source"].casefold() in terms) + sum(1 for term in terms if term in row["source"].casefold()))
            # Context memory is associative, not a rolling transcript.  Do
            # not inject unrelated recent translations into a short prompt;
            # that can make a generative translator copy an earlier answer.
            if text and score == 0:
                continue
            key = (row["kind"], row["source"], row["target"])
            if key in seen:
                continue
            seen.add(key)
            selected.append(ContextItem(row["kind"], row["source"], row["target"], row["speaker_id"], row["group_index"], json.loads(row["metadata_json"] or "{}")))
            if len(selected) >= limit:
                break
        return selected

    def glossary(self, *, limit: int = 500) -> dict[str, str]:
        with self._lock:
            rows = self._db.execute("SELECT source, target FROM context_items WHERE target IS NOT NULL ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return {row["source"]: row["target"] for row in reversed(rows)}

    def consistency_check(self, source_text: str, translated_text: str, *, glossary: Optional[Mapping[str, str]] = None) -> list[ConsistencyIssue]:
        issues = []
        mapping = dict(self.glossary())
        mapping.update(glossary or {})
        for source, target in mapping.items():
            if source.casefold() in source_text.casefold() and target.casefold() not in translated_text.casefold():
                issues.append(ConsistencyIssue("glossary", source, target, translated_text, "error"))
        for value in _tokens(source_text, kind="number"):
            if value not in translated_text:
                issues.append(ConsistencyIssue("number", value, value, translated_text, "error"))
        for value in _tokens(source_text, kind="unit"):
            if value.casefold() not in translated_text.casefold():
                issues.append(ConsistencyIssue("unit", value, value, translated_text, "warning"))
        for value in _tokens(source_text, kind="acronym"):
            if value.casefold() not in translated_text.casefold():
                issues.append(ConsistencyIssue("acronym", value, value, translated_text, "warning"))
        return issues

    def close(self) -> None:
        with self._lock:
            self._db.close()


def extract_entities(text: str) -> list[tuple[str, str]]:
    patterns = [
        ("acronym", r"\b[A-Z]{2,}(?:-[A-Z0-9]+)?\b"),
        ("number", r"\b\d+(?:[.,]\d+)?\b"),
        ("date", r"\b(?:\d{1,4}[-/]\d{1,2}[-/]\d{1,4}|\d{1,2}\.\d{1,2}\.\d{2,4})\b"),
        ("unit", r"\b\d+(?:[.,]\d+)?\s?(?:kg|g|mg|km|m|cm|mm|%|°C|GB|MB|Hz|s|min)\b"),
    ]
    entities = []
    for kind, pattern in patterns:
        entities.extend((kind, match.group(0)) for match in re.finditer(pattern, text or ""))
    # Capitalized multi-word names and organizations are useful recurring
    # terms, but avoid treating the first word of every sentence as an entity.
    entities.extend(("name", match.group(0)) for match in re.finditer(r"\b[A-Z][a-zÀ-ž]+(?:\s+[A-Z][a-zÀ-ž]+)+\b", text or ""))
    return list(dict.fromkeys(entities))


def _tokens(text: str, *, kind: str) -> list[str]:
    return [value for found_kind, value in extract_entities(text) if found_kind == kind]
