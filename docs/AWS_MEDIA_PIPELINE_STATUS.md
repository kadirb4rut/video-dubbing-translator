# AWS Media Pipeline Status

Date: 2026-09-05  
Environment: \`eu-north-1\`  
Public application: \`https://d3ncg3eqih0ccj.cloudfront.net\`  
Current code commit: \`eb8519b\` (Google primary + Hy-MT2 duration-triggered refinement)
Current status-document commit: \`ea34cd2\`
Live application commit: \`a0cfc97\` (older deployed API; latest CPU worker rollout is still pending)
Current API task revision: \`20\`
Current GPU worker task revision: \`15\`
Current CPU validation worker task revision: \`6\` (desired/running/pending \`0/0/0\`)

## 1 Executive summary

The production-style asynchronous media architecture is deployed and the API is healthy. The current API task is live at revision \`20\`; the GPU worker task definition remains present and the worker service remains at desired/running/pending \`0/0/0\`. The repository source of truth is \`01412e5\`; the last immutable image set known to be published successfully is \`03b673e\`, while the latest CPU worker rollout still needs live AWS access.

The pending GPU quota request remains untouched: the EC2 service quota \`Running On-Demand G and VT instances\` is still \`0\` and the one-instance increase request is \`CASE_OPENED\`. GPU execution is therefore still a later performance-upgrade path. The CPU validation path was enabled without removing the GPU architecture. Google Translate through \`deep-translator\` is now the fast primary provider; Hy-MT2-1.8B is a lazy, duration-triggered refinement provider, and AWS Translate remains an explicit optional comparison/primary mode. A real Google CPU translation smoke passed; the full API → S3 → SQS → CPU worker → Google → Chatterbox → mux → S3 → download run remains PARTIAL because the account's current AWS console state prevents the needed live CPU worker rollout. No expensive GPU compute is running.

The backend tests, lint checks, and Terraform checks pass for \`ea34cd2\`. The latest CI workflow is green through backend/infrastructure/frontend checks, including the CPU worker image build; the latest production image publish is still running. The last completed immutable API/GPU/CPU image set is \`03b673e\` and its manifests were verified. The worker keeps a provider abstraction so the same job contract can later run on GPU.

## 2 Architecture before this goal

The repository already contained a working local/media-processing codebase and a frontend/API surface. Provider abstractions existed for transcription, translation, voice synthesis, stem separation, noise removal, storage, and queueing. Before this validation, the measured cloud lifecycle and CPU model execution were not yet proven.

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
        -> ECS Fargate CPU worker (temporary validation) or ECS EC2 GPU service + ASG capacity provider
        -> real worker models and FFmpeg
        -> private S3 output/artifacts
        -> PostgreSQL job and telemetry records
        -> API job detail and presigned download
        -> queue drain and worker/ASG scale-to-zero

ECS EC2 GPU was retained instead of introducing AWS Batch or EKS. The existing Terraform implementation already has a GPU task contract, queue-driven ECS Application Auto Scaling target, ASG minimum/desired capacity zero, host model-cache mounting, and CloudWatch step alarms. This keeps the first E2E experiment within the existing provider/worker contract and avoids adding a second job-submission system before the real path is proven. AWS Batch remains a follow-up option if measured ECS capacity-provider startup or scheduling is insufficient.

## 4 AWS services used

- CloudFront: frontend hosting and same-origin API routing.
- ECS Fargate: steady-state API service.
- ECS Fargate: temporary CPU worker service used for real validation; desired count returned to zero after testing.
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
- Google Translate through `deep-translator`: fast primary translation provider.
- Hy-MT2-1.8B: duration-triggered self-hosted refinement provider, loaded only for overlong translated speech.
- AWS Translate: optional comparison/fallback provider through the ECS task role.
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

The lifecycle is implemented. CPU proved the storage/queue/worker/model/output/download path for transcription and TTS. The hybrid translation routing and timing contract is covered by worker tests, and a real GoogleTranslator CPU smoke completed. The full hybrid media acceptance run is still blocked by live AWS worker rollout access, not by the provider contract or GPU quota. GPU execution is still pending quota approval.

## 6 AI pipeline stages

### CPU validation result

The temporary CPU worker used the same provider/worker contract as the GPU worker, with CPU PyTorch, Chatterbox device \`cpu\`, Fargate 4 vCPU/16 GiB, and a temporary telemetry price of approximately $0.233/hour. It was returned to desired/running/pending \`0/0/0\` after the validation.

| Validation | Result | Evidence |
|---|---|---|
| Real transcription | PASS | \`artifacts/aws-cpu-e2e/transcription-8e7fd9cf-fd44-4e2b-a5d1-3ea601d0324b/evidence.json\`; SRT/VTT/TXT downloaded |
| Real CPU TTS | PASS | \`artifacts/aws-cpu-e2e/tts-316d86a2-25d2-4884-b008-38b7623f4668/evidence.json\`; WAV downloaded and FFprobe-validated |
| Real Google translation | PASS | Real `deep-translator.GoogleTranslator` EN→TR smoke; 1.5465 s; no mock |
| Hybrid timing routing | PASS | `artifacts/hybrid-timing-routing.json`; fit case skipped, moderate/large mismatch cases refined once |
| Real Hy-MT2 refinement inference | NOT RUN in this turn | Existing real CPU Hy-MT2 inference evidence remains in `artifacts/translation-benchmark-smoke.json`; full selective refinement run awaits worker/model image rollout |
| Real dubbing attempt | PARTIAL | Earlier AWS Translate attempt reached the account gate; full hybrid media path needs live CPU worker rollout |
| GPU execution | PENDING | quota remains zero; request remains \`CASE_OPENED\` |

Measured successful CPU runs:

| Job | Total wall time | Stage timing | RAM* | CPU utilization | RTF | Actual cost | Cost/input minute |
|---|---:|---|---:|---:|---:|---:|---:|
| Whisper transcription, 5.167 s input | 18.196 s | download 0.087 s; Whisper 17.564 s; upload 0.311 s | ~1,832 MB | 704.376% aggregate process CPU | 3.521594 | $0.001178 | $0.013679 |
| Chatterbox TTS, 5.520 s output | 107.422 s | model load 42.130 s included; synthesis 105.708 s; upload 0.182 s | ~7,151 MB | 137.548% aggregate process CPU | ~19.46 output-seconds/second | $0.006953 | N/A (no input media) |

The CPU percentage is process CPU time divided by wall time; values over 100% indicate multi-core use on the 4-vCPU task. *The historical job JSON stored Linux \`ru_maxrss\` in GiB under the \`peak_ram_mb\` field; values above are the corrected MiB interpretation, and commit \`7344f13\` fixes the unit conversion for future records. The failed dubbing attempt cost $0.005268 ($0.061173/input minute) before the external Translate failure, including retries; it is not a successful dubbing cost.

Practical CPU conclusion so far: S3/SQS/API orchestration, FFmpeg, Whisper small, Demucs htdemucs, Chatterbox multilingual-v3, S3 output, job status, artifact download, Google translation, and Hy-MT2 refinement all have CPU-capable adapters. Google translation is fast enough for the primary path; the isolated Hy-MT2 smoke measured 156.5701 s for one 26-character segment (0.0064 segments/sec), so Hy-MT2 is selectively useful but GPU is the recommended performance path when refinement is triggered. AWS Translate is optional and no longer required for the default path.

The dubbing operation is implemented as explicit stages:

1. media validation and FFprobe metadata inspection;
2. audio extraction with FFmpeg;
3. two-stem background separation with Demucs when background preservation is enabled;
4. Whisper transcription with source-language detection;
5. segment translation through GoogleTranslator by default, with Hy-MT2 invoked only when first-pass TTS exceeds the timing tolerance (AWS Translate remains optional);
6. reference-voice retrieval from a consented voice profile;
7. Chatterbox multilingual synthesis per translated segment;
8. timing-window checks and constrained speed adjustment;
9. translated speech mix with preserved instrumental/background audio;
10. final video/audio mux with FFmpeg;
11. output validation, S3 upload, database completion, and download exposure.

DeepFilterNet3 remains a separate modular noise-enhancement operation. Lip sync is optional and is not part of the baseline dubbing path or this E2E gate.

## 7 Hybrid translation providers and runtime

The primary provider is `TRANSLATION_PROVIDER=google-deep-translator`, implemented with `deep_translator.GoogleTranslator`. Each segment keeps its original timing and a `source_text` field so the optional refinement stage can receive the Google translation plus source/context metadata. Empty responses and provider/network errors fail explicitly; source text is never silently passed through as a translation.

The refinement provider is controlled independently with `TRANSLATION_REFINEMENT_PROVIDER=hymt2` and `TRANSLATION_REFINEMENT_MAX_PASSES=1`. It is not loaded during normal translation. After the first Chatterbox synthesis, the worker compares measured speech duration against the existing timing window and configured tolerance. Only an overlong segment enters Hy-MT2; the worker sends the Google translation, source text, surrounding context, glossary, style, names/numbers/technical-term constraints, and maximum spoken duration. The segment is synthesized again, measured again, then the existing bounded FFmpeg `atempo` adjustment is applied as the last step. A segment that fits the tolerance never constructs or invokes Hy-MT2.

Hy-MT2 is a lazy-loaded, in-process Transformers adapter using `AutoTokenizer` and `AutoModelForCausalLM`; the deprecated Transformers v5 translation pipeline is not used. `TRANSLATION_DEVICE=auto` selects CUDA when available and CPU otherwise. `TRANSLATION_DTYPE=auto` selects BF16, matching the published checkpoint tensor type; operators can explicitly select float32 where CPU BF16 support is unavailable. The model is released at the end of a refinement stage while the downloaded checkpoint remains in the worker cache.

For dubbing, the adapter sends bounded contextual batches containing source/target language, ordered segment IDs, text, surrounding transcript context, glossary entries, register/style, and a concise-spoken-duration instruction. Responses must contain exactly one `<SEG_id>` marker per input in the original order. Missing, duplicate, reordered, empty, or commentary-surrounded output is rejected; one bounded repair inference is allowed. Unsupported language codes fail explicitly. After TTS, output duration is measured per segment; a segment over the configured 20% tolerance receives at most one Hy-MT2 duration rewrite before the existing 1.6x speed-adjustment ceiling is applied.

The reproducible corpus is `scripts/benchmarks/fixtures/hymt2-dubbing.json` (20 segments covering names, numbers, URLs, idioms, technical terms, context, glossary, and duration-aware speech). `scripts/benchmarks/benchmark_translation.py` records model/runtime/device/dtype, cold model load, wall/CPU time, peak RAM, segment/character throughput, batch count, retries, output text, and cost-per-minute estimates when `COMPUTE_HOURLY_PRICE_USD` is provided. `scripts/benchmarks/benchmark_hybrid_timing.py` intentionally covers fit, moderate-mismatch, and large-mismatch cases and proves that only mismatch cases receive one refinement pass. The real Google smoke is recorded in the current run output; the existing real Hy-MT2 smoke is recorded in `artifacts/translation-benchmark-smoke.json`. The current repository tests cover Google provider selection, empty-result failure, Hy-MT2 mapping, language validation, bounded refinement, duration telemetry, and AWS adapter compatibility.

## 8 Selected model/checkpoint for every stage

| Stage | Selected model/provider | Configured version/checkpoint | Technical reason |
|---|---|---|---|
| Speech to text | OpenAI Whisper | \`small\` | Multilingual transcription, segment timestamps, moderate VRAM/cold-start profile, existing real adapter |
| Translation primary | GoogleTranslator | `deep-translator==1.11.4` | Fast segment translation with low worker memory and no local model load |
| Translation refinement | Hy-MT2 | `tencent/Hy-MT2-1.8B` @ pinned revision, Transformers in-process, BF16 default | Selective natural shortening only when first-pass TTS exceeds timing tolerance |
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
- \`scripts/benchmarks/benchmark_translation.py\` with \`scripts/benchmarks/fixtures/hymt2-dubbing.json\`;
- \`scripts/benchmarks/benchmark_media.py\`.

The existing local complete-dubbing evidence measured the configured stack: Whisper \`small\`, Demucs \`htdemucs\`, Chatterbox multilingual-v3, and FFmpeg. The real GoogleTranslator smoke took 1.5465 s for one EN→TR segment. The hybrid timing harness produced one fit case with zero refinement calls and two mismatch cases with one bounded refinement pass each. No large candidate sweep was run because the goal requires controlled spend and the AWS GPU quota is unavailable. A real comparative AWS Translate benchmark remains optional and pending account access.

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
- duration: \`5.167\` seconds;
- approximately 2.05 MB;
- separate authorized reference voice recording prepared for voice cloning;
- not a sine-wave or silent fixture;
- intentionally shorter than the preferred 30–120 seconds to keep the first AWS experiment within the cost ceiling.

## 10 Exact E2E test procedure

CPU validation can run now with the temporary Fargate CPU worker. The GPU golden run remains pending quota approval:

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

The CPU evidence harness used the same public API and real media. It completed transcription and TTS separately because the full dubbing path reached the real AWS Translate provider and the AWS account returned \`SubscriptionRequiredException\`; the AWS console currently reports incomplete account setup/free-plan service limitations. No fixture translator, direct SQS enqueue, or prerecorded output was used.

The repeatable client for this procedure is \`scripts/aws_golden_e2e.py\`. It uploads real media through the API, verifies the input download, creates an authorized reference voice and dubbing job, polls the real job state, downloads every output artifact, runs FFprobe, and writes evidence JSON without recording passwords or presigned URLs.

## 11 E2E test result

| Required proof | Status | Evidence |
|---|---|---|
| Real CPU transcription API → S3 → SQS → worker → model → S3 → download | PASS | Three real transcript artifacts downloaded; evidence JSON above |
| Real CPU TTS API → SQS → Chatterbox → S3 → download | PASS | 265,004-byte PCM WAV, 5.52 s, FFprobe validated |
| Real CPU Google translation | PASS | One real EN→TR segment through deep-translator; 1.5465 s |
| Hybrid timing refinement routing | PASS | Fit segment skipped; mismatch segments each used one bounded refinement pass in the targeted harness and worker integration test |
| Real CPU Hy-MT2 translation/refinement | PARTIAL | Existing real Hy-MT2 inference passed in isolation; new selective refinement prompt is covered by tests; live full worker rollout remains pending |
| Real CPU dubbing attempt | PARTIAL | Existing Demucs/Whisper path passed; live full hybrid worker rollout remains pending |
| Real API job submission / input in S3 / SQS | PASS for CPU jobs | Public API harness, no direct queue injection |
| GPU compute starts automatically | Pending | EC2 G/VT quota is \`0\` |
| Real GPU inference and GPU output | Pending | Quota request remains open |
| CPU worker returns to zero | PASS | CPU service ended desired/running/pending \`0/0/0\` |
| GPU worker/ASG returns to zero after GPU job | Pending | No GPU job can run while quota is zero |

The CPU validation gate is therefore \`PASS\` for transcription/TTS and \`PARTIAL\` for full dubbing. The overall GPU acceptance gate remains \`PENDING\`, not a failure of the CPU path.

## 12 Metrics and cost status

GPU golden metrics do not exist yet. CPU measurements are recorded by the worker and are summarized in section 6.1. The fields include:

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
| CPU transcription | 18.196 s total; RTF 3.521594; corrected RAM ~1,832 MiB; aggregate CPU 704.376% |
| CPU TTS | 107.422 s total; 5.520 s output; corrected RAM ~7,151 MiB; aggregate CPU 137.548% |
| GoogleTranslator CPU/network smoke | 1.5465 s for one EN→TR segment; real output |
| Hy-MT2 CPU smoke | 156.5701 s for one 26-character segment; 0.0064 segments/s; BF16; real output; RAM not captured by wrapper |
| Hybrid timing benchmark | 3 segments; 1 fit + 2 mismatch; 2 refinement calls; 66.67% routing refinement rate; one pass per mismatch segment |
| CPU telemetry rate | \`$0.233/hour\` temporary Fargate 4 vCPU/16 GiB approximation |
| Successful CPU input-minute cost | Transcription \`$0.013679/min\`; dubbing success unavailable |
| Failed dubbing attempt cost | \`$0.005268\` total / \`$0.061173\` per input minute before Translate failure |
| Planned GPU | \`g4dn.xlarge\` |
| Configured GPU on-demand price | \`$0.558/hour\` in Stockholm |
| AWS GPU test spend | \`$0.00\` so far |
| GPU compute cost per input minute | Unavailable until quota and GPU run |
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
- optional CPU Fargate worker task/service with CPU Chatterbox, writable ephemeral model cache, soundfile WAV output, and the same S3/SQS/secret/IAM contract; desired count is currently zero;
- ECR/GitHub OIDC role and repository/branch restrictions;
- private encrypted S3, lifecycle, SQS/DLQ, RDS, Secrets Manager references, and CloudWatch log groups;
- immutable API/worker image deployment variables.

Terraform applied only targeted API/CPU worker task-definition and service changes to avoid unrelated frontend asset drift. The live API is task revision 20 with the temporary pricing override returned to \`false\`; the GPU worker remains zero and the CPU worker task definition is revision 6 with desired count zero. The API carries the \`DATABASE_URL\` Secrets Manager reference. The immutable image publish for \`7344f13\` completed successfully and its API/GPU/CPU manifests were verified.

The full plan also showed local frontend asset drift because the local \`frontend/dist\` file set does not exactly match the already-hosted asset set. That drift was not applied or treated as an E2E prerequisite.

## 15 Test and security results

- Backend tests: \`58 passed\` locally, with one existing FastAPI/Starlette deprecation warning; Google selection, empty-result handling, Hy-MT2 mapping, language, batch, bounded refinement, and duration telemetry tests are included.
- GitHub CI: backend, frontend, infrastructure, migration, dependency audit, Ruff, Bandit, compile, and Docker checks are green for \`01412e5\`; the latest image publishing workflow is still running. The last completed immutable image set is \`03b673e\`; the first unpinned-Hugging-Face run failed on B615 and the revision pin fixed it.
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

Real cloud interruption and duplicate-output behavior still require the GPU golden run as evidence; the CPU validation already produced real artifacts and verified download and zero-capacity cleanup for the CPU service.

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

    Hy-MT2 translation benchmark (real provider, 20-segment corpus):
    PYTHONPATH=backend python scripts/benchmarks/benchmark_translation.py --output artifacts/translation-benchmark.json

    Provider benchmark:
    PYTHONPATH=backend python scripts/benchmarks/benchmark_providers.py <media> --voice-reference <wav>

    Complete dubbing benchmark:
    PYTHONPATH=backend python scripts/benchmarks/benchmark_dubbing.py <video> --reference-voice <wav>

    Real AWS golden E2E (run only after GPU quota approval):
    python scripts/aws_golden_e2e.py --api-url https://<cloudfront-host> --media <real-video.mp4> --voice <authorized-reference.wav> --email <test-email> --password <test-password> --target-language es --output-dir artifacts/aws-golden-e2e

    CPU evidence already captured:
    artifacts/aws-cpu-e2e/transcription-8e7fd9cf-fd44-4e2b-a5d1-3ea601d0324b/evidence.json
    artifacts/aws-cpu-e2e/tts-316d86a2-25d2-4884-b008-38b7623f4668/evidence.json

    Browser acceptance:
    cd frontend && npm run test:e2e

    GPU benchmark workflow:
    .github/workflows/gpu-benchmarks.yml (manual, only after an eligible GPU runner exists)

    Infrastructure:
    infrastructure/terraform

    Image publishing:
    .github/workflows/build-images.yml

## 20 Manual user action still required

The remaining account-level actions are completing AWS account activation/free-plan access requirements for Translate and approval of the AWS EC2 quota increase request for at least one On-Demand G/VT instance. These are account-level actions and were not changed automatically because they may involve payment, identity, or plan decisions. They no longer block CPU progress: the CPU path is validated and the GPU request remains open as a later performance-upgrade gate.

## Final gate

    CPU E2E MEDIA PIPELINE: PASS for transcription/TTS; PARTIAL for full dubbing
    REAL CPU MODEL INFERENCE: PASS — Whisper, Demucs, Chatterbox
    REAL CPU OUTPUT GENERATED: YES — transcript artifacts and WAV
    CPU OUTPUT DOWNLOAD VERIFIED: YES
    REAL TRANSLATION INFERENCE: PASS — real CPU BF16 EN→TR smoke; 156.5701 s; bounded plain-output fallback
    CPU FULL DUBBING E2E: PARTIAL — Hy-MT2 isolated inference PASS; live API/S3/SQS/worker/TTS/mux/download rollout pending
    GPU E2E MEDIA PIPELINE: PENDING — quota request remains open
    SELECTED STT MODEL: Whisper small
    DEFAULT TRANSLATION PROVIDER: Google Translate via deep-translator.GoogleTranslator
    REFINEMENT PROVIDER: Hy-MT2-1.8B, duration-triggered, max one pass per segment
    TRANSLATION MODEL/CHECKPOINT: deep-translator==1.11.4; Hy-MT2 tencent/Hy-MT2-1.8B pinned revision
    TRANSLATION RUNTIME: Google network adapter; Hy-MT2 Transformers in-process when triggered
    TRANSLATION DTYPE: Hy-MT2 BF16 default; CPU/GPU selectable by environment
    SELECTED TTS/VOICE MODEL: Chatterbox multilingual-v3
    SELECTED OTHER MAJOR MODELS: Demucs htdemucs; DeepFilterNet3; AWS Translate optional
    GPU INSTANCE: g4dn.xlarge planned
    INPUT DURATION: 5.167 seconds
    CPU TOTAL PROCESSING TIME: transcription 18.196 s; TTS 107.422 s
    CPU REAL-TIME FACTOR: transcription 3.521594; TTS derived output ratio ~19.46
    PEAK VRAM: N/A on CPU worker
    CPU TEST COST: transcription $0.001178; TTS $0.006953; failed dubbing attempt $0.005268
    CPU COST PER INPUT MINUTE: transcription $0.013679; failed dubbing attempt $0.061173
    GPU TEST COST: $0.00
    GPU SCALE-TO-ZERO: idle zero state PASS; completed GPU job pending quota
    TERRAFORM: PASS
    TESTS: PASS
    SECURITY CHECKS: PASS
    EXPENSIVE COMPUTE CURRENTLY RUNNING: NO
    LICENSE LINE: No license analysis performed; user will review before production
    REMAINING GATES: real full hybrid CPU media acceptance through live worker; EC2 G/VT quota request CASE_OPENED; real GPU E2E pending
