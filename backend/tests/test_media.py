from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from app.media import inspect_media, validate_upload
from app.providers_real import (
    AwsTranslateProvider,
    DeepFilterNetNoiseProvider,
    FixtureTranslationProvider,
    ProviderUnavailable,
)
from app.worker import MAX_DUBBING_SPEEDUP


def test_api_container_contract_includes_ffmpeg_for_upload_inspection():
    dockerfile = Path(__file__).parents[2] / "backend" / "Dockerfile"
    contents = dockerfile.read_text(encoding="utf-8")
    assert "apt-get install -y --no-install-recommends ffmpeg" in contents
    assert "rm -rf /var/lib/apt/lists/*" in contents


def test_cpu_worker_contract_includes_deepfilter_native_runtime():
    backend_dir = Path(__file__).parents[2] / "backend"
    requirements = backend_dir / "requirements.worker-cpu.txt"
    contents = requirements.read_text(encoding="utf-8")
    common_contents = (backend_dir / "requirements.worker-common.txt").read_text(encoding="utf-8")
    assert "-r requirements.worker-common.txt" in contents
    contents = f"{contents}\n{common_contents}"
    assert "deepfilternet==0.5.6" in contents
    assert "deepfilterlib==0.5.6" in contents


def test_gpu_worker_contract_scales_to_zero_and_reuses_live_host_model_cache():
    terraform = Path(__file__).parents[2] / "infrastructure" / "terraform" / "main.tf"
    contents = terraform.read_text(encoding="utf-8")
    assert "min_size            = 0" in contents
    assert "desired_capacity    = 0" in contents
    assert 'desired_count   = var.worker_desired_count' in contents
    assert "sourceVolume" in contents and '"model-cache"' in contents
    assert 'host_path = "/var/lib/lingowave/model-cache"' in contents
    assert 'XDG_CACHE_HOME", value = "/home/lingowave/.cache"' in contents


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
