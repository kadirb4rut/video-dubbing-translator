from __future__ import annotations

import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from app.config import settings
from app.media import inspect_media, validate_upload
from app.providers_real import (
    AwsTranslateProvider,
    DeepFilterNetNoiseProvider,
    FixtureTranslationProvider,
    GoogleDeepTranslatorProvider,
    HyMT2TranslationProvider,
    ProviderUnavailable,
    VoxCPM2VoiceProvider,
    WhisperTranscriptionProvider,
    translation_provider,
)
from app.worker import MAX_DUBBING_SPEEDUP, JobWorker


def test_api_container_contract_includes_ffmpeg_for_upload_inspection():
    dockerfile = Path(__file__).parents[2] / "backend" / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends ffmpeg" in contents
    assert "rm -rf /var/lib/apt/lists/*" in contents


def test_cpu_worker_contract_includes_deepfilter_native_runtime():
    backend_dir = Path(__file__).parents[2] / "backend"
    requirements = backend_dir / "requirements.worker-cpu.txt"
    full_requirements = backend_dir / "requirements.worker-cpu-full.txt"
    contents = requirements.read_text(encoding="utf-8")
    common_contents = (backend_dir / "requirements.worker-common.txt").read_text(encoding="utf-8")
    assert "-r requirements.worker-common.txt" in contents
    contents = f"{contents}\n{common_contents}"
    assert "deepfilternet==0.5.6" in contents
    assert "deepfilterlib==0.5.6" in contents
    full_contents = full_requirements.read_text(encoding="utf-8")
    assert "transformers==5.16.1" in full_contents
    assert "--extra-index-url https://download.pytorch.org/whl/cpu" in full_contents
    dockerfile = (backend_dir / "Dockerfile.worker").read_text(encoding="utf-8")
    assert "INSTALL_" not in dockerfile
    assert "voxcpm==2.0.3" in full_contents


def test_gpu_worker_contract_scales_to_zero_and_reuses_live_host_model_cache():
    terraform = Path(__file__).parents[2] / "infrastructure" / "terraform" / "main.tf"
    contents = terraform.read_text(encoding="utf-8")
    assert "min_size            = 0" in contents
    assert "desired_capacity    = 0" in contents
    assert 'desired_count   = var.worker_desired_count' in contents
    assert "sourceVolume" in contents and '"model-cache"' in contents
    assert 'host_path = "/var/lib/lingowave/model-cache"' in contents
    assert 'XDG_CACHE_HOME", value = "/home/lingowave/.cache"' in contents


def test_stage_provider_release_evicts_in_memory_models(monkeypatch):
    released = []
    monkeypatch.setattr("app.providers_real._release_torch_memory", lambda: released.append(True))
    whisper_model = object()
    voxcpm_model = object()
    WhisperTranscriptionProvider._models["small"] = whisper_model
    VoxCPM2VoiceProvider._models[("cuda", "float16", "openbmb/VoxCPM2", "revision")] = voxcpm_model

    try:
        WhisperTranscriptionProvider("small").release()
        provider = VoxCPM2VoiceProvider.__new__(VoxCPM2VoiceProvider)
        provider.device = "cuda"
        provider.dtype = "float16"
        provider.model_id = "openbmb/VoxCPM2"
        provider.revision = "revision"
        provider.release()
        assert "small" not in WhisperTranscriptionProvider._models
        assert ("cuda", "float16", "openbmb/VoxCPM2", "revision") not in VoxCPM2VoiceProvider._models
        assert released == [True, True]
    finally:
        WhisperTranscriptionProvider._models.pop("small", None)
        VoxCPM2VoiceProvider._models.pop(("cuda", "float16", "openbmb/VoxCPM2", "revision"), None)


def test_noise_removal_requires_explicit_dev_fallback(monkeypatch, tmp_path):
    source = tmp_path / "source.wav"
    output = tmp_path / "enhanced.wav"
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ac", "1", "-ar", "16000", str(source)],
        check=True,
    )
    monkeypatch.setattr("app.providers_real.shutil.which", lambda _: None)
    with pytest.raises(ProviderUnavailable):
        DeepFilterNetNoiseProvider().enhance(source, output_path=output)

    monkeypatch.setenv("NOISE_REMOVAL_FALLBACK", "ffmpeg-afftdn")
    result = DeepFilterNetNoiseProvider().enhance(source, output_path=output)
    assert result == output
    assert inspect_media(output)["duration_seconds"] > 0


def test_fixture_translation_is_explicit_and_preserves_segment_timing():
    segments = [{"start": 0, "end": 1.25, "text": "Hello world"}]
    translated = FixtureTranslationProvider().translate(segments, source="en", target="es")
    assert translated == [{"start": 0.0, "end": 1.25, "text": "hola mundo"}]
    with pytest.raises(ProviderUnavailable, match="no mapping"):
        FixtureTranslationProvider().translate([{"start": 0, "end": 1, "text": "unknown phrase"}], source="en", target="es")


