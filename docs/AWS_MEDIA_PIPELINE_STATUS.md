# AWS Media Pipeline Status

Date: 2026-09-05
Environment: `eu-north-1`
Public application: `https://d3ncg3eqih0ccj.cloudfront.net`
Implementation commit: `4fe48638bcb5723fdecf226ef4a4fc0dfff5f2a4`
Documentation commit: `da36b68`
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
| API ECS service | Revision 20, `1/1/0` | 1 desired, 1 running, 0 pending |
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

## Architecture before this goal

The repository already had a CloudFront frontend, ECS API, S3 media storage, SQS job queue, RDS persistence, ECR images, Secrets Manager references, CloudWatch logs, Terraform-managed infrastructure, and provider abstractions. The remaining gap was evidence that a real changed-language dubbing job could cross the complete public API, storage, asynchronous worker, model, output, database, and download path. The GPU image/build and quota path were also not yet a reliable acceptance path.

## Architecture after this goal

The production-style path is now:

    Browser / frontend
        -> CloudFront
        -> ECS Fargate API
        -> private encrypted S3 input
        -> SQS queue and DLQ
        -> CPU Fargate worker or ECS EC2 GPU worker/ASG
        -> real model providers and FFmpeg
        -> private S3 output/artifacts
        -> RDS job, artifact, usage, and telemetry records
        -> API completion and presigned download
        -> worker and ASG scale-to-zero

The goal did not introduce AWS Batch, EKS, or a second job-submission system. The existing provider/worker contract remains portable between CPU and GPU. GPU capacity is retained as a later performance path and is not continuously running.

## AWS services used

- CloudFront for frontend hosting and API routing.
- ECS Fargate for the API and temporary CPU validation worker.
- ECS EC2 capacity provider and Auto Scaling Group for the zero-idle GPU worker.
- Private encrypted S3 for source media, voice references, intermediate artifacts, and final output.
- SQS with DLQ for asynchronous job delivery and retry safety.
- RDS PostgreSQL for users, jobs, stages, artifacts, credits, usage, and telemetry.
- Secrets Manager for the database connection reference.
- CloudWatch Logs and queue scaling alarms for lifecycle evidence and autoscaling.
- ECR for immutable API and worker images.
- IAM/OIDC for short-lived GitHub Actions image-publish access.

## Complete job lifecycle

1. The real client authenticates and requests a media upload through the public API.
2. The API creates the asset record and stores the uploaded media in private S3.
3. The client completes the asset and submits a dubbing job with target language, voice profile, and idempotency key.
4. The API reserves credits, persists the job, and enqueues an SQS message.
5. ECS worker capacity is enabled for validation; the worker claims the message, leases the job, and creates an isolated workspace.
6. The worker downloads the S3 input, validates media with FFprobe, separates audio with Demucs, and transcribes with Whisper.
7. Each Whisper segment is translated by GoogleTranslator, synthesized by Chatterbox, measured against the original timing window, and selectively refined by Hy-MT2 at most once when required.
8. FFmpeg mixes translated speech with the preserved background and reconstructs the final video.
9. The worker validates the output, uploads the job-scoped artifact to private S3, and writes stage, telemetry, cost, credit, and completion records to RDS.
10. The API reports `completed` and returns an artifact download link. The downloaded output is validated with FFprobe.
11. After the test, CPU desired/running/pending and GPU worker/ASG capacity are all zero. Retry, visibility timeout, DLQ, cleanup, unique output keys, and idempotency remain enabled.

## AI pipeline stages

1. FFprobe and media validation.
2. FFmpeg audio extraction.
3. Demucs `htdemucs` two-stem speech/background separation.
4. Whisper `small` multilingual transcription with segment timestamps.
5. GoogleTranslator fast translation through `deep-translator`.
6. Chatterbox multilingual-v3 first-pass voice synthesis.
7. Duration comparison against the existing timing window.
8. Optional Hy-MT2-1.8B linguistic shortening, one pass maximum, followed by a second Chatterbox synthesis.
9. Bounded FFmpeg `atempo` adjustment only after natural translation and refinement.
10. Background-preserving mix, final video mux, S3 upload, database completion, and download.

DeepFilterNet remains a separate modular noise-enhancement operation. Lip sync remains optional and is not a dependency of the baseline dubbing acceptance test.

## Selected models and technical reasons

| Stage | Selected model/provider and version | Technical reason |
|---|---|---|
| Transcription | OpenAI Whisper `small` | Multilingual support, segment timing, existing adapter, controlled memory/cold start |
| Translation | `deep-translator==1.11.4`, `GoogleTranslator` | Fast default, low worker memory, real network translation, no local model load |
| Refinement | `tencent/Hy-MT2-1.8B`, pinned revision `9a341cd1b679d3efd23b46e847b01745a71ed792` | Natural shortening with source/context/glossary constraints; lazy and duration-triggered |
| Voice/TTS | Chatterbox `multilingual-v3`, `chatterbox-tts==0.1.7` | Reference-voice multilingual synthesis and existing provider contract |
| Separation | Demucs `htdemucs` | Practical speech/background split for reconstructed dubbing |
| Denoising | DeepFilterNet3, `deepfilternet==0.5.6`, `deepfilterlib==0.5.6` | Modular enhancement without coupling it to baseline dubbing |
| Timing/mux | FFmpeg container runtime | Deterministic audio filters, mix, speed bound, and video reconstruction |
| Optional comparison | AWS Translate provider | Kept configurable; not required for normal dubbing |

