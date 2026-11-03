"""Phrase-level live dubbing primitives (VAD → ASR prefix → translation → TTS)."""

from __future__ import annotations

import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Iterable, Iterator, Mapping, Optional, Sequence

import numpy as np

from .contracts import ProsodyProfile, SpeakerProfile, TTSBackend, TranslationBackend
from .qa import cosine_similarity, speaker_embedding_from_pcm


@dataclass(frozen=True)
class LatencyMetrics:
    first_audio_seconds: Optional[float] = None
    asr_seconds: float = 0.0
    translation_seconds: float = 0.0
    tts_seconds: float = 0.0
    emitted_pcm_bytes: int = 0
    phrases: int = 0
    speech_end_to_asr_seconds: float = 0.0
    asr_to_translation_seconds: float = 0.0
    translation_to_tts_seconds: float = 0.0
    playback_queue_seconds: float = 0.0
    end_to_end_phrase_seconds: float = 0.0
    last_phrase_latency_seconds: Optional[float] = None
    last_speaker_id: Optional[str] = None
    speaker_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LiveSpeakerAssignment:
    """Online speaker identity selected for one finalized live phrase."""

    speaker_id: str
    similarity: Optional[float]
    is_new: bool
    embedding: tuple[float, ...] = ()


@dataclass
class _LiveSpeaker:
    speaker_id: str
    centroid: tuple[float, ...]
    reference_pcm: bytes
    phrase_count: int = 1


class OnlineSpeakerTracker:
    """Conservative online speaker clustering for finalized live phrases.

    The tracker reuses the deterministic MFCC-statistics embedding used by
    offline TTS QA.  It keeps the first clean exemplar as the prompt-cache
    seed and updates only a centroid, so a noisy later phrase cannot silently
    replace a known-good voice reference.
    """

    def __init__(
        self,
        *,
        sample_rate: int = 16_000,
        match_threshold: float = 0.78,
        min_reference_seconds: float = 0.40,
        embedder: Optional[Callable[[bytes, int], Sequence[float]]] = None,
    ) -> None:
        if sample_rate <= 0:
            raise ValueError("sample_rate must be positive")
        if not 0.0 <= match_threshold <= 1.0:
            raise ValueError("match_threshold must be between 0 and 1")
        if min_reference_seconds < 0:
            raise ValueError("min_reference_seconds must be non-negative")
        self.sample_rate = int(sample_rate)
        self.match_threshold = float(match_threshold)
        self.min_reference_seconds = float(min_reference_seconds)
        self._embedder = embedder or speaker_embedding_from_pcm
        self._speakers: dict[str, _LiveSpeaker] = {}
        self._next_id = 0
        self.history: list[LiveSpeakerAssignment] = []

    @property
    def speakers(self) -> tuple[str, ...]:
        return tuple(self._speakers)

    def _new_id(self) -> str:
        speaker_id = f"LIVE_SPEAKER_{self._next_id:02d}"
        self._next_id += 1
        return speaker_id

    @staticmethod
    def _centroid(left: Sequence[float], right: Sequence[float], count: int) -> tuple[float, ...]:
        if len(left) != len(right):
            return tuple(float(value) for value in left)
        values = [(float(a) * count + float(b)) / (count + 1) for a, b in zip(left, right)]
        norm = float(np.linalg.norm(values))
        return tuple(value / norm for value in values) if norm else tuple(values)

    def assign(self, pcm16: bytes, *, sample_rate: Optional[int] = None) -> LiveSpeakerAssignment:
        rate = int(sample_rate or self.sample_rate)
        if rate <= 0:
            raise ValueError("sample_rate must be positive")
        values = np.frombuffer(pcm16 or b"", dtype=np.int16)
        if values.size == 0:
            raise ValueError("cannot identify an empty live phrase")
        embedding = tuple(float(value) for value in self._embedder(bytes(pcm16), rate))
        if not embedding:
            raise ValueError("speaker embedder returned an empty vector")
        best_id, best_similarity = None, None
        for speaker_id, profile in self._speakers.items():
            similarity = cosine_similarity(embedding, profile.centroid)
            if similarity is not None and (best_similarity is None or similarity > best_similarity):
                best_id, best_similarity = speaker_id, similarity
        if best_id is not None and best_similarity is not None and best_similarity >= self.match_threshold:
            profile = self._speakers[best_id]
            profile.centroid = self._centroid(profile.centroid, embedding, profile.phrase_count)
            profile.phrase_count += 1
            assignment = LiveSpeakerAssignment(best_id, best_similarity, False, embedding)
            self.history.append(assignment)
            return assignment
        speaker_id = self._new_id()
        self._speakers[speaker_id] = _LiveSpeaker(speaker_id, embedding, bytes(pcm16))
        assignment = LiveSpeakerAssignment(speaker_id, None, True, embedding)
        self.history.append(assignment)
        return assignment

    def reference_pcm(self, speaker_id: str) -> bytes:
        try:
            return self._speakers[speaker_id].reference_pcm
        except KeyError as exc:
            raise KeyError(f"unknown live speaker: {speaker_id}") from exc

    def snapshot(self) -> list[Mapping[str, object]]:
        return [
            {"speaker_id": profile.speaker_id, "phrase_count": profile.phrase_count, "reference_bytes": len(profile.reference_pcm)}
            for profile in self._speakers.values()
        ]


