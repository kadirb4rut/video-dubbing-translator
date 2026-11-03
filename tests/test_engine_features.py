import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from engine.context import ContextMemory
from engine.qa import evaluate_tts, synthesize_with_qa_retry, word_error_rate
from engine.reports import QualityReport, SCHEMA_VERSION
from engine.streaming import JitterBuffer, OnlineSpeakerTracker, PhraseVAD, StablePrefixBuffer
from translation.backends import (
    M2M100_FALLBACK_LANGUAGES,
    VOXCPM2_LANGUAGES,
    capability_matrix,
    normalize_language,
)


class EngineFeatureTests(unittest.TestCase):
    def test_language_matrix_is_30_and_tl_is_tagalog(self):
        self.assertEqual(len(VOXCPM2_LANGUAGES), 30)
        self.assertEqual(len(M2M100_FALLBACK_LANGUAGES), 7)
        self.assertEqual(normalize_language("fil-PH"), "tl")
        self.assertEqual(next(item for item in capability_matrix() if item["code"] == "tl")["name"], "Tagalog / Filipino")

    def test_context_memory_and_consistency(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ContextMemory(Path(directory) / "context.sqlite")
            memory.remember_translation("VoxCPM2 ships 12 units", "VoxCPM2 12 birim gönderir")
            self.assertEqual(memory.glossary()["VoxCPM2 ships 12 units"], "VoxCPM2 12 birim gönderir")
            self.assertFalse(memory.consistency_check("VoxCPM2 ships 12 units", "VoxCPM2 12 birim gönderir"))
            memory.remember_translation("An unrelated earlier sentence", "Önceki alakasız cümle")
            relevant = memory.relevant("VoxCPM2 ships 12 units")
            self.assertEqual([item.source for item in relevant], ["VoxCPM2 ships 12 units"])

    def test_qa_metrics_and_signal_health(self):
        with tempfile.TemporaryDirectory() as directory:
            wav = Path(directory) / "tone.wav"
            sf.write(wav, 0.4 * np.sin(2 * np.pi * 220 * np.arange(16000) / 16000), 16000)
            quality = evaluate_tts(wav, "hello world", recovered_text="hello world", expected_duration=1.0, reference_wav=wav)
            self.assertEqual(quality.wer, 0.0)
            self.assertEqual(quality.cer, 0.0)
            self.assertGreater(quality.speaker_similarity.cosine, 0.9)
            self.assertTrue(quality.passed)

    def test_live_context_retrieves_distant_unicode_name_without_other_speaker(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'context.sqlite'
            memory = ContextMemory(path)
            memory.remember(kind='live_committed', source='Şule Demir configures CUDA at 48 kHz',
                            target='Şule Demir CUDA sistemini 48 kHz olarak ayarlar', speaker_id='A')
            for index in range(100):
                memory.remember(kind='live_committed', source=f'Unrelated weather observation {index}', speaker_id='A')
                memory.remember(kind='live_committed', source=f'Şule Demir has a different setting {index}', speaker_id='B')
            memory.close()
            memory = ContextMemory(path)
            recent = memory.relevant('şule demir CUDA 48 kHz', speaker_id='A', limit=6)
            distant = memory.relevant('şule demir CUDA 48 kHz', speaker_id='A', limit=6, distant=True)
            self.assertFalse(recent)
            self.assertEqual([item.source for item in distant], ['Şule Demir configures CUDA at 48 kHz'])
            self.assertFalse(memory.relevant('The changes are pending', speaker_id='A', limit=6, distant=True))
            memory.close()

    def test_qa_retries_once_only_for_hard_failures(self):
        attempts = []
        def synthesize(attempt):
            attempts.append(attempt)
            return f"path-{attempt}"
        class Quality:
            def __init__(self, hard): self.hard_failures = hard
        result, quality = synthesize_with_qa_retry(synthesize, "x", evaluator=lambda path, attempt: Quality(("bad",) if attempt == 0 else ()), max_extra_retries=4)
        self.assertEqual(attempts, [0, 1])
        self.assertEqual(result, "path-1")
        self.assertTrue(not quality.hard_failures)

    def test_report_writes_versioned_json_html_csv(self):
        with tempfile.TemporaryDirectory() as directory:
            report = QualityReport(run_id="test")
            report.finish()
            paths = report.write(directory)
            payload = json.loads(paths["json"].read_text())
            self.assertEqual(payload["schema_version"], SCHEMA_VERSION)
            self.assertTrue(paths["html"].exists())
            self.assertTrue(paths["csv"].exists())

    def test_jitter_buffer_orders_and_deduplicates(self):
        jitter = JitterBuffer(target_chunks=0)
        jitter.add(1, b"b")
        jitter.add(0, b"a")
        jitter.add(0, b"duplicate")
        self.assertEqual(jitter.pop_ready(), [b"a", b"b"])

    def test_stable_prefix_waits_for_final_word(self):
        prefix = StablePrefixBuffer()
        self.assertEqual(prefix.update("hello world"), "hello")
        self.assertEqual(prefix.update("hello world now."), "hello world now.")
        self.assertEqual(prefix.update("hello world now."), "hello world now.")

    def test_phrase_vad_returns_complete_pcm16_phrase(self):
        vad = PhraseVAD(sample_rate=1_000, frame_ms=100, silence_ms=200, threshold=0.01)
        voiced = (np.ones(100, dtype=np.int16) * 4_000).tobytes()
        silence = np.zeros(200, dtype=np.int16).tobytes()
        phrases = vad.feed(voiced + silence)
        self.assertEqual(len(phrases), 1)
        decoded = np.frombuffer(phrases[0], dtype=np.int16)
        self.assertGreaterEqual(decoded.size, 100)
        self.assertGreater(float(np.max(np.abs(decoded))), 1000.0)

    def test_online_speaker_tracker_keeps_stable_ids(self):
        vectors = {b"aa": (1.0, 0.0), b"bb": (0.99, 0.01), b"cc": (0.0, 1.0)}
        tracker = OnlineSpeakerTracker(match_threshold=0.90, embedder=lambda pcm, rate: vectors[pcm])
        first = tracker.assign(b"aa")
        same = tracker.assign(b"bb")
        other = tracker.assign(b"cc")
        self.assertEqual(first.speaker_id, "LIVE_SPEAKER_00")
        self.assertTrue(first.is_new)
        self.assertEqual(same.speaker_id, first.speaker_id)
        self.assertFalse(same.is_new)
        self.assertEqual(other.speaker_id, "LIVE_SPEAKER_01")
        self.assertEqual(tracker.speakers, ("LIVE_SPEAKER_00", "LIVE_SPEAKER_01"))

    def test_word_error_rate_is_normalized(self):
        self.assertEqual(word_error_rate("Hello, world!", "hello world"), 0.0)

    def test_turkish_dotted_i_does_not_split_a_word(self):
        self.assertEqual(word_error_rate("İlginç.", "ilginç."), 0.0)
        self.assertEqual(word_error_rate("İstanbul", "i\u0307stanbul"), 0.0)
        # This is not accent stripping or conflating Turkish ı with i.
        self.assertGreater(word_error_rate("ılık", "ilik"), 0.0)


if __name__ == "__main__":
    unittest.main()