def test_hymt2_segment_contract_is_deterministic_and_rejects_filler_or_reordering():
    provider = HyMT2TranslationProvider(model_name="test-model")
    segments = [
        {"id": "intro", "start": 0, "end": 1, "text": "Hello"},
        {"id": "detail", "start": 1, "end": 2, "text": "World"},
    ]
    assert provider._parse("<SEG_intro> Merhaba\n<SEG_detail> Dünya", segments) == ["Merhaba", "Dünya"]
    for malformed in ("Note:\n<SEG_intro> Merhaba\n<SEG_detail> Dünya", "<SEG_detail> Dünya\n<SEG_intro> Merhaba", "<SEG_intro> Merhaba\n<SEG_intro> Dünya"):
        with pytest.raises(ProviderUnavailable):
            provider._parse(malformed, segments)


def test_hymt2_batches_respect_count_and_character_bounds(monkeypatch):
    monkeypatch.setattr("app.providers_real.settings", replace(settings, translation_batch_size=2, translation_max_chars_per_batch=10))
    provider = HyMT2TranslationProvider(model_name="test-model")
    segments = [{"start": i, "end": i + 1, "text": text} for i, text in enumerate(("one", "two", "three"))]
    assert [[item["text"] for item in batch] for batch in provider._batches(segments)] == [["one", "two"], ["three"]]


def test_hymt2_rejects_unsupported_language_without_loading_model():
    provider = HyMT2TranslationProvider(model_name="test-model")
    with pytest.raises(ProviderUnavailable, match="does not support"):
        provider.translate([{"start": 0, "end": 1, "text": "Hello"}], source="xx", target="tr")


def test_hymt2_wraps_plain_model_output_with_deterministic_source_id(monkeypatch):
    monkeypatch.setattr("app.providers_real.settings", replace(settings, translation_max_retries=0))
    provider = HyMT2TranslationProvider(model_name="test-model")
    outputs = iter(["Merhaba dünya", "Merhaba dünya"])
    monkeypatch.setattr(provider, "_generate", lambda prompt: next(outputs))
    result = provider.translate_segments([{"id": "s01", "start": 0, "end": 1, "text": "Hello world"}], source="en", target="tr")
    assert result == [{"id": "s01", "start": 0.0, "end": 1.0, "text": "Merhaba dünya"}]
    assert provider.last_metrics["single_segment_fallback_count"] == 1


def test_hymt2_is_selectable_as_the_default_provider(monkeypatch):
    monkeypatch.setattr("app.providers_real.settings", replace(settings, translation_provider="hymt2"))
    assert isinstance(translation_provider(), HyMT2TranslationProvider)


def test_google_deep_translator_is_the_default_primary_provider(monkeypatch):
    monkeypatch.setattr("app.providers_real.settings", replace(settings, translation_provider="google-deep-translator"))
    assert isinstance(translation_provider(), GoogleDeepTranslatorProvider)


def test_google_deep_translator_preserves_source_and_timing():
    calls = []

    class FakeGoogleTranslator:
        def translate(self, text):
            calls.append(text)
            return f"çeviri: {text}"

    provider = GoogleDeepTranslatorProvider(translator_factory=lambda **kwargs: FakeGoogleTranslator())
    segments = [{"id": "s01", "start": 0, "end": 1.25, "text": "Hello AWS"}]
    translated = provider.translate_segments(segments, source="en", target="tr", context="AWS context", duration_aware=True)
    assert calls == ["Hello AWS"]
    assert translated == [{"id": "s01", "start": 0.0, "end": 1.25, "source_text": "Hello AWS", "text": "çeviri: Hello AWS"}]
    assert provider.last_metrics["runtime"] == "deep-translator.GoogleTranslator"
    assert provider.last_metrics["duration_aware"] is True


@pytest.mark.parametrize("result", ["", None])
def test_google_deep_translator_rejects_empty_result(result):
    class EmptyTranslator:
        def translate(self, text):
            return result

    provider = GoogleDeepTranslatorProvider(translator_factory=lambda **kwargs: EmptyTranslator())
    with pytest.raises(ProviderUnavailable, match="empty text"):
        provider.translate([{"start": 0, "end": 1, "text": "Hello"}], source="en", target="tr")


def test_duration_refinement_trigger_is_bounded_and_selective():
    assert not JobWorker._requires_duration_refinement(1.19, 1.0, 0.2)
    assert JobWorker._requires_duration_refinement(1.21, 1.0, 0.2)
    assert JobWorker._requires_duration_refinement(5.0, 1.0, 0.2)


def test_aws_translate_provider_preserves_segment_timing():
    class FakeTranslateClient:
        def translate_text(self, *, Text, SourceLanguageCode, TargetLanguageCode):
            assert (SourceLanguageCode, TargetLanguageCode) == ("en", "es")
            return {"TranslatedText": f"translated: {Text}"}

    segments = [{"start": 0, "end": 1.25, "text": "Hello world"}]
    translated = AwsTranslateProvider(client=FakeTranslateClient()).translate(segments, source="en", target="es")
    assert translated == [{"start": 0.0, "end": 1.25, "text": "translated: Hello world"}]


def test_dubbing_speedup_limit_is_bounded():
    assert MAX_DUBBING_SPEEDUP == 1.6


@pytest.mark.parametrize("filename", ["../escape.wav", r"..\\escape.wav", ""])
def test_upload_names_cannot_escape_object_namespace(filename: str):
    with pytest.raises(ValueError):
        validate_upload(filename, "audio/wav", 10)
