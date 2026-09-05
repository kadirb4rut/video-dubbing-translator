from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

_CONFIG_MODULE = Path(__file__).resolve()
BASE_DIR = next(
    (parent for parent in _CONFIG_MODULE.parents if (parent / "config").is_dir()),
    _CONFIG_MODULE.parents[2],
)


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
    s3_presign_endpoint_url: str | None = os.getenv("S3_PRESIGN_ENDPOINT_URL") or None
    sqs_queue_url: str | None = os.getenv("SQS_QUEUE_URL") or None
    sqs_endpoint_url: str | None = os.getenv("SQS_ENDPOINT_URL") or None
    sqs_visibility_timeout_seconds: int = int(os.getenv("SQS_VISIBILITY_TIMEOUT_SECONDS", "3600"))
    session_cookie_name: str = os.getenv("SESSION_COOKIE_NAME", "lingowave_session")
    session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))
    cookie_secure: bool = _bool("COOKIE_SECURE", False)
    max_upload_bytes: int = int(os.getenv("MAX_UPLOAD_BYTES", str(4 * 1024 * 1024 * 1024)))
    max_jobs_per_user: int = int(os.getenv("MAX_JOBS_PER_USER", "2"))
    cost_profile_path: Path = Path(os.getenv("COST_PROFILE_PATH", str(BASE_DIR / "config" / "cost_profiles.json")))
    frontend_origin: str = os.getenv("FRONTEND_ORIGIN", "http://localhost:5173")
    google_client_id: str | None = os.getenv("GOOGLE_CLIENT_ID") or None
    google_client_secret: str | None = os.getenv("GOOGLE_CLIENT_SECRET") or None
    google_redirect_uri: str | None = os.getenv("GOOGLE_REDIRECT_URI") or None
    google_state_ttl_seconds: int = int(os.getenv("GOOGLE_STATE_TTL_SECONDS", "600"))
    translation_api_url: str | None = os.getenv("TRANSLATION_API_URL") or None
    translation_api_key: str | None = os.getenv("TRANSLATION_API_KEY") or None
    # Google/deep-translator is the fast default. Hy-MT2 is loaded lazily only
    # for duration-aware linguistic refinement; AWS Translate remains an
    # explicit optional comparison/primary provider.
    translation_provider: str = os.getenv("TRANSLATION_PROVIDER", "google-deep-translator")
    translation_refinement_provider: str = os.getenv("TRANSLATION_REFINEMENT_PROVIDER", "hymt2")
    translation_model: str = os.getenv("TRANSLATION_MODEL", "tencent/Hy-MT2-1.8B")
    translation_model_revision: str = os.getenv("TRANSLATION_MODEL_REVISION", "9a341cd1b679d3efd23b46e847b01745a71ed792")
    translation_device: str = os.getenv("TRANSLATION_DEVICE", "auto")
    translation_dtype: str = os.getenv("TRANSLATION_DTYPE", "auto")
    translation_batch_size: int = int(os.getenv("TRANSLATION_BATCH_SIZE", "4"))
    translation_max_chars_per_batch: int = int(os.getenv("TRANSLATION_MAX_CHARS_PER_BATCH", "4000"))
    translation_max_new_tokens: int = int(os.getenv("TRANSLATION_MAX_NEW_TOKENS", "1024"))
    translation_duration_tolerance: float = float(os.getenv("TRANSLATION_DURATION_TOLERANCE", "0.2"))
    translation_refinement_max_passes: int = int(os.getenv("TRANSLATION_REFINEMENT_MAX_PASSES", "1"))
    translation_max_retries: int = int(os.getenv("TRANSLATION_MAX_RETRIES", "1"))
    whisper_model: str = os.getenv("WHISPER_MODEL", "small")
    voxcpm_device: str = os.getenv("VOXCPM_DEVICE", "auto")
    voxcpm_dtype: str = os.getenv("VOXCPM_DTYPE", "auto")
    voxcpm_model: str = os.getenv("VOXCPM_MODEL", "openbmb/VoxCPM2")
    voxcpm_model_revision: str = os.getenv("VOXCPM_MODEL_REVISION", "32279effe8c19989596f05d353d1447f51d9e915")
    voxcpm_output_sample_rate: int = int(os.getenv("VOXCPM_OUTPUT_SAMPLE_RATE", "48000"))
    voxcpm_cfg_value: float = float(os.getenv("VOXCPM_CFG_VALUE", "2.0"))
    voxcpm_inference_steps: int = int(os.getenv("VOXCPM_INFERENCE_STEPS", "10"))
    demucs_model: str = os.getenv("DEMUCS_MODEL", "htdemucs")
    deepfilter_command: str = os.getenv("DEEPFILTER_COMMAND", "deepFilter")
    noise_removal_fallback: str = os.getenv("NOISE_REMOVAL_FALLBACK", "")
    rate_limit_per_minute: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "30"))
    max_voice_seconds: int = int(os.getenv("MAX_VOICE_SECONDS", "90"))
    min_voice_seconds: int = int(os.getenv("MIN_VOICE_SECONDS", "3"))
    dev_mail_sink: bool = _bool("DEV_MAIL_SINK", not os.getenv("DATABASE_URL") or os.getenv("DATABASE_URL", "").startswith("sqlite"))
    mail_provider: str = os.getenv("MAIL_PROVIDER", "dev")
    mail_from: str = os.getenv("MAIL_FROM", "no-reply@lingowave.local")
    smtp_host: str | None = os.getenv("SMTP_HOST") or None
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_username: str | None = os.getenv("SMTP_USERNAME") or None
    smtp_password: str | None = os.getenv("SMTP_PASSWORD") or None
    retention_days: int = int(os.getenv("MEDIA_RETENTION_DAYS", "30"))
    stripe_secret_key: str | None = os.getenv("STRIPE_SECRET_KEY") or None
    stripe_webhook_secret: str | None = os.getenv("STRIPE_WEBHOOK_SECRET") or None
    stripe_success_url: str = os.getenv("STRIPE_SUCCESS_URL", "http://localhost:5173/?billing=success")
    stripe_cancel_url: str = os.getenv("STRIPE_CANCEL_URL", "http://localhost:5173/?billing=cancelled")
    stripe_billing_portal_return_url: str = os.getenv("STRIPE_BILLING_PORTAL_RETURN_URL", "http://localhost:5173/?billing=portal")
    stripe_price_creator: str | None = os.getenv("STRIPE_PRICE_CREATOR") or None
    stripe_price_pro: str | None = os.getenv("STRIPE_PRICE_PRO") or None
    stripe_price_studio: str | None = os.getenv("STRIPE_PRICE_STUDIO") or None
    stripe_price_credits_starter: str | None = os.getenv("STRIPE_PRICE_CREDITS_STARTER") or None
    stripe_price_credits_growth: str | None = os.getenv("STRIPE_PRICE_CREDITS_GROWTH") or None
    stripe_price_credits_scale: str | None = os.getenv("STRIPE_PRICE_CREDITS_SCALE") or None


settings = Settings()


def cost_profiles() -> dict[str, dict]:
    if not settings.cost_profile_path.exists():
        return {}
    return json.loads(settings.cost_profile_path.read_text(encoding="utf-8"))
