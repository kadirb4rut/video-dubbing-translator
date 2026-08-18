# Local development

## API and UI

1. Create a Python environment and install `backend/requirements.txt`.
2. From the repository root, run `PYTHONPATH=backend uvicorn app.main:app --reload --port 8000`.
3. In a second shell, run `cd frontend && npm install && npm run dev`.
4. Run `PYTHONPATH=backend python -m app.worker --loop` in a third shell. The local queue is in-process, so the worker also polls queued rows from SQLite; use SQS for a multi-process deployment.

`ffmpeg` and `ffprobe` must be installed. The API does not accept an upload until FFprobe returns a positive duration and a supported audio/video stream.

## ML worker images

The API image intentionally does not install GPU ML dependencies. Build `backend/Dockerfile.worker` with `WORKER_REQUIREMENTS=requirements.worker-cpu.txt` for transcription/stems/noise capabilities, or the GPU requirements file for Chatterbox. Missing packages fail a job explicitly and release its reservation.

## Database

SQLite is a local fallback only. For PostgreSQL, set `DATABASE_URL` and run `cd backend && alembic upgrade head`. The migration is the source of truth; `create_tables()` exists only to make a fresh local SQLite checkout usable.
