#!/usr/bin/env python3
"""Deterministic simulated-live regression test for phrase streaming."""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.contracts import AudioResult, SpeakerProfile, TTSBackend, TranslationBackend  # noqa: E402
from engine.streaming import StreamingSession  # noqa: E402


class EchoTranslation(TranslationBackend):
    name = "simulated-hymt2"

    def translate(self, text, source_language, target_language, *, context=None, glossary=None):
        return f"[{target_language}] {text}"


class ChunkTTS(TTSBackend):
    name = "simulated-voxcpm2-stream"

    def synthesize(self, text, speaker, **kwargs):
        return AudioResult(pcm=(text.encode("utf-8") + b"\0"), sample_rate=48_000, backend=self.name)

    def stream(self, text, speaker, **kwargs):
        payload = (text.encode("utf-8") + b"\0")
        midpoint = max(1, len(payload) // 2)
        yield payload[:midpoint]
        yield payload[midpoint:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "temp" / "live-smoke.json")
    args = parser.parse_args()
    speaker = SpeakerProfile("SPEAKER_00", ROOT / "speaker_reference_clips" / "SPEAKER_00.wav")
    session = StreamingSession(translator=EchoTranslation(), tts=ChunkTTS(), speaker=speaker, source_language="en", target_language="tr")
    first = list(session.push_text("Hello live dubbing."))
    second = list(session.push_text("A second phrase arrives."))
    all_chunks = first + second
    payload = {
        "status": "passed" if all_chunks and len(all_chunks) == len(set(all_chunks)) else "failed",
        "phrases": session.metrics.phrases,
        "chunks": len(all_chunks),
        "duplicate_pcm_chunks": len(all_chunks) - len(set(all_chunks)),
        "first_audio_seconds": session.metrics.first_audio_seconds,
        "translation_seconds": session.metrics.translation_seconds,
        "tts_seconds": session.metrics.tts_seconds,
        "emitted_pcm_bytes": session.metrics.emitted_pcm_bytes,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
