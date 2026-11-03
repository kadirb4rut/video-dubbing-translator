#!/usr/bin/env python3
"""Real file-fed live-dubbing smoke test.

This deliberately processes only a short prefix of the existing fixture.  It
feeds microphone-sized PCM chunks through VAD, faster-whisper, online speaker
tracking, local Hy-MT2, and the real Modal native VoxCPM2 streaming function.
The output is a diagnostic JSON record; it is not a substitute for a browser
full-duplex session.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
import sys

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.contracts import SpeakerProfile, TTSBackend, TranslationBackend  # noqa: E402
from engine.streaming import OnlineSpeakerTracker, StreamingSession  # noqa: E402
from translation.backends import HyMT2Backend  # noqa: E402


class ModalStreamingTTS(TTSBackend):
    """Adapter around the already-deployed native Modal generator."""

    name = "voxcpm2-modal-native-stream"

    def stream(self, text, speaker, **kwargs):
        # Resolve the deployed generator by name so this standalone script can
        # call the warm Modal function outside ``modal run`` hydration.
        import modal

        stream_fn = modal.Function.from_name("video-dubbing-translator", "stream_pcm")
        reference = Path(speaker.reference_wav).read_bytes()
        for chunk in stream_fn.remote_gen(text, reference, speaker.transcript):
            yield bytes(chunk)

    def synthesize(self, text, speaker, **kwargs):  # pragma: no cover - live path uses stream
        raise NotImplementedError("live smoke uses streaming only")


def _resample_mono(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if source_rate == target_rate:
        return audio.astype(np.float32, copy=False)
    target_length = max(1, round(len(audio) * target_rate / source_rate))
    source_positions = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
    target_positions = np.linspace(0.0, 1.0, num=target_length, endpoint=False)
    return np.interp(target_positions, source_positions, audio).astype(np.float32)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, default=ROOT / "original_wav" / "test_multispeaker.wav")
    parser.add_argument("--seconds", type=float, default=12.0, help="Short fixture prefix to feed (default: 12s).")
    parser.add_argument("--chunk-ms", type=int, default=320, help="Microphone-like input chunk size.")
    parser.add_argument("--whisper-model", default="tiny", help="faster-whisper model size or local path.")
    parser.add_argument("--output", type=Path, default=ROOT / "temp" / "live-file-smoke.json")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.seconds <= 0 or args.chunk_ms <= 0:
        raise SystemExit("--seconds and --chunk-ms must be positive")
    audio, source_rate = sf.read(args.audio, dtype="float32")
    audio = _resample_mono(np.asarray(audio), int(source_rate), 16_000)
    audio = audio[: max(1, int(args.seconds * 16_000))]
    pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype(np.int16).tobytes()

    from faster_whisper import WhisperModel

    whisper = WhisperModel(args.whisper_model, device="cpu", compute_type="int8")
    asr_calls = []

    def asr(phrase_pcm: bytes) -> str:
        started = time.perf_counter()
        values = np.frombuffer(phrase_pcm, dtype=np.int16).astype(np.float32) / 32768.0
        segments, info = whisper.transcribe(values, language="en", beam_size=1, vad_filter=False, condition_on_previous_text=False)
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        asr_calls.append({"wall_seconds": time.perf_counter() - started, "text": text, "language": getattr(info, "language", None), "audio_seconds": len(values) / 16_000.0})
        return text

    # The custom embedder is intentionally not used here: this is the real
    # MFCC-statistics tracker operating on the actual source PCM.
    tracker = OnlineSpeakerTracker(sample_rate=16_000, match_threshold=0.78)
    speaker = SpeakerProfile("LIVE_SPEAKER_00", ROOT / "speaker_reference_clips" / "SPEAKER_00.wav", "So it's a big fan and that's really what got me into AI was, it's actually a long story.")
    # MPS keeps the real local Hy-MT2 step bounded while remaining the same
    # validated 1.8B model used by the integrated pipeline.
    translator: TranslationBackend = HyMT2Backend(device="mps")
    tts = ModalStreamingTTS()
    session = StreamingSession(
        translator=translator,
        tts=tts,
        speaker=speaker,
        source_language="en",
        target_language="tr",
        asr=asr,
        target_buffer_ms=120,
        speaker_tracker=tracker,
    )
    emitted: list[bytes] = []
    chunk_size = max(2, int(16_000 * args.chunk_ms / 1000) * 2)
    started = time.perf_counter()
    for offset in range(0, len(pcm), chunk_size):
        emitted.extend(session.push_audio(pcm[offset : offset + chunk_size]))
    emitted.extend(session.flush_audio())
    total_audio_seconds = len(b"".join(emitted)) / (48_000 * 2)
    duplicate_chunks = len(emitted) - len({bytes(chunk) for chunk in emitted})
    cumulative_repeats = sum(
        1 for index, chunk in enumerate(emitted) if any(bytes(chunk) == b"".join(emitted[:prior + 1]) for prior in range(index))
    )
    metrics = session.metrics
    payload = {
        "status": "passed" if metrics.phrases and emitted and duplicate_chunks == 0 and cumulative_repeats == 0 else "failed",
        "audio_fixture": str(args.audio),
        "fed_seconds": len(audio) / 16_000.0,
        "input_chunk_ms": args.chunk_ms,
        "asr": {"backend": "faster-whisper", "model": args.whisper_model, "calls": asr_calls},
        "speaker_tracking": {"backend": "qa-mfcc-statistics", "history": [{"speaker_id": item.speaker_id, "similarity": item.similarity, "is_new": item.is_new} for item in tracker.history], "speakers": tracker.snapshot()},
        "translation_backend": translator.name,
        "tts_backend": tts.name,
        "metrics": {
            "phrases": metrics.phrases,
            "speech_end_to_asr_seconds": metrics.speech_end_to_asr_seconds,
            "asr_to_translation_seconds": metrics.asr_to_translation_seconds,
            "translation_to_tts_request_seconds": metrics.translation_to_tts_seconds,
            "tts_ttfa_seconds": metrics.first_audio_seconds,
            "playback_queue_seconds": metrics.playback_queue_seconds,
            "total_phrase_latency_seconds": metrics.end_to_end_phrase_seconds,
            "tts_wall_seconds": metrics.tts_seconds,
            "generated_audio_seconds": total_audio_seconds,
            "rtf": metrics.tts_seconds / max(total_audio_seconds, 1e-9),
            "emitted_pcm_bytes": metrics.emitted_pcm_bytes,
            "wall_seconds": time.perf_counter() - started,
        },
        "pcm_integrity": {"chunks": len(emitted), "duplicate_chunks": duplicate_chunks, "cumulative_chunk_repeats": cumulative_repeats, "sample_rate": 48_000, "sample_width": 2},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    translator.close()
    return 0 if payload["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
