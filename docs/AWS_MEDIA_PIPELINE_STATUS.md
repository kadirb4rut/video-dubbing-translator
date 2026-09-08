# AWS Media Pipeline Status

Date: 2026-09-08
Environment: `eu-north-1`
Public application: `https://d3ncg3eqih0ccj.cloudfront.net`
GPU quota: `Running On-Demand G/VT instances = 0`, request `CASE_OPENED`, untouched

## Current migration

LingoWave now uses pinned VoxCPM2 as its sole production voice provider:

    real media → public API → private S3 → SQS → CPU/GPU worker
    → Demucs → Whisper → GoogleTranslator → optional Hy-MT2
    → VoxCPM2 → bounded FFmpeg timing/mix → private S3
    → completed job → downloadable artifact

The existing S3, SQS/DLQ, telemetry, retries, credit ledger, artifact validation, and scale-to-zero safeguards remain in place. The production API now runs through CloudFront → API Gateway HTTP API → Lambda → Aurora Data API. Google/deep-translator is the normal translation path. Hy-MT2 is lazy and duration-triggered with at most one refinement pass per segment. AWS Translate remains an explicit optional comparison/fallback.

The exact voice model is `openbmb/VoxCPM2`, revision `32279effe8c19989596f05d353d1447f51d9e915`, package `voxcpm==2.0.3`, with 48 kHz output validation. CPU uses a runtime-selected or configured CPU-safe dtype; the planned NVIDIA T4 path uses FP16 rather than assuming BF16 support.

The real Hy-MT2 CPU benchmark also completed independently: `tencent/Hy-MT2-1.8B`, revision `9a341cd1b679d3efd23b46e847b01745a71ed792`, Transformers in-process, CPU bfloat16, 156.5701 seconds for one segment. The adapter handled a plain model response through its bounded single-segment fallback; no mock translation was used.

## Google Auth production deployment

Google OAuth is live on the production CloudFront origin. The serverless Lambda API reads the three Google OAuth values from the existing SSM SecureString parameters populated from the Secrets Manager integration; the legacy ECS `api_secrets` mapping was removed with the legacy API. The Lambda role retains only the Secrets Manager/SSM, Aurora Data API, S3, SQS, and logging permissions required by the serverless path.

Production migration `0013_google_oauth_identities` is applied. The revision ID is intentionally shorter than the migration filename because PostgreSQL's existing `alembic_version.version_num` column is `varchar(32)`. The live API is Lambda-backed and healthy; there is no always-on API ECS task.

Live verification passed:

- `/api/auth/google/config` returned `{"enabled":true}`;
- `/api/auth/google/login` returned a Google 303 redirect;
- the callback returned successfully to the application;
- a real Google account created a new LingoWave user;
- repeated login with the linked Google identity worked;
- the same verified email linked safely to an existing local account;
- email/password login, reload session, logout invalidation, and cancelled OAuth were verified.

No OAuth secret values were printed, committed, or exposed. No manual user action remains for this integration. Test user records created during live validation remain in the production database unless explicitly cleaned up later.

## Validation status

The immutable prewarmed CPU image is now deployed with `VOXCPM_ALLOW_DOWNLOAD=false`; the GPU architecture image is also pinned but remains at zero capacity. The final real E2E used the production serverless API and the queue-driven CPU worker. The checked-in credit profiles contain evidence-backed internal rates for every enabled core operation; the lip-sync profile remains explicitly disabled. ECR was pruned to four explicitly retained API manifests and four explicitly retained worker manifests, with the lifecycle policy still active.

| Gate | Status | Evidence |
|---|---|---|
| VoxCPM2 provider import/contract | PASS | Backend tests and worker image check |
| Exact model revision | PASS | Runtime/config/manifest pin |
| Real CPU VoxCPM2 inference | PASS | GitHub Actions run `33981024341`, real CPU synthesis with pinned model |
| Real full CPU dubbing E2E | PASS | Live serverless run `55135cea-53b7-454a-902f-c7719ffb83c0`: API→S3→SQS→Aurora CPU worker→S3→download |
| Targeted timing routing benchmark | PASS | 3 cases: fit not refined; moderate/large mismatch refined once |
| GPU quota | PENDING | Existing `CASE_OPENED` request preserved |
| CPU/GPU scale-to-zero | PASS | Live queue-driven CPU `0 → 1 → 0`; final CPU `0/0/0`; GPU ASG desired `0` |

## Latest live serverless CPU E2E

