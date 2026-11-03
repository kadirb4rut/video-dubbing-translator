"""Composable production engine that wires context, prosody, TTS, and QA."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Mapping, Optional

from .backends import BackendRouter
from .context import ContextMemory
from .prosody import extract_prosody, prosody_distance, style_instruction
from .qa import TTSQuality, evaluate_tts, synthesize_with_qa_retry
from .reports import QualityReport
from .contracts import SpeakerProfile, TTSBackend, TranslationBackend


class DubbingEngine:
    """One phrase/group path shared by batch and simulated-live callers."""

    def __init__(self, *, translator: TranslationBackend, tts: TTSBackend, context: Optional[ContextMemory] = None, qa_retries: int = 1, use_prosody_style: bool = False):
        self.translator = translator
        self.tts = tts
        self.context = context or ContextMemory()
        self.qa_retries = min(1, max(0, int(qa_retries)))
        self.use_prosody_style = use_prosody_style

    def process_group(self, source_text: str, speaker: SpeakerProfile, *, source_language: str, target_language: str, output_wav: str | Path, expected_duration: Optional[float] = None, source_wav: Optional[str | Path] = None, glossary: Optional[Mapping[str, str]] = None, group_index: int = 0) -> tuple[str, TTSQuality]:
        relevant = self.context.relevant(source_text, speaker_id=speaker.speaker_id)
        context_text = " | ".join(f"{item.source} → {item.target}" for item in relevant if item.target)
        translated = self.translator.translate(source_text, source_language, target_language, context=context_text or None, glossary=glossary or self.context.glossary())
        source_prosody = extract_prosody(source_wav) if source_wav else None
        style = style_instruction(source_prosody) if self.use_prosody_style and source_prosody else None
        output_wav = Path(output_wav)

        def synthesize(attempt: int):
            path = output_wav.with_name(output_wav.stem + f".qa{attempt}" + output_wav.suffix)
            self.tts.synthesize(translated, speaker, prosody=source_prosody, style=style, output_wav=path, seed=42 + attempt)
            return path

        def evaluate(path, attempt):
            return evaluate_tts(path, translated, expected_duration=expected_duration, reference_wav=speaker.reference_wav, retry_count=attempt)

        path, quality = synthesize_with_qa_retry(synthesize, translated, evaluator=evaluate, max_extra_retries=self.qa_retries)
        output_wav.parent.mkdir(parents=True, exist_ok=True)
        if Path(path).resolve() != output_wav.resolve():
            output_wav.write_bytes(Path(path).read_bytes())
        if source_wav:
            try:
                distance = prosody_distance(extract_prosody(source_wav), extract_prosody(output_wav))
            except Exception:
                distance = None
        else:
            distance = None
        self.context.remember_translation(source_text, translated, speaker_id=speaker.speaker_id, group_index=group_index)
        self.context.remember_entities(source_text, speaker_id=speaker.speaker_id, group_index=group_index)
        return translated, quality

    def build_report(self, run_id: str, groups: list[tuple[int, str, SpeakerProfile, Path]], *, source_language: str, target_language: str, output_dir: str | Path) -> dict[str, Path]:
        report = QualityReport(run_id=run_id, source=source_language, target=target_language)
        started = time.perf_counter()
        for index, source_text, speaker, output_wav in groups:
            translated, quality = self.process_group(source_text, speaker, source_language=source_language, target_language=target_language, output_wav=output_wav, group_index=index)
            report.add_group(index, quality=quality, translation_backend=getattr(self.translator, "name", None), tts_backend=getattr(self.tts, "name", None), extra={"translated_text": translated})
        report.finish(duration_seconds=time.perf_counter() - started)
        return report.write(output_dir)
