# Local development

## API and UI

1. Create a Python environment and install `backend/requirements-dev.txt` for the API plus test tooling. Production API and worker images install only their runtime/provider requirement files.
2. From the repository root, run `PYTHONPATH=backend uvicorn app.main:app --reload --port 8000`.
3. In a second shell, run `cd frontend && npm install && npm run dev`.
4. Run `PYTHONPATH=backend python -m app.worker` in a third shell. Add `--once` for a single poll. The local queue is in-process, so the worker also polls queued rows from SQLite; use SQS for a multi-process deployment.

`ffmpeg` and `ffprobe` must be installed. The API does not accept an upload until FFprobe returns a positive duration and a supported audio/video stream.

## Real provider smoke tests

The API tests use deterministic provider doubles. For real media checks, use the worker image or a matching isolated environment and run `PYTHONPATH=backend python scripts/benchmarks/benchmark_providers.py <media>`. The complete dubbing benchmark is `PYTHONPATH=backend python scripts/benchmarks/benchmark_dubbing.py <video> --reference-voice <voice.wav>`, which records stage timings, runtime GPU/instance metadata when supplied or detected, an optional measured `$ / processed minute`, and validates the final MP4. Use `--voxcpm-python <compatible-python>` only when a workstation needs a separate benchmark runtime; production workers use the in-process VoxCPM2 adapter. The live CPU acceptance evidence and exact timings are maintained in `docs/AWS_MEDIA_PIPELINE_STATUS.md`.

Whisper and Demucs can run on CPU. DeepFilterNet is pinned to `0.5.6` and remains the default production noise provider, so its worker image must include the native `libdf` dependency. In a constrained development environment only, set `NOISE_REMOVAL_FALLBACK=ffmpeg-afftdn` to use FFmpeg's real `afftdn` filter explicitly; this is a deterministic audio fallback, not an ML noise-removal claim.

## ML worker images

The API image intentionally does not install ML dependencies. Build `backend/Dockerfile.worker` with `WORKER_REQUIREMENTS=requirements.worker-cpu-full.txt` for CPU validation or `requirements.worker-gpu.txt` for the planned g4dn.xlarge/T4 path. Both images pin `voxcpm==2.0.3` and use the exact VoxCPM2 checkpoint revision from configuration. CPU uses an explicit CPU dtype; the T4 plan uses FP16 rather than assuming BF16 support. Keep the provider import smoke test and `pip-audit --local` green before enabling a GPU image. The Compose worker uses `VOXCPM_DEVICE=cpu` and `VOXCPM_DTYPE=bfloat16` for local development. Missing packages fail a job explicitly and release its reservation.

The production default is `TRANSLATION_PROVIDER=google-deep-translator`, implemented with `deep-translator`/`GoogleTranslator`; it keeps translation fast and does not require a local translation model. When a translated segment's first TTS duration exceeds the configured tolerance, `TRANSLATION_REFINEMENT_PROVIDER=hymt2` enables one lazy `tencent/Hy-MT2-1.8B` shortening pass before the bounded FFmpeg speed adjustment. `TRANSLATION_PROVIDER=configured-api` and `TRANSLATION_PROVIDER=aws-translate` remain explicit alternatives for comparison or fallback.

Dubbing fits each generated clip into the available segment window, pads shorter speech, and refuses a segment that would require more than `DUBBING_MAX_SPEEDUP` (default `1.6x`) acceleration. This keeps translated speech from overlapping later segments or being silently truncated at the video boundary.

## Database

SQLite is a local fallback only. For PostgreSQL, set `DATABASE_URL` and run `cd backend && alembic upgrade head`. For the default root-level SQLite database, run `DATABASE_URL=sqlite:////absolute/path/to/My-SaaS/data/lingowave.db PYTHONPATH=. alembic upgrade head` from `backend/`. The migration is the source of truth; `create_tables()` exists only to make a fresh local SQLite checkout usable.

The browser uses `/api/media/presign` plus a direct `PUT` when S3-compatible storage is configured, then calls `/complete` for FFprobe inspection. Local storage returns `409` from the presign endpoint and intentionally falls back to the multipart API upload.

Browser acceptance coverage lives in `frontend/e2e/lingowave.spec.js` and uses Playwright against a running frontend and API. Run `cd frontend && npm run test:e2e`; set `E2E_FRONTEND_URL` when the frontend is not at `http://127.0.0.1:5173`. Media-provider workflows require an eligible worker and model cache and are intentionally not part of the fast unit-test job. The manual `.github/workflows/browser-e2e.yml` workflow runs the suite against an externally started/deployed stack and uploads Playwright traces and reports.

When the API runs in Compose, `S3_ENDPOINT_URL` must use the service name (`http://minio:9000`) so the API and worker can reach MinIO over the Compose network. Browser clients instead need `S3_PRESIGN_ENDPOINT_URL` set to the host-visible address (`http://localhost:9000`). In AWS, leave both endpoint variables empty so the SDK targets regional S3.

The Compose bootstrap creates `lingowave-jobs-dlq` and configures `lingowave-jobs` with a three-receive redrive policy, matching the production queue topology. The worker leaves an in-flight SQS message unacknowledged until the job settles, allowing visibility-timeout redelivery after a crash.

Rate limits are stored in the `rate_limit_buckets` table rather than process memory, so multiple API replicas share the same fixed-window counters. Cleanup runs opportunistically during requests and removes windows older than two minutes.
