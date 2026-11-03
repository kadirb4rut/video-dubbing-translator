"""Dependency-light acoustic prosody extraction and matching."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from .contracts import ProsodyProfile


def _frames(values: np.ndarray, count: int = 24) -> tuple[float, ...]:
    if values.size == 0:
        return ()
    if values.size <= count:
        return tuple(float(x) for x in values)
    indices = np.linspace(0, values.size - 1, count).astype(int)
    return tuple(float(values[index]) for index in indices)


def extract_prosody(path: str | Path, *, normalize: bool = True) -> ProsodyProfile:
    """Extract F0, energy, rate, pauses, and voiced ratio from a WAV file.

    ``librosa.pyin`` is used when available; a robust zero-crossing fallback
    keeps QA useful on minimal installations.  All features are acoustic and
    reproducible—no opaque emotion classifier is required.
    """
    import soundfile as sf

    audio, sample_rate = sf.read(Path(path), always_2d=False, dtype="float32")
    samples = np.asarray(audio, dtype=np.float32)
    if samples.ndim > 1:
        samples = samples.mean(axis=1)
    if samples.size == 0:
        return ProsodyProfile(0.0, 0.0)
    duration = samples.size / float(sample_rate)
    frame_length = max(256, int(round(sample_rate * 0.04)))
    hop = max(128, frame_length // 2)
    try:
        import librosa

        rms = librosa.feature.rms(y=samples, frame_length=frame_length, hop_length=hop, center=True)[0]
        rms_db = librosa.amplitude_to_db(np.maximum(rms, 1e-8), ref=1.0)
        try:
            f0, _, voiced_prob = librosa.pyin(
                samples,
                fmin=librosa.note_to_hz("C2"),
                fmax=librosa.note_to_hz("C7"),
                sr=sample_rate,
                frame_length=frame_length,
                hop_length=hop,
            )
            voiced = np.isfinite(f0) & (np.nan_to_num(voiced_prob) >= 0.35)
            f0_values = np.asarray(f0[voiced], dtype=np.float32)
            voiced_ratio = float(np.mean(voiced)) if voiced.size else 0.0
        except Exception:
            f0_values = np.array([], dtype=np.float32)
            voiced_ratio = 0.0
    except Exception:
        # A small NumPy-only path for deployments that intentionally omit
        # librosa.  It still reports signal energy and pause structure.
        windows = [samples[i : i + frame_length] for i in range(0, len(samples), hop)]
        rms = np.array([np.sqrt(np.mean(np.square(w))) if len(w) else 0 for w in windows])
        rms_db = 20 * np.log10(np.maximum(rms, 1e-8))
        f0_values = np.array([], dtype=np.float32)
        voiced_ratio = float(np.mean(rms > np.max(rms) * 0.08)) if rms.size else 0.0

    median_f0 = float(np.median(f0_values)) if f0_values.size else 0.0
    pitch_range = float(np.percentile(f0_values, 90) - np.percentile(f0_values, 10)) if f0_values.size > 1 else 0.0
    mean_rms_db = float(np.mean(rms_db)) if rms_db.size else -60.0
    threshold = max(-50.0, float(np.percentile(rms_db, 30)) if rms_db.size else -50.0)
    silent = rms_db < threshold
    pause_durations: list[float] = []
    run = 0
    for is_silent in silent:
        run = run + 1 if bool(is_silent) else 0
        if not is_silent and run:
            pause_durations.append(run * hop / float(sample_rate))
            run = 0
    if run:
        pause_durations.append(run * hop / float(sample_rate))
    intensity_trend = float(np.polyfit(np.arange(rms_db.size), rms_db, 1)[0] * rms_db.size) if rms_db.size > 1 else 0.0
    speaking_rate = float(max(0.1, voiced_ratio * 2.0))
    if normalize and median_f0 > 0:
        # Per-speaker normalization keeps pitch comparisons meaningful across
        # speakers while retaining contour shape and range as metadata.
        contour = np.asarray(f0_values, dtype=np.float32) / median_f0
        normalized_median = 1.0
        normalized_range = pitch_range / median_f0 if median_f0 else 0.0
    else:
        contour = f0_values
        normalized_median, normalized_range = median_f0, pitch_range
    return ProsodyProfile(
        duration_seconds=duration,
        median_f0_hz=normalized_median,
        f0_contour_hz=_frames(contour),
        pitch_range_hz=float(normalized_range),
        rms_db=mean_rms_db,
        energy_contour_db=_frames(np.asarray(rms_db)),
        speaking_rate=speaking_rate,
        pause_durations=tuple(float(max(0.0, p)) for p in pause_durations),
        voiced_ratio=float(np.clip(voiced_ratio, 0.0, 1.0)),
        intensity_trend_db=intensity_trend,
        normalized=normalize,
        metadata={"sample_rate": int(sample_rate), "frame_length": frame_length, "hop_length": hop},
    )


def prosody_distance(source: ProsodyProfile, target: ProsodyProfile) -> float:
    """Return a bounded weighted acoustic distance (lower is better)."""
    terms = [
        (abs(source.pitch_range_hz - target.pitch_range_hz), 0.22),
        (abs(source.rms_db - target.rms_db) / 30.0, 0.18),
        (abs(source.speaking_rate - target.speaking_rate), 0.22),
        (abs(source.voiced_ratio - target.voiced_ratio), 0.16),
        (abs(source.intensity_trend_db - target.intensity_trend_db) / 10.0, 0.12),
        (abs(len(source.pause_durations) - len(target.pause_durations)) / 10.0, 0.10),
    ]
    return float(min(1.0, sum(value * weight for value, weight in terms)))


def style_instruction(profile: ProsodyProfile, *, source: Optional[ProsodyProfile] = None) -> str:
    """Describe acoustic style for VoxCPM2 controllable cloning."""
    pace = "slower" if profile.speaking_rate < 0.85 else "faster" if profile.speaking_rate > 1.15 else "natural pace"
    energy = "intense" if profile.rms_db > -18 else "soft" if profile.rms_db < -32 else "conversational"
    trend = "building intensity" if profile.intensity_trend_db > 2 else "winding down" if profile.intensity_trend_db < -2 else "steady intensity"
    return f"{pace}, {energy} delivery, {trend}, preserve the speaker's natural pauses"
