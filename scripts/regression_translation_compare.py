#!/usr/bin/env python3
"""Reproduce the first bad translation with and without the E2E glossary."""

from __future__ import annotations

import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Video_Translator import create_Sentences, create_TimeStamps, media_duration, speaker_aware_segments
from dubbing.candidates import protected_tokens
from dubbing.segments import from_legacy_lists
from dubbing.speech_length import SpeakerRateEstimator
from dubbing.timing import TimingGroupConfig, build_timing_groups, calculate_duration_budgets
from engine.context import ContextMemory
from translation.base import TranslationContext
from translation.hymt2 import HyMT2Translator


INV = ROOT / "temp" / "regression-investigation-20261024"


def build_groups():
    transcript = pickle.loads((ROOT / "transcripts" / "test_multispeaker").read_bytes())
    starts, ends = create_TimeStamps(transcript, [".", "!", "?"])
    sentences = create_Sentences(transcript, [".", "!", "?"])
    segments = from_legacy_lists(sentences, starts, ends)
    segments = speaker_aware_segments(
        segments,
        str(ROOT / "vocal-remover" / "vocals" / "test_multispeaker_Vocals.wav"),
        enabled=True,
        num_speakers=2,
    )
    return calculate_duration_budgets(
        build_timing_groups(segments, config=TimingGroupConfig()),
        media_duration(ROOT / "test_multispeaker.mp4"),
    )


def main() -> int:
    groups = build_groups()
    group = groups[1]  # earliest bad group in the preserved Modal run
    glossary = json.loads((ROOT / "temp" / "e2e-modal-glossary.json").read_text())
    rate = SpeakerRateEstimator().rate_for(group.speaker)
    budget = group.budget
    assert budget is not None
    context_memory = ContextMemory()
    context_memory.remember_translation(
        groups[0].text,
        "Yes, exactly. Very busy. Love writing, but what I love most is teaching AI.",
        speaker_id=groups[0].speaker,
        group_index=0,
    )
    remembered = context_memory.relevant(group.text, speaker_id=group.speaker)
    remembered_context = " | ".join(f"{item.source} → {item.target}" for item in remembered if item.target)
    previous = f"{groups[0].text} | memory: {remembered_context}" if remembered_context else groups[0].text
    common = {
        "previous_text": previous,
        "next_text": groups[2].text,
        "speaker": group.speaker,
        "available_duration_seconds": budget.safe_available_duration,
        "target_spoken_units": rate * budget.preferred_duration,
        "source_pause_seconds": sum(group.internal_pauses),
        "preferred_duration_seconds": budget.preferred_duration,
        "safe_available_duration_seconds": budget.safe_available_duration,
    }
    results = []
    with HyMT2Translator("hymt2-1.8b", "mps") as translator:
        for label, current_glossary in (("without_glossary", None), ("with_modal_glossary", glossary)):
            context = TranslationContext(
                **common,
                protected_tokens=protected_tokens(group.text, current_glossary),
            )
            started = time.perf_counter()
            text = translator.translate(group.text, "en", "tr", context=context, glossary=current_glossary)
            results.append(
                {
                    "label": label,
                    "source_text": group.text,
                    "previous_context": previous,
                    "next_context": groups[2].text,
                    "glossary": current_glossary or {},
                    "prompt": translator.last_generation.get("prompt"),
                    "generation": {key: value for key, value in translator.last_generation.items() if key != "prompt"},
                    "translation": text,
                    "wall_seconds": time.perf_counter() - started,
                }
            )
    output = INV / "translation-compare.json"
    output.write_text(json.dumps({"status": "passed", "group_index": 2, "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output.read_text())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
