from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parents[2]


def _bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", f"sqlite:///{BASE_DIR / 'data' / 'lingowave.db'}")
    storage_backend: str = os.getenv("STORAGE_BACKEND", "local")
    local_storage_dir: Path = Path(os.getenv("LOCAL_STORAGE_DIR", str(BASE_DIR / "data" / "objects")))
    s3_bucket: str | None = os.getenv("S3_BUCKET") or None
    s3_region: str = os.getenv("AWS_REGION", "eu-central-1")
    s3_endpoint_url: str | None = os.getenv("S3_ENDPOINT_URL") or None
    sqs_queue_url: str | None = os.getenv("SQS_QUEUE_URL") or None
    sqs_endpoint_url: str | None = os.getenv("SQS_ENDPOINT_URL") or None
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "lingowave_session")
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024 * 1024)))
    max_jobs_per_user: int = int(os.getenv("MAX_JOBS_PER_USER", "2"))
    cost_profile_path: Path = Path(os.getenv("COST_PROFILE_PATH", str(BASE_DIR / "config" / "cost_profiles.json")))
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    translation_api_url: str | None = os.getenv("TRANSLATION_API_URL") or None
    translation_api_key: str | None = os.getenv("TRANSLATION_API_KEY") or None
    translation_provider: str = os.getenv("TRANSLATION_PROVIDER", "configured-api")
    whisper_model: str = os.getenv("WHISPER_MODEL", "small")
    chatterbox_device: str = os.getenv("CHATTERBOX_DEVICE", "cuda")
    demucs_model: str = os.getenv("DEMUCS_MODEL", "htdemucs")
    deepfilter_command: str = os.getenv("DEEPFILTER_COMMAND", "deepFilter")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    max_voice_seconds: int = int(os.getenv("MAX_VOICE_SECONDS", "90"))
    min_voice_seconds: int = int(os.getenv("MIN_VOICE_SECONDS", "3"))
    dev_mail_sink: bool = _bool("DEV_MAIL_SINK", True)
    retention_days: int = int(os.getenv("MEDIA_RETENTION_DAYS", "30"))


settings = Settings()


def cost_profiles() -> dict[str, dict]:
    if not settings.cost_profile_path.exists():
        return {}
    return json.loads(settings.cost_profile_path.read_text(encoding="utf-8"))
