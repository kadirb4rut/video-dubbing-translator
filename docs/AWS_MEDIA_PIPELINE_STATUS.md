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

The real Hy-MT2 CPU benchmark also completed independently: `tencent/Hy-MT2-1.8B`, revision `9a341cd1b679d3efd23b46e847b01745a71ed792`, Transformers in-process, CPU bfloat16, 156.5701 seconds for one segment. The adapter handled a plain model response through its bounded single-segment fallback; no mock translation was used.

## Validation status

The immutable CPU image was published successfully. Earlier acceptance attempts correctly stopped before compute because the GitHub OIDC role lacked ECR layer-pull and ECS deployment permissions. For the live validation, the required permissions were enabled only for the manual run, the API was temporarily configured with `ALLOW_UNMEASURED_PRICING=true`, and the CPU worker was temporarily deployed with the immutable VoxCPM2 image. The real E2E completed successfully, the API and CPU worker were restored, and the temporary acceptance policy was deleted. The permanent ECR policy and opt-in Terraform path remain available for a future explicitly authorized run.

| Gate | Status | Evidence |
|---|---|---|
| VoxCPM2 provider import/contract | PASS | Backend tests and worker image check |
| Exact model revision | PASS | Runtime/config/manifest pin |
| Real CPU VoxCPM2 inference | PASS | GitHub Actions run `33981024341`, real CPU synthesis with pinned model |
| Real full CPU dubbing E2E | PASS | GitHub Actions run `33982181160`, API→S3→SQS→CPU worker→S3→download |
| Targeted timing routing benchmark | PASS | 3 cases: fit not refined; moderate/large mismatch refined once |
| GPU quota | PENDING | Existing `CASE_OPENED` request preserved |
| CPU/GPU scale-to-zero | PASS | Final CPU `0/0/0`; GPU ASG desired `0` |

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

- CPU validation is temporary and ended at desired/running/pending `0/0/0`.
- GPU worker and ASG remain `0/0/0`; the quota request is not cancelled.
- The $25 AWS budget guardrail is not changed.
- No credentials are committed; image publishing uses OIDC.
- Existing private S3, SQS redrive/DLQ, RDS, retries, leases, output validation, and cost telemetry remain enabled.

## Checks

The migration gate runs backend tests, Ruff, Bandit, pip-audit, Terraform fmt/validate, CI/image build verification, the provider smoke test, and the real CPU E2E. The values below are taken from the live artifacts downloaded from run `33982181160` and the provider smoke artifact from run `33981024341`.

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
- average duration deviation before refinement: -2.5455%
- average duration deviation after refinement: -2.5455%
- translation time: 0.1278 s
- Hy-MT2 refinement time: 0 s (not triggered)
- separate real Hy-MT2 CPU benchmark: 156.5701 s for 1 segment; E2E refinement was not triggered

VOXCPM2:
- model load time: 69.2947 s
- synthesis time: 64.9108 s
- generated audio duration: 10.72 s
- RTF: 6.0551 (VoxCPM2 synthesis); 13.7578 (whole job)
- peak RAM: 11,695.398 MB
- CPU utilization: 124.725%
- estimated/actual cost: $0.009795

PIPELINE:
- input duration: 11.0 s
- Demucs time: 9.2720 s
- Whisper time: 12.7818 s
- translation time: 0.1278 s
- Hy-MT2 time if triggered: 0 s (not triggered)
- targeted routing benchmark: 3 segments, 2 refinement calls, maximum one pass per segment
- VoxCPM2 time: 126.8118 s stage wall time; 64.9108 s synthesis telemetry
- FFmpeg/mixing time: 0.4936 s
- total job time: 151.3362 s
- cost/input minute: $0.053427

INFRA:
- CPU worker desired/running/pending: 0/0/0 after test
- GPU worker/ASG state: 0/0/0
- GPU quota: CASE_OPENED, quota remains 0
- expensive compute currently running: no
- tests: 63 passed locally; CI image build passed; live E2E passed
- Terraform: fmt/validate passed locally; temporary acceptance policy removed after run
- security checks: Bandit API scope passed; pip-audit dev requirements passed
- repo status: live acceptance artifacts downloaded; follow-up docs/Terraform changes pending commit

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