No license or commercial-use conclusion was made. The user owns that review.

## Models and checkpoints benchmarked

The repository includes `benchmark_providers.py`, `benchmark_dubbing.py`, `benchmark_translation.py`, `benchmark_media.py`, and `benchmark_hybrid_timing.py`. Controlled evidence includes local Whisper, Demucs, Chatterbox, and FFmpeg timing; real GoogleTranslator smoke; isolated real Hy-MT2 CPU inference; AWS CPU transcription/TTS; and the live full CPU dubbing run. The comparison was intentionally small to control AWS spend and did not claim a quality winner from speed alone.

| Benchmark | Evidence |
|---|---|
| AWS full CPU dubbing | 13.36 s real input; final MP4 generated, downloaded, and FFprobe-validated |
| AWS CPU Whisper | 18.196 s for 5.167 s input; RTF 3.521594; approximately 1,832 MiB |
| AWS CPU Chatterbox | 107.422 s; 5.520 s output; approximately 7,151 MiB |
| Real GoogleTranslator | EN→TR network smoke; 1.5465 s |
| Real Hy-MT2 CPU inference | One segment; 156.5701 s; BF16; output recorded |
| Hybrid routing benchmark | Fit skipped; moderate and large mismatch each refined once |

## Test media description

The final live test used a short real spoken-audio MP4 to control compute cost: 13.36 seconds, 640×360, 25 fps, and 113,751 bytes. It was uploaded through the public API and processed with an authorized reference voice profile. It was not silent, a sine wave, a mock, a direct SQS payload, or prerecorded output. The short duration is suitable for a smoke acceptance test but is not a 30–120 second quality baseline.

## Exact E2E test procedure

1. Confirm no expensive worker or builder is running and preserve the existing budget guardrail.
2. Build and publish an immutable CPU worker image through GitHub Actions.
3. Temporarily select the CPU worker task definition and enable one CPU worker task.
4. Use `scripts/aws_golden_e2e.py` against the public CloudFront API with real media, a real target language, and an authorized voice reference.
5. Verify API upload, S3 object existence, asset completion, job creation, and SQS delivery.
6. Poll the public job endpoint and capture CloudWatch/ECS evidence through completion.
7. Verify all expected stages, provider/model manifest, duration telemetry, cost, retry count, and completed state.
8. Download the final artifact through the presigned API endpoint and validate it with FFprobe.
9. Restore the API task definition and return CPU service, GPU service, and ASG capacity to zero.
10. Recheck desired/running/pending counts, repository state, and the final evidence JSON.

The final run followed this procedure and produced job `d859ac1f-8b02-4e0c-905f-d03c331378f7`. The optional local real-refinement benchmark was not reported as passing because the local environment lacks Torch; the real Hy-MT2 inference evidence is separately recorded.

## Terraform changes

Terraform retains the ECS GPU capacity provider, zero-minimum ASG, queue-depth scaling, model-cache volume, CPU Fargate worker option, private encrypted S3, SQS/DLQ, RDS, Secrets Manager reference, CloudWatch logs, OIDC image-publish role, and immutable image variables. Translation variables now default to Google/deep-translator with Hy-MT2 refinement enabled and a maximum of one pass. AWS Translate IAM permission is conditional on explicit AWS Translate selection. `terraform fmt` and `terraform validate` pass; no unrelated destructive recreation was performed.

## Remaining technical risks

1. GPU quota approval may still be unavailable or may not cover the desired instance family.
2. GPU cold-start, VRAM, image pull, and model-cache behavior still need a real GPU run.
3. The short input does not establish long-form quality or throughput.
4. Hy-MT2 is technically CPU-capable but too slow for broad CPU production use.
5. Manual audio quality review of background preservation and pronunciation remains useful.
6. The manual CPU workflow needs additional IAM permissions if it is to be fully automated through GitHub Actions.

## Remaining product work

- Run the real GPU golden E2E after the existing quota request is approved.
- Measure GPU startup, model load, VRAM, RTF, cost per minute, and post-job scale-to-zero.
- Validate one live overlong segment that triggers Hy-MT2 on GPU.
- Optionally enable AWS Translate for comparison only after its account service gate is resolved.
- Perform the user-owned model/checkpoint commercial-use review before production.

## Reproduction commands and manual action

    Public health:
    curl -fsS https://d3ncg3eqih0ccj.cloudfront.net/health

    Backend tests:
    PYTHONPATH=backend:. pytest -q backend/tests

    Hybrid timing routing benchmark:
    PYTHONPATH=backend python scripts/benchmarks/benchmark_hybrid_timing.py --output artifacts/hybrid-timing-routing.json

    Real AWS E2E harness:
    python scripts/aws_golden_e2e.py --api-url https://<cloudfront-host> --media <real-video.mp4> --voice <authorized-reference.wav> --email <test-email> --password <test-password> --target-language es --output-dir artifacts/aws-golden-e2e

    Terraform:
    terraform fmt -check -recursive infrastructure/terraform
    terraform -chdir=infrastructure/terraform validate

Manual user action still required: approve or wait for the existing AWS G/VT quota request. It was not cancelled. After approval, run the GPU golden E2E and review the generated media. No action is required to continue CPU validation.

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
