from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .domain import JobState
from .ledger import finalize, release
from .models import Job, JobEvent, MediaAsset, UsageRecord, VoiceProfile, now
from .providers_real import ChatterboxMultilingualVoiceProvider, ConfiguredTranslationProvider, DeepFilterNetNoiseProvider, DemucsStemSeparationProvider, ProviderUnavailable, WhisperTranscriptionProvider, write_srt
from .queueing import JobMessage, job_queue
from .storage import object_key, object_store


class JobWorker:
    def __init__(self):
        self.queue = job_queue()
        self.worker_type = os.getenv("WORKER_TYPE", "cpu-audio")

    def _claim(self, db: Session, message: JobMessage | None = None) -> Job | None:
        if message:
            job = db.scalar(select(Job).where(Job.id == message.job_id, Job.state == JobState.QUEUED.value))
        else:
            job = db.scalar(select(Job).where(Job.state == JobState.QUEUED.value).order_by(Job.created_at.asc()).with_for_update(skip_locked=True))
        if not job:
            return None
        job.state = JobState.PROVISIONING.value
        db.add(JobEvent(job_id=job.id, state=job.state, message="Worker claimed job", metadata_json=json.dumps({"worker_type": self.worker_type})))
        db.commit()
        return job

    def _event(self, db: Session, job: Job, state: str, message: str, metadata: dict | None = None) -> None:
        job.state = state
        job.updated_at = now()
        db.add(JobEvent(job_id=job.id, state=state, message=message, metadata_json=json.dumps(metadata or {})))
        db.commit()

    def _download(self, asset: MediaAsset, work: Path) -> Path:
        source = work / asset.original_filename
        object_store().download(asset.object_key, source)
        return source

    def _ffmpeg(self, args: list[str], *, timeout: int = 3600) -> None:
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *args], check=True, timeout=timeout)

    def _extract_audio(self, source: Path, output: Path) -> Path:
        self._ffmpeg(["-i", str(source), "-vn", "-ac", "1", "-ar", "24000", str(output)])
        return output

    def _upload_output(self, job: Job, output: Path) -> str:
        key = object_key(job.user_id, "outputs", output.name)
        with output.open("rb") as source:
            object_store().put(key, source, content_type="application/octet-stream")
        return key

    def _transcribe(self, audio: Path, output: Path, language: str | None) -> Path:
        segments = WhisperTranscriptionProvider().transcribe(audio, language=language)
        return write_srt(segments, output)

    def _stems(self, audio: Path, output: Path, stem_count: int) -> Path:
        stems = DemucsStemSeparationProvider().separate(audio, stems=stem_count, output_dir=output.parent / "demucs")
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, path in stems.items():
                archive.write(path, arcname=f"{name}.wav")
        return output

    def _noise(self, audio: Path, output: Path) -> Path:
        return DeepFilterNetNoiseProvider().enhance(audio, output_path=output)

    def _tts(self, db: Session, job: Job, work: Path, audio: Path, output: Path) -> Path:
        options = json.loads(job.options_json)
        voice_id = options.get("voice_profile_id")
        text = options.get("text")
        language = options.get("target_language") or "en"
        if not voice_id or not text:
            raise ProviderUnavailable("tts jobs require voice_profile_id and text options")
        profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == job.user_id, VoiceProfile.deleted_at.is_(None)))
        if not profile:
            raise ProviderUnavailable("Voice profile not found")
        reference = work / ("reference" + Path(profile.reference_object_key).suffix)
        object_store().download(profile.reference_object_key, reference)
        return ChatterboxMultilingualVoiceProvider().synthesize(text, reference_voice=reference, language=language, output_path=output)

    def _dubbing(self, db: Session, job: Job, work: Path, source: Path, audio: Path, output: Path) -> Path:
        options = json.loads(job.options_json)
        source_language = options.get("source_language")
        target_language = options.get("target_language")
        voice_id = options.get("voice_profile_id")
        if not target_language or not voice_id:
            raise ProviderUnavailable("dubbing jobs require target_language and voice_profile_id")
        segments = list(WhisperTranscriptionProvider().transcribe(audio, language=source_language))
        segments = list(ConfiguredTranslationProvider().translate(segments, source=source_language or "auto", target=target_language))
        profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == job.user_id, VoiceProfile.deleted_at.is_(None)))
        if not profile:
            raise ProviderUnavailable("Voice profile not found")
        reference = work / ("reference" + Path(profile.reference_object_key).suffix)
        object_store().download(profile.reference_object_key, reference)
        clips: list[Path] = []
        for index, segment in enumerate(segments):
            clip = work / f"segment-{index:04d}.wav"
            ChatterboxMultilingualVoiceProvider().synthesize(segment["text"], reference_voice=reference, language=target_language, output_path=clip)
            clips.append(clip)
        if not clips:
            raise ProviderUnavailable("Transcription returned no speech segments")
        inputs = []
        filters = []
        for index, clip in enumerate(clips):
            inputs.extend(["-i", str(clip)])
            delay = max(0, round(float(segments[index]["start"]) * 1000))
            filters.append(f"[{index}:a]adelay={delay}|{delay},apad[a{index}]")
        mix_inputs = "".join(f"[a{index}]" for index in range(len(clips)))
        filters.append(f"{mix_inputs}amix=inputs={len(clips)}:duration=longest:normalize=0[dub]")
        dubbed_audio = work / "dubbed.wav"
        self._ffmpeg([*inputs, "-filter_complex", ";".join(filters), "-map", "[dub]", "-ar", "48000", str(dubbed_audio)])
        self._ffmpeg(["-i", str(source), "-i", str(dubbed_audio), "-map", "0:v?", "-map", "1:a", "-c:v", "copy", "-shortest", str(output)])
        return output

    def process(self, job_id: str) -> None:
        started = time.monotonic()
        db = SessionLocal()
        job = db.get(Job, job_id)
        if not job or job.state in {JobState.CANCELLED.value, JobState.COMPLETED.value}:
            db.close()
            return
        try:
            asset = db.get(MediaAsset, job.media_asset_id)
            if not asset:
                raise ProviderUnavailable("Input media asset not found")
            with tempfile.TemporaryDirectory(prefix=f"lingowave-{job.id}-") as temp:
                work = Path(temp)
                self._event(db, job, JobState.DOWNLOADING.value, "Downloading source media")
                source = self._download(asset, work)
                audio = self._extract_audio(source, work / "source.wav")
                options = json.loads(job.options_json)
                operation = job.operation
                if operation == "transcription":
                    self._event(db, job, JobState.TRANSCRIBING.value, "Transcribing source audio")
                    output = self._transcribe(audio, work / "transcript.srt", options.get("source_language"))
                elif operation == "stems":
                    self._event(db, job, JobState.SEPARATING_AUDIO.value, "Separating audio stems with Demucs")
                    output = self._stems(audio, work / "stems.zip", int(options.get("stems", 4)))
                elif operation == "noise":
                    self._event(db, job, JobState.SEPARATING_AUDIO.value, "Removing background noise with DeepFilterNet")
                    output = self._noise(audio, work / "enhanced.wav")
                elif operation == "tts":
                    self._event(db, job, JobState.SYNTHESIZING.value, "Synthesizing with Chatterbox Multilingual")
                    output = self._tts(db, job, work, audio, work / "speech.wav")
                elif operation == "dubbing":
                    self._event(db, job, JobState.SEPARATING_AUDIO.value, "Preparing source audio")
                    self._event(db, job, JobState.TRANSCRIBING.value, "Transcribing source speech")
                    self._event(db, job, JobState.TRANSLATING.value, "Translating subtitle segments")
                    self._event(db, job, JobState.SYNTHESIZING.value, "Synthesizing translated speech with Chatterbox Multilingual")
                    output = self._dubbing(db, job, work, source, audio, work / "dubbed.mp4")
                else:
                    raise ProviderUnavailable(f"Unsupported operation: {operation}")
                self._event(db, job, JobState.UPLOADING.value, "Uploading completed output")
                job.output_object_key = self._upload_output(job, output)
            finalize(db, job, job.reserved_credits)
            job.completed_at = now()
            self._event(db, job, JobState.COMPLETED.value, "Job completed", {"output_object_key": job.output_object_key})
            db.add(UsageRecord(user_id=job.user_id, job_id=job.id, input_duration_seconds=asset.duration_seconds, wall_clock_seconds=time.monotonic() - started, worker_type=self.worker_type, retry_count=0))
            db.commit()
        except Exception as exc:
            db.rollback()
            job = db.get(Job, job_id)
            if job and job.state != JobState.CANCELLED.value:
                release(db, job, "provider_or_infrastructure_failure")
                job.state = JobState.FAILED.value
                job.error_code = "PROVIDER_FAILURE" if isinstance(exc, ProviderUnavailable) else "WORKER_FAILURE"
                job.error_message = str(exc)[:2000]
                db.add(JobEvent(job_id=job.id, state=job.state, message=job.error_message, metadata_json=json.dumps({"error_type": type(exc).__name__})))
                asset = db.get(MediaAsset, job.media_asset_id)
                db.add(UsageRecord(user_id=job.user_id, job_id=job.id, input_duration_seconds=asset.duration_seconds if asset else None, wall_clock_seconds=time.monotonic() - started, worker_type=self.worker_type, retry_count=0))
                db.commit()
        finally:
            db.close()

    def run_once(self) -> bool:
        db = SessionLocal()
        try:
            message = self.queue.receive(timeout=0.05)
            job = self._claim(db, message)
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