On 2026-09-08 the direct production path was validated after routing the CPU
worker through Aurora Data API. The real 13.2-second black-video fixture was
submitted through the public CloudFront API. The queue alarm automatically
started the CPU Fargate Spot worker (`desired 0 → 1`); the first start pulled
the 5.42 GiB prewarmed image, then the worker claimed the job from SQS,
updated Aurora, ran Demucs, Whisper, GoogleTranslator, and VoxCPM2 on CPU,
mixed with FFmpeg, uploaded the MP4 to private S3, and the test harness
downloaded and ffprobe-validated the signed artifact. The worker log showed
VoxCPM2 loading from the baked local snapshot; no VoxCPM2 runtime download
occurred. Once SQS and in-flight messages were empty, the scale-in alarm
returned the service to `desired/running/pending 0/0/0` without manual
capacity changes.

No GPU capacity was started and the pending GPU quota request was not changed.

## Provider and timing policy

1. Whisper produces timestamped segments.
2. GoogleTranslator translates each segment.
3. VoxCPM2 synthesizes the first target-language clip using the consented reference voice and optional reference transcript.
4. The worker measures the generated duration against the original timing window.
5. If the clip exceeds tolerance, Hy-MT2 may rewrite it once with source/context/glossary/style constraints.
6. VoxCPM2 synthesizes the rewritten text once.
7. Bounded FFmpeg speed adjustment is the final timing step; text is never truncated.

Each segment records original duration, first and refined TTS durations, before/after deviation, refinement usage, refined text, and final speed ratio. Worker stage metrics record wall time; usage telemetry records model load, peak RAM/VRAM, CPU utilization, total job time, and estimated cost.

## Live Chrome UX validation

On 2026-09-06 the production CloudFront application was tested in Chrome with a real Google-authenticated test user. The 15-second video `input-highlight-15s.mp4` completed the real API → S3 → SQS → CPU worker → Whisper → Google translation → VoxCPM2 → FFmpeg → S3 artifact path and rendered a completed `dubbed.mp4` result with a working signed download/open action. The real 12.8-second SampleLib audio was used for consented Voice Studio reference creation, Stem Splitter, Voice Studio speech generation, and the initial media-processing checks. A separate 12-second public-domain speech WAV was used for successful Transcription and Noise Remover validation.

The following live product flows passed: Google login, video upload and FFprobe inspection, dubbing progress/status, downloadable dubbed result, four-stem export, speech transcription with editable subtitle preview, consented voice storage, VoxCPM2 speech generation, and Noise Remover original/enhanced audio comparison. A music-only input correctly produced an empty-transcript failure; the speech asset then produced valid SRT/VTT/TXT artifacts. DeepFilterNet initially exposed a real `torchaudio.backend` compatibility failure; the provider now falls back to real FFmpeg `afftdn` processing only when the explicit fallback setting is enabled, while the default DeepFilterNet production path remains unchanged.

UI fixes deployed to CloudFront: media-required actions are disabled until a file is uploaded, estimates are requested before enabling a processing action, a failed estimate disables the action instead of allowing a guaranteed API failure, consented voice is required before dubbing, pricing failures remain visible in the cost panel, Noise Remover is labelled for audio or video, and error/warning toasts no longer use a misleading green success check. The production UI had no Chrome console errors or warnings during the final pass. The mobile Playwright check at 390×844 passed with no horizontal overflow; desktop session/reload persistence also passed.

## Required final evidence

The completed run must record:

- total/Google-translated/Hy-MT2-refined segment counts and refinement rate;
- average timing deviation before and after refinement;
- Whisper, translation, Hy-MT2, VoxCPM2, FFmpeg, and total wall time;
- VoxCPM2 model load, synthesis time, output duration, real-time factor, peak RAM, CPU utilization, and estimated cost;
- input duration, cost per input minute, final WAV/video FFprobe validation, and downloadable artifact verification;
- CPU worker desired/running/pending, GPU worker/ASG state, quota, and confirmation that expensive compute is stopped.

## Infrastructure safety

- The final CPU validation ended at desired/running/pending `0/0/0`; the
  queue-driven autoscaler is responsible for future `0 → N → 0` operation.
- GPU worker and ASG remain `0/0/0`; the quota request is not cancelled.
- The $25 AWS budget guardrail is not changed.
- No credentials are committed; image publishing uses OIDC.
- Existing private S3, SQS redrive/DLQ, Aurora Data API, retries, leases, output validation, and cost telemetry remain enabled.

## Checks

The migration gate runs backend tests, Ruff, Bandit, pip-audit, Terraform fmt/validate, CI/image build verification, the provider smoke test, and the real CPU E2E. The values below use the final direct live artifact from 2026-09-08; earlier runs remain useful historical evidence.

## Final measured report

