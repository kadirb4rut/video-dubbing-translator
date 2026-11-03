"""Real stateful Silero VAD and rolling faster-whisper adapters (lazy imports)."""
from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
import numpy as np

from .adaptive import Word, source_text


class SileroStream:
    """Preserve recurrent state between 32 ms frames, including quiet frames."""

    frame_samples = 512

    def __init__(self, threshold=0.5):
        from faster_whisper.vad import get_vad_model
        self.model = get_vad_model()
        if hasattr(self.model, 'encoder_session'):
            self.backend = 'silero-v5-split'
        elif hasattr(self.model, 'session') and {item.name for item in self.model.session.get_inputs()} == {'input', 'h', 'c'}:
            self.backend = 'silero-v6-combined'
        else:
            raise RuntimeError('Unsupported Silero ONNX signature; no stateless VAD fallback was substituted')
        self.threshold = threshold
        self.state = np.zeros((2, 1, 128), dtype=np.float32)
        self.context = np.zeros((1, 64), dtype=np.float32)
        self.pending = np.empty(0, dtype=np.float32)
        self.samples = 0

    def push(self, pcm):
        values = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        self.pending = np.concatenate((self.pending, values))
        results = []
        while len(self.pending) >= self.frame_samples:
            frame, self.pending = self.pending[:512], self.pending[512:]
            window = np.concatenate((self.context, frame[None]), axis=1)
            if self.backend == 'silero-v5-split':
                encoded = self.model.encoder_session.run(None, {'input': window})[0]
                encoded = encoded.reshape(1, -1)
                probability, self.state = self.model.decoder_session.run(None, {'input': encoded, 'state': self.state})
            else:
                probability, h, c = self.model.session.run(None,
                    {'input': window, 'h': self.state[:1], 'c': self.state[1:]})
                self.state = np.concatenate((h, c), axis=0)
            self.context = frame[None, -64:]
            self.samples += 512
            value = float(np.asarray(probability).reshape(-1)[0])
            results.append((self.samples / 16000, value >= self.threshold, value))
        return results


@dataclass(frozen=True)
class Transcript:
    words: tuple[Word, ...]
    language: str
    language_probability: float
    mean_log_probability: float | None
    runtime_metrics: dict = field(default_factory=dict)
    worker_timing: dict = field(default_factory=dict)

    @property
    def text(self):
        return source_text(self.words)


class IncrementalWhisper:
    def __init__(self, model='small', device='cpu', *, model_instance=None):
        if model_instance is None:
            from faster_whisper import WhisperModel
            model_instance = WhisperModel(model, device=device,
                compute_type='float16' if device.startswith('cuda') else 'int8',
                local_files_only=True)
        self.model = model_instance

    def _transcribe_audio(self, audio, start, language, *, word_timestamps):
        segments, info = self.model.transcribe(audio, language=language, beam_size=3,
            word_timestamps=word_timestamps, vad_filter=False,
            condition_on_previous_text=False, hallucination_silence_threshold=0.6,
            temperature=0.0)
        words, logprobs = [], []
        for segment in segments:
            if segment.no_speech_prob > 0.6 and segment.avg_logprob < -1.0:
                continue
            logprobs.append(float(segment.avg_logprob))
            segment_words = segment.words or ()
            if word_timestamps:
                words.extend(Word(w.word.strip(), start + w.start, start + w.end, w.word)
                    for w in segment_words if w.word.strip())
            else:
                # If CTranslate2 cannot align a very short decoded segment,
                # preserve its recognized text as one indivisible timing unit.
                # This keeps endpointing conservative while allowing the same
                # ASR text to continue through translation or TTS QA.
                text = str(getattr(segment, 'text', '') or '').strip()
                if text:
                    surface = str(segment.text)
                    if words and surface and not surface[0].isspace():
                        surface = ' ' + surface
                    words.append(Word(text, start + float(segment.start),
                        start + float(segment.end), surface))
        return Transcript(tuple(words), info.language, float(info.language_probability),
                          float(np.mean(logprobs)) if logprobs else None)

    def transcribe(self, pcm: bytes, start: float = 0.0, language=None) -> Transcript:
        audio = np.frombuffer(pcm, dtype='<i2').astype(np.float32) / 32768
        try:
            return self._transcribe_audio(audio, start, language, word_timestamps=True)
        except IndexError as exc:
            message = str(exc)
            # faster-whisper 1.x can fail in add_word_timestamps when a short
            # utterance yields text but the alignment window has no encoder
            # frames. Retry only that known degenerate alignment case. ASR text,
            # target-language checks and all TTS QA gates remain enabled.
            if ('boolean index did not match indexed array' not in message
                    or 'size of axis is 0' not in message):
                raise
            transcript = self._transcribe_audio(audio, start, language,
                word_timestamps=False)
            return Transcript(transcript.words, transcript.language,
                transcript.language_probability, transcript.mean_log_probability,
                runtime_metrics={'word_timing_alignment_fallback': {
                    'reason': 'empty_alignment_frames',
                    'word_timestamps_requested': True,
                    'word_timestamps_used': False,
                    'text_redecoded': True}})
