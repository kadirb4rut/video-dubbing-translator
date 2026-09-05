# AWS Media Pipeline Status

Date: 2026-09-05
Environment: `eu-north-1`
Public application: `https://d3ncg3eqih0ccj.cloudfront.net`
Implementation commit: `4fe48638bcb5723fdecf226ef4a4fc0dfff5f2a4`
Documentation commit: `4cd20ca`
CPU image: `520646547849.dkr.ecr.eu-north-1.amazonaws.com/lingowave-worker:4fe48638bcb5723fdecf226ef4a4fc0dfff5f2a4-cpu`
CI image build: GitHub Actions run `33956600442` — success
GPU quota: `Running On-Demand G/VT instances = 0`, request `CASE_OPENED`, untouched

## Executive summary

The hybrid translation implementation is now deployed and validated on a real CPU path. The complete production-style flow passed with a short real media asset:

    real media → public API → private S3 → SQS → CPU worker
    → Demucs → Whisper → GoogleTranslator → Chatterbox → FFmpeg
    → private S3 output → completed job → presigned download

The final dubbed MP4 was generated, downloaded, and validated with FFprobe. The tested segment fit the timing window, so Hy-MT2 was correctly not invoked in that live job. The targeted timing benchmark separately proves that fitting segments skip Hy-MT2 and moderate/large mismatches receive exactly one bounded refinement pass. A real isolated CPU Hy-MT2 inference also completed, but its measured latency makes GPU the practical production target for that selective stage.

The original live attempt failed because the previously deployed worker image did not contain `deep-translator`. That dependency was added as `deep-translator==1.11.4`, a new immutable image was built and published, and the same real E2E was rerun successfully. AWS Translate is no longer required for normal dubbing; it remains optional.

The API was restored to revision `20`, the temporary CPU service is at desired/running/pending `0/0/0`, and the GPU worker/ASG remain `0/0/0`. The GPU quota request and GPU architecture were not cancelled or removed.

## Architecture and provider policy

The existing API → S3 → SQS → worker → RDS/telemetry → artifact-download architecture remains intact. Retries, lease/heartbeat, DLQ, output validation, private encrypted S3, credit accounting, and scale-to-zero controls were preserved.

The normal dubbing strategy is:

1. `GoogleDeepTranslatorProvider` using `deep-translator==1.11.4` and `GoogleTranslator`.
2. First Chatterbox synthesis and measured speech duration.
3. At most one `tencent/Hy-MT2-1.8B` rewrite when the configured duration tolerance requires linguistic shortening.
4. A second Chatterbox synthesis, then the existing bounded FFmpeg speed adjustment as the last resort.
5. `AwsTranslateProvider` remains configurable for comparison or explicit fallback, but is not needed by the default path.

The provider abstraction is configured through environment settings. Hy-MT2 is lazy-loaded and is not constructed for segments that already fit. The worker records original duration, first TTS duration, deviation, refinement usage/text, refined TTS duration, final deviation, and final speed-adjustment ratio.

## Live AWS state after validation

| Component | Final state | Notes |
|---|---|---|
| Public API | Healthy | CloudFront `/health` returns HTTP 200 |
| API ECS service | Revision 20, `1/0/0` | 1 desired, 1 running, 0 pending |
| CPU validation worker | Revision 8, `0/0/0` | Temporary Fargate worker stopped after test |
| GPU worker service | Revision 15, `0/0/0` | No expensive GPU capacity running |
| GPU ASG | `0/0/0` | Desired/min/max capacity remains zero |
| GPU quota | Pending | On-Demand G/VT remains zero; `CASE_OPENED` request preserved |
| Pricing guardrail | Restored | Temporary test override removed; production API uses normal guardrail |

## Real full CPU E2E result

| Field | Result |
|---|---|
| Job | `d859ac1f-8b02-4e0c-905f-d03c331378f7` |
| Asset | `280b6aeb-4d4d-4b79-a1da-cb521d3185d8` |
| Input | Real MP4, 13.36 s, 640×360, 25 fps, 113,751 bytes |
| Output | Dubbed MP4, 198,681 bytes; 13.36 s container duration |
| Output media | H.264 video 640×360, AAC stereo audio 11.05 s |
| Job state | `completed`; actual credits 3; retry count 0 |
| S3 output | Private job-scoped artifact recorded by API |
| Download | Presigned artifact download succeeded; FFprobe passed |
| Real models | Demucs, Whisper, GoogleTranslator, Chatterbox, FFmpeg |
| Hy-MT2 in live job | Not triggered because the translated speech fit the timing window |

The evidence JSON and downloaded output were captured in the temporary test directory used by the AWS golden harness. No mocks, direct SQS injection, prerecorded output, or fixture translator was used.

## Translation metrics

| Metric | Value |
|---|---:|
| Total segments | 1 |
| Google-translated segments | 1 |
| Hy-MT2-refined segments | 0 |
| Refinement rate | 0% in live fit case |
| Average deviation before refinement | -14.3713% |
| Average deviation after refinement | -14.3713% |
| Translation wall time | 0.469985 s |
| Hy-MT2 refinement time | 0 s |
| First/final TTS duration | 11.44 s / 11.44 s |
| Final speed adjustment ratio | 0.856287 |
| Translation runtime | `deep-translator.GoogleTranslator` |
| Refinement policy | `hymt2`, maximum 1 pass per segment |

