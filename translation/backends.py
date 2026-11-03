"""Translation backends and the explicit language capability matrix."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

from engine.contracts import BackendUnavailable, TranslationBackend
from .base import TranslationContext, glossary_preservation
from .hymt2 import HyMT2Translator


VOXCPM2_LANGUAGES = (
    "ar", "my", "zh", "da", "nl", "en", "fi", "fr", "de", "el",
    "he", "hi", "id", "it", "ja", "km", "ko", "lo", "ms", "no",
    "pl", "pt", "ru", "es", "sw", "sv", "tl", "th", "tr", "vi",
)

# These are the seven languages intentionally routed to the optional local
# M2M100 fallback. Hy-MT2 remains the primary model for every other language.
M2M100_FALLBACK_LANGUAGES = ("da", "fi", "el", "lo", "no", "sw", "sv")
HYMT2_LANGUAGES = tuple(language for language in VOXCPM2_LANGUAGES if language not in M2M100_FALLBACK_LANGUAGES)
LANGUAGE_NAMES = {
    "ar": "Arabic", "my": "Burmese", "zh": "Chinese", "da": "Danish",
    "nl": "Dutch", "en": "English", "fi": "Finnish", "fr": "French",
    "de": "German", "el": "Greek", "he": "Hebrew", "hi": "Hindi",
    "id": "Indonesian", "it": "Italian", "ja": "Japanese", "km": "Khmer",
    "ko": "Korean", "lo": "Lao", "ms": "Malay", "no": "Norwegian",
    "pl": "Polish", "pt": "Portuguese", "ru": "Russian", "es": "Spanish",
    "sw": "Swahili", "sv": "Swedish", "tl": "Tagalog / Filipino", "th": "Thai",
    "tr": "Turkish", "vi": "Vietnamese",
}


def normalize_language(code: str) -> str:
    value = (code or "").strip().lower().replace("_", "-")
    aliases = {"fil": "tl", "fil-ph": "tl", "tl-ph": "tl", "zh-cn": "zh", "nb": "no"}
    return aliases.get(value, value.split("-", 1)[0])


@dataclass
class HyMT2Backend(TranslationBackend):
    """Adapter that keeps Hy-MT2 1.8B as the only primary local LLM."""

    device: str = "auto"
    model_key: str = "hymt2-1.8b"

    def __post_init__(self) -> None:
        if self.model_key != "hymt2-1.8b":
            raise ValueError("Only Hy-MT2 1.8B is supported by the production backend")
        self._translator = HyMT2Translator(self.model_key, self.device)
        self.name = "hymt2-1.8b"

    def translate(self, text: str, source_language: Optional[str], target_language: str, *, context=None, glossary: Optional[Mapping[str, str]] = None, deterministic: bool = False) -> str:
        return self._translator.translate(text, source_language, target_language,
            context=context, glossary=glossary, deterministic=deterministic)

    def rewrite_for_duration(self, *args, **kwargs) -> str:
        return self._translator.rewrite_for_duration(*args, **kwargs)

    def propose_simultaneous(self, *args, **kwargs) -> str:
        return self._translator.propose_simultaneous(*args, **kwargs)

    def prepare(self) -> None:
        self._translator.prepare()

    def close(self) -> None:
        self._translator.close()


class M2M100Backend(TranslationBackend):
    """Optional offline fallback for exactly seven Hy-MT2 gaps.

    Loading is lazy and therefore does not download weights during import.  A
    caller must install/cache ``facebook/m2m100_418M`` explicitly and pass
    ``local_files_only=True`` for a strictly self-hosted deployment.
    """

    name = "m2m100-418m"

    def __init__(self, model_id: str = "facebook/m2m100_418M", device: str = "cpu", local_files_only: bool = True):
        self.model_id, self.device, self.local_files_only = model_id, device, local_files_only
        self._tokenizer = None
        self._model = None

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
            import torch

            self._tokenizer = M2M100Tokenizer.from_pretrained(self.model_id, local_files_only=self.local_files_only)
            self._model = M2M100ForConditionalGeneration.from_pretrained(self.model_id, local_files_only=self.local_files_only)
            self._model.to(self.device if self.device != "auto" else ("cuda" if torch.cuda.is_available() else "cpu"))
            self._model.eval()
        except Exception as exc:
            raise BackendUnavailable(
                f"M2M100 fallback could not load: {type(exc).__name__}: {exc}. "
                "Check the cached model/tokenizer and runtime; no download was attempted."
            ) from exc

    def translate(self, text: str, source_language: Optional[str], target_language: str, *, context=None, glossary: Optional[Mapping[str, str]] = None) -> str:
        source = normalize_language(source_language or "en")
        target = normalize_language(target_language)
        if source not in VOXCPM2_LANGUAGES or target not in VOXCPM2_LANGUAGES:
            raise ValueError(f"Unsupported language pair: {source}->{target}")
        if target not in M2M100_FALLBACK_LANGUAGES and source not in M2M100_FALLBACK_LANGUAGES:
            raise ValueError("M2M100Backend is reserved for the seven documented fallback languages")
        self._load()
        self._tokenizer.src_lang = source
        encoded = self._tokenizer(text, return_tensors="pt")
        encoded = {key: value.to(self._model.device) for key, value in encoded.items()}
        forced = self._tokenizer.get_lang_id(target)
        generated = self._model.generate(**encoded, forced_bos_token_id=forced, max_new_tokens=256)
        result = self._tokenizer.batch_decode(generated, skip_special_tokens=True)[0].strip()
        missing = [term for term, valid in glossary_preservation(text, result, glossary).items() if not valid]
        if missing:
            raise ValueError('explicit_glossary_not_preserved: ' + ', '.join(missing))
        return result


class CapabilityRouter(TranslationBackend):
    """Route seven fallback languages to M2M100 while preserving Hy-MT2 primary."""

    name = "translation-router"

    def __init__(self, hymt2: TranslationBackend, fallback: Optional[TranslationBackend] = None):
        self.hymt2, self.fallback = hymt2, fallback

    def translate(self, text: str, source_language: Optional[str], target_language: str, *, context=None, glossary: Optional[Mapping[str, str]] = None, deterministic: bool = False) -> str:
        source, target = normalize_language(source_language or "en"), normalize_language(target_language)
        if (target in M2M100_FALLBACK_LANGUAGES or source in M2M100_FALLBACK_LANGUAGES) and self.fallback is not None:
            if deterministic:
                raise BackendUnavailable('Deterministic incremental translation is available only on Hy-MT2 routes')
            return self.fallback.translate(text, source, target, context=context, glossary=glossary)
        return self.hymt2.translate(text, source, target, context=context,
                                    glossary=glossary, deterministic=deterministic)

    def propose_simultaneous(self, *args, source_language=None, target_language=None, **kwargs) -> str:
        source = normalize_language(source_language or "en")
        target = normalize_language(target_language or "")
        if ((target in M2M100_FALLBACK_LANGUAGES or source in M2M100_FALLBACK_LANGUAGES)
                and self.fallback is not None):
            raise BackendUnavailable('Incremental READ/WAIT/WRITE is available only on Hy-MT2 routes')
        proposer = getattr(self.hymt2, 'propose_simultaneous', None)
        if not callable(proposer):
            raise BackendUnavailable('The selected translation backend cannot propose incremental chunks')
        return proposer(*args, source_language=source, target_language=target, **kwargs)

    def close(self) -> None:
        self.hymt2.close()
        if self.fallback is not None:
            self.fallback.close()


def capability_matrix() -> list[dict[str, object]]:
    return [
        {
            "code": code,
            "name": LANGUAGE_NAMES[code],
            "voxcpm2": True,
            "translation_backend": "m2m100-418m" if code in M2M100_FALLBACK_LANGUAGES else "hymt2-1.8b",
            "fallback": code in M2M100_FALLBACK_LANGUAGES,
        }
        for code in VOXCPM2_LANGUAGES
    ]
