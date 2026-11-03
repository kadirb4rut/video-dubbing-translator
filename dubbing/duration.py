"""Closed-loop duration control with bounded final tempo correction."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
import shutil
from typing import Callable, Mapping, Optional

from translation.base import TranslationContext, Translator

from .references import SpeakerReference
from .segments import SpeechSegment
from .candidates import assess_timing_candidate, choose_best_timing_candidate


@dataclass(frozen=True)
class SynthesisResult:
    translation: str
    output_path: Path
    initial_duration: float
    final_duration: float
    refined: bool
    refinement_passes: int
    selected_version: str = "initial"
    initial_tempo: float = 1.0
    final_tempo: float = 1.0
    timing_limited: bool = False
    refined_duration: Optional[float] = None
    preferred_duration: float = 0.0
    safe_available_duration: float = 0.0
    refinement_direction: str = "none"
    applied_tempo: float = 1.0
    synchronization_error: float = 0.0
    borrowed_silence: float = 0.0


def duration_ratio(actual_seconds: float, target_seconds: float) -> float:
    if actual_seconds <= 0 or target_seconds <= 0:
        raise ValueError("Actual and target durations must be positive.")
    return actual_seconds / target_seconds


def should_refine(
    actual_seconds: float, target_seconds: float, tolerance: float = 0.20
) -> bool:
    if tolerance < 0:
        raise ValueError("Duration tolerance cannot be negative.")
    return abs(duration_ratio(actual_seconds, target_seconds) - 1.0) > tolerance


def refinement_direction(
    actual_seconds: float,
    preferred_duration: float,
    *,
    ideal_min_tempo: float = 0.90,
    ideal_max_tempo: float = 1.10,
) -> str:
    """Return ``shorten``, ``expand``, or ``none`` for one feedback pass."""

    ratio = duration_ratio(actual_seconds, preferred_duration)
    if ratio > ideal_max_tempo:
        return "shorten"
    if ratio < ideal_min_tempo:
        return "expand"
    return "none"


def _timing_error(actual: float, target: float) -> float:
    return abs(duration_ratio(actual, target) - 1.0)


def _tempo(actual: float, target: float) -> float:
    return duration_ratio(actual, target)


def _copy_selected(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)


def synthesize_with_duration_refinement(
    *,
    segment: SpeechSegment,
    initial_translation: str,
    reference: SpeakerReference,
    output_path: Path,
    translator: Translator,
    source_language: Optional[str],
    target_language: str,
    synthesize: Callable[[str, SpeakerReference, Path], None],
    measure_duration: Callable[[Path], float],
    context: Optional[TranslationContext] = None,
    glossary: Optional[Mapping[str, str]] = None,
    tolerance: float = 0.20,
    max_refinement_passes: int = 1,
    target_duration_seconds: Optional[float] = None,
    preferred_duration_seconds: Optional[float] = None,
    safe_available_duration_seconds: Optional[float] = None,
    target_spoken_units: Optional[float] = None,
    min_tempo: float = 0.85,
    max_tempo: float = 1.25,
    ideal_min_tempo: float = 0.90,
    ideal_max_tempo: float = 1.10,
) -> SynthesisResult:
    """Synthesize, refine bidirectionally once, and keep the best safe WAV.

    ``target_duration_seconds`` remains a backwards-compatible alias for the
    preferred target. New callers should pass the original speech duration as
    ``preferred_duration_seconds`` and the larger hard window separately.
    """

    if max_refinement_passes < 0:
        raise ValueError("max_refinement_passes cannot be negative.")
    if (
        tolerance < 0
        or min_tempo <= 0
        or max_tempo <= 0
        or min_tempo > max_tempo
        or ideal_min_tempo <= 0
        or ideal_max_tempo < ideal_min_tempo
    ):
        raise ValueError("Invalid duration or tempo settings.")
    preferred = preferred_duration_seconds or target_duration_seconds or segment.duration
    safe_available = safe_available_duration_seconds or target_duration_seconds or preferred
    if preferred <= 0 or safe_available < preferred:
        raise ValueError("Preferred duration and safe timing window must be positive and ordered.")
    translation = initial_translation.strip()
    output_path = Path(output_path)
    initial_path = output_path.with_name(output_path.stem + ".initial" + output_path.suffix)
    refined_path = output_path.with_name(output_path.stem + ".refined" + output_path.suffix)
    try:
        synthesize(translation, reference, initial_path)
        initial_duration = measure_duration(initial_path)
        final_duration = initial_duration
        selected_path = initial_path
        selected_version = "initial"
        passes = 0
        refined_duration = None
        direction = refinement_direction(
            initial_duration,
            preferred,
            ideal_min_tempo=ideal_min_tempo,
            ideal_max_tempo=ideal_max_tempo,
        )
        if max_refinement_passes and direction != "none":
            corrected_units = target_spoken_units
            if corrected_units is None and context is not None:
                corrected_units = context.target_spoken_units
            if corrected_units is not None and initial_duration > 0:
                corrected_units *= preferred / initial_duration
            refinement_context = context
            if context is not None:
                refinement_context = replace(
                    context,
                    target_spoken_units=corrected_units,
                    available_duration_seconds=safe_available,
                    preferred_duration_seconds=preferred,
                    safe_available_duration_seconds=safe_available,
                    refinement_direction=direction,
                    candidate_style=f"duration_refinement_{direction}",
                )
            translation = translator.rewrite_for_duration(
                segment.text,
                translation,
                source_language,
                target_language,
                preferred,
                initial_duration,
                context=refinement_context,
                glossary=glossary,
            ).strip()
            synthesize(translation, reference, refined_path)
            refined_duration = measure_duration(refined_path)
            passes = 1

        protected = context.protected_tokens if context is not None else ()
        assessments = [
            assess_timing_candidate(
                version="initial",
                text=initial_translation.strip(),
                duration=initial_duration,
                preferred_duration=preferred,
                safe_available_duration=safe_available,
                protected=protected,
                min_tempo=min_tempo,
                max_tempo=max_tempo,
                ideal_min_tempo=ideal_min_tempo,
                ideal_max_tempo=ideal_max_tempo,
            )
        ]
        if refined_duration is not None:
            assessments.append(
                assess_timing_candidate(
                    version="refined",
                    text=translation,
                    duration=refined_duration,
                    preferred_duration=preferred,
                    safe_available_duration=safe_available,
                    protected=protected,
                    min_tempo=min_tempo,
                    max_tempo=max_tempo,
                    ideal_min_tempo=ideal_min_tempo,
                    ideal_max_tempo=ideal_max_tempo,
                )
            )
        selected = choose_best_timing_candidate(assessments)
        selected_path = refined_path if selected.version == "refined" else initial_path
        selected_version = selected.version
        final_duration = selected.duration
        if selected.version == "initial":
            translation = initial_translation.strip()

        _copy_selected(selected_path, output_path)
        final_tempo = selected.required_tempo
        initial_tempo = _tempo(initial_duration, preferred)
        timing_limited = not selected.hard_band or not selected.safe_fit
        return SynthesisResult(
            translation=translation,
            output_path=output_path,
            initial_duration=initial_duration,
            final_duration=final_duration,
            refined=passes > 0,
            refinement_passes=passes,
            selected_version=selected_version,
            initial_tempo=initial_tempo,
            final_tempo=final_tempo,
            timing_limited=timing_limited,
            refined_duration=refined_duration,
            preferred_duration=preferred,
            safe_available_duration=safe_available,
            refinement_direction=direction,
            applied_tempo=selected.applied_tempo,
            synchronization_error=selected.synchronization_error,
            borrowed_silence=selected.borrowed_silence,
        )
    finally:
        for candidate in (initial_path, refined_path):
            if candidate != output_path:
                candidate.unlink(missing_ok=True)
