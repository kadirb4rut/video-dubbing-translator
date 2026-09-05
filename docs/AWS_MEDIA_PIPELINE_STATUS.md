# AWS Media Pipeline Status

Date: 2026-09-05  
Environment: `eu-north-1`  
Public application: `https://d3ncg3eqih0ccj.cloudfront.net`

## Executive status

The frontend and API are live on AWS. The API health check is returning HTTP 200 and reports PostgreSQL, S3, and SQS as configured. The worker architecture is provisioned with ECS EC2 GPU capacity, queue-driven autoscaling, a dead-letter queue, model-cache storage, and scale-to-zero settings. The API image containing the telemetry migration has been built and deployed.

The remaining end-to-end blocker is AWS GPU capacity: the `Running On-Demand G and VT instances` quota is currently `0`, and the increase request for one instance is still `CASE_OPENED`. Therefore the real production model inference test has not been started and no GPU instance is currently running.

## What was implemented

- CloudFront-hosted frontend and public API route.
- ECS Fargate API service with startup migration execution.
- PostgreSQL through RDS and a Secrets Manager-backed `DATABASE_URL`.
- S3 media storage with private access, encryption, CORS, lifecycle rules, and presigned downloads.
- SQS job queue with a dead-letter queue and a one-hour visibility timeout.
- ECS EC2 GPU worker service with an Auto Scaling Group, GPU task requirement, host model cache, and queue-based Application Auto Scaling.
- Queue scale-out after sustained visible messages and scale-in after a sustained empty queue.
- AWS Translate provider for production translation.
- Worker telemetry persisted in `UsageRecord`: queue wait, input/output bytes, source/target language, stage timings, worker startup, model loading, total time, real-time factor, GPU type, peak VRAM, peak RAM, retry count, success/failure, estimated and actual cost.
- API job detail now returns the persisted telemetry object.
- Idempotent job creation, retry handling, leases, heartbeat, and DLQ routing remain enabled.
- GitHub Actions OIDC image publishing to both API and worker ECR repositories with repository/branch restrictions.
- Monthly AWS budget guardrail remains enabled.

## Selected model stack

These are the production adapters currently wired in code. Final commercial-use and license review was not performed.

| Stage | Selected model or provider | Reason for selection |
|---|---|---|
| Speech to text | OpenAI Whisper `small` | Existing production adapter, multilingual transcription, segment timestamps |
| Translation | AWS Translate | Native AWS task-role integration, no external API secret required |
| Background separation | Demucs `htdemucs`, two stems | Existing dubbing path and instrumental-background output |
| Noise removal | DeepFilterNet3 | Existing real-provider adapter for explicit noise-removal jobs |
| Voice synthesis | Chatterbox multilingual `multilingual-v3` | Existing reference-voice dubbing path and multilingual output |
| Lip sync | Not enabled in the current AWS deployment | Basic dubbing path aligns generated segments to source timing; LatentSync is not part of this validation |

The repository contains reproducible benchmark entry points at `scripts/benchmarks/benchmark_providers.py` and `scripts/benchmarks/benchmark_dubbing.py`. They report stage wall time, runtime metadata, output sizes, and estimated cost when executed in a worker environment with the selected dependencies installed. A real GPU benchmark is still blocked by the AWS quota.

## Validation completed

- Backend: 45 tests passed.
- Ruff: passed.
- Bandit medium-and-higher severity scan: passed.
- Frontend production build: passed.
- Frontend `npm audit --audit-level=high`: 0 high-severity vulnerabilities.
- Terraform formatting and validation: passed.
- Terraform apply: OIDC image publishing role, worker telemetry environment, and ECR permissions applied.
- Public `/health`: HTTP 200.
- API deployment: new image with migration `0010_media_pipeline_telemetry` is serving successfully.
- ECS worker task definition revision 13 now points to the immutable GPU worker image for commit `39cf5ef`; service remains `0/0/0` by design.
- CPU worker image: built, pushed, and smoke-checked previously.
- GPU image publishing workflow: API and GPU worker image builds/pushes succeeded; both immutable images for commit `39cf5ef` were verified in ECR. The GPU worker image is approximately 4.97 GB.

## Real E2E test status

