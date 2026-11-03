"""Small pitch-preserving correction; never truncate speech to fit video."""
from pathlib import Path
from functools import lru_cache
import math
import subprocess
import time
import wave


@lru_cache(maxsize=1)
def rubberband_available():
    """Check the installed ffmpeg once before attempting the optional filter."""
    try:
        result = subprocess.run(['ffmpeg', '-hide_banner', '-filters'],
            capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and any(
        len(parts) > 1 and parts[1] == 'rubberband'
        for parts in (line.split() for line in result.stdout.splitlines()))


def presentation_pcm(native_wav, source_seconds, *, maximum_tempo=1.20,
                    speed_factor=1.0, method='atempo'):
    if (not math.isfinite(source_seconds) or source_seconds <= 0
            or not math.isfinite(maximum_tempo) or not 1 <= maximum_tempo <= 1.40
            or not math.isfinite(speed_factor) or not 1 <= speed_factor <= 1.40
            or method not in ('atempo', 'rubberband')):
        raise ValueError('Invalid bounded live timing correction')
    native_wav = Path(native_wav)

    def read(path):
        with wave.open(str(path), 'rb') as audio:
            if (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) != (48000, 1, 2):
                raise ValueError('Live presentation requires mono 48 kHz PCM16')
            data = audio.readframes(audio.getnframes())
        if not data:
            raise ValueError('Empty live presentation audio')
        return data

    data = read(native_wav)
    native_seconds = len(data)/96000
    source_fit_tempo = min(maximum_tempo, max(1., native_seconds/source_seconds))
    tempo = min(maximum_tempo, source_fit_tempo*speed_factor)
    stats = {'native_seconds':native_seconds, 'maximum_tempo':maximum_tempo,
             'requested_speed_factor':speed_factor,
             'applied_speed_factor':tempo/source_fit_tempo,
             'tempo':1., 'method':method, 'pitch_preserving':True, 'processing_seconds':0.}
    output = native_wav
    if tempo > 1.01:
        suffix = '.presentation.wav' if method == 'atempo' else '.presentation.rubberband.wav'
        output = native_wav.with_name(native_wav.stem+suffix)
        started = time.perf_counter()
        audio_filter = (f'atempo={tempo:.8f}' if method == 'atempo'
                        else f'rubberband=tempo={tempo:.8f}')
        process = subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(native_wav),
            '-af', audio_filter, '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(output)],
            capture_output=True, text=True, timeout=10)
        if process.returncode:
            raise RuntimeError('Live timing correction failed: '+process.stderr[-500:])
        data = read(output)
        stats.update(tempo=tempo, processing_seconds=time.perf_counter()-started)
    stats['presentation_seconds'] = len(data)/96000
    return data, output, stats
