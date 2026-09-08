from scripts.production_readiness import collect_checks


def test_production_readiness_accepts_aurora_data_api(monkeypatch, tmp_path):
    values = {
        "DATABASE_URL": "postgresql+auroradataapi://:@/lingowave",
        "AURORA_CLUSTER_ARN": "arn:aws:rds:eu-north-1:123:cluster:lingowave",
        "AURORA_SECRET_ARN": "arn:aws:secretsmanager:eu-north-1:123:secret:lingowave",
        "DB_POOL_MODE": "null",
        "STORAGE_BACKEND": "s3",
        "S3_BUCKET": "private-media",
        "SQS_QUEUE_URL": "https://sqs.example/queue",
        "COOKIE_SECURE": "true",
        "FRONTEND_ORIGIN": "https://app.example.com",
        "ALLOW_UNMEASURED_PRICING": "false",
        "GPU_TYPE": "approved-gpu",
        "AWS_REGION": "eu-north-1",
        "MAIL_PROVIDER": "ses",
        "MAIL_FROM": "no-reply@example.com",
        "STRIPE_SECRET_KEY": "sk_live_placeholder",
        "STRIPE_WEBHOOK_SECRET": "whsec_placeholder",
        "STRIPE_SUCCESS_URL": "https://app.example.com/?billing=success",
        "STRIPE_CANCEL_URL": "https://app.example.com/?billing=cancelled",
        "STRIPE_BILLING_PORTAL_RETURN_URL": "https://app.example.com/?billing=portal",
    }
    values.update({name: f"price_{name.removeprefix('STRIPE_PRICE_').lower()}" for name in ("STRIPE_PRICE_CREATOR", "STRIPE_PRICE_PRO", "STRIPE_PRICE_STUDIO", "STRIPE_PRICE_CREDITS_STARTER", "STRIPE_PRICE_CREDITS_GROWTH", "STRIPE_PRICE_CREDITS_SCALE")})
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    profiles = tmp_path / "cost_profiles.json"
    profiles.write_text('{"transcription":{"measured":true}}', encoding="utf-8")
    manifest = tmp_path / "model_release_manifest.json"
    manifest.write_text('{"artifacts":[{"commercial_status":"cleared","checkpoint_snapshot":"sha256:approved"}]}', encoding="utf-8")
    monkeypatch.setenv("COST_PROFILE_PATH", str(profiles))
    monkeypatch.setenv("MODEL_RELEASE_MANIFEST", str(manifest))

    assert all(check["status"] == "pass" for check in collect_checks())