class PhraseVAD:
    """Lightweight energy VAD suitable for local streaming and tests."""

    def __init__(self, sample_rate: int = 16_000, frame_ms: int = 30, silence_ms: int = 420, threshold: float = 0.012):
        self.sample_rate, self.frame_samples = sample_rate, max(1, sample_rate * frame_ms // 1000)
        self.silence_frames = max(1, silence_ms // frame_ms)
        self.threshold = threshold
        self._buffer = np.array([], dtype=np.int16)
        self._phrase_frames: list[np.ndarray] = []
        self._silence = 0
        self._active = False

    def feed(self, pcm: bytes, *, sample_width: int = 2) -> list[bytes]:
        if sample_width != 2:
            raise ValueError("PhraseVAD expects PCM16 input")
        values = np.frombuffer(pcm, dtype=np.int16)
        self._buffer = np.concatenate([self._buffer, values])
        phrases = []
        while len(self._buffer) >= self.frame_samples:
            frame, self._buffer = self._buffer[: self.frame_samples], self._buffer[self.frame_samples :]
            voiced = float(np.sqrt(np.mean(np.square(frame.astype(np.float32) / 32768.0)))) >= self.threshold
            if voiced:
                self._active = True
                self._silence = 0
                self._phrase_frames.append(frame.copy())
            elif self._active:
                self._phrase_frames.append(frame.copy())
                self._silence += 1
                if self._silence >= self.silence_frames:
                    phrases.append(self._finish_phrase())
        return [phrase for phrase in phrases if phrase]

    def _finish_phrase(self) -> bytes:
        parts = self._phrase_frames
        self._phrase_frames = []
        self._silence = 0
        self._active = False
        if not parts:
            return b""
        return np.concatenate(parts).astype(np.int16, copy=False).tobytes()

    def flush(self) -> bytes:
        phrase = self._finish_phrase()
        remainder = self._buffer.astype(np.int16, copy=False).tobytes()
        self._buffer = np.array([], dtype=np.int16)
        return phrase + remainder


class StablePrefixBuffer:
    """Keep only the stable prefix of rolling ASR hypotheses."""

    def __init__(self):
        self._stable = ""

    @property
    def stable(self) -> str:
        return self._stable

    def update(self, hypothesis: str) -> str:
        candidate = " ".join((hypothesis or "").split())
        if not self._stable:
            # Hold the final word until a delimiter or a subsequent hypothesis
            # confirms it; this prevents duplicate words at chunk boundaries.
            words = candidate.split()
            self._stable = " ".join(words[:-1]) if len(words) > 1 else ""
            return self._stable
        prefix = ""
        for left, right in zip(self._stable.split(), candidate.split()):
            if left != right:
                break
            prefix = (prefix + " " + left).strip()
        if candidate.startswith(self._stable):
            prefix = self._stable
        if re.search(r"[.!?。！？]$", candidate):
            prefix = candidate
        self._stable = prefix
        return prefix

    def consume(self) -> str:
        value, self._stable = self._stable, ""
        return value


class JitterBuffer:
    """Sequence-aware PCM jitter buffer that drops duplicate chunks."""

    def __init__(self, target_chunks: int = 2):
        self.target_chunks = max(0, int(target_chunks))
        self._chunks: dict[int, bytes] = {}
        self._next = 0

    def add(self, sequence: int, pcm: bytes) -> None:
        if sequence < self._next:
            return
        self._chunks.setdefault(int(sequence), bytes(pcm))

    def pop_ready(self, *, force: bool = False) -> list[bytes]:
        result = []
        while self._next in self._chunks and (force or len(self._chunks) > self.target_chunks):
            result.append(self._chunks.pop(self._next))
            self._next += 1
        return result

    def flush(self) -> list[bytes]:
        return self.pop_ready(force=True)


class StreamingSession:
    """Phrase-level, not full-duplex, live dubbing session."""

    def __init__(self, *, translator: TranslationBackend, tts: TTSBackend, speaker: SpeakerProfile, source_language: str, target_language: str, asr: Optional[Callable[[bytes], str]] = None, target_buffer_ms: int = 120, speaker_tracker: Optional[OnlineSpeakerTracker] = None):
        self.translator, self.tts, self.speaker = translator, tts, speaker
        self.source_language, self.target_language, self.asr = source_language, target_language, asr
        self.speaker_tracker = speaker_tracker
        self.vad = PhraseVAD()
        self.asr_prefix = StablePrefixBuffer()
        self.jitter = JitterBuffer(target_chunks=max(0, target_buffer_ms // 40))
        self.started = time.perf_counter()
        self.metrics = LatencyMetrics()
        self._sequence = 0
        self._pending_live_timings: dict[str, float] = {}
        self.active_speaker_id = speaker.speaker_id

    def process_phrase(self, source_text: str) -> Iterator[bytes]:
        if not source_text.strip():
            return
        phrase_started = time.perf_counter()
        pending = self._pending_live_timings
        translation_started = time.perf_counter()
        target_text = self.translator.translate(source_text, self.source_language, self.target_language)
        translation_finished = time.perf_counter()
        translation_time = translation_finished - translation_started
        tts_started = time.perf_counter()
        first = True
        total = 0
        first_audio_at = None
        for pcm in self.tts.stream(target_text, self.speaker):
            self.jitter.add(self._sequence, pcm)
            self._sequence += 1
            for ready in self.jitter.pop_ready():
                if first:
                    first = False
                    first_audio_at = time.perf_counter()
                total += len(ready)
                yield ready
        for ready in self.jitter.flush():
            if first:
                first = False
                first_audio_at = time.perf_counter()
            total += len(ready)
            yield ready
        phrase_finished = time.perf_counter()
        first_audio_seconds = self.metrics.first_audio_seconds
        if first_audio_seconds is None and first_audio_at is not None:
            first_audio_seconds = first_audio_at - self.started
        asr_seconds = pending.get("asr_seconds", 0.0)
        speech_end_to_asr = pending.get("speech_end_to_asr_seconds", 0.0)
        asr_to_translation = max(0.0, translation_started - pending.get("asr_finished_at", translation_started)) if pending else 0.0
        translation_to_tts = max(0.0, tts_started - translation_finished)
        playback_queue = max(0.0, first_audio_at - tts_started) if first_audio_at is not None else 0.0
        speaker_ids = self.metrics.speaker_ids
        if self.active_speaker_id not in speaker_ids:
            speaker_ids = speaker_ids + (self.active_speaker_id,)
        self.metrics = LatencyMetrics(
            first_audio_seconds=first_audio_seconds,
            asr_seconds=self.metrics.asr_seconds + asr_seconds,
            translation_seconds=self.metrics.translation_seconds + translation_time,
            tts_seconds=self.metrics.tts_seconds + phrase_finished - tts_started,
            emitted_pcm_bytes=self.metrics.emitted_pcm_bytes + total,
            phrases=self.metrics.phrases + 1,
            speech_end_to_asr_seconds=self.metrics.speech_end_to_asr_seconds + speech_end_to_asr,
            asr_to_translation_seconds=self.metrics.asr_to_translation_seconds + asr_to_translation,
            translation_to_tts_seconds=self.metrics.translation_to_tts_seconds + translation_to_tts,
            playback_queue_seconds=self.metrics.playback_queue_seconds + playback_queue,
            end_to_end_phrase_seconds=self.metrics.end_to_end_phrase_seconds + phrase_finished - phrase_started,
            last_phrase_latency_seconds=phrase_finished - phrase_started,
            last_speaker_id=self.active_speaker_id,
            speaker_ids=speaker_ids,
        )
        self._pending_live_timings = {}

    def push_audio(self, pcm16: bytes) -> Iterator[bytes]:
        if self.asr is None:
            raise RuntimeError("StreamingSession requires an ASR callable for push_audio")
        for phrase in self.vad.feed(pcm16):
            speech_end = time.perf_counter()
            if self.speaker_tracker is not None:
                assignment = self.speaker_tracker.assign(phrase)
                self.active_speaker_id = assignment.speaker_id
            asr_started = time.perf_counter()
            text = self.asr(phrase)
            asr_finished = time.perf_counter()
            self._pending_live_timings = {
                "speech_end_to_asr_seconds": max(0.0, asr_started - speech_end),
                "asr_seconds": asr_finished - asr_started,
                "asr_finished_at": asr_finished,
            }
            yield from self.process_phrase(text)

    def flush_audio(self) -> Iterator[bytes]:
        """Finalize any speech remaining at end-of-stream/file.

        File-fed and microphone sessions both need an explicit end marker;
        without this method a final phrase shorter than the silence endpoint
        would be silently dropped.
        """
        if self.asr is None:
            raise RuntimeError("StreamingSession requires an ASR callable for flush_audio")
        phrase = self.vad.flush()
        if not phrase:
            return
        speech_end = time.perf_counter()
        if self.speaker_tracker is not None:
            assignment = self.speaker_tracker.assign(phrase)
            self.active_speaker_id = assignment.speaker_id
        asr_started = time.perf_counter()
        text = self.asr(phrase)
        asr_finished = time.perf_counter()
        self._pending_live_timings = {
            "speech_end_to_asr_seconds": max(0.0, asr_started - speech_end),
            "asr_seconds": asr_finished - asr_started,
            "asr_finished_at": asr_finished,
        }
        yield from self.process_phrase(text)

    def push_text(self, source_text: str) -> Iterator[bytes]:
        """Testable/low-latency path when an upstream ASR provides text."""
        yield from self.process_phrase(source_text)
