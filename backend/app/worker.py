from __future__ import annotations

import argparse
import json
import logging
import os
import resource
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .config import settings
from .db import SessionLocal
from .domain import JobState
from .ledger import finalize, release
from .mail import mail_provider
from .media import inspect_media, validate_output
from .models import (
    GpuCostProfile,
    Job,
    JobArtifact,
    JobEvent,
    JobStageMetric,
    MediaAsset,
    UsageRecord,
    User,
    VoiceProfile,
    WorkerLease,
    now,
)
from .providers_real import (
    ChatterboxMultilingualVoiceProvider,
    DeepFilterNetNoiseProvider,
    DemucsStemSeparationProvider,
    ProviderUnavailable,
    WhisperTranscriptionProvider,
    translation_provider,
    validate_segments,
    write_srt,
    write_txt,
    write_vtt,
)
from .queueing import JobMessage, job_queue
from .storage import object_key, object_store

logger = logging.getLogger("lingowave.worker")


RECOVERABLE_WORKER_STATES = (
    JobState.PROVISIONING.value,
    JobState.DOWNLOADING.value,
    JobState.SEPARATING_AUDIO.value,
    JobState.TRANSCRIBING.value,
    JobState.TRANSLATING.value,
    JobState.SYNTHESIZING.value,
    JobState.MIXING.value,
    JobState.LIP_SYNCING.value,
    JobState.UPLOADING.value,
)
MAX_DUBBING_SPEEDUP = float(os.getenv("DUBBING_MAX_SPEEDUP", "1.6"))


