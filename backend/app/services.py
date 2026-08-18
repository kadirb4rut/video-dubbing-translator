from __future__ import annotations

import hashlib
import json
import math
import tempfile
from pathlib import Path

from fastapi import HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import plan
from .config import cost_profiles, settings
from .domain import JobState
from .ledger import balance, grant, release, reserve
from .media import inspect_media, validate_upload
from .models import AuditEvent, CreditLedgerEntry, Job, JobEvent, MediaAsset, User, VoiceConsent, VoiceProfile, now
from .queueing import JobMessage, job_queue
from .storage import object_key, object_store


def _credit_rate(operation: str) -> float:
    profile = cost_profiles().get(operation)
    if not profile:
        raise HTTPException(status_code=503, detail=f"No configured cost profile for {operation}")
    rate = profile.get("credits_per_minute", profile.get("credits_per_media_minute"))
    if not isinstance(rate, (int, float)) or rate <= 0:
        raise HTTPException(status_code=503, detail=f"Cost profile for {operation} is not usable")
    return float(rate)


def estimate_for_duration(duration_seconds: float, operation: str, *, lip_sync: bool = False, quality: str = "balanced") -> int:
    if duration_seconds <= 0:
        raise HTTPException(status_code=422, detail="Media duration must be positive")
    multiplier = {"draft": 0.75, "balanced": 1.0, "studio": 1.25}.get(quality, 1.0)
    minutes = duration_seconds / 60
    total = minutes * _credit_rate(operation) * multiplier
    if lip_sync and operation == "dubbing":
        total += minutes * _credit_rate("lip_sync")
    return max(1, math.ceil(total))


def add_job_event(db: Session, job: Job, state: str, message: str, metadata: dict | None = None) -> None:
    job.state = state
    job.updated_at = now()
    db.add(JobEvent(job_id=job.id, state=state, message=message, metadata_json=json.dumps(metadata or {})))


def asset_for_user(db: Session, user: User, asset_id: str) -> MediaAsset:
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == user.id, MediaAsset.deleted_at.is_(None)))
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


def upload_asset(db: Session, user: User, upload: UploadFile) -> MediaAsset:
    filename = upload.filename or "upload"
    content_type = upload.content_type or "application/octet-stream"
    key = object_key(user.id, "media", filename)
    store = object_store()
    try:
        size = store.put(key, upload.file, content_type=content_type)
        validate_upload(filename, content_type, size)
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            store.download(key, temp_path)
            metadata = inspect_media(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except (ValueError, RuntimeError, OSError) as exc:
        store.delete(key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    asset = MediaAsset(
        user_id=user.id,
        object_key=key,
        original_filename=filename,
        mime_type=content_type,
        size_bytes=size,
        duration_seconds=metadata["duration_seconds"],
        width=metadata["width"],
        height=metadata["height"],
        fps=metadata["fps"],
        media_kind=metadata["media_kind"],
    )
    db.add(asset)
    db.add(AuditEvent(user_id=user.id, event_type="media.uploaded", metadata_json=json.dumps({"asset_id": asset.id, "size_bytes": size})))
    db.commit()
    db.refresh(asset)
    return asset


def create_job(db: Session, user: User, payload: dict, header_idempotency_key: str | None = None) -> Job:
    idempotency_key = (header_idempotency_key or payload.get("idempotency_key") or hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest())[:128]
    existing = db.scalar(select(Job).where(Job.user_id == user.id, Job.idempotency_key == idempotency_key))
    if existing:
        return existing
    active_jobs = db.scalar(select(func.count(Job.id)).where(Job.user_id == user.id, Job.state.in_(["queued", "provisioning", "downloading", "separating_audio", "transcribing", "translating", "synthesizing", "mixing", "lip_syncing", "uploading"]))) or 0
    if active_jobs >= min(settings.max_jobs_per_user, plan(user.plan_key).max_concurrent_jobs):
        raise HTTPException(status_code=429, detail="Concurrent job limit reached")
    asset = asset_for_user(db, user, payload["media_asset_id"])
    operation = payload.get("operation", "dubbing")
    amount = estimate_for_duration(asset.duration_seconds or 0, operation, lip_sync=bool(payload.get("lip_sync")), quality=payload.get("quality", "balanced"))
    if balance(db, user.id) < amount:
        raise HTTPException(status_code=402, detail={"message": "Insufficient credits", "required": amount, "available": balance(db, user.id)})
    job = Job(user_id=user.id, media_asset_id=asset.id, operation=operation, state=JobState.QUEUED.value, idempotency_key=idempotency_key, options_json=json.dumps(payload), estimate_credits=amount, reserved_credits=amount)
    db.add(job)
    db.flush()
    reserve(db, user, job, amount)
    add_job_event(db, job, JobState.QUEUED.value, "Job accepted and credits reserved", {"estimated_credits": amount})
    db.commit()
    db.refresh(job)
    try:
        job_queue().send(JobMessage(job_id=job.id, operation=operation))
    except Exception as exc:
        release(db, job, "queue_unavailable")
        job.state = JobState.FAILED.value
        job.error_code = "QUEUE_UNAVAILABLE"
        job.error_message = "The job queue could not accept this job"
        add_job_event(db, job, JobState.FAILED.value, job.error_message)
        db.commit()
        raise HTTPException(status_code=503, detail="Job queue unavailable") from exc
    return job


def create_voice_profile(db: Session, user: User, name: str, declaration: str, authorized: bool, upload: UploadFile) -> VoiceProfile:
    if not authorized:
        raise HTTPException(status_code=422, detail="Explicit voice authorization is required")
    profile_count = db.scalar(select(func.count(VoiceProfile.id)).where(VoiceProfile.user_id == user.id, VoiceProfile.deleted_at.is_(None))) or 0
    if profile_count >= plan(user.plan_key).max_voice_profiles:
        raise HTTPException(status_code=403, detail="Voice profile limit reached")
    consent = VoiceConsent(user_id=user.id, declaration=declaration, authorized=True)
    db.add(consent)
    db.flush()
    filename = upload.filename or "voice-reference.wav"
    content_type = upload.content_type or "audio/wav"
    key = object_key(user.id, "voices", filename)
    store = object_store()
    try:
        size = store.put(key, upload.file, content_type=content_type)
        validate_upload(filename, content_type, size)
    except (ValueError, RuntimeError, OSError) as exc:
        store.delete(key)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    profile = VoiceProfile(user_id=user.id, name=name, reference_object_key=key, consent_id=consent.id)
    db.add(profile)
    db.add(AuditEvent(user_id=user.id, event_type="voice.consent.created", metadata_json=json.dumps({"consent_id": consent.id, "profile_name": name})))
    db.commit()
    db.refresh(profile)
    return profile


def serialize_asset(asset: MediaAsset) -> dict:
    return {"id": asset.id, "filename": asset.original_filename, "mime_type": asset.mime_type, "size_bytes": asset.size_bytes, "duration_seconds": asset.duration_seconds, "width": asset.width, "height": asset.height, "fps": asset.fps, "media_kind": asset.media_kind, "created_at": asset.created_at.isoformat()}


def serialize_job(job: Job) -> dict:
    return {"id": job.id, "operation": job.operation, "state": job.state, "estimate_credits": job.estimate_credits, "reserved_credits": job.reserved_credits, "actual_credits": job.actual_credits, "output_object_key": job.output_object_key, "error_code": job.error_code, "error_message": job.error_message, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(), "completed_at": job.completed_at.isoformat() if job.completed_at else None}
