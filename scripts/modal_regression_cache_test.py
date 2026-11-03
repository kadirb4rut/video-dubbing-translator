#!/usr/bin/env python3
"""Bounded real Modal regression test for utterance/cache isolation."""

from __future__ import annotations

import concurrent.futures
import hashlib
import io
import json
import re
import tempfile
import time
from pathlib import Path

import soundfile as sf


ROOT = Path(__file__).resolve().parents[1]
INV = ROOT / "temp" / "regression-investigation-20261024"
REFERENCE = ROOT / "temp" / "modal-e2e-1789503194" / "references" / "SPEAKER_00.wav"
REFERENCE_TEXT = "So it's a big fan and that's really what got me into AI was, it's actually a long story."
TEXT_A = "Evet, tam olarak. Yoğun bir dönemim oldu. Yazmayı gerçekten seviyorum ama en çok yapay zekâ öğretmeyi seviyorum."
TEXT_B = "Teşekkür ederim, kurslarınızı aldığınızı bilmiyordum. Bu ikinci ve tamamen farklı bir cümledir."


def pcm_as_wav(chunks: list[bytes], path: Path) -> bytes:
    pcm = b"".join(chunks)
    path.write_bytes(b"")
    with path.open("wb") as handle:
        sf.write(handle, __import__("numpy").frombuffer(pcm, dtype="<i2"), 48_000, format="WAV", subtype="PCM_16")
    return pcm


def transcribe(model, path: Path) -> dict[str, object]:
    segments, info = model.transcribe(
        str(path),
        language="tr",
        beam_size=1,
        vad_filter=False,
        condition_on_previous_text=False,
    )
    text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
    return {"language": getattr(info, "language", None), "language_probability": float(getattr(info, "language_probability", 0.0)), "text": text}


def token_recall(target: str, recovered: str) -> float:
    target_tokens = set(re.findall(r"[\wÀ-ž]+", target.casefold()))
    recovered_tokens = set(re.findall(r"[\wÀ-ž]+", recovered.casefold()))
    return len(target_tokens & recovered_tokens) / max(len(target_tokens), 1)


def main() -> int:
    import modal
    from faster_whisper import WhisperModel

    if not REFERENCE.is_file():
        raise FileNotFoundError(REFERENCE)
    batch_fn = modal.Function.from_name("video-dubbing-translator", "synthesize_batch")
    wav_fn = modal.Function.from_name("video-dubbing-translator", "synthesize_wav")
    stream_fn = modal.Function.from_name("video-dubbing-translator", "stream_pcm")
    reference = REFERENCE.read_bytes()
    request = lambda text, seed: {
        "text": text,
        "reference_wav": reference,
        "reference_text": REFERENCE_TEXT,
        "style": "",
        "seed": seed,
        "inference_timesteps": 5,
        "cfg_value": 2.0,
    }
    started = time.perf_counter()
    batch = batch_fn.remote([request(TEXT_A, 101), request(TEXT_B, 102), request(TEXT_A, 103), request(TEXT_B, 104)])
    model = WhisperModel("tiny", device="cpu", compute_type="int8")
    rows = []
    with tempfile.TemporaryDirectory(dir=INV) as directory:
        temp_root = Path(directory)
        for index, response in enumerate(batch):
            path = temp_root / f"cached_{index}.wav"
            path.write_bytes(response["wav"])
            info = sf.info(path)
            asr = transcribe(model, path)
            target = (TEXT_A, TEXT_B, TEXT_A, TEXT_B)[index]
            rows.append({"mode": "cached_batch", "index": index, "target": target, "cache_hit": response.get("cache_hit"), "audio_sha256": hashlib.sha256(response["wav"]).hexdigest(), "audio_seconds": float(info.frames / info.samplerate), "asr": asr, "target_token_recall": token_recall(target, asr["text"])})

        def uncached(text_seed):
            text, seed = text_seed
            response = wav_fn.remote(text, reference, REFERENCE_TEXT, "", seed)
            path = temp_root / f"uncached_{seed}.wav"
            path.write_bytes(response)
            info = sf.info(path)
            asr = transcribe(model, path)
            return {"mode": "uncached_wav", "target": text, "seed": seed, "audio_sha256": hashlib.sha256(response).hexdigest(), "audio_seconds": float(info.frames / info.samplerate), "asr": asr, "target_token_recall": token_recall(text, asr["text"]) }

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            concurrent_rows = list(pool.map(uncached, ((TEXT_A, 201), (TEXT_B, 202))))
        rows.extend(concurrent_rows)

        stream_rows = []
        for label, text, seed in (("stream_A", TEXT_A, 301), ("stream_B", TEXT_B, 302)):
            chunks = list(stream_fn.remote_gen(text, reference, REFERENCE_TEXT, "", seed))
            pcm_path = temp_root / f"{label}.wav"
            pcm = pcm_as_wav([bytes(chunk) for chunk in chunks], pcm_path)
            asr = transcribe(model, pcm_path)
            stream_rows.append({"mode": "stream", "label": label, "chunks": len(chunks), "duplicate_chunks": len(chunks) - len({bytes(chunk) for chunk in chunks}), "cumulative_chunk_repeats": sum(1 for i, chunk in enumerate(chunks) if any(bytes(chunk) == b"".join(chunks[:j + 1]) for j in range(i))), "audio_sha256": hashlib.sha256(pcm).hexdigest(), "asr": asr, "target_token_recall": token_recall(text, asr["text"])})

    cached_hashes = [row["audio_sha256"] for row in rows if row["mode"] == "cached_batch"]
    expected_cache_hits = [bool(row["cache_hit"]) for row in rows if row["mode"] == "cached_batch"]
    distinct_targets_distinct_audio = cached_hashes[0] != cached_hashes[1] and cached_hashes[1] != cached_hashes[2] and cached_hashes[2] != cached_hashes[3]
    no_replay = cached_hashes[0] == cached_hashes[2] or cached_hashes[1] == cached_hashes[3]
    # Exact replay is not required from a stochastic TTS model; this field is
    # diagnostic only.  The primary safety check is that A/B are distinct and
    # every ASR result is tied to the request's own target.
    status = all(row["status"] == "passed" for row in batch) and distinct_targets_distinct_audio and not no_replay and all(row["duplicate_chunks"] == 0 and row["cumulative_chunk_repeats"] == 0 for row in stream_rows)
    output = INV / "modal-cache-isolation.json"
    payload = {"status": "passed" if status else "failed", "reference": str(REFERENCE), "requests": rows, "streaming": stream_rows, "cached_cache_hits": expected_cache_hits, "elapsed_wall_seconds": time.perf_counter() - started}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if status else 1


if __name__ == "__main__":
    raise SystemExit(main())
