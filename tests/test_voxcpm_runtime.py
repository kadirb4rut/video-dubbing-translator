import tempfile
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf

from voxcpm_runtime import continuation_prompt, synthesize_cloned_speech


class _FakeTTSModel:
    sample_rate = 48_000


class _FakeVoxCPM:
    tts_model = _FakeTTSModel()

    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return np.zeros(480, dtype=np.float32)


class VoxCPMRuntimeRegressionTests(unittest.TestCase):
    def test_micro_target_uses_reference_only_with_a_long_continuation(self):
        prompt = "So it's a big fan and that's really what got me into AI was, it's actually a long story."
        self.assertIsNone(continuation_prompt('Teşekkür ederim. Bilmiyordum.', prompt))
        self.assertIsNone(continuation_prompt('Bugün hava çok güzel, dışarıda yürüyüş yapıyoruz.', prompt))
        long_target = 'Yazmayı çok seviyorum ama yapay zekâyı öğrenmek ve öğretmek daha çok hoşuma gidiyor.'
        self.assertEqual(continuation_prompt(long_target, prompt), prompt)

    def test_style_metadata_never_becomes_spoken_target_text(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.wav"
            output = root / "output.wav"
            sf.write(reference, np.zeros(16_000, dtype=np.float32), 16_000)
            model = _FakeVoxCPM()
            synthesize_cloned_speech(
                model,
                "Merhaba, bu hedef cümledir.",
                reference,
                output,
                prompt_text="Referans cümlesi.",
                style="slower, soft delivery, preserve pauses",
            )
            self.assertEqual(model.calls[0]["text"], "Merhaba, bu hedef cümledir.")
            self.assertNotIn("soft delivery", model.calls[0]["text"])


if __name__ == "__main__":
    unittest.main()
