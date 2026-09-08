# LingoWave architecture notes

## Product boundary

The existing `Video_Translator.py` and local `web_gui.py` remain available for local development and output comparison. The SaaS surface is a separate React application and an API boundary; GPU code does not run in the web process. In serverless mode, API Gateway invokes `backend/app/lambda_handler.py`; `backend/app/main.py` still owns auth, media inspection, jobs, credits, and consent records, while `backend/app/worker.py` owns media execution outside Lambda.

## Provider boundary

The API submits an immutable job spec. Workers resolve configured implementations through the provider protocols in `backend/app/providers.py` and the real adapters in `backend/app/providers_real.py`: Whisper transcription, Google/deep-translator translation, selective Hy-MT2 refinement, pinned VoxCPM2 voice cloning, Demucs stems, and DeepFilterNet enhancement. Dubbing places generated clips inside their available timing windows, applies at most the configured `DUBBING_MAX_SPEEDUP`, pads shorter clips, and fails loudly when safe timing cannot be maintained. DeepFilterNet is the production/default noise path; a constrained development environment may opt into the explicit FFmpeg `afftdn` fallback with `NOISE_REMOVAL_FALLBACK=ffmpeg-afftdn`, which does not claim ML enhancement. Lip sync is deliberately exposed as an unavailable capability until a commercially cleared, configured provider is installed; requests are rejected before credit reservation.

## Billing boundary

Credits are an auditable ledger, not a mutable balance supplied by the browser. A job reserves the server-side estimate, finalizes only after output creation, and releases the reservation on provider or infrastructure failure. Stripe hosted Checkout handles subscriptions and one-time credit packs, while the customer portal handles subscription management. Raw-body webhook verification, provider-event idempotency, and ledger reference idempotency protect the payment boundary; checkout stays disabled until deployment secrets and price IDs are supplied.

## Commercial readiness checklist

- Confirm the selected TTS model and checkpoint license for commercial SaaS before enabling voice cloning.
- Audit Whisper/WhisperX, Demucs code and checkpoints, noise-removal checkpoints, LatentSync, FFmpeg distribution, fonts, and icon assets.
- Store consent records and deletion events for reference voices.
- Enforce upload MIME/size validation and safe subprocess argument arrays.
- Keep database-backed rate limits, abuse reporting, public-figure review policy, and private signed downloads enabled before launch; automated identity verification is intentionally outside the current credential boundary.
- Keep worker telemetry honest: output duration, model time, provider/model version, retry count, input/output bytes, and wall-clock time are recorded. Estimated and actual compute cost remain null until a measured GPU profile matches the worker; expiring worker leases provide the active-worker count without inferring it from queue depth.

## Serverless database boundary

Aurora Serverless v2 is accessed through the RDS Data API using the `aurora_data_api` SQLAlchemy dialect. The Lambda process uses `NullPool` so warm execution environments do not hold open PostgreSQL connections and prevent Aurora auto-pause. The Aurora application credential is intentionally retained in Secrets Manager because the Data API requires a secret ARN; ordinary application secrets use SSM Parameter Store Standard and are loaded only at Lambda cold start. The migration Lambda runs Alembic on demand rather than during every API invocation. Idempotent migrations use information-schema helpers in `backend/app/migration_compat.py` because the Data API rejects PostgreSQL internal `CHAR` catalog result types that the standard SQLAlchemy inspector selects.

## Verification boundary

The local API, ledger, media inspection, job lifecycle, artifact delivery, consent records, rate-limit persistence, billing webhook accounting, and tool shell are covered by automated tests and browser smoke checks. Real model acceptance is tracked in `docs/AWS_MEDIA_PIPELINE_STATUS.md`; that report distinguishes local/provider evidence from the live CPU worker path and records exact VoxCPM2 timing and resource measurements. The available local runtime may still lack native `libdf`, so DeepFilterNet reports that dependency failure by default when unavailable; the explicit `NOISE_REMOVAL_FALLBACK=ffmpeg-afftdn` path is available for constrained development and is not ML evidence. GPU production acceptance still requires eligible quota capacity, while the GPU architecture remains preserved and scale-to-zero by default.
