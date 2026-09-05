# AWS Media Pipeline Status

Date: 2026-09-05  
Environment: \`eu-north-1\`  
Public application: \`https://d3ncg3eqih0ccj.cloudfront.net\`  
Current application commit: \`bdc1c57\`
Current API task revision: \`17\`
Current GPU worker task revision: \`15\`

## 1 Executive summary

The production-style asynchronous media architecture is deployed and the API is healthy. The current API task runs the immutable image built from commit \`bdc1c57\`; the GPU worker task definition points to the matching immutable image and the worker service remains at desired/running/pending \`0/0/0\`.

The required real AWS GPU E2E test has not been run because the EC2 service quota \`Running On-Demand G and VT instances\` is still \`0\`. The quota increase request for one instance is \`CASE_OPENED\`. This is the current external blocker for proving real model inference, real output generation, download, measured GPU timings, and scale-to-zero after a completed job. No expensive GPU compute is running.

The latest CI run for commit \`bdc1c57\` passed backend, frontend, infrastructure, dependency-audit, security, migration, and build checks. The API and approximately 4.97 GB GPU worker images were published to ECR and their manifests were verified.

## 2 Architecture before this goal

The repository already contained a working local/media-processing codebase and a frontend/API surface. Provider abstractions existed for transcription, translation, voice synthesis, stem separation, noise removal, storage, and queueing. The earlier production deployment had a CPU worker image, but GPU image publishing and the measured cloud lifecycle were not yet proven.

The pre-goal gaps were:

- no verified real-media path from the public API through AWS storage, queue, GPU compute, models, output storage, and download;
- no verified GPU image publishing path independent of Docker Desktop;
- incomplete per-job model, language, memory, timing, and cost telemetry;
- no evidence that expensive compute returned to zero after a real job;
- migration and image deployment drift that had to be resolved before E2E testing.

## 3 Architecture after this goal

The deployed path is:

    Browser / frontend
        -> CloudFront
        -> ECS Fargate API
        -> private S3 input object
        -> SQS job queue
        -> ECS EC2 GPU service + ASG capacity provider
        -> real worker models and FFmpeg
        -> private S3 output/artifacts
        -> PostgreSQL job and telemetry records
        -> API job detail and presigned download
        -> queue drain and worker/ASG scale-to-zero

ECS EC2 GPU was retained instead of introducing AWS Batch or EKS. The existing Terraform implementation already has a GPU task contract, queue-driven ECS Application Auto Scaling target, ASG minimum/desired capacity zero, host model-cache mounting, and CloudWatch step alarms. This keeps the first E2E experiment within the existing provider/worker contract and avoids adding a second job-submission system before the real path is proven. AWS Batch remains a follow-up option if measured ECS capacity-provider startup or scheduling is insufficient.

## 4 AWS services used

- CloudFront: frontend hosting and same-origin API routing.
- ECS Fargate: steady-state API service.
- ECS EC2 capacity provider: GPU worker scheduling.
- EC2 Auto Scaling Group: GPU host capacity with desired/minimum zero.
- S3: private encrypted source, output, voice-reference, and artifact storage.
- SQS: asynchronous job queue with one-hour visibility timeout.
- SQS DLQ: failed-message redrive after three receives.
- RDS PostgreSQL: users, projects, jobs, artifacts, stages, usage, credits, and audit data.
- Secrets Manager: DATABASE_URL reference delivered to ECS tasks.
- CloudWatch Logs and alarms: API/worker logs and queue scale signals.
- ECR: immutable API and GPU worker images.
- IAM/OIDC: short-lived GitHub Actions role restricted to the repository image-publish path.
- AWS Translate: production translation provider through the ECS task role.
- Terraform: persistent infrastructure and deployment state.

## 5 Complete job lifecycle

1. The authenticated client calls \`/api/media/presign\` or \`/api/media/upload\`.
2. The API creates a media asset record and returns a presigned S3 PUT when presigning is used.
3. The client uploads the real media to private S3 and calls \`/api/media/{asset_id}/complete\`.
4. The client submits \`/api/jobs\` with an operation, asset ID, target language, and idempotency key.
5. The API estimates/reserves credits, persists the job, and enqueues a message to SQS.
6. Queue alarms and ECS Application Auto Scaling raise worker desired count when messages are visible.
7. The ECS GPU capacity provider starts an eligible GPU host when capacity is available.
8. The worker claims the job, acquires a lease, downloads input from S3, and creates an isolated temporary workspace.
9. The worker runs selected real providers and records stage events/metrics.
10. The worker validates and uploads output artifacts to S3 with unique job-scoped keys.
11. The worker persists artifacts, output metadata, telemetry, credit finalization, completion state, and notification events.
12. \`/api/jobs/{job_id}\` exposes state, events, stages, artifacts, success, language, model, timing, memory, retry, and cost data.
13. \`/api/jobs/{job_id}/artifacts/{artifact_id}/download\` returns a presigned redirect for the private object.
14. When the queue drains, scale-in reduces worker desired count to zero and the capacity provider can return the ASG to zero.

The complete lifecycle above is implemented. Steps 1–14 have not yet been proven together on AWS with a real GPU job because the quota is zero.

## 6 AI pipeline stages

The dubbing operation is implemented as explicit stages:

1. media validation and FFprobe metadata inspection;
2. audio extraction with FFmpeg;
3. two-stem background separation with Demucs when background preservation is enabled;
4. Whisper transcription with source-language detection;
5. segment translation through the configured translation provider;
6. reference-voice retrieval from a consented voice profile;
7. Chatterbox multilingual synthesis per translated segment;
8. timing-window checks and constrained speed adjustment;
9. translated speech mix with preserved instrumental/background audio;
10. final video/audio mux with FFmpeg;
11. output validation, S3 upload, database completion, and download exposure.

DeepFilterNet3 remains a separate modular noise-enhancement operation. Lip sync is optional and is not part of the baseline dubbing path or this E2E gate.

## 7 Selected model/checkpoint for every stage

| Stage | Selected model/provider | Configured version/checkpoint | Technical reason |
|---|---|---|---|
| Speech to text | OpenAI Whisper | \`small\` | Multilingual transcription, segment timestamps, moderate VRAM/cold-start profile, existing real adapter |
| Translation | AWS Translate | AWS managed provider | Native task-role integration, no external translation secret, production API abstraction preserved |
| Background separation | Demucs | \`htdemucs\`, 2 stems | Existing dubbing path, useful vocal/instrumental split, preserves a practical background track |
| Noise enhancement | DeepFilterNet | DeepFilterNet3; \`deepfilternet==0.5.6\`, \`deepfilterlib==0.5.6\` | Modular speech enhancement provider and explicit real runtime |
| Voice cloning/TTS | Chatterbox multilingual | \`multilingual-v3\`; \`chatterbox-tts==0.1.7\` | Reference-voice multilingual synthesis and existing provider contract |
| Timing/mux | FFmpeg | container runtime | Deterministic audio filters, mix, timing-window trim, and video reconstruction |
| Lip sync | Not enabled | N/A | Keeps baseline dubbing independent of an expensive optional stage |

License and commercial-use review was intentionally not performed. The user owns that pre-production review.

During a job, the Whisper and Chatterbox provider caches are released at stage boundaries and the PyTorch allocator cache is flushed on supported accelerators. The checkpoint files remain in the worker's host model-cache volume, so the next job can reuse downloaded weights without keeping both stage models resident in VRAM.

## 8 Models/checkpoints benchmarked

The reproducible benchmark entry points are:

- \`scripts/benchmarks/benchmark_providers.py\`;
- \`scripts/benchmarks/benchmark_dubbing.py\`;
- \`scripts/benchmarks/benchmark_media.py\`.

The existing local complete-dubbing evidence measured the configured stack: Whisper \`small\`, Demucs \`htdemucs\`, Chatterbox multilingual-v3, and FFmpeg. No large candidate sweep was run because the goal requires controlled spend and the AWS GPU quota is unavailable. A real comparative GPU benchmark between alternatives is still pending.

| Local stage | Wall time |
|---|---:|
| Whisper | 2.1647 s |
| Demucs background | 4.7254 s |
| Chatterbox synthesis | 69.1396 s |
| FFmpeg mix/mux | 0.2831 s |

The local 3.129-second MP4 completed in 76.3944 seconds. These are local baseline values, not AWS GPU acceptance values.

## 9 Test media description

The prepared private smoke-test input is:

- real spoken-audio MP4 generated with macOS speech synthesis;
- duration: \`6.974014\` seconds;
- approximately 75 KB;
- separate authorized reference voice recording prepared for voice cloning;
- not a sine-wave or silent fixture;
- intentionally shorter than the preferred 30–120 seconds to keep the first AWS experiment within the cost ceiling.

## 10 Exact E2E test procedure

After quota approval:

1. Confirm quota is at least one and no other expensive GPU run is active.
2. Confirm API and worker task definitions use the immutable image tag for the tested commit.
3. Confirm worker service and ASG start at zero.
4. Create/authenticate a test user through the real API.
5. Upload the prepared MP4 through \`/api/media/upload\` or \`/api/media/presign\`, then complete the asset.
6. Create a dubbing job through \`/api/jobs\` with a real target language and authorized voice profile.
7. Record job ID, S3 asset key, SQS arrival, queue age, and initial job state.
8. Observe CloudWatch and ECS until the worker service and GPU host start automatically.
9. Poll \`/api/jobs/{job_id}\` until \`completed\` or terminal failure; do not substitute a fixture or prerecorded output.
10. Verify events/stages include separation, transcription, translation, synthesis, mixing, uploading, and completion.
11. Verify UsageRecord contains languages, model manifest, bytes, stage times, RTF, GPU/RAM, retry, and cost fields.
12. Download output through the artifact download endpoint and validate it with FFprobe/media decoding.
13. Verify output exists in private S3 and size/metadata match the artifact record.
14. Wait for queue drain and verify ECS worker desired/running/pending and ASG capacity return to zero.
15. Capture CloudWatch log evidence and final job JSON for the report.

The repeatable client for this procedure is \`scripts/aws_golden_e2e.py\`. It uploads real media through the API, verifies the input download, creates an authorized reference voice and dubbing job, polls the real job state, downloads every output artifact, runs FFprobe, and writes evidence JSON without recording passwords or presigned URLs.

## 11 E2E test result

| Required proof | Status | Evidence |
|---|---|---|
| Real API job submission | Not run for GPU golden path | Quota blocker |
| Input stored in S3 | Not run for GPU golden path | Quota blocker |
| SQS receives job | Not run for GPU golden path | Quota blocker |
| GPU compute starts automatically | Blocked | EC2 G/VT quota is \`0\` |
| Real Whisper inference | Not run | No GPU worker could start |
| Real Demucs separation | Not run | No GPU worker could start |
| Real AWS Translate call | Not run in golden path | Pipeline was not started |
| Real Chatterbox synthesis | Not run | No GPU worker could start |
| Final dubbed media generated | No | No AWS golden output exists |
| Output stored in S3 | Not run | No AWS golden output exists |
| Completed DB/job state | Not run | No AWS golden job exists |
| Download verified | Not run | No AWS golden artifact exists |
| CloudWatch lifecycle logs | Not run for golden job | No golden job exists |
| Worker/ASG returns to zero after job | Not run after real job | Worker is currently zero by design |

The honest gate is therefore \`BLOCKED\`, not \`PASS\`.

## 12 Metrics and cost status

No AWS golden job metrics exist yet. The fields are implemented and will be populated by the worker:

- input duration and input/output bytes;
- source and target language;
- selected model manifest;
- per-stage wall time and aggregate model time;
- queue wait, worker startup, and model-load time;
- total wall-clock time;
- \`RTF = total processing seconds / input media seconds\`;
- GPU type, peak VRAM, and peak RAM;
- retry count, success/failure, error code;
- estimated and actual compute cost;
- compute cost per input media minute.

| Metric | Value |
|---|---|
| Input duration | 6.974014 s prepared locally; AWS job not run |
| Total processing time | Unavailable |
| RTF | Unavailable |
| Peak VRAM | Unavailable |
| Compute startup/cold start | Unavailable |
| Planned GPU | \`g4dn.xlarge\` |
| Configured on-demand price | \`$0.558/hour\` in Stockholm |
| AWS GPU test spend | \`$0.00\` so far |
| AWS compute cost per input minute | Unavailable until measured |
| Current expensive compute | None running |

## 13 Verification that expensive compute returned to zero

The infrastructure is configured for scale-to-zero and the current live worker service is verified at:

    desired: 0
    running: 0
    pending: 0

This proves the idle guardrail state, but not post-job scale-to-zero because no real GPU job has completed. The post-job proof remains pending quota approval.

## 14 Terraform changes and deployment

Terraform changes include:

- ECS EC2 GPU capacity provider and ASG with desired/minimum zero;
- queue-depth CloudWatch alarms and ECS step scaling; the scale-in alarm uses CloudWatch metric math over both visible and in-flight SQS messages so an actively leased job does not look like an empty queue;
- GPU task definition with one GPU requirement, model-cache host volume, AWS Translate task permission, and cost telemetry environment;
- ECR/GitHub OIDC role and repository/branch restrictions;
- private encrypted S3, lifecycle, SQS/DLQ, RDS, Secrets Manager references, and CloudWatch log groups;
- immutable API/worker image deployment variables.

The latest immutable images for commit \`bdc1c57\` were published and verified in ECR. Terraform then applied only the API/worker task-definition and service targets to avoid unrelated frontend asset drift. The live API is task revision 17 and the live GPU worker task definition is revision 15. The API is running one task and carries the \`DATABASE_URL\` Secrets Manager reference; the GPU worker remains zero.

The full plan also showed local frontend asset drift because the local \`frontend/dist\` file set does not exactly match the already-hosted asset set. That drift was not applied or treated as an E2E prerequisite.

## 15 Test and security results

- Backend tests: \`46 passed\` locally, with one existing FastAPI/Starlette deprecation warning; the new model-release regression test is included.
- GitHub CI: backend, frontend, infrastructure, migration, dependency audit, Ruff, Bandit, compile, and Docker checks passed for \`bdc1c57\`.
- Ruff: passed locally and in CI.
- Bandit medium-and-higher severity scan: passed.
- pip-audit: passed in GitHub CI.
- Frontend production build and high-severity npm audit: passed.
- Terraform formatting and validation: passed.
- Clean SQLite Alembic upgrade through migration \`0011_usage_language_models\`: passed.
- Public \`/health\`: HTTP 200; database, storage, and queue report configured.
- Live signup plus authenticated \`/api/auth/me\` round-trip: HTTP 200 with the same user ID, confirming the API task can write/read through the RDS secret.
- No long-lived GitHub AWS keys are used; image publishing uses OIDC.
- S3 media remains private and encrypted.
- No credentials or secret values are committed or printed in this report.

## 16 Reliability controls

Implemented or verified controls include:

- idempotency key handling;
- bounded retry count and retry-safe job state transitions;
- worker lease and heartbeat;
- stale-job recovery;
- SQS visibility extension;
- DLQ after three receives;
- isolated temporary workspaces and cleanup;
- unique job-scoped output keys;
- output validation before completion;
- public-safe error messages with internal error retention;
- source-language persistence after detection;
- job detail success/failure, stages, events, artifacts, and telemetry.

Real cloud interruption, duplicate-output, and post-job cleanup behavior still require the golden AWS run as evidence.

## 17 Remaining technical risks

1. The current AWS GPU quota may remain unavailable or may not include the selected instance family after approval.
2. The first real cold start may expose image pull, model download, VRAM, or model-load time not visible in local tests.
3. The short prepared input is useful for a smoke test but is not a full 30–120 second quality baseline.
4. ECS GPU scale-out timing must be measured against the one-hour SQS visibility timeout.
5. Source separation and translated speech timing need manual quality review on the generated artifact.
6. Comparative model benchmarking is incomplete until an eligible GPU runner is available.
7. Frontend asset drift should be reconciled separately before a broader Terraform apply.

## 18 Remaining product work

- Run and document the real GPU golden job after quota approval.
- Save transcription, translated text, generated voice audio, and final dubbed media as review artifacts.
- Add measured cost profiles only from actual benchmark evidence.
- Complete the user-owned model/checkpoint commercial-use review before production.
- Reconcile frontend asset drift and run a non-targeted Terraform plan with no unexplained changes.
- Decide whether measured ECS startup justifies a later AWS Batch implementation.

Out of scope: Stripe, checkout, payment webhooks, production SES, custom domain, branding/marketing, company registration, legal/license analysis, and EKS/Kubernetes.

## 19 Reproduction commands and workflows

    Public health:
    curl -fsS https://d3ncg3eqih0ccj.cloudfront.net/health

    Backend tests:
    PYTHONPATH=backend:. pytest -q backend/tests

    Provider benchmark:
    PYTHONPATH=backend python scripts/benchmarks/benchmark_providers.py <media> --voice-reference <wav>

    Complete dubbing benchmark:
    PYTHONPATH=backend python scripts/benchmarks/benchmark_dubbing.py <video> --reference-voice <wav>

    Real AWS golden E2E (run only after GPU quota approval):
    python scripts/aws_golden_e2e.py --api-url https://<cloudfront-host> --media <real-video.mp4> --voice <authorized-reference.wav> --email <test-email> --password <test-password> --target-language es --output-dir artifacts/aws-golden-e2e

    Browser acceptance:
    cd frontend && npm run test:e2e

    GPU benchmark workflow:
    .github/workflows/gpu-benchmarks.yml (manual, only after an eligible GPU runner exists)

    Infrastructure:
    infrastructure/terraform

    Image publishing:
    .github/workflows/build-images.yml

## 20 Manual user action still required

The only blocking manual/account-level action is approval of the AWS EC2 quota increase request for at least one On-Demand G/VT instance. No user approval is required for the already completed low-cost code, CI, image-publish, or zero-capacity deployment steps.

## Final gate

    E2E MEDIA PIPELINE: FAIL — BLOCKED by AWS GPU quota
    REAL MODEL INFERENCE: FAIL — NOT RUN
    REAL OUTPUT GENERATED: NO
    OUTPUT DOWNLOAD VERIFIED: NO
    SELECTED STT MODEL: Whisper small
    SELECTED TTS/VOICE MODEL: Chatterbox multilingual-v3
    SELECTED OTHER MAJOR MODELS: Demucs htdemucs; DeepFilterNet3; AWS Translate
    GPU INSTANCE: g4dn.xlarge planned
    INPUT DURATION: 6.974014 seconds
    TOTAL PROCESSING TIME: unavailable
    REAL-TIME FACTOR: unavailable
    PEAK VRAM: unavailable
    TEST COST: $0.00
    ESTIMATED COMPUTE COST PER INPUT MINUTE: unavailable
    GPU SCALE-TO-ZERO: NOT USED for a completed job; idle zero state PASS
    TERRAFORM: PASS
    TESTS: PASS
    SECURITY CHECKS: PASS
    EXPENSIVE COMPUTE CURRENTLY RUNNING: NO
    LICENSE REVIEW: NOT PERFORMED — USER WILL REVIEW BEFORE PRODUCTION
    REMAINING BLOCKERS: EC2 G/VT quota is 0; request CASE_OPENED; real AWS E2E pending