## Pipeline timings and resource measurements

| Stage or metric | Measured value |
|---|---:|
| Downloading | 0.219264 s |
| Audio separation | 12.843553 s |
| Whisper transcription | 14.143042 s |
| Google translation | 0.469985 s |
| Chatterbox synthesis | 143.952073 s |
| FFmpeg mixing/muxing | 0.843512 s |
| Uploading | 0.174811 s |
| Total job wall time | 174.667102 s |
| Model seconds | 172.252166 s |
| Compute startup telemetry | 403.754218 s |
| Model load | 54.664899 s |
| Real-time factor | 13.073885 |
| Peak RAM | 7204.07 MiB |
| CPU utilization | 134.324% aggregate process CPU |
| Peak VRAM | Not applicable on CPU |
| Actual estimated compute cost | $0.011305 |
| Cost per processed input minute | $0.050771 |

The compute-startup value is reported separately because it includes worker startup outside the job wall-clock interval. CPU percentages above 100% indicate multi-core process utilization.

## Targeted timing benchmark

`scripts/benchmarks/benchmark_hybrid_timing.py` was run with three controlled segment-duration cases:

| Case | Timing result | Hy-MT2 routing |
|---|---|---|
| Fit | Within tolerance | Skipped |
| Moderate mismatch | Over tolerance | One refinement pass |
| Large mismatch | Over tolerance | One refinement pass |

Result: 3 segments, 2 refinement calls, 66.67% routing refinement rate, and no segment exceeded the one-pass bound. The default benchmark is a deterministic routing/telemetry benchmark; it does not claim that synthetic durations are model inference. Real Hy-MT2 CPU inference was separately completed for one segment in `artifacts/translation-benchmark-smoke.json` at 156.5701 s. The optional local `--real-refinement` mode was not claimed as passing because the local environment lacks Torch; the live AWS path still proved real Google/TTS/FFmpeg media processing.

## CPU versus GPU decision

Practically usable on CPU now:

- API, S3, SQS, RDS status updates, retries, DLQ, and artifact downloads;
- FFprobe and FFmpeg timing/muxing;
- Whisper small, Demucs htdemucs, GoogleTranslator, Chatterbox multilingual-v3;
- final output validation and telemetry.

Technically CPU-capable but recommended for GPU in production:

- Hy-MT2-1.8B selective refinement. It ran on CPU, but the isolated one-segment inference took 156.5701 s. It should remain selective and move to GPU when the quota is approved.

The product is not redesigned around CPU. CPU is the verified fallback and validation path; GPU remains the performance upgrade for throughput and Hy-MT2 latency.

## Verification and security checks

- Backend tests: `58 passed`, one existing warning.
- Ruff: passed.
- Bandit medium severity: passed.
- `pip-audit`: passed.
- Terraform fmt/validate: passed where affected.
- CI/image build: passed; API, GPU, and CPU manifests verified for commit `4fe4863`.
- Public `/health`: passed.
- CPU output: generated, downloaded, and FFprobe-validated.
- No long-lived AWS credentials committed; GitHub image publishing uses OIDC.
- GPU quota request was not cancelled; no expensive compute remains running.

## Remaining work

1. Wait for the existing GPU quota request to be approved; do not cancel it.
2. Run the real GPU golden E2E and measure GPU startup, model load, VRAM, RTF, cost, and scale-to-zero.
3. Keep Google/deep-translator as the default and verify Hy-MT2 refinement on a live overlong segment once GPU is available.
4. Optionally enable AWS Translate only for comparison/fallback after the account service gate is resolved.
5. Reconcile the manual validation workflow's IAM prerequisites if it is to be automated; the current GitHub OIDC role is ECR-focused, so the completed live run used the AWS console for the temporary service rollout.
6. Perform the user-owned model and checkpoint license/commercial-use review before production.

## Reproduction references

- `backend/app/providers_real.py` — Google, Hy-MT2, and AWS translation adapters.
- `backend/app/worker.py` — selective timing refinement, TTS, FFmpeg, telemetry, and cost.
- `scripts/benchmarks/benchmark_hybrid_timing.py` — fit/moderate/large routing benchmark.
- `scripts/aws_golden_e2e.py` — real API/S3/SQS/worker/output/download harness.
- `.github/workflows/aws-cpu-golden-e2e.yml` — manual CPU golden workflow scaffold; IAM prerequisites remain.
- `infrastructure/terraform` — persistent services, worker definitions, scale-to-zero, and guardrails.

## Final gate

    DEFAULT TRANSLATION PROVIDER: Google Translate via deep-translator 1.11.4
    REFINEMENT PROVIDER: tencent/Hy-MT2-1.8B, selective, maximum one pass
    REAL FULL DUBBING E2E: PASS (CPU)
    FINAL DUBBED MEDIA GENERATED: YES
    OUTPUT DOWNLOAD VERIFIED: YES
    CPU worker desired/running/pending: 0/0/0
    GPU worker/ASG state: 0/0/0
    Expensive compute currently running: none
    Overall GPU acceptance: PENDING — quota CASE_OPENED remains open

LICENSE REVIEW: NOT PERFORMED — USER WILL REVIEW BEFORE PRODUCTION
