from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from .config import settings


class ProviderUnavailable(RuntimeError):
    """Raised when a configured provider cannot run in the current worker image."""


def _require(module: str):
    try:
        return __import__(module)
    except ImportError as exc:
        raise ProviderUnavailable(f"Python dependency '{module}' is not installed in this worker image") from exc


class WhisperTranscriptionProvider:
    name = "whisper"
    _models: dict[str, object] = {}

    def __init__(self, model_name: str | None = None):
        self.model_name = model_name or settings.whisper_model

    def transcribe(self, audio_path: Path, *, language: str | None = None) -> Sequence[dict]:
        whisper = _require("whisper")
        model = self._models.get(self.model_name)
        if model is None:
            model = whisper.load_model(self.model_name)
            self._models[self.model_name] = model
        result = model.transcribe(str(audio_path), language=language, verbose=False)
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
        return translated


class ChatterboxMultilingualVoiceProvider:
    name = "chatterbox-multilingual-v3"
    _models: dict[tuple[str, str], object] = {}

    def __init__(self, device: str | None = None):
        self.device = device or settings.chatterbox_device

    def synthesize(self, text: str, *, reference_voice: Path, language: str, output_path: Path) -> Path:
        if not reference_voice.is_file():
            raise ProviderUnavailable("Reference voice file is missing")
        _require("torch")
        try:
            from chatterbox.mtl_tts import ChatterboxMultilingualTTS
            import torchaudio
        except ImportError as exc:
            raise ProviderUnavailable("Chatterbox Multilingual and torchaudio are not installed") from exc
        key = (self.device, "v3")
        model = self._models.get(key)
        if model is None:
            model = ChatterboxMultilingualTTS.from_pretrained(device=self.device, t3_model="v3")
            self._models[key] = model
        output_path.parent.mkdir(parents=True, exist_ok=True)
        wav = model.generate(text, language_id=language, audio_prompt_path=str(reference_voice))
        torchaudio.save(str(output_path), wav, model.sr)
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
        command.append(str(audio_path))
        subprocess.run(command, check=True, timeout=3600)
        model_dir = output_dir / settings.demucs_model / audio_path.stem
        if stems == 2:
            expected = {"vocals": model_dir / "vocals.wav", "no_vocals": model_dir / "no_vocals.wav"}
        else:
            expected = {name: model_dir / f"{name}.wav" for name in ("vocals", "drums", "bass", "other")}
        missing = [name for name, path in expected.items() if not path.is_file()]
        if missing:
            raise ProviderUnavailable(f"Demucs completed without expected stems: {', '.join(missing)}")
        return expected


class DeepFilterNetNoiseProvider:
    name = "deepfilternet"

    def enhance(self, audio_path: Path, *, output_path: Path) -> Path:
        command = shutil.which(settings.deepfilter_command)
        if not command:
            raise ProviderUnavailable(f"{settings.deepfilter_command} is not installed in this worker image")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run([command, "-m", "DeepFilterNet3", "-o", str(output_path.parent), str(audio_path)], check=True, timeout=3600)
        produced = output_path.parent / audio_path.name
        if produced != output_path and produced.is_file():
            produced.replace(output_path)
        if not output_path.is_file():
            raise ProviderUnavailable("DeepFilterNet did not produce the requested output")
        return output_path


def write_srt(segments: Sequence[dict], output_path: Path) -> Path:
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
