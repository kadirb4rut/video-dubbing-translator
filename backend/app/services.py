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
from .ledger import balance, release, reserve
from .media import inspect_media, validate_upload
from .models import (
    AuditEvent,
    Job,
    JobArtifact,
    JobEvent,
    MediaAsset,
    Project,
    User,
    VoiceConsent,
    VoiceProfile,
    now,
)
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
    if lip_sync and operation == "dubbing":
        raise HTTPException(status_code=503, detail="Lip sync is not enabled in this deployment")
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
    asset = db.scalar(select(MediaAsset).where(MediaAsset.id == asset_id, MediaAsset.user_id == user.id, MediaAsset.deleted_at.is_(None), MediaAsset.status == "ready"))
    if not asset:
        raise HTTPException(status_code=404, detail="Media asset not found")
    return asset


def pending_asset_for_user(db: Session, user: User, asset_id: str) -> MediaAsset:
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
        size = store.put(key, upload.file, content_type=content_type, max_bytes=settings.max_upload_bytes)
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


def presign_asset(db: Session, user: User, filename: str, content_type: str, size_bytes: int) -> tuple[MediaAsset, str]:
    validate_upload(filename, content_type, size_bytes)
    store = object_store()
    presigned_put = getattr(store, "presigned_put", None)
    if not presigned_put:
        raise HTTPException(status_code=409, detail="Direct browser uploads require S3 storage")
    key = object_key(user.id, "media", filename)
    asset = MediaAsset(user_id=user.id, object_key=key, original_filename=filename, mime_type=content_type, size_bytes=size_bytes, media_kind="unknown", status="pending")
    db.add(asset)
    db.commit()
    db.refresh(asset)
    try:
        url = presigned_put(key, content_type=content_type, size_bytes=size_bytes)
    except Exception as exc:
        db.delete(asset)
        db.commit()
        raise HTTPException(status_code=503, detail="Storage could not create an upload URL") from exc
    return asset, url


