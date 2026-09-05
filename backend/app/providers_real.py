from __future__ import annotations

import gc
import inspect
import math
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import ClassVar

from .config import settings
from .media import validate_output


class ProviderUnavailable(RuntimeError):
    """Raised when a configured provider cannot run in the current worker image."""


def validate_segments(segments: Sequence[dict]) -> list[dict]:
    validated: list[dict] = []
    previous_start = -1.0
    for index, segment in enumerate(segments):
        try:
            start = float(segment["start"])
            end = float(segment["end"])
            text = str(segment["text"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderUnavailable(f"Invalid transcript segment at index {index}") from exc
        if not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or start < previous_start or not text:
            raise ProviderUnavailable(f"Invalid transcript timing or text at index {index}")
        validated.append({**segment, "start": start, "end": end, "text": text})
        previous_start = start
    return validated


def _require(module: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise ProviderUnavailable(f"Python dependency '{module}' is not installed in this worker image") from exc


def _release_torch_memory() -> None:
    """Release stage model references and flush accelerator allocator caches."""
    gc.collect()
    try:
        import torch

        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            torch.cuda.empty_cache()
        mps = getattr(torch, "mps", None)
        if mps is not None and hasattr(mps, "empty_cache"):
            mps.empty_cache()
    except (ImportError, RuntimeError):
        # Memory cleanup is best effort; the provider operation has already
        # completed and must not fail because an optional allocator API is
        # unavailable on a CPU-only worker.
        pass


class WhisperTranscriptionProvider:
    name = "whisper"
    _models: ClassVar[dict[str, object]] = {}

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.whisper_model
        self.detected_language: str | None = None
        self.last_model_load_seconds = 0.0

    def release(self) -> None:
        self._models.pop(self.model_name, None)
        _release_torch_memory()

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> Sequence[dict]:
        whisper = _require("whisper")
        model = self._models.get(self.model_name)
        if model is None:
            started = time.monotonic()
            model = whisper.load_model(self.model_name)
            self.last_model_load_seconds = time.monotonic() - started
            self._models[self.model_name] = model
        result = model.transcribe(str(audio_path), language=language, verbose=False)
        self.detected_language = result.get("language")
        return [{"start": float(s["start"]), "end": float(s["end"]), "text": s["text"].strip()} for s in result.get("segments", []) if s.get("text", "").strip()]


class ConfiguredTranslationProvider:
    name = "configured-translation-api"

    def translate(self, segments: Sequence[dict], *, source: str, target: str) -> Sequence[dict]:
        if not settings.translation_api_url:
            raise ProviderUnavailable("TRANSLATION_API_URL is not configured; refusing to silently pass source text through")
        import httpx

        headers = {"Authorization": f"Bearer {settings.translation_api_key}"} if settings.translation_api_key else {}
        with httpx.Client(timeout=120) as client:
            response = client.post(settings.translation_api_url, json={"source": source, "target": target, "segments": list(segments)}, headers=headers)
            response.raise_for_status()
            payload = response.json()
        translated = payload.get("segments") if isinstance(payload, dict) else payload
        if not isinstance(translated, list) or len(translated) != len(segments):
            raise ProviderUnavailable("Translation provider returned an invalid segment payload")
        return validate_segments(translated)


class HyMT2TranslationProvider:
    """Self-hosted Hy-MT2 translation with deterministic dubbing segments.

    The model is loaded lazily so API/test processes do not need a local ML
    runtime. Dubbing uses one structured prompt per bounded batch; each source
    segment is identified by an immutable marker and the response is rejected
    if markers are missing, duplicated, reordered, or surrounded by filler.
    """

    name = "hymt2"
    _models: ClassVar[dict[tuple[str, str, str, str], tuple[object, object]]] = {}
    _supported_languages: ClassVar[set[str]] = {
        "ar", "bn", "bo", "cs", "de", "en", "es", "fa", "fr", "gu", "he", "hi", "id", "it", "ja", "kk", "km", "ko", "mn", "mr", "ms", "my", "nl", "pl", "pt", "ru", "ta", "te", "th", "tl", "tr", "uk", "ug", "vi", "yue", "zh", "zh-hant",
    }
    _language_names: ClassVar[dict[str, str]] = {
        "ar": "Arabic", "bn": "Bengali", "bo": "Tibetan", "cs": "Czech", "de": "German", "en": "English", "es": "Spanish", "fa": "Persian", "fr": "French", "gu": "Gujarati", "he": "Hebrew", "hi": "Hindi", "id": "Indonesian", "it": "Italian", "ja": "Japanese", "kk": "Kazakh", "km": "Khmer", "ko": "Korean", "mn": "Mongolian", "mr": "Marathi", "ms": "Malay", "my": "Burmese", "nl": "Dutch", "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "ta": "Tamil", "te": "Telugu", "th": "Thai", "tl": "Filipino", "tr": "Turkish", "uk": "Ukrainian", "ug": "Uyghur", "vi": "Vietnamese", "yue": "Cantonese", "zh": "Chinese", "zh-hant": "Traditional Chinese",
    }

    def __init__(self, model_name: str | None = None, *, device: str | None = None, dtype: str | None = None, tokenizer=None, model=None):
        self.model_name = model_name or settings.translation_model
        self.device = self._resolve_device(device or settings.translation_device)
        self.dtype_name = dtype or settings.translation_dtype
        self._tokenizer = tokenizer
        self._model = model
        self.last_model_load_seconds = 0.0
        self.last_metrics: dict[str, object] = {}

    @staticmethod
    def _resolve_device(value: str) -> str:
        if value != "auto":
            return value
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _dtype(self, torch):
        if self.dtype_name == "float32":
            return torch.float32
        if self.dtype_name in {"float16", "fp16"}:
            return torch.float16
        if self.dtype_name in {"bfloat16", "bf16"}:
            return torch.bfloat16
        # Hy-MT2 is published as BF16. Keeping that default on CPU is
        # important for the 1.8B checkpoint's memory envelope; operators can
        # opt into float32 explicitly when their CPU lacks BF16 support.
        return torch.bfloat16

    def _load(self) -> tuple[object, object]:
        if self._tokenizer is not None and self._model is not None:
            return self._tokenizer, self._model
        key = (self.model_name, settings.translation_model_revision, self.device, self.dtype_name)
        cached = self._models.get(key)
        if cached is not None:
            self._tokenizer, self._model = cached
            return cached
        torch = _require("torch")
        try:
            from transformers import AutoModelForCausalLM, AutoTokenizer
        except ImportError as exc:
            raise ProviderUnavailable("Transformers is not installed; install the full worker requirements for Hy-MT2") from exc
        started = time.monotonic()
        self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, revision=settings.translation_model_revision, trust_remote_code=True)
        kwargs = {"dtype": self._dtype(torch), "trust_remote_code": True}
        if self.device.startswith("cuda"):
            kwargs["device_map"] = "auto"
        self._model = AutoModelForCausalLM.from_pretrained(self.model_name, revision=settings.translation_model_revision, **kwargs)
        if not self.device.startswith("cuda"):
            self._model.to(self.device)
        self._model.eval()
        self.last_model_load_seconds = time.monotonic() - started
        self._models[key] = (self._tokenizer, self._model)
        return self._tokenizer, self._model

    def release(self) -> None:
        if self._tokenizer is None and self._model is None:
            return
        key = (self.model_name, settings.translation_model_revision, self.device, self.dtype_name)
        self._models.pop(key, None)
        self._tokenizer = None
        self._model = None
        _release_torch_memory()

    @classmethod
    def _normalize_language(cls, value: str) -> str:
        normalized = value.strip().lower().replace("_", "-")
        if normalized == "zh-hant":
            return normalized
        if normalized not in cls._supported_languages:
            raise ProviderUnavailable(f"Hy-MT2 does not support language code '{value}'")
        return normalized

    @staticmethod
    def _segment_id(segment: dict, index: int) -> str:
        value = str(segment.get("id") or f"seg-{index + 1:04d}").strip()
        if not value or any(char.isspace() for char in value) or ">" in value or "<" in value:
            raise ProviderUnavailable(f"Invalid translation segment id at index {index}")
        return value

    def _prompt(self, batch: Sequence[dict], *, source: str, target: str, context: str | None, glossary: Sequence[dict] | None, style: str | None, duration_aware: bool) -> str:
        source_name = self._language_names[source]
        target_name = self._language_names[target]
        lines = [
            f"Translate the following {source_name} subtitle segments into {target_name}.",
            "Return ONLY one translated line for every marker, preserving marker spelling and order exactly.",
            "Do not add commentary, explanations, speaker labels, or filler. Keep names, numbers, URLs, and product terms accurate.",
        ]
        if context:
            lines.extend(["[Surrounding Context]", context.strip()])
        if glossary:
            entries = [f"{item.get('source', '')} => {item.get('target', '')}" for item in glossary if item.get("source") and item.get("target")]
            if entries:
                lines.extend(["[Glossary]", *entries])
        if style:
            lines.append(f"[Style] {style}")
        if duration_aware:
            lines.append("Use natural, concise spoken language suitable for dubbing; do not omit meaning.")
        lines.append("[Segments]")
        for index, segment in enumerate(batch):
            lines.append(f"<SEG_{self._segment_id(segment, index)}> {segment['text'].strip()}")
        lines.append("[Output]")
        return "\n".join(lines)

    def _generate(self, prompt: str) -> str:
        tokenizer, model = self._load()
        torch = _require("torch")
        messages = [{"role": "user", "content": prompt}]
        inputs = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt")
        if hasattr(inputs, "to"):
            inputs = inputs.to(model.device)
        with torch.no_grad():
            kwargs = {"max_new_tokens": settings.translation_max_new_tokens, "do_sample": False}
            outputs = model.generate(**inputs, **kwargs)
        prompt_length = inputs["input_ids"].shape[-1]
        return tokenizer.decode(outputs[0][prompt_length:], skip_special_tokens=True).strip()

    def _parse(self, raw: str, batch: Sequence[dict]) -> list[str]:
        expected = [self._segment_id(segment, index) for index, segment in enumerate(batch)]
        pattern = re.compile("<SEG_([^>]+)>[ \\t]*(.*?)(?=\\n<SEG_[^>]+>|$)", re.DOTALL)
        matches = list(pattern.finditer(raw))
        if not matches or raw[: matches[0].start()].strip():
            raise ProviderUnavailable("Hy-MT2 returned commentary or a missing segment marker")
        remainder = pattern.sub("", raw).strip()
        if remainder:
            raise ProviderUnavailable("Hy-MT2 returned text outside segment markers")
        received = [match.group(1).strip() for match in matches]
        if received != expected or len(set(received)) != len(received):
            raise ProviderUnavailable("Hy-MT2 returned missing, duplicate, or reordered segment markers")
        texts = [match.group(2).strip() for match in matches]
        if any(not text for text in texts):
            raise ProviderUnavailable("Hy-MT2 returned an empty translated segment")
        return texts

    def _single_prompt(self, segment: dict, *, source: str, target: str, context: str | None, glossary: Sequence[dict] | None, style: str | None, duration_aware: bool) -> str:
        source_name = self._language_names[source]
        target_name = self._language_names[target]
        lines = [
            f"Translate this {source_name} spoken subtitle into {target_name}.",
            "Output only the translated sentence. Do not add a label, explanation, quotation marks, or commentary.",
            f"[Source] {segment['text'].strip()}",
        ]
        if context:
            lines.insert(2, f"[Context] {context.strip()}")
        if glossary:
            entries = [f"{item.get('source', '')} => {item.get('target', '')}" for item in glossary if item.get("source") and item.get("target")]
            if entries:
                lines.insert(2, "[Glossary] " + "; ".join(entries))
        if style:
            lines.insert(2, f"[Style] {style}")
        if duration_aware:
            lines.insert(2, "Use natural, concise spoken language suitable for dubbing; do not omit meaning.")
        return "\n".join(lines)

    @staticmethod
    def _parse_single(raw: str) -> str:
        text = raw.strip().strip('"').strip()
        if not text or "<SEG_" in text or "Translation:" in text or "Here is" in text:
            raise ProviderUnavailable("Hy-MT2 returned commentary or an invalid single-segment translation")
        if "\n" in text:
            raise ProviderUnavailable("Hy-MT2 returned multiple lines for a single segment")
        return text

    def _batches(self, segments: Sequence[dict]) -> list[list[dict]]:
        batches: list[list[dict]] = []
        current: list[dict] = []
        chars = 0
        for segment in segments:
            size = len(segment["text"])
            if current and (len(current) >= max(1, settings.translation_batch_size) or chars + size > settings.translation_max_chars_per_batch):
                batches.append(current)
                current, chars = [], 0
            current.append(segment)
            chars += size
        if current:
            batches.append(current)
        return batches

    def translate(self, segments: Sequence[dict], *, source: str, target: str) -> Sequence[dict]:
        return self.translate_segments(segments, source=source, target=target)

    def translate_segments(self, segments: Sequence[dict], *, source: str, target: str, context: str | None = None, glossary: Sequence[dict] | None = None, style: str | None = None, duration_aware: bool = False) -> Sequence[dict]:
        validated = validate_segments(segments)
        source = self._normalize_language(source)
        target = self._normalize_language(target)
        if source == target:
            return validated
        started = time.monotonic()
        retries = 0
        malformed = 0
        single_segment_fallbacks = 0
        translated: list[dict] = []
        batches = self._batches(validated)
        for batch in batches:
            prompt = self._prompt(batch, source=source, target=target, context=context, glossary=glossary, style=style, duration_aware=duration_aware)
            raw = self._generate(prompt)
            try:
                texts = self._parse(raw, batch)
            except ProviderUnavailable:
                malformed += 1
                if retries < settings.translation_max_retries:
                    retries += 1
                    repair = "Return only the corrected marked translations, with no commentary.\n" + raw[:6000]
                    try:
                        texts = self._parse(self._generate(repair), batch)
                    except ProviderUnavailable:
                        texts = []
                else:
                    texts = []
                if not texts:
                    # Hy-MT2's published translation instruction prefers a
                    # plain translation and may ignore output markers. Keep
                    # the logical batch/context contract, but isolate each
                    # line and wrap it with the deterministic source ID in
                    # our own result object rather than accepting ambiguous
                    # model output.
                    single_segment_fallbacks += len(batch)
                    texts = [
                        self._parse_single(
                            self._generate(
                                self._single_prompt(
                                    segment,
                                    source=source,
                                    target=target,
                                    context=context,
                                    glossary=glossary,
                                    style=style,
                                    duration_aware=duration_aware,
                                )
                            )
                        )
                        for segment in batch
                    ]
            translated.extend({**segment, "text": text} for segment, text in zip(batch, texts, strict=True))
        self.last_metrics = {
            "provider": self.name,
            "model": self.model_name,
            "model_revision": settings.translation_model_revision,
            "runtime": "transformers-in-process",
            "device": self.device,
            "dtype": self.dtype_name if self.dtype_name != "auto" else "bfloat16",
            "source_language": source,
            "target_language": target,
            "segment_count": len(validated),
            "batch_count": len(batches),
            "input_chars": sum(len(segment["text"]) for segment in validated),
            "output_chars": sum(len(segment["text"]) for segment in translated),
            "model_load_seconds": round(self.last_model_load_seconds, 4),
            "retry_count": retries,
            "malformed_response_count": malformed,
            "single_segment_fallback_count": single_segment_fallbacks,
            "wall_clock_seconds": round(time.monotonic() - started, 4),
            "duration_aware": duration_aware,
        }
        return validate_segments(translated)

    def rewrite_for_duration(self, text: str, *, target: str, max_seconds: float) -> str:
        target = self._normalize_language(target)
        prompt = (
            f"Rewrite the following {self._language_names[target]} dubbing line so it can be spoken naturally in at most {max_seconds:.2f} seconds. "
            "Preserve the meaning, names, numbers, and tone. Output only the rewritten line, with no explanation.\n"
            f"{text.strip()}"
        )
        rewritten = self._generate(prompt).strip()
        if not rewritten or "<SEG_" in rewritten:
            raise ProviderUnavailable("Hy-MT2 duration rewrite returned an invalid response")
        return rewritten


class AwsTranslateProvider:
    """Translate segments through Amazon Translate while preserving source timing."""

    name = "aws-translate"

    def __init__(self, client=None):
        if client is None:
            import boto3

            client = boto3.client("translate", region_name=settings.s3_region)
        self.client = client

    def translate(self, segments: Sequence[dict], *, source: str, target: str) -> Sequence[dict]:
        translated = []
        for segment in validate_segments(segments):
            try:
                response = self.client.translate_text(
                    Text=segment["text"],
                    SourceLanguageCode=source,
                    TargetLanguageCode=target,
                )
                text = str(response.get("TranslatedText", "")).strip()
            except Exception as exc:
                raise ProviderUnavailable("AWS Translate request failed") from exc
            if not text:
                raise ProviderUnavailable("AWS Translate returned empty text")
            translated.append({**segment, "text": text})
        return validate_segments(translated)


class ChatterboxMultilingualVoiceProvider:
    name = "chatterbox-multilingual-v3"
    _models: ClassVar[dict[tuple[str, str], object]] = {}

    def __init__(self, device: str | None = None):
        self.device = device or settings.chatterbox_device
        self.last_model_load_seconds = 0.0

    def release(self) -> None:
        self._models.pop((self.device, "v3"), None)
        _release_torch_memory()

    def synthesize(self, text: str, *, reference_voice: Path | None, language: str, output_path: Path) -> Path:
        if reference_voice is not None and not reference_voice.is_file():
            raise ProviderUnavailable("Reference voice file is missing")
        _require("torch")
        try:
            import soundfile as sf
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        except ImportError as exc:
            raise ProviderUnavailable("Chatterbox Multilingual and soundfile are not installed") from exc
        key = (self.device, "v3")
        model = self._models.get(key)
        if model is None:
            started = time.monotonic()
            loader = ChatterboxMultilingualTTS.from_pretrained
            if "t3_model" in inspect.signature(loader).parameters:
                model = loader(device=self.device, t3_model="v3")
            else:
                # Current Chatterbox checks the string value for CPU/MPS before
                # choosing map_location; passing torch.device('cpu') would
                # accidentally try to deserialize CUDA checkpoints on CPU.
                model = loader(device=self.device)
            self.last_model_load_seconds = time.monotonic() - started
            self._models[key] = model
        output_path.parent.mkdir(parents=True, exist_ok=True)
        wav = model.generate(text, language_id=language, audio_prompt_path=str(reference_voice) if reference_voice else None)
        # torchaudio 2.10 delegates save() to TorchCodec, whose native wheel
        # is not ABI-compatible with every pinned PyTorch worker build. The
        # provider only needs a real PCM WAV here, so use libsndfile directly
        # while retaining torchaudio for Chatterbox's runtime contract.
        waveform = wav.detach().cpu().squeeze(0).numpy() if hasattr(wav, "detach") else wav
        sf.write(str(output_path), waveform, model.sr, subtype="PCM_16")
        validate_output(output_path, "audio")
        return output_path


class DemucsStemSeparationProvider:
    name = "demucs"

    def separate(self, audio_path: Path, *, stems: int, output_dir: Path) -> dict[str, Path]:
        if stems not in {2, 4}:
            raise ValueError("Demucs stem count must be 2 or 4")
        _require("demucs")
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [sys.executable, "-m", "demucs.separate", "-n", settings.demucs_model, "-o", str(output_dir)]
        if stems == 2:
            command.extend(["--two-stems", "vocals"])
        command.append("--mp3")
        command.append(str(audio_path))
        subprocess.run(command, check=True, timeout=3600)
        model_dir = output_dir / settings.demucs_model / audio_path.stem
        demucs_names = ("vocals", "no_vocals") if stems == 2 else ("vocals", "drums", "bass", "other")
        encoded = {name: model_dir / f"{name}.mp3" for name in demucs_names}
        missing = [name for name, path in encoded.items() if not path.is_file()]
        if missing:
            raise ProviderUnavailable(f"Demucs completed without expected stems: {', '.join(missing)}")
        expected = {}
        for name, mp3_path in encoded.items():
            output_name = "instrumental" if name == "no_vocals" else name
            wav_path = model_dir / f"{output_name}.wav"
            subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mp3_path), "-ar", "48000", "-ac", "2", str(wav_path)], check=True, timeout=600)
            validate_output(wav_path, "audio")
            expected[output_name] = wav_path
        return expected


