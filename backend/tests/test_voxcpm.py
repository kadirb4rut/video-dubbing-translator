from __future__ import annotations

from pathlib import Path

import pytest
from app.providers_real import VoxCPM2VoiceProvider
from app.voxcpm_runtime import (
    VOXCPM_MODEL_ID,
    VOXCPM_MODEL_REVISION,
    VOXCPM_OUTPUT_SAMPLE_RATE,
    resolve_device,
    resolve_dtype,
    synthesize_cloned_speech,
)


def test_voxcpm2_model_pin_and_provider_contract():
    provider = VoxCPM2VoiceProvider(device="cpu", dtype="float32")
    assert provider.name == "voxcpm2"
    assert provider.model_id == VOXCPM_MODEL_ID == "openbmb/VoxCPM2"
    assert provider.revision == VOXCPM_MODEL_REVISION == "32279effe8c19989596f05d353d1447f51d9e915"
    assert provider.last_metrics["sample_rate"] == VOXCPM_OUTPUT_SAMPLE_RATE


def test_voxcpm2_device_and_dtype_policy():
    assert resolve_device("cpu") == "cpu"
    assert resolve_dtype("cpu", "fp32") == "float32"
    assert resolve_dtype("mps", "auto") == "float32"
    assert resolve_dtype("cuda", "fp16") == "float16"
    with pytest.raises(ValueError, match="Unsupported VOXCPM_DTYPE"):
        resolve_dtype("cpu", "int8")


def test_voxcpm2_empty_text_and_reference_validation_fail_before_inference(tmp_path: Path):
    output = tmp_path / "speech.wav"
    with pytest.raises(ValueError, match="must not be empty"):
        synthesize_cloned_speech(object(), " ", None, output)
    with pytest.raises(FileNotFoundError, match="Speaker reference audio"):
        synthesize_cloned_speech(object(), "Hello", tmp_path / "missing.wav", output)


def test_voxcpm2_release_evicts_exact_model_key(monkeypatch):
    released: list[bool] = []
    monkeypatch.setattr("app.providers_real._release_torch_memory", lambda: released.append(True))
    key = ("cpu", "float32", VOXCPM_MODEL_ID, VOXCPM_MODEL_REVISION)
    VoxCPM2VoiceProvider._models[key] = object()
    try:
        provider = VoxCPM2VoiceProvider(device="cpu", dtype="float32")
        provider.release()
        assert key not in VoxCPM2VoiceProvider._models
        assert released == [True]
    finally:
        VoxCPM2VoiceProvider._models.pop(key, None)