```text
TTS/VOICE PROVIDER: VoxCPM2
MODEL: openbmb/VoxCPM2
REVISION: 32279effe8c19989596f05d353d1447f51d9e915
RUNTIME: voxcpm==2.0.3
CPU DTYPE: bfloat16
GPU DTYPE PLAN: float16 for NVIDIA T4; BF16 only where hardware supports it
OUTPUT SAMPLE RATE: 48000 Hz

CHATTERBOX REMOVED COMPLETELY: YES

REAL VOXCPM2 INFERENCE: PASS
REAL FULL DUBBING E2E: PASS
FINAL DUBBED MEDIA GENERATED: YES
OUTPUT DOWNLOAD VERIFIED: YES

TRANSLATION METRICS:
- total segments: 1
- Google-translated segments: 1
- Hy-MT2-refined segments: 0
- refinement rate: 0%
- average duration deviation before refinement: -26.0606%
- average duration deviation after refinement: -26.0606%
- translation time: 0.1246 s
- Hy-MT2 refinement time: 0 s (not triggered)
- separate real Hy-MT2 CPU benchmark: 156.5701 s for 1 segment; E2E refinement was not triggered

VOXCPM2:
- model load time: 21.0986 s
- synthesis time: 53.9630 s model telemetry / 66.2701 s stage wall time
- generated audio duration: 9.76 s
- RTF: 5.5290 (VoxCPM2 synthesis); 7.0772 (whole job)
- peak RAM: 11,272.195 MB
- CPU utilization: 131.979%
- estimated/actual cost: $0.006046

PIPELINE:
- input duration: 13.2 s
- Demucs time: 7.9456 s
- Whisper time: 13.3249 s
- translation time: 0.1246 s
- Hy-MT2 time if triggered: 0 s (not triggered)
- targeted routing benchmark: 3 segments, 2 refinement calls, maximum one pass per segment
- VoxCPM2 time: 66.2701 s stage wall time; 53.9630 s synthesis telemetry
- FFmpeg/mixing time: 0.4425 s
- upload time: 0.2709 s
- total processing time: 93.4195 s
- queue wait before worker claim: 330.3478 s (includes image pull/startup)
- cost/input minute: $0.027482

INFRA:
- CPU worker desired/running/pending: 0/0/0 after test; final task definition 25
- GPU worker/ASG state: 0/0/0
- GPU quota: CASE_OPENED, quota remains 0
- expensive compute currently running: no
- tests: backend suite, frontend production build, npm audit, live artifact download, and ffprobe passed
- Terraform: fmt/validate/plan/apply passed; Lambda/API, prewarmed CPU, GPU pin, Aurora routing, and CPU autoscaling applied
- security checks: Ruff passed for the full backend; Bandit passed at the CI medium-severity threshold (low subprocess/URL findings remain informational); pip-audit and npm audit found no known vulnerabilities
- ECR: API reduced to 4 manifests/1.04 GiB logical; worker reduced to 4 manifests/15.90 GiB logical; retention audit in `docs/ecr-retention-audit.md`
- repo status: clean after final documentation/test commit on `codex/production-saas`

GOOGLE AUTH:
- implementation: PASS
- live Google login: PASS
- new Google user: PASS
- account linking: PASS
- email/password login: PASS
- session: PASS
- logout: PASS
- database migration: PASS (`0013_google_oauth_identities`)
- secrets exposed: NO
- frontend build: PASS
- backend tests: PASS (82 passed, 1 deprecation warning)
- security checks: PASS
- Terraform: PASS
- live API: PASS (CloudFront → API Gateway → Lambda; no API ECS service)
- manual user action still required: NONE

LICENSE REVIEW:
NOT PERFORMED — USER WILL REVIEW BEFORE PRODUCTION
```

## Reproduction commands

    curl -fsS https://d3ncg3eqih0ccj.cloudfront.net/health
    PYTHONPATH=backend:. pytest -q backend/tests
    PYTHONPATH=backend python scripts/benchmarks/benchmark_hybrid_timing.py --output artifacts/hybrid-timing-routing.json
    python scripts/aws_golden_e2e.py --api-url https://<cloudfront-host> --media <real-video.mp4> --voice <authorized-reference.wav> --email <test-email> --password <test-password> --target-language es --output-dir artifacts/aws-golden-e2e
    terraform fmt -check -recursive infrastructure/terraform
    terraform -chdir=infrastructure/terraform validate

GPU approval remains a later performance step. It did not block CPU validation, and the request remains open. The manual acceptance permissions were removed after the run; the CPU worker is at `0/0/0`, the GPU ASG desired capacity is `0`, and the $25 budget guardrail was not changed.