class DeepFilterNetNoiseProvider:
    name = "deepfilternet"

    def enhance(self, audio_path: Path, *, output_path: Path) -> Path:
        command = shutil.which(settings.deepfilter_command)
        if not command:
            fallback = os.getenv("NOISE_REMOVAL_FALLBACK", settings.noise_removal_fallback)
            if fallback == "ffmpeg-afftdn":
                output_path.parent.mkdir(parents=True, exist_ok=True)
                ffmpeg = shutil.which("ffmpeg") or "ffmpeg"
                subprocess.run(
                    [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(audio_path), "-af", "afftdn=nf=-25", str(output_path)],
                    check=True,
                    timeout=3600,
                )
                validate_output(output_path, "audio")
                return output_path
            raise ProviderUnavailable(f"{settings.deepfilter_command} is not installed in this worker image")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([command, "-m", "DeepFilterNet3", "-o", str(output_path.parent), str(audio_path)], check=True, timeout=3600)
        produced = output_path.parent / audio_path.name
        if produced != output_path and produced.is_file():
            produced.replace(output_path)
        if not output_path.is_file():
            raise ProviderUnavailable("DeepFilterNet did not produce the requested output")
        validate_output(output_path, "audio")
        return output_path


def write_srt(segments: Sequence[dict], output_path: Path) -> Path:
    segments = validate_segments(segments)

    def stamp(seconds: float) -> str:
        millis = max(0, round(seconds * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        seconds, millis = divmod(millis, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        for index, segment in enumerate(segments, start=1):
            handle.write(f"{index}\n{stamp(float(segment['start']))} --> {stamp(float(segment['end']))}\n{segment['text'].strip()}\n\n")
    return output_path


def write_vtt(segments: Sequence[dict], output_path: Path) -> Path:
    segments = validate_segments(segments)

    def stamp(seconds: float) -> str:
        millis = max(0, round(seconds * 1000))
        hours, millis = divmod(millis, 3_600_000)
        minutes, millis = divmod(millis, 60_000)
        seconds, millis = divmod(millis, 1_000)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}"

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as handle:
        handle.write("WEBVTT\n\n")
        for segment in segments:
            handle.write(f"{stamp(float(segment['start']))} --> {stamp(float(segment['end']))}\n{segment['text'].strip()}\n\n")
    return output_path


def write_txt(segments: Sequence[dict], output_path: Path) -> Path:
    segments = validate_segments(segments)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(segment["text"].strip() for segment in segments).strip() + "\n", encoding="utf-8")
    return output_path


class FixtureTranslationProvider:
    """Deterministic fixture translator for tests; unknown text fails loudly."""

    name = "fixture-translation"
    development_only = True
    _phrases: ClassVar[dict[tuple[str, str, str], str]] = {
        ("hello world", "en", "es"): "hola mundo",
        ("hello", "en", "es"): "hola",
        ("this is a test", "en", "es"): "esto es una prueba",
    }

    def translate(self, segments: Sequence[dict], *, source: str, target: str) -> Sequence[dict]:
        translated = []
        for segment in segments:
            key = (segment["text"].strip().lower(), source, target)
            if key not in self._phrases:
                raise ProviderUnavailable("Fixture translator has no mapping for this segment; configure TRANSLATION_API_URL for production")
            translated.append({**segment, "text": self._phrases[key]})
        return translated


def translation_provider():
    if settings.translation_provider == "fixture":
        return FixtureTranslationProvider()
    if settings.translation_provider == "hymt2":
        return HyMT2TranslationProvider()
    if settings.translation_provider == "aws-translate":
        return AwsTranslateProvider()
    return ConfiguredTranslationProvider()
