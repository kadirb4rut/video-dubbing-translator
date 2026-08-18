from __future__ import annotations

from pydantic import BaseModel, Field


class SignupRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=256)
    display_name: str = Field(default="", max_length=120)


class LoginRequest(BaseModel):
    email: str
    password: str


class JobCreateRequest(BaseModel):
    media_asset_id: str | None = None
    project_id: str | None = None
    operation: str = Field(default="dubbing", pattern="^(dubbing|transcription|subtitle_translation|stems|noise|tts)$")
    target_language: str | None = Field(default=None, max_length=16)
    source_language: str | None = Field(default=None, max_length=16)
    preserve_voice: bool = True
    keep_background: bool = True
    lip_sync: bool = False
    quality: str = Field(default="balanced", pattern="^(draft|balanced|studio)$")
    voice_profile_id: str | None = None
    text: str | None = Field(default=None, max_length=20_000)
    stems: int = Field(default=4, ge=2, le=4)
    idempotency_key: str | None = Field(default=None, max_length=128)


class EstimateRequest(BaseModel):
    media_asset_id: str | None = None
    operation: str = Field(default="dubbing", pattern="^(dubbing|transcription|subtitle_translation|stems|noise|tts)$")
    lip_sync: bool = False
    quality: str = Field(default="balanced", pattern="^(draft|balanced|studio)$")
    text: str | None = Field(default=None, max_length=20_000)
    source_language: str | None = Field(default=None, max_length=16)
    target_language: str | None = Field(default=None, max_length=16)


class AccountUpdateRequest(BaseModel):
    display_name: str = Field(max_length=120)


class ProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PasswordResetRequest(BaseModel):
    email: str


class PasswordResetConfirmRequest(BaseModel):
    token: str
    password: str = Field(min_length=12, max_length=256)


class AbuseReportRequest(BaseModel):
    event_type: str = Field(default="abuse_report", max_length=64)
    target_type: str | None = Field(default=None, max_length=32)
    target_id: str | None = None
    description: str = Field(min_length=10, max_length=4000)


class VoiceSynthesisRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)
    language: str = Field(min_length=2, max_length=16)


class MediaPresignRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(min_length=3, max_length=120)
    size_bytes: int = Field(gt=0)


class GpuProfileRequest(BaseModel):
    provider: str = Field(max_length=80)
    gpu_type: str = Field(max_length=80)
    region: str = Field(max_length=40)
    pricing_mode: str = Field(max_length=24)
    hourly_price_usd: float | None = None
    startup_seconds: float | None = None
    model_load_seconds: float | None = None
    processed_minutes_per_hour: float | None = None
    measured: bool = False
    metadata: dict = Field(default_factory=dict)


class ModelVersionRequest(BaseModel):
    provider: str = Field(max_length=80)
    model_name: str = Field(max_length=160)
    version: str = Field(max_length=160)
    artifact_license: str | None = None
    enabled: bool = False
    metadata: dict = Field(default_factory=dict)
