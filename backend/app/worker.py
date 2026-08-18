from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .domain import JobState
from .ledger import finalize, release
from .media import validate_output
from .models import Job, JobArtifact, JobEvent, JobStageMetric, MediaAsset, UsageRecord, VoiceProfile, now
from .providers_real import ChatterboxMultilingualVoiceProvider, DeepFilterNetNoiseProvider, DemucsStemSeparationProvider, ProviderUnavailable, WhisperTranscriptionProvider, translation_provider, write_srt, write_txt, write_vtt
from .queueing import JobMessage, job_queue
from .storage import object_key, object_store


class JobWorker:
    def __init__(self):
        self.queue = job_queue()
        self.worker_type = os.getenv("WORKER_TYPE", "cpu-audio")
        self.gpu_type = os.getenv("GPU_TYPE") or None
        self.max_retries = int(os.getenv("JOB_MAX_RETRIES", "3"))

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
        stale = db.scalars(select(Job).where(Job.state == JobState.PROVISIONING.value, Job.updated_at < cutoff)).all()
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
        db.add(JobEvent(job_id=job.id, state=state, message=message, metadata_json=json.dumps(metadata or {})))
        db.commit()

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

    def _transcribe(self, audio: Path, output_dir: Path, language: str | None) -> tuple[list[dict], dict[str, Path]]:
        segments = list(WhisperTranscriptionProvider().transcribe(audio, language=language))
        return segments, {"srt": write_srt(segments, output_dir / "transcript.srt"), "vtt": write_vtt(segments, output_dir / "transcript.vtt"), "txt": write_txt(segments, output_dir / "transcript.txt")}

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
        result = ChatterboxMultilingualVoiceProvider().synthesize(text, reference_voice=self._reference(db, job, work), language=options.get("target_language") or "en", output_path=output)
        validate_output(result, "audio")
        return result

    def _dubbing(self, db: Session, job: Job, work: Path, source: Path, audio: Path, output: Path) -> Path:
        options = json.loads(job.options_json)
        source_language = options.get("source_language")
        target_language = options.get("target_language")
        if not target_language:
            raise ProviderUnavailable("dubbing jobs require target_language")
        background = None
        if options.get("keep_background", True):
            with self._stage(db, job, JobState.SEPARATING_AUDIO.value, "Separating background audio with Demucs"):
                background = DemucsStemSeparationProvider().separate(audio, stems=2, output_dir=work / "background")["no_vocals"]
        with self._stage(db, job, JobState.TRANSCRIBING.value, "Transcribing source speech"):
            segments = list(WhisperTranscriptionProvider().transcribe(audio, language=source_language))
        with self._stage(db, job, JobState.TRANSLATING.value, "Translating subtitle segments"):
            segments = list(translation_provider().translate(segments, source=source_language or "auto", target=target_language))
        with self._stage(db, job, JobState.SYNTHESIZING.value, "Synthesizing translated speech with Chatterbox Multilingual"):
            reference = self._reference(db, job, work)
            clips: list[Path] = []
            for index, segment in enumerate(segments):
                clip = work / f"segment-{index:04d}.wav"
                ChatterboxMultilingualVoiceProvider().synthesize(segment["text"], reference_voice=reference, language=target_language, output_path=clip)
                clips.append(clip)
        if not clips:
            raise ProviderUnavailable("Transcription returned no speech segments")
        inputs: list[str] = []
        filters: list[str] = []
        for index, clip in enumerate(clips):
            inputs.extend(["-i", str(clip)])
            delay = max(0, round(float(segments[index]["start"]) * 1000))
            filters.append(f"[{index}:a]adelay={delay}|{delay},apad[a{index}]")
        mix_inputs = "".join(f"[a{index}]" for index in range(len(clips)))
        filters.append(f"{mix_inputs}amix=inputs={len(clips)}:duration=longest:normalize=0[dub]")
        dubbed_audio = work / "dubbed.wav"
        with self._stage(db, job, JobState.MIXING.value, "Mixing translated speech"):
            self._ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[dub]", "-ar", "48000", str(dubbed_audio)])
            if background:
                mixed_audio = work / "mixed.wav"
                self._ffmpeg(["-i", str(background), "-i", str(dubbed_audio), "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0[mix]", "-map", "[mix]", "-ar", "48000", str(mixed_audio)])
                dubbed_audio = mixed_audio
            self._ffmpeg(["-i", str(source), "-i", str(dubbed_audio), "-map", "0:v?", "-map", "1:a", "-c:v", "copy", "-shortest", str(output)])
        validate_output(output, "video")
        return output

    def _record_usage(self, db: Session, job: Job, duration: float | None, started: float) -> None:
        record = db.scalar(select(UsageRecord).where(UsageRecord.job_id == job.id))
        values = {"user_id": job.user_id, "job_id": job.id, "input_duration_seconds": duration, "wall_clock_seconds": time.monotonic() - started, "worker_type": self.worker_type, "gpu_type": self.gpu_type, "retry_count": max(0, (job.retry_count or 1) - 1)}
        if record:
            for key, value in values.items():
                setattr(record, key, value)
        else:
            db.add(UsageRecord(**values))

    def process(self, job_id: str) -> None:
        started = time.monotonic()
        db = SessionLocal()
        job = db.get(Job, job_id)
        if not job or job.state in {JobState.CANCELLED.value, JobState.COMPLETED.value}:
            db.close()
            return
        asset = db.get(MediaAsset, job.media_asset_id) if job.media_asset_id else None
        try:
            with tempfile.TemporaryDirectory(prefix=f"lingowave-{job.id}-") as temp:
                work = Path(temp)
                options = json.loads(job.options_json)
                operation = job.operation
                artifacts: list[tuple[Path, str, str]] = []
                source = audio = None
                if asset:
                    with self._stage(db, job, JobState.DOWNLOADING.value, "Downloading source media"):
                        source = self._download(asset, work)
                    audio = self._extract_audio(source, work / "source.wav")
                if operation in {"transcription", "subtitle_translation"}:
                    with self._stage(db, job, JobState.TRANSCRIBING.value, "Transcribing source audio"):
                        segments, outputs = self._transcribe(audio, work, options.get("source_language"))
                    if operation == "subtitle_translation":
                        with self._stage(db, job, JobState.TRANSLATING.value, "Translating subtitle segments"):
                            segments = list(translation_provider().translate(segments, source=options.get("source_language") or "auto", target=options.get("target_language") or "en"))
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
                        self._upload_artifact(db, job, path, artifact_name=artifact_name, content_type=content_type)
                    db.commit()
            finalize(db, job, job.reserved_credits)
            job.completed_at = now()
            self._event(db, job, JobState.COMPLETED.value, "Job completed", {"artifact_count": len(artifacts)})
            self._record_usage(db, job, asset.duration_seconds if asset else None, started)
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if job and job.state != JobState.CANCELLED.value:
                if (job.retry_count or 0) >= self.max_retries:
                    release(db, job, "provider_or_infrastructure_failure")
                    job.state = JobState.FAILED.value
                    job.error_code = "PROVIDER_FAILURE" if isinstance(exc, ProviderUnavailable) else "WORKER_FAILURE"
                    job.error_message = str(exc)[:2000]
                    db.add(JobEvent(job_id=job.id, state=job.state, message=job.error_message, metadata_json=json.dumps({"error_type": type(exc).__name__, "attempt": job.retry_count})))
                    self._record_usage(db, job, asset.duration_seconds if asset else None, started)
                    db.commit()
                else:
                    job.state = JobState.QUEUED.value
                    job.error_code = "RETRYING"
                    job.error_message = str(exc)[:2000]
                    db.add(JobEvent(job_id=job.id, state=job.state, message="Job returned to queue after worker failure", metadata_json=json.dumps({"error_type": type(exc).__name__, "attempt": job.retry_count})))
                    db.commit()
                    self.queue.send(JobMessage(job.id, job.operation))
        finally:
            db.close()

    def run_once(self) -> bool:
        db = SessionLocal()
        try:
            self._recover_stale(db)
            message = self.queue.receive(timeout=0.05)
            job = self._claim(db, message)
            if message and not job:
                self.queue.delete(message)
            if not job:
                return False
            if message:
                self.queue.delete(message)
            job_id = job.id
        finally:
            db.close()
        self.process(job_id)
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
