#!/usr/bin/env python3
"""Bounded Group 1 Hy-MT2 generation diagnostic for the real fixture."""
from __future__ import annotations

import json
import os
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Video_Translator import create_Sentences, create_TimeStamps, media_duration, speaker_aware_segments
from dubbing.segments import from_legacy_lists
from dubbing.timing import TimingGroupConfig, build_timing_groups, calculate_duration_budgets
from dubbing.speech_length import SpeakerRateEstimator
from dubbing.candidates import protected_tokens, score_candidate
from engine.context import ContextMemory
from translation.base import TranslationContext
from translation.hymt2 import HyMT2Translator

def main() -> int:
    transcript = pickle.loads((ROOT / "transcripts" / "test_multispeaker").read_bytes())
    starts, ends = create_TimeStamps(transcript, [".", "!", "?"])
    sentences = create_Sentences(transcript, [".", "!", "?"])
    segments = from_legacy_lists(sentences, starts, ends)
    segments = speaker_aware_segments(segments, str(ROOT / "vocal-remover/vocals/test_multispeaker_Vocals.wav"), enabled=True, num_speakers=2)
    groups = calculate_duration_budgets(build_timing_groups(segments, config=TimingGroupConfig()), media_duration=media_duration(ROOT / "test_multispeaker.mp4"))
    group = groups[0]
    glossary = json.loads((ROOT / "temp/e2e-modal-glossary.json").read_text())
    context_memory = ContextMemory()
    rate = SpeakerRateEstimator().rate_for(group.speaker)
    preferred = group.budget.preferred_duration
    target_units = rate * preferred
    context = TranslationContext(
        previous_text=None,
        next_text=groups[1].text,
        speaker=group.speaker,
        available_duration_seconds=group.budget.safe_available_duration,
        target_spoken_units=target_units,
        source_pause_seconds=sum(group.internal_pauses),
        protected_tokens=protected_tokens(group.text, glossary),
        preferred_duration_seconds=preferred,
        safe_available_duration_seconds=group.budget.safe_available_duration,
    )
    started = time.perf_counter()
    device = os.environ.get("HYMT2_SMOKE_DEVICE", "cpu")
    with HyMT2Translator("hymt2-1.8b", device) as translator:
        translation = translator.translate(group.text, "en", "tr", context=context, glossary=glossary)
        generation = dict(translator.last_generation)
    wall = time.perf_counter() - started
    candidate = score_candidate(group.text, translation, target_language="tr", speaker_rate=rate, available_duration=group.budget.safe_available_duration, preferred_duration=preferred, glossary=glossary)
    words = len(translation.split())
    chars = len(translation.replace(" ", ""))
    semantic_valid = bool(translation.strip()) and not any(label in translation.casefold() for label in ("translate only", "background information", "output only"))
    result = {
        "status": "passed" if semantic_valid and generation.get("generated_tokens", 0) <= generation.get("max_new_tokens", 0) else "failed",
        "group_index": 1,
        "source_text": group.text,
        "prompt_tokens": generation.get("prompt_tokens"),
        "generated_tokens": generation.get("generated_tokens"),
        "max_new_tokens": generation.get("max_new_tokens"),
        "eos_seen": generation.get("eos_seen"),
        "stop_reason": generation.get("stop_reason"),
        "decoded_translation": translation,
        "generation_wall_seconds": wall,
        "target_speech_duration_seconds": preferred,
        "safe_available_duration_seconds": group.budget.safe_available_duration,
        "predicted_duration_seconds": candidate.predicted_duration,
        "output_word_count": words,
        "output_character_count": chars,
        "semantic_sanity": semantic_valid,
        "duration_sanity": bool(candidate.predicted_duration <= group.budget.safe_available_duration * 1.5),
        "speaker": group.speaker,
        "device": device,
    }
    path = ROOT / "temp/hymt2-group1-smoke.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
