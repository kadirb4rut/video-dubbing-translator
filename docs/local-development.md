# Local development

## API and UI

1. Create a Python environment and install `backend/requirements.txt`.
2. From the repository root, run `PYTHONPATH=backend uvicorn app.main:app --reload --port 8000`.
3. In a second shell, run `cd frontend && npm install && npm run dev`.
4. Run `PYTHONPATH=backend python -m app.worker` in a third shell. Add `--once` for a single poll. The local queue is in-process, so the worker also polls queued rows from SQLite; use SQS for a multi-process deployment.

`ffmpeg` and `ffprobe` must be installed. The API does not accept an upload until FFprobe returns a positive duration and a supported audio/video stream.

## Real provider smoke tests

The API tests use deterministic provider doubles. For real media checks, use the worker image or a matching isolated environment and run `PYTHONPATH=backend python scripts/benchmarks/benchmark_providers.py <media>`. The complete dubbing benchmark is `PYTHONPATH=backend python scripts/benchmarks/benchmark_dubbing.py <video> --reference-voice <voice.wav> --translated-segments <segments.json>`, which records stage timings, runtime GPU/instance metadata when supplied or detected, an optional measured `$ / processed minute`, and validates the final MP4. When local Python ABI conflicts prevent loading the audio and voice stacks together, `--chatterbox-python <compatible-python>` runs Chatterbox in a separate benchmark-only runtime; it does not change the production in-process provider contract. The measured local multi-runtime fixture completed a 3.129-second MP4 in 76.3944 seconds: Whisper 2.1647s, Demucs background 4.7254s, Chatterbox synthesis 69.1396s, and FFmpeg mix/mux 0.2831s. A separate real API voice acceptance generated and downloaded `speech.wav` with Chatterbox and confirmed that deleting the consented profile removed its stored reference; that acceptance used the same benchmark-only compatible runtime bridge because the local backend Python ABI cannot load Chatterbox. Whisper and Demucs can run on CPU; DeepFilterNet is pinned to `0.5.6` and remains the default production noise provider, so its worker image must include the native `libdf` dependency. In a constrained development environment only, set `NOISE_REMOVAL_FALLBACK=ffmpeg-afftdn` to use FFmpeg's real `afftdn` filter explicitly; this is a deterministic audio fallback, not an ML noise-removal claim. Chatterbox should run from the GPU requirements image with `CHATTERBOX_DEVICE=cuda` (CPU is supported only for development experiments). Keep the resulting timing/output evidence outside the API test suite and do not mark unmeasured cost profiles as production values.

## ML worker images

The API image intentionally does not install ML dependencies. Build `backend/Dockerfile.worker` with `WORKER_REQUIREMENTS=requirements.worker-cpu.txt` for a lightweight transcription/stems/noise worker, or the GPU requirements file for Chatterbox. The Compose worker uses the ML requirements image with `CHATTERBOX_DEVICE=cpu` so local development can exercise TTS and dubbing without an AWS GPU; use a GPU-backed image and `CHATTERBOX_DEVICE=cuda` for production inference. Missing packages fail a job explicitly and release its reservation.

When translation credentials are unavailable, set `TRANSLATION_PROVIDER=fixture` for deterministic development smoke tests. The fixture preserves segment timing and fails on unknown text; it never silently treats source text as a successful translation. Production can use `TRANSLATION_PROVIDER=configured-api` with `TRANSLATION_API_URL` and optional `TRANSLATION_API_KEY`, or `TRANSLATION_PROVIDER=aws-translate` with the worker's AWS task-role permission.

Dubbing fits each generated clip into the available segment window, pads shorter speech with silence, and refuses a segment that would require more than `DUBBING_MAX_SPEEDUP` (default `1.6x`) acceleration. This keeps translated speech from overlapping later segments or being silently truncated at the video boundary.

## Database

SQLite is a local fallback only. For PostgreSQL, set `DATABASE_URL` and run `cd backend && alembic upgrade head`. For the default root-level SQLite database, run `DATABASE_URL=sqlite:////absolute/path/to/My-SaaS/data/lingowave.db PYTHONPATH=. alembic upgrade head` from `backend/`. The migration is the source of truth; `create_tables()` exists only to make a fresh local SQLite checkout usable.

The browser uses `/api/media/presign` plus a direct `PUT` when S3-compatible storage is configured, then calls `/complete` for FFprobe inspection. Local storage returns `409` from the presign endpoint and intentionally falls back to the multipart API upload.

Browser acceptance coverage lives in `frontend/e2e/lingowave.spec.js` and uses Playwright against a running frontend, API, and worker. Run `cd frontend && npm run test:e2e`; set `E2E_FRONTEND_URL` when the frontend is not at `http://127.0.0.1:5173`. The tests generate a short FFmpeg fixture, create a real account, upload through the UI, wait for a real worker result, and assert downloadable transcript artifacts. They are intentionally not part of the fast unit-test job because they require a browser installation and a running media stack. The manual `.github/workflows/browser-e2e.yml` workflow runs the same suite against an externally started/deployed stack and uploads Playwright traces and reports.

When the API runs in Compose, `S3_ENDPOINT_URL` must use the service name (`http://minio:9000`) so the API and worker can reach MinIO over the Compose network. Browser clients instead need `S3_PRESIGN_ENDPOINT_URL` set to the host-visible address (`http://localhost:9000`); the API uses that separate endpoint only while generating presigned URLs. In an AWS deployment, leave both endpoint variables empty so the SDK targets the regional S3 service.

The Compose bootstrap creates `lingowave-jobs-dlq` and configures `lingowave-jobs` with a three-receive redrive policy, matching the production queue topology. The worker leaves an in-flight SQS message unacknowledged until the job settles, allowing visibility-timeout redelivery after a crash.

Rate limits are stored in the `rate_limit_buckets` table rather than process memory, so multiple API replicas share the same fixed-window counters. Cleanup runs opportunistically during requests and removes windows older than two minutes.
