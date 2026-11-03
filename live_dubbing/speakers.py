"""Conservative online embedding matching; uncertain voices never poison caches."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import math
from pathlib import Path
import numpy as np


SPEAKER_SAMPLE_RATE = 16000
SPEAKER_MAX_WINDOW_SECONDS = 20.0
SPEAKER_WINDOW_OVERLAP_SECONDS = 1.0


def bounded_speaker_windows(pcm: bytes, *, max_seconds: float = SPEAKER_MAX_WINDOW_SECONDS,
                            overlap_seconds: float = SPEAKER_WINDOW_OVERLAP_SECONDS) -> list[bytes]:
    """Cover a source clip with bounded, ordered 16 kHz mono PCM windows.

    This bounds the isolated speaker worker's request size without trimming the
    source clip used for ASR, translation, or TTS. Adjacent windows overlap so
    a speaker transition at a window edge remains visible to the caller.
    """
    if not isinstance(pcm, bytes) or not pcm or len(pcm) % 2:
        raise ValueError('speaker audio must be non-empty 16-bit mono PCM')
    if (not np.isfinite(max_seconds) or not np.isfinite(overlap_seconds) or
            max_seconds <= 0 or overlap_seconds < 0 or overlap_seconds >= max_seconds):
        raise ValueError('invalid speaker analysis window bounds')
    samples = len(pcm) // 2
    max_samples = int(max_seconds * SPEAKER_SAMPLE_RATE)
    overlap_samples = int(overlap_seconds * SPEAKER_SAMPLE_RATE)
    if samples <= max_samples:
        return [pcm]
    count = max(2, math.ceil((samples - overlap_samples) /
                             (max_samples - overlap_samples)))
    window_samples = math.ceil((samples + (count - 1) * overlap_samples) / count)
    stride = window_samples - overlap_samples
    if window_samples > max_samples or stride <= 0:
        raise ValueError('could not bound speaker analysis windows')
    windows = []
    for index in range(count):
        start = index * stride
        end = min(samples, start + window_samples)
        if index == count - 1:
            end = samples
            start = max(0, end - window_samples)
        windows.append(pcm[start * 2:end * 2])
    if any(not window or len(window) > max_samples * 2 or len(window) % 2
           for window in windows):
        raise ValueError('speaker analysis window exceeded its PCM bound')
    return windows


def combine_speaker_window_results(results: list[dict], source_seconds: float) -> dict:
    """Fuse bounded speaker analyses conservatively across a longer utterance."""
    if not results or not np.isfinite(source_seconds) or source_seconds <= 0:
        raise ValueError('speaker analysis requires bounded windows and source duration')
    vectors = []
    durations = []
    cross_window_cosines = []
    for result in results:
        vector = np.asarray(result.get('embedding'), dtype=np.float32).reshape(-1)
        norm = float(np.linalg.norm(vector))
        if not vector.size or not np.all(np.isfinite(vector)) or norm <= 1e-9:
            raise ValueError('speaker window returned an invalid embedding')
        vectors.append(vector / norm)
        durations.append(max(1e-6, float(result.get('source_duration_seconds') or 0)))
    for first in range(len(vectors)):
        for second in range(first + 1, len(vectors)):
            if vectors[first].shape != vectors[second].shape:
                raise ValueError('speaker windows returned incompatible embeddings')
            cross_window_cosines.append(float(np.dot(vectors[first], vectors[second])))
    weighted = np.average(np.stack(vectors), axis=0, weights=np.asarray(durations))
    weighted_norm = float(np.linalg.norm(weighted))
    if weighted_norm <= 1e-9:
        raise ValueError('speaker window embeddings could not be combined')
    reported_cosines = [float(result['half_embedding_cosine']) for result in results
                        if result.get('half_embedding_cosine') is not None]
    reported_cosines.extend(cross_window_cosines)
    half_cosine = min(reported_cosines) if reported_cosines else None
    overlap_fraction = max(float(result.get('overlap_fraction') or 0) for result in results)
    mixed_turn = (any(bool(result.get('mixed_turn')) for result in results) or
                  any(cosine < .35 for cosine in cross_window_cosines))
    return {
        'embedding': (weighted / weighted_norm).tolist(),
        'overlap': any(bool(result.get('overlap')) for result in results),
        'overlap_fraction': overlap_fraction,
        'mixed_turn': mixed_turn,
        'half_embedding_cosine': half_cosine,
        'valid_segmentation_frames': sum(int(result.get('valid_segmentation_frames') or 0)
                                         for result in results),
        'source_duration_seconds': float(source_seconds),
        'analysis_window_count': len(results),
        'overlap_scope': 'max bounded-window overlap; window-edge transitions compared',
        'backend': results[0].get('backend', 'community1-online-embedding-and-segmentation'),
    }


@dataclass(frozen=True)
class SpeakerChoice:
    speaker_id: str
    reference: Path | None
    transcript: str
    similarity: float | None
    reason: str
    reference_sha256: str | None = None


@dataclass
class _Profile:
    identifier: str
    embedding: np.ndarray
    path: Path
    text: str
    sha256: str


def segment_reference_fallback(pcm: bytes, text: str, directory: Path, identifier: str,
                               *, min_seconds: float = 3.0) -> SpeakerChoice:
    """Use only the current clean utterance as a temporary voice reference.

    This preserves the existing per-segment cloning fallback when online
    speaker models are unavailable. It never assigns a stable speaker ID or
    inserts the clip into SpeakerTracker's cross-phrase profiles.
    """
    if not isinstance(pcm, bytes) or len(pcm) % 2:
        raise ValueError('fallback speaker audio must be bounded 16-bit mono PCM')
    duration = len(pcm) / 32000
    if not text.strip() or duration < min_seconds:
        return SpeakerChoice('UNCONFIRMED', None, '', None,
            'speaker_models_unavailable_short_segment_no_reference')
    values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
    if (not values.size or not np.all(np.isfinite(values)) or
            float(np.mean(np.abs(values) > .995)) > .001 or
            float(np.sqrt(np.mean(values ** 2))) < .008):
        return SpeakerChoice('UNCONFIRMED', None, '', None,
            'speaker_models_unavailable_unclean_segment_no_reference')
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{identifier}.wav'
    import soundfile as sf
    sf.write(path, values, 16000, subtype='PCM_16')
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return SpeakerChoice('UNCONFIRMED', path, text, None,
        'speaker_models_unavailable_current_segment_reference_only', digest)


class SpeakerTracker:
    def __init__(self, directory: Path, *, threshold=0.65, margin=0.08, max_speakers=16, min_reference_seconds=3.0):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.threshold, self.margin, self.max_speakers = threshold, margin, max_speakers
        self.min_reference_seconds = min_reference_seconds
        self.profiles: list[_Profile] = []

    def select(self, pcm, text, embedding, *, overlap=False, mixed_turn=False) -> SpeakerChoice:
        duration = len(pcm) / 32000
        if overlap or mixed_turn:
            return SpeakerChoice('UNCONFIRMED', None, '', None, 'overlap_or_mixed_turn_no_reference_update')
        vector = np.asarray(embedding, dtype=np.float32).reshape(-1)
        if not vector.size or not np.all(np.isfinite(vector)) or not np.linalg.norm(vector):
            return SpeakerChoice('UNCONFIRMED', None, '', None, 'unusable_embedding')
        vector = vector / np.linalg.norm(vector)
        ranked = sorted(((float(np.dot(vector, p.embedding)), p) for p in self.profiles), key=lambda x: x[0], reverse=True)
        if ranked and ranked[0][0] >= self.threshold:
            similarity, profile = ranked[0]
            if len(ranked) > 1 and similarity - ranked[1][0] < self.margin:
                return SpeakerChoice('UNCONFIRMED', None, '', similarity, 'ambiguous_speaker_margin')
            # A short interjection may match, but never updates an immutable reference.
            return SpeakerChoice(profile.identifier, profile.path, profile.text, similarity, 'embedding_match', profile.sha256)
        if duration < self.min_reference_seconds or len(self.profiles) >= self.max_speakers:
            return SpeakerChoice('UNCONFIRMED', None, '', ranked[0][0] if ranked else None, 'short_or_capacity_limited_temporary_base_voice')
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        if float(np.mean(np.abs(values) > .995)) > .001 or float(np.sqrt(np.mean(values ** 2))) < .008:
            return SpeakerChoice('UNCONFIRMED', None, '', None, 'unclean_reference_temporary_base_voice')
        import soundfile as sf
        identifier = f'LIVE_SPEAKER_{len(self.profiles):02d}'
        path = self.directory / f'{identifier}.wav'
        sf.write(path, values, 16000, subtype='PCM_16')
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.profiles.append(_Profile(identifier, vector.copy(), path, text, digest))
        return SpeakerChoice(identifier, path, text, None, 'new_clean_immutable_reference', digest)


class PyannoteOnlineModels:
    """Use Community-1's local embedding/segmentation models, not offline clustering.

    Created in the existing isolated diarization runtime by speaker_worker.py.
    The segmentation model provides actual overlapping-speech evidence; simple
    cosine similarity is not called an overlap probability.
    """

    def __init__(self, model_directory, device='cpu'):
        import torch
        from pyannote.audio import Model, Inference
        from pyannote.audio.pipelines.speaker_verification import PretrainedSpeakerEmbedding
        root = Path(model_directory)
        for part in ('embedding', 'segmentation'):
            if not (root / part / 'pytorch_model.bin').is_file():
                raise FileNotFoundError(f'Install Community-1 first; missing local {part} checkpoint')
        self.device = torch.device(device)
        self.embedding = PretrainedSpeakerEmbedding(str(root / 'embedding/pytorch_model.bin'), device=self.device)
        segmentation = Model.from_pretrained(str(root / 'segmentation/pytorch_model.bin'))
        self.segmentation = Inference(segmentation, device=self.device, skip_aggregation=True)

    def analyze(self, pcm):
        import torch
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        tensor = torch.from_numpy(values.copy())[None, None]
        embedding = np.asarray(self.embedding(tensor))[0]
        scores = self.segmentation({'waveform': tensor[0], 'sample_rate': 16000})
        # Inference converts powerset classes to per-speaker activations.
        array = np.asarray(scores.data)
        if array.ndim != 3:
            raise RuntimeError('Unsupported chunk/frame/speaker segmentation output')
        simultaneous = np.sum(array > .5, axis=-1) >= 2
        # skip_aggregation=True retains padded model frames. Do not let
        # padding dilute (or create) measured overlap in a short real clip.
        frames = self.segmentation.model.receptive_field
        chunks = scores.sliding_window
        centers = (chunks.start + np.arange(array.shape[0])[:, None] * chunks.step +
                   frames.start + np.arange(array.shape[1])[None, :] * frames.step + frames.duration / 2)
        valid = (centers >= 0) & (centers <= len(values) / 16000)
        overlap_fraction = float(np.mean(simultaneous[valid])) if np.any(valid) else 0.0
        mixed_turn = False
        half_cosine = None
        # A real 2.3-second transition contained two distinct speakers.
        # Give each half at least one second of acoustic evidence; shorter
        # windows remain subject to overlap and uncertain-reference guards.
        if len(values) >= 2 * 16000:
            half = len(values) // 2
            a = np.asarray(self.embedding(tensor[..., :half]))[0]
            b = np.asarray(self.embedding(tensor[..., half:]))[0]
            cosine = float(np.dot(a, b) / max(1e-9, np.linalg.norm(a) * np.linalg.norm(b)))
            half_cosine = cosine
            mixed_turn = cosine < .35
        return {'embedding': embedding.tolist(), 'overlap': overlap_fraction > .04,
                'overlap_fraction': overlap_fraction, 'mixed_turn': mixed_turn,
                'half_embedding_cosine': half_cosine,
                'valid_segmentation_frames': int(np.sum(valid)),
                'source_duration_seconds': len(values) / 16000,
                'overlap_scope': 'valid model frames; short-context calibration is limited',
                'backend': 'community1-online-embedding-and-segmentation'}