The real test input has been prepared locally as a 6.97-second video with real spoken audio generated through the macOS speech synthesizer, plus a separate authorized voice-reference recording. It is not a synthetic sine-wave fixture and is intended only for the private AWS smoke test.

The following evidence is intentionally not claimed until the quota is approved:

| Required proof | Status |
|---|---|
| Real media upload to S3 | Not run against the GPU path |
| SQS enqueue and worker auto-start | Blocked by quota |
| Real Whisper inference | Not run |
| Real Demucs separation | Not run |
| Real AWS Translate call | Not run |
| Real Chatterbox synthesis | Not run |
| Output uploaded to S3 | Not run |
| Download endpoint verified | Not run |
| Worker scale-to-zero after completion | Not run |

Consequently, there is currently no honest value for input processing time, stage timings, RTF, peak VRAM, or measured compute cost. The configured on-demand price used by telemetry for `g4dn.xlarge` in Stockholm is `$0.558/hour`; it must be replaced by measured runtime data after the test.

## Security and cost controls

- S3 buckets are private and encrypted; public access is blocked.
- API and worker database access uses Secrets Manager references rather than plaintext secret values.
- GitHub Actions assumes a short-lived AWS role through OIDC; no long-lived AWS key is stored in GitHub.
- ECR push permissions are restricted to the two application repositories.
- Root MFA was verified earlier in the setup process.
- The worker service is intentionally at desired/running/pending `0/0/0` while GPU capacity is unavailable, preventing idle GPU spend.
- The monthly budget alert remains configured at `$25` with an 80% actual/forecast threshold.

## Remaining work

1. Obtain the requested AWS GPU quota.
2. Confirm the GPU worker image finishes publishing and verify its ECR digest.
3. Point Terraform at the immutable API and GPU worker tags.
4. Submit the prepared real media job through the public API.
5. Capture queue wait, startup, model-load, per-stage, total, RTF, byte, memory, retry, and cost telemetry.
6. Verify the output download and S3 object metadata.
7. Observe the worker and ASG return to zero after the queue drains.
8. Complete the user-owned model/checkpoint license and commercial-use review before production.

Out of scope for this work: Stripe live credentials and webhooks, SES production identity, a custom domain, EKS, and marketing work.

## Reproduction references

```text
Public health: curl -fsS https://d3ncg3eqih0ccj.cloudfront.net/health
Backend tests: cd backend && PYTHONPATH=. pytest -q tests
Provider benchmark: python scripts/benchmarks/benchmark_providers.py <media> --voice-reference <wav>
Complete benchmark: python scripts/benchmarks/benchmark_dubbing.py <video> --reference-voice <wav>
Infrastructure: infrastructure/terraform
Image workflow: .github/workflows/build-images.yml
```

## Final gate

```text
E2E MEDIA PIPELINE: BLOCKED — AWS GPU quota pending
REAL MODEL INFERENCE: NOT RUN
REAL OUTPUT GENERATED: NO
OUTPUT DOWNLOAD VERIFIED: NO
SELECTED STT MODEL: Whisper small
SELECTED TTS/VOICE MODEL: Chatterbox multilingual-v3
SELECTED OTHER MAJOR MODELS: Demucs htdemucs; DeepFilterNet3; AWS Translate
GPU INSTANCE: g4dn.xlarge (planned)
INPUT DURATION: 6.97 seconds (prepared test media)
TOTAL PROCESSING TIME: unavailable — test not run
REAL-TIME FACTOR: unavailable — test not run
PEAK VRAM: unavailable — test not run
TEST COST: $0.00 AWS GPU test spend so far
ESTIMATED COMPUTE COST PER INPUT MINUTE: pending measured runtime
GPU SCALE-TO-ZERO: CONFIGURED, NOT E2E-VERIFIED
TERRAFORM: PASS
TESTS: PASS
SECURITY CHECKS: PASS
EXPENSIVE COMPUTE CURRENTLY RUNNING: NO
LICENSE REVIEW: NOT PERFORMED — USER WILL REVIEW BEFORE PRODUCTION
REMAINING BLOCKERS: AWS GPU quota request CASE_OPENED; real E2E validation pending
```
