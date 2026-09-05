# AWS Media Pipeline Status

Date: 2026-09-05
Environment: `eu-north-1`
Public application: `https://d3ncg3eqih0ccj.cloudfront.net`
GPU quota: `Running On-Demand G/VT instances = 0`, request `CASE_OPENED`, untouched

## Current migration

LingoWave now uses pinned VoxCPM2 as its sole production voice provider:

    real media → public API → private S3 → SQS → CPU/GPU worker
    → Demucs → Whisper → GoogleTranslator → optional Hy-MT2
    → VoxCPM2 → bounded FFmpeg timing/mix → private S3
    → completed job → downloadable artifact

The existing API, S3, SQS/DLQ, RDS, telemetry, retries, credit ledger, artifact validation, and scale-to-zero architecture remain unchanged. Google/deep-translator is the normal translation path. Hy-MT2 is lazy and duration-triggered with at most one refinement pass per segment. AWS Translate remains an explicit optional comparison/fallback.

The exact voice model is `openbmb/VoxCPM2`, revision `32279effe8c19989596f05d353d1447f51d9e915`, package `voxcpm==2.0.3`, with 48 kHz output validation. CPU uses a runtime-selected or configured CPU-safe dtype; the planned NVIDIA T4 path uses FP16 rather than assuming BF16 support.

## Validation status

The real VoxCPM2 provider smoke test and the real short CPU API→S3→SQS→worker→output E2E are run after the immutable worker image for this migration is published. This report is updated with the measured evidence immediately after that run. No synthetic timings are presented as real model evidence.

| Gate | Status | Evidence |
|---|---|---|
| VoxCPM2 provider import/contract | PASS | Backend tests and worker image check |
| Exact model revision | PASS | Runtime/config/manifest pin |
| Real CPU VoxCPM2 inference | PENDING | New CPU worker image validation |
| Real full CPU dubbing E2E | PENDING | Public API, S3, SQS, worker, output/download |
| GPU quota | PENDING | Existing `CASE_OPENED` request preserved |
| CPU/GPU scale-to-zero | PASS/PENDING | Recheck after E2E cleanup |

## Provider and timing policy

1. Whisper produces timestamped segments.
2. GoogleTranslator translates each segment.
3. VoxCPM2 synthesizes the first target-language clip using the consented reference voice and optional reference transcript.
4. The worker measures the generated duration against the original timing window.
5. If the clip exceeds tolerance, Hy-MT2 may rewrite it once with source/context/glossary/style constraints.
6. VoxCPM2 synthesizes the rewritten text once.
7. Bounded FFmpeg speed adjustment is the final timing step; text is never truncated.

Each segment records original duration, first and refined TTS durations, before/after deviation, refinement usage, refined text, and final speed ratio. Worker stage metrics record wall time; usage telemetry records model load, peak RAM/VRAM, CPU utilization, total job time, and estimated cost.

## Required final evidence

The completed run must record:

- total/Google-translated/Hy-MT2-refined segment counts and refinement rate;
- average timing deviation before and after refinement;
- Whisper, translation, Hy-MT2, VoxCPM2, FFmpeg, and total wall time;
- VoxCPM2 model load, synthesis time, output duration, real-time factor, peak RAM, CPU utilization, and estimated cost;
- input duration, cost per input minute, final WAV/video FFprobe validation, and downloadable artifact verification;
- CPU worker desired/running/pending, GPU worker/ASG state, quota, and confirmation that expensive compute is stopped.

## Infrastructure safety

- CPU validation is temporary and must end at desired/running/pending `0/0/0`.
- GPU worker and ASG remain `0/0/0`; the quota request is not cancelled.
- The $25 AWS budget guardrail is not changed.
- No credentials are committed; image publishing uses OIDC.
- Existing private S3, SQS redrive/DLQ, RDS, retries, leases, output validation, and cost telemetry remain enabled.

## Checks

The migration gate runs backend tests, Ruff, Bandit, pip-audit, Terraform fmt/validate, CI/image build verification, the provider smoke test, and the real CPU E2E. The final values are written below only after the commands and live evidence exist.

## Final measured report

```text
TTS/VOICE PROVIDER: VoxCPM2
MODEL: openbmb/VoxCPM2
REVISION: 32279effe8c19989596f05d353d1447f51d9e915
RUNTIME: voxcpm==2.0.3
CPU DTYPE: pending measured CPU run
GPU DTYPE PLAN: float16 for NVIDIA T4; BF16 only where hardware supports it
OUTPUT SAMPLE RATE: 48000 Hz

CHATTERBOX REMOVED COMPLETELY: YES

REAL VOXCPM2 INFERENCE: PENDING
REAL FULL DUBBING E2E: PENDING
FINAL DUBBED MEDIA GENERATED: PENDING
OUTPUT DOWNLOAD VERIFIED: PENDING

VOXCPM2:
- model load time: pending
- synthesis time: pending
- generated audio duration: pending
- RTF: pending
- peak RAM: pending
- CPU utilization: pending
- estimated cost: pending

PIPELINE:
- input duration: pending
- Demucs time: pending
- Whisper time: pending
- translation time: pending
- Hy-MT2 time if triggered: pending
- VoxCPM2 time: pending
- FFmpeg time: pending
- total job time: pending
- cost/input minute: pending

INFRA:
- CPU worker state: expected 0/0/0 after test
- GPU worker/ASG state: 0/0/0
- GPU quota: CASE_OPENED, quota remains 0
- expensive compute currently running: no
- tests: pending final run
- Terraform: pending final run
- security checks: pending final run
- repo status: pending final commit/push

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

GPU approval remains a later performance step. It must not block CPU validation, and the request must remain open.
