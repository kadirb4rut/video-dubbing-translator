import json
import io
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from translation import model_manager
from translation.base import TranslationContext
from translation.prompts import (
    duration_prompt,
    sanitize_translation_output,
    translation_prompt,
)
from translation.registry import MODELS, get_model


ROOT = Path(__file__).resolve().parents[1]


class HyMT2TranslationTests(unittest.TestCase):
    def test_explicit_glossary_contract_is_unicode_based_and_not_a_semantic_score(self):
        from translation.base import glossary_preservation
        for term in ('東京大学', 'جامعة القاهرة', 'มหาวิทยาลัย', 'Élodie'):
            self.assertEqual(glossary_preservation(term, term, {term:term, 'absent':'other'}), {term:True})
            self.assertEqual(glossary_preservation(term, 'different text', {term:term}), {term:False})
        self.assertEqual(glossary_preservation('E\u0301lodie', 'ÉLODIE', {'Élodie':'Élodie'}), {'Élodie':True})

    def test_fallback_does_not_append_missing_glossary_as_unrelated_spoken_text(self):
        from translation.backends import M2M100Backend
        backend = M2M100Backend()
        backend._model = Mock(device='cpu')
        backend._tokenizer = Mock(return_value={})
        backend._tokenizer.batch_decode.return_value = ['Muisto on valmis.']
        with self.assertRaisesRegex(ValueError, 'explicit_glossary_not_preserved: memory'):
            backend.translate('The memory is ready.', 'en', 'fi', glossary={'memory':'Muistijärjestelmä'})
        self.assertEqual(backend.translate('The memory is ready.', 'en', 'fi'), 'Muisto on valmis.')

    def test_live_draft_rejects_violated_mapping_before_any_speech_generation(self):
        from live_dubbing.models import LocalModels
        component = object.__new__(LocalModels)
        component.glossary = {'Dr. Ada':'Dr. Ada'}
        component.memory = Mock(); component.memory.relevant.return_value = []
        component.translator = Mock(); component.translator.translate.return_value = 'الدكتورة أدا'
        component.target_language = 'ar'
        from live_dubbing.inference_scheduler import PriorityInferenceScheduler
        component.inference_scheduler = PriorityInferenceScheduler()
        with self.assertRaisesRegex(ValueError, 'explicit_glossary_not_preserved: Dr. Ada'):
            component.translate_draft(source_text='Dr. Ada leads the project.', source_language='en')

    def test_registry_contains_exactly_the_three_supported_models(self):
        self.assertEqual(
            [model.model_id for model in MODELS],
            [
                "tencent/Hy-MT2-1.8B",
                "tencent/Hy-MT2-7B",
                "tencent/Hy-MT2-30B-A3B",
            ],
        )
        self.assertEqual(get_model("hymt2-7b").display_name, "Hy-MT2 7B")
        with self.assertRaises(ValueError):
            get_model("unknown")

    def test_installed_detection_and_only_requested_model_download(self):
        calls = []

        def fake_download(**kwargs):
            calls.append(kwargs)
            destination = Path(kwargs["local_dir"])
            (destination / "config.json").write_text("{}")
            (destination / "tokenizer_config.json").write_text("{}")
            (destination / "model.safetensors").touch()
            return str(destination)

        with tempfile.TemporaryDirectory() as directory, patch.object(
            model_manager, "MODELS_DIR", Path(directory)
        ):
            path = model_manager.download_model(
                "hymt2-1.8b", snapshot_download_fn=fake_download
            )
            self.assertTrue(model_manager.is_installed("hymt2-1.8b"))
            self.assertFalse(model_manager.is_installed("hymt2-7b"))
            self.assertEqual(calls[0]["repo_id"], "tencent/Hy-MT2-1.8B")
            self.assertEqual(path.name, "hymt2-1.8b")

    def test_prompts_include_context_glossary_and_duration_constraints(self):
        context = TranslationContext("Previous", "Next", "SPEAKER_02")
        prompt = translation_prompt(
            "Use VoxCPM2.", "en", "tr", context=context, glossary={"VoxCPM2": "VoxCPM2"}
        )
        self.assertIn("Previous segment: Previous", prompt)
        self.assertIn("Next segment: Next", prompt)
        timed_prompt = translation_prompt(
            "Current", "en", "tr",
            context=TranslationContext(
                available_duration_seconds=2.5,
                target_spoken_units=12,
                source_pause_seconds=0.4,
                protected_tokens=("GPT-4",),
            ),
        )
        self.assertIn("Available natural speaking window: 2.50 seconds", timed_prompt)
        self.assertIn("Target spoken-length budget", timed_prompt)
        self.assertIn("GPT-4", timed_prompt)
        self.assertIn("Current speaker: SPEAKER_02", prompt)
        self.assertIn("VoxCPM2 translates to VoxCPM2", prompt)
        self.assertIn("only the [Current Segment]", prompt)
        unrelated = translation_prompt(
            "A sentence without the glossary term.",
            "en",
            "tr",
            glossary={"VoxCPM2": "VoxCPM2", "CUDA": "CUDA"},
        )
        self.assertNotIn("VoxCPM2 translates to", unrelated)
        self.assertNotIn("CUDA translates to", unrelated)
        rewrite = duration_prompt(
            "A company shipped 12 units.",
            "Bir şirket 12 birim gönderdi.",
            "en",
            "tr",
            3.0,
            5.0,
            glossary={"company": "şirket"},
        )
        self.assertIn("correction ratio 0.600", rewrite)
        for required in ("3.00 seconds", "5.00 seconds", "numbers", "glossary"):
            self.assertIn(required, rewrite)
        expansion = duration_prompt(
            "The team shipped 12 units.",
            "Ekip 12 birim gönderdi.",
            "en",
            "tr",
            4.0,
            2.0,
            context=TranslationContext(
                preferred_duration_seconds=4.0,
                safe_available_duration_seconds=5.0,
                target_spoken_units=20,
                protected_tokens=("12",),
                candidate_style="duration_refinement_expand",
                refinement_direction="expand",
            ),
        )
        self.assertIn("naturally fuller", expansion)
        self.assertIn("do not invent facts", expansion)
        self.assertIn("correction ratio 2.000", expansion)
        self.assertIn("Preferred original speech duration: 4.00 seconds", expansion)
        self.assertIn("Safe available timing window (hard boundary): 5.00 seconds", expansion)

    def test_translation_prompt_includes_already_spoken_target_for_context_only(self):
        prompt = translation_prompt('Continue the analysis.', 'en', 'tr',
            context=TranslationContext(previous_translation='Önceki Türkçe cümle.'))
        self.assertIn('Already spoken target translation: Önceki Türkçe cümle.', prompt)
        self.assertIn('do not repeat it', prompt)

    def test_hymt2_translate_can_request_deterministic_generation(self):
        from translation.hymt2 import HyMT2Translator
        translator = object.__new__(HyMT2Translator)
        translator._request = Mock(return_value='Devam edelim.')
        result = translator.translate('We can continue.', 'en', 'tr', deterministic=True)
        self.assertEqual(result, 'Devam edelim.')
        translator._request.assert_called_once()
        self.assertEqual(translator._request.call_args.kwargs['deterministic'], True)

    def test_hymt2_request_records_timings_without_logging_prompt_text(self):
        from contextlib import redirect_stderr
        from translation.hymt2 import HyMT2Translator

        class InputStream:
            request = None

            def write(self, value):
                self.request = json.loads(value)

            def flush(self):
                return None

        class OutputStream:
            def __init__(self, input_stream):
                self.input_stream = input_stream

            def readline(self):
                return json.dumps({
                    "id": self.input_stream.request["id"],
                    "ok": True,
                    "text": "Merhaba.",
                    "stats": {
                        "model_load_seconds": 1.25,
                        "generation_wall_seconds": 0.75,
                    },
                })

        translator = object.__new__(HyMT2Translator)
        stream = InputStream()
        translator._process = types.SimpleNamespace(
            poll=lambda: None,
            stdin=stream,
            stdout=OutputStream(stream),
        )
        translator._lock = threading.Lock()
        translator.last_generation = {}
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics):
            self.assertEqual(translator._request("translate", "private source sentence"), "Merhaba.")

        self.assertEqual(translator.last_generation["model_load_seconds"], 1.25)
        self.assertEqual(translator.last_generation["generation_wall_seconds"], 0.75)
        self.assertGreaterEqual(translator.last_generation["rpc_wall_seconds"], 0)
        self.assertIn("stage=hymt2 operation=translate", diagnostics.getvalue())
        self.assertNotIn("private source sentence", diagnostics.getvalue())

    def test_hymt2_worker_measures_first_model_load_only(self):
        from translation.hymt2_worker import Runtime

        class FakeModel:
            def to(self, _device):
                return self

            def eval(self):
                return self

        class FakeTokenizer:
            @staticmethod
            def from_pretrained(*_args, **_kwargs):
                return object()

        class FakeAutoModel:
            @staticmethod
            def from_pretrained(*_args, **_kwargs):
                return FakeModel()

        fake_torch = types.SimpleNamespace(
            cuda=types.SimpleNamespace(is_available=lambda: False),
            backends=types.SimpleNamespace(mps=types.SimpleNamespace(is_available=lambda: False)),
            bfloat16=object(),
            device=lambda name: name,
        )
        fake_transformers = types.SimpleNamespace(
            AutoTokenizer=FakeTokenizer,
            AutoModelForCausalLM=FakeAutoModel,
        )
        with patch.dict(sys.modules, {"torch": fake_torch, "transformers": fake_transformers}):
            runtime = Runtime(Path("unused-model"), "cpu")
            runtime.load()
            measured = runtime.last_model_load_seconds
            runtime.load()

        self.assertGreaterEqual(measured, 0)
        self.assertEqual(runtime.last_model_load_seconds, measured)

    def test_translation_output_sanitization(self):
        self.assertEqual(sanitize_translation_output('Translation: "Merhaba dünya"'), "Merhaba dünya")
        with self.assertRaises(RuntimeError):
            sanitize_translation_output("  ")

    def test_duration_rewrite_does_not_copy_previous_translated_answer(self):
        prompt = duration_prompt(
            'So thank you for sharing that. I caught you by surprise. Yes.',
            'Bunu paylaştığınız için teşekkür ederim. Sizi şaşırttım. Evet.',
            'en', 'tr', 2.107, 3.52,
            context=TranslationContext(
                previous_text='Thank you. I did not know. | memory: Teşekkür ederim. Bilmiyordum.',
                next_text='The industry is growing.',
                target_spoken_units=10,
                preferred_duration_seconds=2.107,
                safe_available_duration_seconds=3.672,
            ),
        )
        self.assertNotIn('Bilmiyordum', prompt)
        self.assertNotIn('The industry', prompt)
        self.assertIn('Sizi şaşırttım', prompt)
        self.assertIn('Target spoken-length budget', prompt)

    def test_worker_uses_machine_readable_json_protocol_without_loading_model_for_invalid_op(self):
        process = subprocess.Popen(
            [
                sys.executable,
                str(ROOT / "translation" / "hymt2_worker.py"),
                "--model-path",
                str(ROOT / "does-not-exist"),
                "--device",
                "cpu",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout, _ = process.communicate(
            json.dumps({"id": "abc", "operation": "invalid", "prompt": "x"}) + "\n"
        )
        response = json.loads(stdout.splitlines()[0])
        self.assertEqual(response["id"], "abc")
        self.assertFalse(response["ok"])


if __name__ == "__main__":
    unittest.main()