def complete_presigned_asset(db: Session, user: User, asset_id: str) -> MediaAsset:
    asset = pending_asset_for_user(db, user, asset_id)
    if asset.status != "pending":
        return asset
    store = object_store()
    try:
        head = getattr(store, "head", None)
        if head:
            remote = head(asset.object_key)
            if int(remote.get("ContentLength", -1)) != asset.size_bytes:
                raise ValueError("Uploaded object size does not match the presigned request")
        with tempfile.NamedTemporaryFile(suffix=Path(asset.original_filename).suffix, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            store.download(asset.object_key, temp_path)
            if not head and temp_path.stat().st_size != asset.size_bytes:
                raise ValueError("Uploaded object size does not match the presigned request")
            metadata = inspect_media(temp_path)
        finally:
            temp_path.unlink(missing_ok=True)
    except (ValueError, RuntimeError, OSError) as exc:
        store.delete(asset.object_key)
        asset.status = "rejected"
        db.commit()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    asset.duration_seconds = metadata["duration_seconds"]
    asset.width = metadata["width"]
    asset.height = metadata["height"]
    asset.fps = metadata["fps"]
    asset.media_kind = metadata["media_kind"]
    asset.status = "ready"
    db.add(AuditEvent(user_id=user.id, event_type="media.upload.completed", metadata_json=json.dumps({"asset_id": asset.id})))
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
    operation = payload.get("operation", "dubbing")
    if operation in {"dubbing", "subtitle_translation"} and not payload.get("target_language"):
        raise HTTPException(status_code=422, detail="target_language is required for this operation")
    if payload.get("lip_sync") and operation == "dubbing":
        raise HTTPException(status_code=503, detail="Lip sync is not enabled in this deployment")
    if operation == "dubbing" and payload.get("preserve_voice", True) and not payload.get("voice_profile_id"):
        raise HTTPException(status_code=422, detail="A consented voice profile is required when preserve_voice is enabled")
    if operation == "tts" and not payload.get("voice_profile_id"):
        raise HTTPException(status_code=422, detail="A consented voice profile is required for voice synthesis")
    asset = None
    if payload.get("media_asset_id"):
        asset = asset_for_user(db, user, payload["media_asset_id"])
    elif operation != "tts":
        raise HTTPException(status_code=422, detail="media_asset_id is required for this operation")
    duration_seconds = asset.duration_seconds if asset else max(1.0, len(payload.get("text") or "") / 12)
    if operation == "dubbing" and asset.media_kind != "video":
        raise HTTPException(status_code=422, detail="Dubbing requires a video asset")
    project_id = payload.get("project_id")
    if project_id and not db.scalar(select(Project).where(Project.id == project_id, Project.user_id == user.id)):
        raise HTTPException(status_code=404, detail="Project not found")
    amount = estimate_for_duration(duration_seconds or 0, operation, lip_sync=bool(payload.get("lip_sync")), quality=payload.get("quality", "balanced"))
    if balance(db, user.id) < amount:
        raise HTTPException(status_code=402, detail={"message": "Insufficient credits", "required": amount, "available": balance(db, user.id)})
    job = Job(user_id=user.id, project_id=project_id, media_asset_id=asset.id if asset else None, operation=operation, state=JobState.QUEUED.value, idempotency_key=idempotency_key, options_json=json.dumps(payload), estimate_credits=amount, reserved_credits=amount)
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
        size = store.put(key, upload.file, content_type=content_type, max_bytes=settings.max_upload_bytes)
        validate_upload(filename, content_type, size)
        with tempfile.NamedTemporaryFile(suffix=Path(filename).suffix, delete=False) as temp:
            temp_path = Path(temp.name)
        try:
            store.download(key, temp_path)
            metadata = inspect_media(temp_path)
            duration = metadata.get("duration_seconds") or 0
            if metadata.get("media_kind") != "audio" or duration < settings.min_voice_seconds or duration > settings.max_voice_seconds:
                raise ValueError(f"Reference voice must be audio between {settings.min_voice_seconds} and {settings.max_voice_seconds} seconds")
        finally:
            temp_path.unlink(missing_ok=True)
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
    return {"id": asset.id, "filename": asset.original_filename, "mime_type": asset.mime_type, "size_bytes": asset.size_bytes, "duration_seconds": asset.duration_seconds, "width": asset.width, "height": asset.height, "fps": asset.fps, "media_kind": asset.media_kind, "status": asset.status, "created_at": asset.created_at.isoformat()}


def serialize_job(job: Job, *, include_internal_error: bool = False) -> dict:
    public_error = {
        "PROVIDER_FAILURE": "A configured media provider could not complete this job. Please retry or contact support.",
        "WORKER_FAILURE": "The media worker could not complete this job. Please retry.",
        "WORKER_LEASE_EXHAUSTED": "The media worker stopped responding. Please retry.",
        "QUEUE_UNAVAILABLE": "The job queue is temporarily unavailable. Please retry shortly.",
    }.get(job.error_code, job.error_message)
    return {"id": job.id, "project_id": job.project_id, "operation": job.operation, "state": job.state, "estimate_credits": job.estimate_credits, "reserved_credits": job.reserved_credits, "actual_credits": job.actual_credits, "output_object_key": job.output_object_key, "error_code": job.error_code, "error_message": job.error_message if include_internal_error else public_error, "created_at": job.created_at.isoformat(), "updated_at": job.updated_at.isoformat(), "completed_at": job.completed_at.isoformat() if job.completed_at else None}


def serialize_artifact(artifact: JobArtifact) -> dict:
    return {"id": artifact.id, "name": artifact.artifact_name, "filename": artifact.filename, "content_type": artifact.content_type, "size_bytes": artifact.size_bytes, "created_at": artifact.created_at.isoformat()}
