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
    media_asset_id: str
    operation: str = Field(default="dubbing", pattern="^(dubbing|transcription|stems|noise|tts)$")
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
    media_asset_id: str
    operation: str = Field(default="dubbing", pattern="^(dubbing|transcription|stems|noise|tts)$")
    lip_sync: bool = False
    quality: str = Field(default="balanced", pattern="^(draft|balanced|studio)$")
