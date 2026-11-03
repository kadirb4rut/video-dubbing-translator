"""Bounded live TTS text comparison, with original measurements retained."""
from dataclasses import replace
import subprocess


def tolerate_surface_difference(quality, *, assessment=None):
    # A text model may misclassify repetition as minor. Large character
    # differences never get this override, regardless of its answer.
    if (not assessment or quality.cer is None or not 0 < quality.cer <= .5
            or type(assessment.get('minor')) is not bool):
        return quality
    minor = assessment['minor']
    return replace(quality,
        hard_failures=(tuple(f for f in quality.hard_failures if f != 'high_wer') if minor else
                       tuple(dict.fromkeys((*quality.hard_failures, 'tts_text_meaning_mismatch')))),
        warnings=tuple(dict.fromkeys((*quality.warnings, 'live_model_assessed_minor_difference'))) if minor else quality.warnings,
        metadata={**quality.metadata, 'live_text_assessment':assessment,
                  'semantic_correctness_certified':False})


def retry_conservative_presentation(native_wav, source_seconds, *, original, timing, assess,
                                    maximum_tempo=1.20):
    """Try a bounded fallback presentation when the faster version fails QA.

    Defaults to the established 1.20x conservative cap. Callers may select the
    previous mode cap when a newly tested Low Latency cap fails, so a failed
    speed-up cannot silently regress below the prior accepted presentation.
    """
    failures = set(original.get('failures', ()))
    if (not failures or failures - {'high_wer', 'tts_text_meaning_mismatch'}
            or timing.get('tempo', 1.) <= maximum_tempo):
        return None
    from .timing import presentation_pcm
    result = {'selected':False, 'reason':'conservative_qa_pending', 'original':original}
    try:
        pcm, path, conservative_timing = presentation_pcm(
            native_wav, source_seconds, maximum_tempo=maximum_tempo)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(reason='conservative_unavailable', error=f'{type(exc).__name__}: {exc}')
        return result
    conservative = assess(pcm, path)
    result.update(reason='conservative_rejected' if conservative['failures'] else 'conservative_passed',
                  conservative=conservative, conservative_timing=conservative_timing)
    if not conservative['failures']:
        result.update(selected=True, pcm=pcm, path=path)
    return result


def retry_verified_presentation(native_wav, native_pcm, source_seconds, *, original, timing, assess):
    """Try one alternate stretch only after native and new audio both pass QA.

    The original rejection is never overridden for signal failures or a
    wrong-language result. A missing optional ffmpeg filter leaves the normal
    synthesis retry/caption path intact. ``assess`` is the same QA function
    used for the original presentation, including the native signal checks.
    """
    failures = tuple(original.get('failures', ()))
    if (not failures or set(failures) - {'high_wer', 'tts_text_meaning_mismatch'}
            or timing.get('tempo', 1.) <= 1.01):
        return None
    from .timing import presentation_pcm, rubberband_available
    if not rubberband_available():
        return {'selected':False, 'reason':'alternate_unavailable', 'original':original,
                'native':None, 'error':'ffmpeg rubberband filter is unavailable'}
    result = {'selected':False, 'reason':'native_rejected', 'original':original}
    native = assess(native_pcm, native_wav)
    result['native'] = native
    if native['failures']:
        return result
    try:
        pcm, path, alternate_timing = presentation_pcm(
            native_wav, source_seconds, maximum_tempo=1.15, method='rubberband')
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        result.update(reason='alternate_unavailable', error=f'{type(exc).__name__}: {exc}')
        return result
    alternate = assess(pcm, path)
    result.update(reason='alternate_rejected', alternate=alternate,
                  alternate_timing=alternate_timing)
    if not alternate['failures']:
        result.update(selected=True, reason='alternate_passed', pcm=pcm, path=path)
    return result
