from __future__ import annotations

import gc
import inspect
import math
import os
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
    if settings.translation_provider == "aws-translate":
        return AwsTranslateProvider()
    return ConfiguredTranslationProvider()
