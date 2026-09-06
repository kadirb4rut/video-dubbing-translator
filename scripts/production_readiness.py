"""Check the deployment gates required before enabling public processing.

The command intentionally reports configuration status, not secret values. It
is strict by default: a deployment is not ready while pricing, billing, mail,
or model-release evidence is incomplete.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_STRIPE_VARS = (
    "STRIPE_SECRET_KEY",
    "STRIPE_WEBHOOK_SECRET",
    "STRIPE_PRICE_CREATOR",
    "STRIPE_PRICE_PRO",
    "STRIPE_PRICE_STUDIO",
    "STRIPE_PRICE_CREDITS_STARTER",
    "STRIPE_PRICE_CREDITS_GROWTH",
    "STRIPE_PRICE_CREDITS_SCALE",
)
REQUIRED_STRIPE_URLS = (
    "STRIPE_SUCCESS_URL",
    "STRIPE_CANCEL_URL",
    "STRIPE_BILLING_PORTAL_RETURN_URL",
)


def _present(name: str) -> bool:
    return bool(os.getenv(name, "").strip())


def _is_local_url(value: str | None) -> bool:
    unspecified_host = ".".join("0" for _ in range(4))
    return not value or any(host in value.lower() for host in ("localhost", "127.0.0.1", unspecified_host))


def _check(checks: list[dict[str, object]], name: str, passed: bool, detail: str) -> None:
    checks.append({"name": name, "status": "pass" if passed else "fail", "detail": detail})


def _load_json(path: Path) -> tuple[object | None, str | None]:
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, str(exc)


def collect_checks() -> list[dict[str, object]]:
    checks: list[dict[str, object]] = []
    database_url = os.getenv("DATABASE_URL", "")
    _check(checks, "postgresql", database_url.startswith(("postgresql://", "postgresql+psycopg://")), "DATABASE_URL must use PostgreSQL")
    _check(checks, "private-storage", os.getenv("STORAGE_BACKEND") == "s3" and _present("S3_BUCKET"), "S3 storage and bucket must be configured")
    _check(checks, "queue", _present("SQS_QUEUE_URL"), "SQS_QUEUE_URL must be configured")
    _check(checks, "secure-cookies", os.getenv("COOKIE_SECURE", "").lower() in {"1", "true", "yes", "on"}, "COOKIE_SECURE must be true")
    _check(checks, "public-origin", not _is_local_url(os.getenv("FRONTEND_ORIGIN")), "FRONTEND_ORIGIN must not be local")
    _check(checks, "pricing-override", os.getenv("ALLOW_UNMEASURED_PRICING", "false").lower() not in {"1", "true", "yes", "on"}, "ALLOW_UNMEASURED_PRICING must remain false")

    profile_path = Path(os.getenv("COST_PROFILE_PATH", str(ROOT / "config" / "cost_profiles.json")))
    profiles, profile_error = _load_json(profile_path)
    profiles_ok = isinstance(profiles, dict) and bool(profiles)
    _check(checks, "pricing-profiles-present", profiles_ok and profile_error is None, "cost profile file must contain an object")
    measured = profiles_ok and all(
        isinstance(profile, dict) and (profile.get("enabled", True) is False or profile.get("measured") is True)
        for profile in profiles.values()
    )
    _check(checks, "pricing-profiles-measured", measured, "every enabled production credit profile must be measured")

    manifest_path = Path(os.getenv("MODEL_RELEASE_MANIFEST", str(ROOT / "config" / "model_release_manifest.json")))
    manifest, manifest_error = _load_json(manifest_path) if manifest_path.exists() else (None, "file does not exist")
    artifacts = manifest.get("artifacts") if isinstance(manifest, dict) else None
    manifest_ok = (
        manifest_error is None
        and isinstance(artifacts, list)
        and bool(artifacts)
        and all(
            isinstance(artifact, dict)
            and artifact.get("commercial_status") == "cleared"
            and artifact.get("checkpoint_snapshot")
            and not str(artifact.get("checkpoint_snapshot")).lower().startswith(("replace", "pending"))
            for artifact in artifacts
        )
    )
    _check(checks, "model-release-manifest", manifest_ok, "exact checkpoint snapshots and commercial clearance are required")

    _check(checks, "gpu-profile", _present("GPU_TYPE") and _present("AWS_REGION"), "GPU_TYPE and AWS_REGION must identify measured worker runs")
    _check(checks, "noise-provider", not _present("NOISE_REMOVAL_FALLBACK"), "the FFmpeg noise fallback must be disabled")

    mail_provider = os.getenv("MAIL_PROVIDER", "dev").lower()
    mail_ok = mail_provider in {"smtp", "ses"} and _present("MAIL_FROM") and not os.getenv("MAIL_FROM", "").endswith(".local")
    _check(checks, "mail", mail_ok, "SMTP or SES with a verified non-local sender is required")

    billing_ok = all(_present(name) for name in REQUIRED_STRIPE_VARS) and all(not _is_local_url(os.getenv(name)) for name in REQUIRED_STRIPE_URLS)
    _check(checks, "stripe", billing_ok, "Stripe secret, webhook, six price IDs, and public return URLs are required")
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    args = parser.parse_args()
    checks = collect_checks()
    passed = sum(check["status"] == "pass" for check in checks)
    failed = len(checks) - passed
    result = {"ready": failed == 0, "checks": checks, "summary": {"passed": passed, "failed": failed}}
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for check in checks:
            print(f"[{str(check['status']).upper()}] {check['name']}: {check['detail']}")
        print(f"\nready={result['ready']} passed={passed} failed={failed}")
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    sys.exit(main())
