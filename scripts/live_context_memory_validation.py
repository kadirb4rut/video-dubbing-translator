"""Bounded real Hy-MT2 validation of distant live SQLite context retrieval.

Filler rows test distance/persistence only; they are not generated conversation
or browser evidence. Run on the development GPU with explicitly cached weights.
"""
from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.context import ContextItem, ContextMemory, extract_entities
from translation.base import TranslationContext
from translation.hymt2 import HyMT2Translator


def main():
    output = ROOT / 'temp' / f'live-distant-context-{int(time.time())}'
    output.mkdir(parents=True)
    database = output / 'context.sqlite3'
    memory = ContextMemory(database)
    translator = None
    rows = []
    glossary = {'Maya Chen': 'Maya Chen', 'CUDA': 'CUDA', 'kHz': 'kHz', 'ms': 'ms'}
    sources = (
        'Dr. Maya Chen uses CUDA 12.8 to process 128 frames at 48 kHz with a 20 ms delay.',
        'Maya Chen confirms that CUDA 12.8 still processes 128 frames at 48 kHz with a 20 ms delay.',
        'Do not change the 48 kHz rate or the 20 ms delay. Maya Chen still uses CUDA 12.8.',
        'At the end, Maya Chen keeps CUDA 12.8, 128 frames, 48 kHz, and the 20 ms delay.',
    )
    try:
        translator = HyMT2Translator('hymt2-1.8b', 'cuda')
        started = time.perf_counter()
        translator.prepare()
        load_seconds = time.perf_counter() - started
        for index, source in enumerate(sources):
            if index:
                for filler in range(80):
                    memory.remember(kind='live_committed',
                        source=f'Unrelated weather observation {index}:{filler}',
                        speaker_id='LIVE_SPEAKER_00', group_index=100*index+filler)
                # Reopen the persisted database, rather than relying on a Python
                # dictionary containing an earlier translation.
                memory.close()
                memory = ContextMemory(database)
            recent = memory.relevant(source, speaker_id='LIVE_SPEAKER_00', limit=6)
            retrieved = memory.relevant(source, speaker_id='LIVE_SPEAKER_00', limit=6, distant=True)
            previous = ' '.join(item.source for item in retrieved if item.kind == 'live_committed')[:800] or None
            if index and (not previous or 'Maya Chen' not in previous or 'CUDA 12.8' not in previous):
                raise AssertionError('Distant committed source context was not retrieved')
            context = TranslationContext(speaker='LIVE_SPEAKER_00', previous_text=previous,
                preferred_duration_seconds=8, target_spoken_units=38.4,
                protected_tokens=tuple(value for _, value in extract_entities(source)))
            relevant_glossary = {key: value for key, value in glossary.items() if key.casefold() in source.casefold()}
            started = time.perf_counter()
            target = translator.translate(source, 'en', 'tr', context=context, glossary=relevant_glossary)
            generation_seconds = time.perf_counter() - started
            # Use an empty checker: prior full translations are context, never
            # whole-sentence glossary requirements.
            checker = ContextMemory()
            try:
                issues = checker.consistency_check(source, target, glossary=relevant_glossary)
            finally:
                checker.close()
            row = {'index': index, 'source': source, 'target': target,
                'recent_committed_context_count': sum(item.kind == 'live_committed' for item in recent),
                'retrieved': [asdict(item) for item in retrieved],
                'previous_source_context': previous, 'glossary': relevant_glossary,
                'generation_seconds': generation_seconds,
                'generation': translator.last_generation,
                'preservation_issues': [asdict(issue) for issue in issues],
                'semantic_review': 'pending human review; preservation is not translation correctness'}
            rows.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            memory.remember(ContextItem('live_committed', source, target, 'LIVE_SPEAKER_00', index))
            memory.remember_entities(source, speaker_id='LIVE_SPEAKER_00', group_index=index)
        result = {'scope': 'Real Hy-MT2 1.8B translation plus persistent SQLite retrieval across 80 unrelated rows between requests; no audio/browser test',
            'model': 'hymt2-1.8b', 'load_seconds': load_seconds,
            'filler_rows': 240, 'requests': rows,
            'preservation_passed': all(not row['preservation_issues'] for row in rows),
            'distant_retrieval_passed': all(row['previous_source_context'] for row in rows[1:])}
        (output / 'result.json').write_text(json.dumps(result, indent=2, ensure_ascii=False))
        (ROOT / 'temp/live-distant-context-latest.txt').write_text(str(output))
        print(json.dumps({'output': str(output), 'preservation_passed': result['preservation_passed'],
                          'distant_retrieval_passed': result['distant_retrieval_passed']}), flush=True)
    finally:
        memory.close()
        if translator is not None:
            translator.close()


if __name__ == '__main__':
    main()