class JobWorker:
    def __init__(self):
        self.queue = job_queue()
        self.worker_type = os.getenv("WORKER_TYPE", "cpu-audio")
        self.gpu_type = os.getenv("GPU_TYPE") or None
        self.max_retries = int(os.getenv("JOB_MAX_RETRIES", "3"))
        self.lease_seconds = int(os.getenv("WORKER_LEASE_SECONDS", "900"))
        self.worker_id = os.getenv("WORKER_ID") or f"{socket.gethostname()}-{uuid.uuid4().hex[:12]}"
        self._requeued_job_ids: set[str] = set()
        self._active_message: JobMessage | None = None
        self._worker_started_monotonic = time.monotonic()
        self._model_load_seconds = 0.0

    def _touch_lease(self, db: Session) -> None:
        lease = db.get(WorkerLease, self.worker_id)
        if not lease:
            lease = WorkerLease(id=self.worker_id, worker_type=self.worker_type, gpu_type=self.gpu_type, started_at=now())
            db.add(lease)
        lease.worker_type = self.worker_type
        lease.gpu_type = self.gpu_type
        lease.heartbeat_at = now()
        lease.expires_at = now() + timedelta(seconds=self.lease_seconds)

    def _heartbeat(self, db: Session) -> None:
        self._touch_lease(db)
        db.commit()

    def _start_lease_heartbeat(self) -> tuple[threading.Event, threading.Thread]:
        stop = threading.Event()
        interval = max(5, min(60, self.lease_seconds // 3, max(5, settings.sqs_visibility_timeout_seconds // 3)))
        active_message = self._active_message

        def loop() -> None:
            while not stop.wait(interval):
                try:
                    with SessionLocal() as heartbeat_db:
                        self._heartbeat(heartbeat_db)
                    if active_message and active_message.receipt_handle:
                        self.queue.change_visibility(active_message, settings.sqs_visibility_timeout_seconds)
                except Exception:
                    logger.exception("worker lease or queue visibility heartbeat failed", extra={"worker_id": self.worker_id})

        thread = threading.Thread(target=loop, name=f"lingowave-lease-{self.worker_id}", daemon=True)
        thread.start()
        return stop, thread

    def _claim(self, db: Session, message: JobMessage | None = None) -> Job | None:
        if message:
            job = db.scalar(select(Job).where(Job.id == message.job_id, Job.state == JobState.QUEUED.value))
        else:
            job = db.scalar(select(Job).where(Job.state == JobState.QUEUED.value).order_by(Job.created_at.asc()).with_for_update(skip_locked=True))
        if not job:
            return None
        job.state = JobState.PROVISIONING.value
        job.retry_count = (job.retry_count or 0) + 1
        db.add(JobEvent(job_id=job.id, state=job.state, message="Worker claimed job", metadata_json=json.dumps({"worker_type": self.worker_type, "attempt": job.retry_count})))
        db.commit()
        return job

    def _recover_stale(self, db: Session) -> None:
        cutoff = now() - timedelta(minutes=int(os.getenv("JOB_STALE_MINUTES", "30")))
        stale = db.scalars(select(Job).where(Job.state.in_(RECOVERABLE_WORKER_STATES), Job.updated_at < cutoff)).all()
        for job in stale:
            if (job.retry_count or 0) < self.max_retries:
                job.state = JobState.QUEUED.value
                db.add(JobEvent(job_id=job.id, state=job.state, message="Recovered stale worker lease", metadata_json=json.dumps({"attempt": job.retry_count})))
                self.queue.send(JobMessage(job.id, job.operation))
            else:
                release(db, job, "worker_lease_exhausted")
                job.state = JobState.FAILED.value
                job.error_code = "WORKER_LEASE_EXHAUSTED"
                job.error_message = "The worker lease expired too many times"
                db.add(JobEvent(job_id=job.id, state=job.state, message=job.error_message, metadata_json="{}"))
        if stale:
            db.commit()

    def _event(self, db: Session, job: Job, state: str, message: str, metadata: dict | None = None) -> None:
        job.state = state
        job.updated_at = now()
        self._touch_lease(db)
        db.add(JobEvent(job_id=job.id, state=state, message=message, metadata_json=json.dumps(metadata or {})))
        db.commit()

    def _ack_if_settled(self, message: JobMessage | None, job_id: str) -> None:
        """Delete a queue message only after processing settled or requeued the job.

        Leaving an active message unacknowledged lets SQS redeliver it after the
        visibility timeout if the worker dies between claim and completion. The
        stale-lease reconciler handles the case where the redelivery sees the
        job still in an active state.
        """
        if not message:
            return
        with SessionLocal() as db:
            job = db.get(Job, job_id)
            terminal = {JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELLED.value}
            if not job or job.state in terminal or job_id in self._requeued_job_ids:
                self.queue.delete(message)
                self._requeued_job_ids.discard(job_id)
            elif message.receipt_handle is None:
                # LocalQueue removes an item on receive, so put an active job
                # back when processing exits unexpectedly before it settles.
                self.queue.send(message)

    @contextmanager
    def _stage(self, db: Session, job: Job, state: str, message: str, metadata: dict | None = None):
        self._event(db, job, state, message, metadata)
        started_at = now()
        started = time.monotonic()
        try:
            yield
        finally:
            finished_at = now()
            db.add(JobStageMetric(job_id=job.id, stage=state, started_at=started_at, finished_at=finished_at, wall_clock_seconds=time.monotonic() - started, metadata_json=json.dumps(metadata or {})))
            db.commit()

    def _download(self, asset: MediaAsset, work: Path) -> Path:
        source = work / Path(asset.original_filename).name
        object_store().download(asset.object_key, source)
        return source

    def _ffmpeg(self, args: list[str], *, timeout: int = 3600) -> None:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=timeout)

    def _extract_audio(self, source: Path, output: Path) -> Path:
        self._ffmpeg(["-i", str(source), "-vn", "-ac", "1", "-ar", "24000", str(output)])
        validate_output(output, "audio")
        return output

    def _upload_artifact(self, db: Session, job: Job, output: Path, *, artifact_name: str, content_type: str) -> JobArtifact:
        if not output.is_file() or output.stat().st_size <= 0:
            raise ProviderUnavailable(f"Output artifact is empty: {output.name}")
        key = object_key(job.user_id, f"outputs/{job.id}", output.name)
        with output.open("rb") as source:
            size = object_store().put(key, source, content_type=content_type)
        artifact = JobArtifact(job_id=job.id, artifact_name=artifact_name, object_key=key, filename=output.name, content_type=content_type, size_bytes=size)
        db.add(artifact)
        db.flush()
        if job.output_object_key is None:
            job.output_object_key = key
        return artifact

    def _transcribe(self, audio: Path, output_dir: Path, language: str | None) -> tuple[list[dict], dict[str, Path], str | None]:
        provider = WhisperTranscriptionProvider()
        try:
            segments = list(provider.transcribe(audio, language=language))
            self._model_load_seconds += provider.last_model_load_seconds
            return segments, {"srt": write_srt(segments, output_dir / "transcript.srt"), "vtt": write_vtt(segments, output_dir / "transcript.vtt"), "txt": write_txt(segments, output_dir / "transcript.txt")}, provider.detected_language
        finally:
            provider.release()

    def _stems(self, audio: Path, output: Path, stem_count: int) -> tuple[Path, dict[str, Path]]:
        stems = DemucsStemSeparationProvider().separate(audio, stems=stem_count, output_dir=output.parent / "demucs")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in stems.items():
                archive.write(path, arcname=f"{name}.wav")
        return output, stems

    def _noise(self, audio: Path, output: Path) -> Path:
        result = DeepFilterNetNoiseProvider().enhance(audio, output_path=output)
        validate_output(result, "audio")
        return result

    def _reference(self, db: Session, job: Job, work: Path) -> Path:
        options = json.loads(job.options_json)
        voice_id = options.get("voice_profile_id")
        if not voice_id:
            raise ProviderUnavailable("A consented voice_profile_id is required")
        profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == job.user_id, VoiceProfile.status == "active", VoiceProfile.deleted_at.is_(None)))
        if not profile:
            raise ProviderUnavailable("Voice profile not found or revoked")
        reference = work / ("reference" + Path(profile.reference_object_key).suffix)
        object_store().download(profile.reference_object_key, reference)
        return reference

    def _tts(self, db: Session, job: Job, work: Path, output: Path) -> Path:
        options = json.loads(job.options_json)
        text = options.get("text")
        if not text:
            raise ProviderUnavailable("tts jobs require text")
        provider = ChatterboxMultilingualVoiceProvider()
        try:
            result = provider.synthesize(text, reference_voice=self._reference(db, job, work), language=options.get("target_language") or "en", output_path=output)
            self._model_load_seconds += provider.last_model_load_seconds
            validate_output(result, "audio")
            return result
        finally:
            provider.release()

    def _dubbing(self, db: Session, job: Job, work: Path, source: Path, audio: Path, output: Path) -> Path:
        options = json.loads(job.options_json)
        source_language = options.get("source_language")
        target_language = options.get("target_language")
        if not target_language:
            raise ProviderUnavailable("dubbing jobs require target_language")
        background = None
        if options.get("keep_background", True):
            with self._stage(db, job, JobState.SEPARATING_AUDIO.value, "Separating background audio with Demucs"):
                background = DemucsStemSeparationProvider().separate(audio, stems=2, output_dir=work / "background")["instrumental"]
        with self._stage(db, job, JobState.TRANSCRIBING.value, "Transcribing source speech"):
            transcriber = WhisperTranscriptionProvider()
            try:
                segments = validate_segments(list(transcriber.transcribe(audio, language=source_language)))
                self._model_load_seconds += transcriber.last_model_load_seconds
            finally:
                transcriber.release()
            if transcriber.detected_language:
                source_language = source_language or transcriber.detected_language
                options["source_language"] = source_language
                job.options_json = json.dumps(options, sort_keys=True)
                db.add(JobEvent(job_id=job.id, state=JobState.TRANSCRIBING.value, message="Source language detected", metadata_json=json.dumps({"language": transcriber.detected_language})))
        with self._stage(db, job, JobState.TRANSLATING.value, "Translating subtitle segments"):
            segments = validate_segments(list(translation_provider().translate(segments, source=source_language or "auto", target=target_language)))
        source_duration = inspect_media(source)["duration_seconds"]
        with self._stage(db, job, JobState.SYNTHESIZING.value, "Synthesizing translated speech with Chatterbox Multilingual"):
            reference = self._reference(db, job, work) if options.get("preserve_voice", True) else None
            voice_provider = ChatterboxMultilingualVoiceProvider()
            clips: list[Path] = []
            try:
                for index, segment in enumerate(segments):
                    clip = work / f"segment-{index:04d}.wav"
                    voice_provider.synthesize(segment["text"], reference_voice=reference, language=target_language, output_path=clip)
                    clips.append(clip)
                self._model_load_seconds += voice_provider.last_model_load_seconds
            finally:
                voice_provider.release()
        if not clips:
            raise ProviderUnavailable("Transcription returned no speech segments")
        inputs: list[str] = []
        filters: list[str] = []
        for index, clip in enumerate(clips):
            inputs.extend(["-i", str(clip)])
            start = max(0.0, float(segments[index]["start"]))
            next_start = float(segments[index + 1]["start"]) if index + 1 < len(segments) else source_duration
            window_seconds = max(0.05, min(source_duration, max(start + 0.05, next_start)) - start)
            clip_duration = inspect_media(clip)["duration_seconds"]
            speedup = clip_duration / window_seconds
            if speedup > MAX_DUBBING_SPEEDUP:
                raise ProviderUnavailable(
                    f"Translated segment {index + 1} is too long for its timing window; "
                    f"required speed-up {speedup:.2f}x exceeds the configured {MAX_DUBBING_SPEEDUP:.2f}x limit"
                )
            audio_chain: list[str] = []
            if speedup > 1.01:
                audio_chain.append(f"atempo={speedup:.6f}")
            delay = round(start * 1000)
            audio_chain.extend(["apad", f"atrim=duration={window_seconds:.3f}", "asetpts=PTS-STARTPTS", f"adelay={delay}|{delay}"])
            filters.append(f"[{index}:a]" + ",".join(audio_chain) + f"[a{index}]")
        mix_inputs = "".join(f"[a{index}]" for index in range(len(clips)))
        filters.append(f"{mix_inputs}amix=inputs={len(clips)}:duration=longest:normalize=0[dub]")
        dubbed_audio = work / "dubbed.wav"
        with self._stage(db, job, JobState.MIXING.value, "Mixing translated speech"):
            self._ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[dub]", "-ar", "48000", "-t", f"{source_duration:.3f}", str(dubbed_audio)])
            if background:
                mixed_audio = work / "mixed.wav"
                self._ffmpeg(["-i", str(background), "-i", str(dubbed_audio), "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[mix]", "-map", "[mix]", "-ar", "48000", "-t", f"{source_duration:.3f}", str(mixed_audio)])
                dubbed_audio = mixed_audio
            self._ffmpeg(["-i", str(source), "-i", str(dubbed_audio), "-map", "0:v?", "-map", "1:a", "-c:v", "copy", "-t", f"{source_duration:.3f}", str(output)])
        validate_output(output, "video", expected_duration_seconds=source_duration)
        return output

    def _model_version(self, operation: str) -> str:
        return {
            "dubbing": "whisper+demucs+chatterbox-multilingual-v3",
            "transcription": "whisper",
            "subtitle_translation": "whisper+configured-translation-api",
            "stems": "demucs",
            "noise": "deepfilternet",
            "tts": "chatterbox-multilingual-v3",
        }.get(operation, "unknown")

    def _model_manifest(self, operation: str) -> dict[str, str]:
        manifest = {
            "stt": f"openai-whisper:{settings.whisper_model}",
            "translation": "aws-translate",
            "separation": f"demucs:{settings.demucs_model}",
            "denoising": "deepfilternet:DeepFilterNet3",
            "tts": "chatterbox-tts:multilingual-v3",
            "mux": "ffmpeg-runtime",
        }
        if operation == "transcription":
            return {"stt": manifest["stt"]}
        if operation == "subtitle_translation":
            return {key: manifest[key] for key in ("stt", "translation")}
        if operation == "stems":
            return {"separation": manifest["separation"]}
        if operation == "noise":
            return {"denoising": manifest["denoising"]}
        if operation == "tts":
            return {"tts": manifest["tts"]}
        return manifest

    def _measured_costs(self, db: Session, duration: float | None, wall_clock_seconds: float) -> tuple[float | None, float | None]:
        profile = None
        if self.gpu_type:
            profile = db.scalar(
                select(GpuCostProfile)
                .where(GpuCostProfile.gpu_type == self.gpu_type, GpuCostProfile.region == settings.s3_region, GpuCostProfile.measured.is_(True))
                .order_by(GpuCostProfile.created_at.desc())
            )
        hourly_price = profile.hourly_price_usd if profile and profile.hourly_price_usd and profile.hourly_price_usd > 0 else None
        if hourly_price is None:
            try:
                env_name = "GPU_HOURLY_PRICE_USD" if self.gpu_type else "COMPUTE_HOURLY_PRICE_USD"
                hourly_price = float(os.getenv(env_name, ""))
            except ValueError:
                hourly_price = None
        if hourly_price is None or hourly_price <= 0:
            return None, None
        actual = (wall_clock_seconds / 3600) * hourly_price
        if not profile:
            return round(actual, 6), round(actual, 6)
        if duration and profile.processed_minutes_per_hour and profile.processed_minutes_per_hour > 0:
            processing_seconds = (duration / 60) / profile.processed_minutes_per_hour * 3600
            startup_seconds = (profile.startup_seconds or 0) + (profile.model_load_seconds or 0)
            estimated = ((processing_seconds + startup_seconds) / 3600) * hourly_price
        else:
            estimated = actual
        return round(estimated, 6), round(actual, 6)

    def _cpu_utilization_percent(self, started: float) -> float | None:
        elapsed = max(0.001, time.monotonic() - started)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        cpu_seconds = usage.ru_utime + usage.ru_stime
        return round((cpu_seconds / elapsed) * 100, 3)

    def _memory_metrics(self) -> tuple[float | None, float | None]:
        peak_vram_mb = None
        try:
            import torch

            cuda = getattr(torch, "cuda", None)
            if cuda is not None and cuda.is_available():
                peak_vram_mb = round(cuda.max_memory_allocated() / (1024 * 1024), 3)
        except (ImportError, RuntimeError):
            pass
        # Linux reports ru_maxrss in KiB; macOS reports bytes. Workers run on Linux.
        divisor = 1024 * 1024 if sys.platform == "darwin" else 1024
        peak_ram_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / divisor / 1024, 3)
        return peak_vram_mb, peak_ram_mb

    def _reset_gpu_peak(self) -> None:
        try:
            import torch

            cuda = getattr(torch, "cuda", None)
            if cuda is not None and cuda.is_available():
                cuda.reset_peak_memory_stats()
        except (ImportError, RuntimeError):
            pass

    def _record_usage(
        self,
        db: Session,
        job: Job,
        duration: float | None,
        output_duration: float | None,
        started: float,
        *,
        input_bytes: int | None,
        output_bytes: int,
        queue_wait_seconds: float | None = None,
        compute_startup_seconds: float | None = None,
        model_load_seconds: float | None = None,
        peak_vram_mb: float | None = None,
        peak_ram_mb: float | None = None,
        cpu_utilization_percent: float | None = None,
    ) -> None:
        options = json.loads(job.options_json or "{}")
        record = db.scalar(select(UsageRecord).where(UsageRecord.job_id == job.id))
        attempt_wall_clock = time.monotonic() - started
        total_wall_clock = (record.wall_clock_seconds if record else 0) + attempt_wall_clock
        estimated_cost, actual_cost = self._measured_costs(db, duration, total_wall_clock)
        model_seconds = db.scalar(
            select(func.coalesce(func.sum(JobStageMetric.wall_clock_seconds), 0))
            .where(
                JobStageMetric.job_id == job.id,
                JobStageMetric.stage.in_({JobState.SEPARATING_AUDIO.value, JobState.TRANSCRIBING.value, JobState.TRANSLATING.value, JobState.SYNTHESIZING.value, JobState.MIXING.value}),
            )
        ) or 0
        cost_per_input_minute = None
        if actual_cost is not None and duration and duration > 0:
            cost_per_input_minute = round(actual_cost / (duration / 60), 6)
        values = {
            "user_id": job.user_id,
            "job_id": job.id,
            "input_duration_seconds": duration,
            "output_duration_seconds": output_duration,
            "input_bytes": input_bytes,
            "output_bytes": output_bytes,
            "wall_clock_seconds": total_wall_clock,
            "model_seconds": float(model_seconds),
            "queue_wait_seconds": queue_wait_seconds,
            "compute_startup_seconds": compute_startup_seconds,
            "model_load_seconds": model_load_seconds,
            "real_time_factor": round(total_wall_clock / duration, 6) if duration and duration > 0 else None,
            "peak_vram_mb": peak_vram_mb,
            "peak_ram_mb": peak_ram_mb,
            "cpu_utilization_percent": cpu_utilization_percent,
            "worker_type": self.worker_type,
            "gpu_type": self.gpu_type,
            "model_version": self._model_version(job.operation),
            "source_language": options.get("source_language"),
            "target_language": options.get("target_language"),
            "models_json": json.dumps(self._model_manifest(job.operation), sort_keys=True),
            "estimated_cost_usd": estimated_cost,
            "actual_cost_usd": actual_cost,
            "compute_cost_per_input_minute_usd": cost_per_input_minute,
            "retry_count": max(0, (job.retry_count or 1) - 1),
        }
        if record:
            for key, value in values.items():
                setattr(record, key, value)
        else:
            db.add(UsageRecord(**values))

    def _notify_job(self, db: Session, job: Job, state: str) -> None:
        user = db.get(User, job.user_id)
        if not user:
            return
        try:
            mail_provider().send_job_update(user.email, job_id=job.id, operation=job.operation, state=state)
        except Exception:
            logger.exception("job notification failed", extra={"job_id": job.id, "state": state})

    def process(self, job_id: str) -> None:
        started = time.monotonic()
        db = SessionLocal()
        job = db.get(Job, job_id)
        if not job or job.state in {JobState.CANCELLED.value, JobState.COMPLETED.value}:
            db.close()
            return
        asset = db.get(MediaAsset, job.media_asset_id) if job.media_asset_id else None
        created_at = job.created_at
        # SQLite returns DateTime(timezone=True) values without tzinfo while
        # PostgreSQL keeps them aware. Normalize both forms for telemetry.
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=now().tzinfo)
        queue_wait_seconds = max(0.0, (now() - created_at).total_seconds())
        compute_startup_seconds = max(0.0, started - self._worker_started_monotonic)
        self._model_load_seconds = 0.0
        self._reset_gpu_peak()
        heartbeat_stop, heartbeat_thread = self._start_lease_heartbeat()
        try:
            with tempfile.TemporaryDirectory(prefix=f"lingowave-{job.id}-") as temp:
                work = Path(temp)
                options = json.loads(job.options_json)
                operation = job.operation
                artifacts: list[tuple[Path, str, str]] = []
                output_bytes = 0
                output_duration: float | None = None
                source = audio = None
                if asset:
                    with self._stage(db, job, JobState.DOWNLOADING.value, "Downloading source media"):
                        source = self._download(asset, work)
                    # Keep the derived audio path distinct from the original
                    # filename. An uploaded audio asset may itself be named
                    # source.wav; using that same path would make ffmpeg fail
                    # with an input/output collision.
                    audio = self._extract_audio(source, work / "extracted-audio.wav")
                if operation in {"transcription", "subtitle_translation"}:
                    with self._stage(db, job, JobState.TRANSCRIBING.value, "Transcribing source audio"):
                        segments, outputs, detected_language = self._transcribe(audio, work, options.get("source_language"))
                        if detected_language:
                            options["source_language"] = options.get("source_language") or detected_language
                            job.options_json = json.dumps(options, sort_keys=True)
                            db.add(JobEvent(job_id=job.id, state=JobState.TRANSCRIBING.value, message="Source language detected", metadata_json=json.dumps({"language": detected_language})))
                    if operation == "subtitle_translation":
                        with self._stage(db, job, JobState.TRANSLATING.value, "Translating subtitle segments"):
                            translation_source = options.get("source_language") or detected_language or "auto"
                            segments = validate_segments(list(translation_provider().translate(segments, source=translation_source, target=options.get("target_language") or "en")))
                            outputs = {"srt": write_srt(segments, work / "translated.srt"), "vtt": write_vtt(segments, work / "translated.vtt"), "txt": write_txt(segments, work / "translated.txt")}
                    artifacts.extend((path, name, {"srt": "application/x-subrip", "vtt": "text/vtt", "txt": "text/plain"}[name]) for name, path in outputs.items())
                elif operation == "stems":
                    with self._stage(db, job, JobState.SEPARATING_AUDIO.value, "Separating audio stems with Demucs"):
                        archive, stems = self._stems(audio, work / "stems.zip", int(options.get("stems", 4)))
                    artifacts.append((archive, "stems_zip", "application/zip"))
                    artifacts.extend((path, name, "audio/wav") for name, path in stems.items())
                elif operation == "noise":
                    with self._stage(db, job, JobState.SEPARATING_AUDIO.value, "Removing background noise with DeepFilterNet"):
                        output = self._noise(audio, work / "enhanced.wav")
                    artifacts.append((output, "enhanced_audio", "audio/wav"))
                elif operation == "tts":
                    with self._stage(db, job, JobState.DOWNLOADING.value, "Preparing consented voice reference"):
                        pass
                    with self._stage(db, job, JobState.SYNTHESIZING.value, "Synthesizing with Chatterbox Multilingual"):
                        output = self._tts(db, job, work, work / "speech.wav")
                    artifacts.append((output, "speech", "audio/wav"))
                elif operation == "dubbing":
                    output = self._dubbing(db, job, work, source, audio, work / "dubbed.mp4")
                    artifacts.append((output, "dubbed_video", "video/mp4"))
                else:
                    raise ProviderUnavailable(f"Unsupported operation: {operation}")
                with self._stage(db, job, JobState.UPLOADING.value, "Uploading completed artifacts"):
                    for path, artifact_name, content_type in artifacts:
                        output_bytes += path.stat().st_size
                        self._upload_artifact(db, job, path, artifact_name=artifact_name, content_type=content_type)
                    if artifacts:
                        try:
                            output_duration = inspect_media(artifacts[0][0]).get("duration_seconds")
                        except (ValueError, RuntimeError, OSError):
                            output_duration = None
                    db.commit()
            finalize(db, job, job.reserved_credits)
            job.completed_at = now()
            self._event(db, job, JobState.COMPLETED.value, "Job completed", {"artifact_count": len(artifacts)})
            peak_vram_mb, peak_ram_mb = self._memory_metrics()
            self._record_usage(db, job, asset.duration_seconds if asset else None, output_duration, started, input_bytes=asset.size_bytes if asset else None, output_bytes=output_bytes, queue_wait_seconds=queue_wait_seconds, compute_startup_seconds=compute_startup_seconds, model_load_seconds=self._model_load_seconds, peak_vram_mb=peak_vram_mb, peak_ram_mb=peak_ram_mb, cpu_utilization_percent=self._cpu_utilization_percent(started))
            db.commit()
            self._notify_job(db, job, JobState.COMPLETED.value)
        except Exception as exc:  # noqa: BLE001 - every worker failure must settle the job safely
            db.rollback()
            job = db.get(Job, job_id)
            if job and job.state != JobState.CANCELLED.value:
                if (job.retry_count or 0) >= self.max_retries:
                    release(db, job, "provider_or_infrastructure_failure")
                    job.state = JobState.FAILED.value
                    job.error_code = "PROVIDER_FAILURE" if isinstance(exc, ProviderUnavailable) else "WORKER_FAILURE"
                    job.error_message = str(exc)[:2000]
                    db.add(JobEvent(job_id=job.id, state=job.state, message=job.error_message, metadata_json=json.dumps({"error_type": type(exc).__name__, "attempt": job.retry_count})))
                    peak_vram_mb, peak_ram_mb = self._memory_metrics()
                    self._record_usage(db, job, asset.duration_seconds if asset else None, None, started, input_bytes=asset.size_bytes if asset else None, output_bytes=0, queue_wait_seconds=queue_wait_seconds, compute_startup_seconds=compute_startup_seconds, model_load_seconds=self._model_load_seconds, peak_vram_mb=peak_vram_mb, peak_ram_mb=peak_ram_mb, cpu_utilization_percent=self._cpu_utilization_percent(started))
                    db.commit()
                    self._notify_job(db, job, JobState.FAILED.value)
                else:
                    job.state = JobState.QUEUED.value
                    job.error_code = "RETRYING"
                    job.error_message = str(exc)[:2000]
                    db.add(JobEvent(job_id=job.id, state=job.state, message="Job returned to queue after worker failure", metadata_json=json.dumps({"error_type": type(exc).__name__, "attempt": job.retry_count})))
                    peak_vram_mb, peak_ram_mb = self._memory_metrics()
                    self._record_usage(db, job, asset.duration_seconds if asset else None, None, started, input_bytes=asset.size_bytes if asset else None, output_bytes=0, queue_wait_seconds=queue_wait_seconds, compute_startup_seconds=compute_startup_seconds, model_load_seconds=self._model_load_seconds, peak_vram_mb=peak_vram_mb, peak_ram_mb=peak_ram_mb, cpu_utilization_percent=self._cpu_utilization_percent(started))
                    db.commit()
                    self.queue.send(JobMessage(job.id, job.operation))
                    self._requeued_job_ids.add(job.id)
        finally:
            heartbeat_stop.set()
            heartbeat_thread.join(timeout=2)
            db.close()

    def run_once(self) -> bool:
        db = SessionLocal()
        try:
            self._heartbeat(db)
            self._recover_stale(db)
            message = self.queue.receive(timeout=0.05)
            job = self._claim(db, message)
            if message and not job:
                current = db.get(Job, message.job_id)
                if not current or current.state in {JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELLED.value}:
                    self.queue.delete(message)
            if not job:
                return False
            job_id = job.id
        finally:
            db.close()
        try:
            self._active_message = message
            self.process(job_id)
        finally:
            self._ack_if_settled(message, job_id)
            self._active_message = None
        return True

    def run(self, poll_seconds: float = 1.0) -> None:
        while True:
            if not self.run_once():
                time.sleep(poll_seconds)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    worker = JobWorker()
    if args.once:
        worker.run_once()
    else:
        worker.run()
