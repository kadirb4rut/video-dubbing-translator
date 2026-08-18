from __future__ import annotations

import json
import hashlib
import secrets
from datetime import timedelta

from fastapi import Depends, File, Form, Header, HTTPException, Request, FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import PLANS, plan
from .config import settings
from .db import create_tables, get_db
from .domain import JobState
from .ledger import adjust, balance, finalize, grant, ledger_rows, reserve
from .models import AbuseEvent, AuditEvent, GpuCostProfile, Job, JobArtifact, JobEvent, MediaAsset, ModelVersion, PasswordResetToken, Project, SessionToken, UsageRecord, User, VoiceConsent, VoiceProfile, now
from .rate_limit import rate_limited
from .providers import provider_registry
from .schemas import AccountUpdateRequest, AbuseReportRequest, EstimateRequest, GpuProfileRequest, JobCreateRequest, LoginRequest, MediaPresignRequest, ModelVersionRequest, PasswordResetConfirmRequest, PasswordResetRequest, ProjectRequest, SignupRequest, VoiceSynthesisRequest
from .security import current_user, hash_password, new_session, require_admin, revoke_session, verify_password
from .services import asset_for_user, complete_presigned_asset, create_job, create_voice_profile, estimate_for_duration, presign_asset, serialize_artifact, serialize_asset, serialize_job, upload_asset
from .storage import object_store


app = FastAPI(title="LingoWave API", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def startup() -> None:
    if settings.database_url.startswith("sqlite"):
        create_tables()


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "plan": plan(user.plan_key).key}


def set_session(response, token: str):
    response.set_cookie(settings.session_cookie_name, token, max_age=settings.session_ttl_days * 86400, httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "api", "database": "configured", "storage": settings.storage_backend, "queue": "sqs" if settings.sqs_queue_url else "local"}


@app.get("/v1/providers")
def providers() -> dict[str, str]:
    return provider_registry()


@app.get("/v1/job-states")
def job_states() -> list[str]:
    return [state.value for state in JobState]


@app.get("/api/plans")
def plans() -> list[dict]:
    return [p.__dict__ for p in PLANS.values()]


@app.post("/api/projects")
def project_create(payload: ProjectRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    project = Project(user_id=user.id, name=payload.name.strip())
    db.add(project)
    db.add(AuditEvent(user_id=user.id, event_type="project.created", metadata_json=json.dumps({"project_name": project.name})))
    db.commit()
    db.refresh(project)
    return {"id": project.id, "name": project.name, "created_at": project.created_at.isoformat(), "updated_at": project.updated_at.isoformat()}


@app.get("/api/projects")
def projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Project).where(Project.user_id == user.id).order_by(Project.updated_at.desc())).all()
    return [{"id": row.id, "name": row.name, "created_at": row.created_at.isoformat(), "updated_at": row.updated_at.isoformat()} for row in rows]


@app.post("/api/auth/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db), _limit: None = Depends(rate_limited("signup", 10))):
    email = payload.email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(email=email, password_hash=hash_password(payload.password), display_name=payload.display_name.strip())
    db.add(user)
    db.flush()
    grant(db, user.id, plan(user.plan_key).monthly_credits, reference_key=f"signup:{user.id}", metadata={"plan": user.plan_key})
    db.commit()
    db.refresh(user)
    return set_session(JSONResponse({"user": user_payload(user), "credits": balance(db, user.id)}), new_session(db, user))


@app.post("/api/auth/login")
def login(payload: LoginRequest, db: Session = Depends(get_db), _limit: None = Depends(rate_limited("login", 10))):
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower()))
    if not user or not user.is_active or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    return set_session(JSONResponse({"user": user_payload(user), "credits": balance(db, user.id)}), new_session(db, user))


@app.get("/api/auth/me")
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    return {"user": user_payload(user), "credits": balance(db, user.id)}


@app.post("/api/auth/logout")
def logout(request: Request, db: Session = Depends(get_db)):
    revoke_session(db, request.cookies.get(settings.session_cookie_name) or request.headers.get("x-session-token"))
    response = JSONResponse({"deleted": True})
    response.delete_cookie(settings.session_cookie_name)
    return response


@app.post("/api/auth/password-reset/request")
def password_reset_request(payload: PasswordResetRequest, db: Session = Depends(get_db), _limit: None = Depends(rate_limited("password-reset", 5))):
    user = db.scalar(select(User).where(User.email == payload.email.strip().lower(), User.is_active.is_(True), User.deleted_at.is_(None)))
    response = {"accepted": True}
    if user:
        raw = secrets.token_urlsafe(32)
        db.add(PasswordResetToken(user_id=user.id, token_hash=hashlib.sha256(raw.encode()).hexdigest(), expires_at=now() + timedelta(minutes=30)))
        db.add(AuditEvent(user_id=user.id, event_type="auth.password_reset.requested", metadata_json="{}"))
        db.commit()
        if settings.dev_mail_sink and settings.database_url.startswith("sqlite"):
            response["dev_token"] = raw
    return response


@app.post("/api/auth/password-reset/confirm")
def password_reset_confirm(payload: PasswordResetConfirmRequest, db: Session = Depends(get_db), _limit: None = Depends(rate_limited("password-reset-confirm", 10))):
    token = db.scalar(select(PasswordResetToken).where(PasswordResetToken.token_hash == hashlib.sha256(payload.token.encode()).hexdigest(), PasswordResetToken.used_at.is_(None)))
    if not token or token.expires_at.replace(tzinfo=token.expires_at.tzinfo or now().tzinfo) <= now():
        raise HTTPException(status_code=400, detail="Invalid or expired reset token")
    user = db.get(User, token.user_id)
    if not user or not user.is_active:
        raise HTTPException(status_code=400, detail="Account unavailable")
    user.password_hash = hash_password(payload.password)
    token.used_at = now()
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id == user.id, SessionToken.revoked_at.is_(None))).all():
        session.revoked_at = now()
    db.add(AuditEvent(user_id=user.id, event_type="auth.password_reset.completed", metadata_json="{}"))
    db.commit()
    return {"reset": True}


@app.get("/api/account")
def account(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return {"user": user_payload(user), "credits": balance(db, user.id), "created_at": user.created_at.isoformat()}


@app.patch("/api/account")
def account_update(payload: AccountUpdateRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    user.display_name = payload.display_name.strip()
    db.add(AuditEvent(user_id=user.id, event_type="account.updated", metadata_json=json.dumps({"fields": ["display_name"]})))
    db.commit()
    return user_payload(user)


@app.delete("/api/account")
def account_delete(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    store = object_store()
    for asset in db.scalars(select(MediaAsset).where(MediaAsset.user_id == user.id, MediaAsset.deleted_at.is_(None))).all():
        store.delete(asset.object_key)
        asset.deleted_at = now()
    for profile in db.scalars(select(VoiceProfile).where(VoiceProfile.user_id == user.id, VoiceProfile.deleted_at.is_(None))).all():
        store.delete(profile.reference_object_key)
        profile.deleted_at = now()
        profile.status = "revoked"
        consent = db.get(VoiceConsent, profile.consent_id)
        if consent:
            consent.revoked_at = now()
    for artifact in db.scalars(select(JobArtifact).join(Job, Job.id == JobArtifact.job_id).where(Job.user_id == user.id)).all():
        store.delete(artifact.object_key)
        db.delete(artifact)
    user.is_active = False
    user.deleted_at = now()
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id == user.id, SessionToken.revoked_at.is_(None))).all():
        session.revoked_at = now()
    db.add(AuditEvent(user_id=user.id, event_type="account.deleted", metadata_json="{}"))
    db.commit()
    response = JSONResponse({"deleted": True})
    response.delete_cookie(settings.session_cookie_name)
    return response


@app.post("/api/abuse/report")
def abuse_report(payload: AbuseReportRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    event = AbuseEvent(user_id=user.id, event_type=payload.event_type, target_type=payload.target_type, target_id=payload.target_id, description=payload.description)
    db.add(event)
    db.flush()
    db.add(AuditEvent(user_id=user.id, event_type="abuse.reported", metadata_json=json.dumps({"abuse_event_id": event.id})))
    db.commit()
    db.refresh(event)
    return {"id": event.id, "status": event.status}


@app.get("/api/credits")
def credits(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    rows = ledger_rows(db, user.id)
    return {"balance": balance(db, user.id), "plan": plan(user.plan_key).__dict__, "ledger": [{"type": row.entry_type, "credits": row.credits, "reference": row.reference_key, "created_at": row.created_at.isoformat()} for row in rows]}


@app.post("/api/admin/credits/grant")
def admin_grant(user_id: str, credits: int, reference_key: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    entry = grant(db, user_id, credits, reference_key=reference_key, entry_type="operator_grant", metadata={"operator_id": admin.id})
    db.commit()
    return {"id": entry.id, "credits": entry.credits, "balance": balance(db, user_id)}


@app.post("/api/admin/credits/revoke")
def admin_revoke(user_id: str, credits: int, reference_key: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    if credits <= 0:
        raise HTTPException(status_code=422, detail="credits must be positive")
    if not db.get(User, user_id):
        raise HTTPException(status_code=404, detail="User not found")
    if balance(db, user_id) < credits:
        raise HTTPException(status_code=409, detail="Cannot revoke more credits than the account balance")
    entry = adjust(db, user_id, -credits, reference_key=reference_key, entry_type="operator_revoke", metadata={"operator_id": admin.id})
    db.commit()
    return {"id": entry.id, "credits": entry.credits, "balance": balance(db, user_id)}


@app.get("/api/admin/metrics")
def admin_metrics(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Job.state, func.count(Job.id)).group_by(Job.state)).all()
    usage_count = db.scalar(select(func.count(UsageRecord.id))) or 0
    processed_seconds = db.scalar(select(func.coalesce(func.sum(UsageRecord.input_duration_seconds), 0))) or 0
    credits_used = db.scalar(select(func.coalesce(func.sum(Job.actual_credits), 0)).where(Job.state == JobState.COMPLETED.value)) or 0
    measured_cost = db.scalar(select(func.coalesce(func.sum(UsageRecord.actual_cost_usd), 0))) or 0
    return {"job_counts": {state: count for state, count in rows}, "usage_records": usage_count, "processed_minutes": float(processed_seconds) / 60, "credits_used": int(credits_used), "measured_compute_cost_usd": float(measured_cost), "measured_cost_available": bool(measured_cost), "operator_id": admin.id}


@app.post("/api/admin/gpu-profiles")
def gpu_profile_create(payload: GpuProfileRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    profile = GpuCostProfile(**payload.model_dump(exclude={"metadata"}), metadata_json=json.dumps(payload.metadata))
    db.add(profile)
    db.add(AuditEvent(user_id=admin.id, event_type="gpu_profile.created", metadata_json=json.dumps({"gpu_type": payload.gpu_type, "measured": payload.measured})))
    db.commit()
    db.refresh(profile)
    return {"id": profile.id, "provider": profile.provider, "gpu_type": profile.gpu_type, "region": profile.region, "measured": profile.measured}


@app.get("/api/admin/gpu-profiles")
def gpu_profiles(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(GpuCostProfile).order_by(GpuCostProfile.created_at.desc())).all()
    return [{"id": row.id, "provider": row.provider, "gpu_type": row.gpu_type, "region": row.region, "pricing_mode": row.pricing_mode, "hourly_price_usd": row.hourly_price_usd, "processed_minutes_per_hour": row.processed_minutes_per_hour, "measured": row.measured} for row in rows]


@app.post("/api/admin/model-versions")
def model_version_create(payload: ModelVersionRequest, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    version = ModelVersion(**payload.model_dump(exclude={"metadata"}), metadata_json=json.dumps(payload.metadata))
    db.add(version)
    db.add(AuditEvent(user_id=admin.id, event_type="model_version.created", metadata_json=json.dumps({"provider": payload.provider, "version": payload.version})))
    db.commit()
    db.refresh(version)
    return {"id": version.id, "provider": version.provider, "model_name": version.model_name, "version": version.version, "enabled": version.enabled}


@app.get("/api/admin/model-versions")
def model_versions(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc())).all()
    return [{"id": row.id, "provider": row.provider, "model_name": row.model_name, "version": row.version, "artifact_license": row.artifact_license, "enabled": row.enabled} for row in rows]


@app.get("/api/admin/jobs")
def admin_jobs(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()
    return [serialize_job(job) | {"user_id": job.user_id} for job in jobs]


@app.get("/api/admin/audit")
def admin_audit(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200)).all()
    return [{"id": event.id, "user_id": event.user_id, "event_type": event.event_type, "metadata": json.loads(event.metadata_json), "created_at": event.created_at.isoformat()} for event in events]


@app.get("/api/admin/abuse")
def admin_abuse(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    events = db.scalars(select(AbuseEvent).order_by(AbuseEvent.created_at.desc()).limit(200)).all()
    return [{"id": event.id, "user_id": event.user_id, "event_type": event.event_type, "target_type": event.target_type, "target_id": event.target_id, "description": event.description, "status": event.status, "created_at": event.created_at.isoformat()} for event in events]


@app.post("/api/admin/abuse/{event_id}/resolve")
def admin_resolve_abuse(event_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    event = db.get(AbuseEvent, event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Abuse event not found")
    event.status = "resolved"
    event.resolved_at = now()
    db.add(AuditEvent(user_id=admin.id, event_type="abuse.resolved", metadata_json=json.dumps({"abuse_event_id": event.id})))
    db.commit()
    return {"id": event.id, "status": event.status}


@app.post("/api/admin/users/{user_id}/disable")
def admin_disable_user(user_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    target.is_active = False
    for session in db.scalars(select(SessionToken).where(SessionToken.user_id == target.id, SessionToken.revoked_at.is_(None))).all():
        session.revoked_at = now()
    db.add(AuditEvent(user_id=admin.id, event_type="account.disabled", metadata_json=json.dumps({"target_user_id": target.id})))
    db.commit()
    return {"id": target.id, "is_active": target.is_active}


@app.post("/api/media/upload")
def media_upload(upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize_asset(upload_asset(db, user, upload))


@app.post("/api/media/presign")
def media_presign(payload: MediaPresignRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset, url = presign_asset(db, user, payload.filename, payload.content_type, payload.size_bytes)
    return {"asset": serialize_asset(asset), "upload_url": url, "method": "PUT", "headers": {"Content-Type": payload.content_type, "x-amz-server-side-encryption": "AES256"}}


@app.post("/api/media/{asset_id}/complete")
def media_complete(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize_asset(complete_presigned_asset(db, user, asset_id))


@app.get("/api/media/{asset_id}")
def media_detail(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize_asset(asset_for_user(db, user, asset_id))


@app.get("/api/media/{asset_id}/download")
def media_download(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = asset_for_user(db, user, asset_id)
    store = object_store()
    presigned = getattr(store, "presigned_get", None)
    if presigned:
        return {"url": presigned(asset.object_key)}
    return FileResponse(store.path(asset.object_key), media_type=asset.mime_type, filename=asset.original_filename)


@app.post("/api/jobs/estimate")
def job_estimate(payload: EstimateRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    asset = asset_for_user(db, user, payload.media_asset_id) if payload.media_asset_id else None
    if not asset and payload.operation != "tts":
        raise HTTPException(status_code=422, detail="media_asset_id is required for this operation")
    if payload.operation == "dubbing" and asset and asset.media_kind != "video":
        raise HTTPException(status_code=422, detail="Dubbing requires a video asset")
    duration_seconds = asset.duration_seconds if asset else max(1.0, len(payload.text or "") / 12)
    amount = estimate_for_duration(duration_seconds or 0, payload.operation, lip_sync=payload.lip_sync, quality=payload.quality)
    return {"media_asset_id": asset.id if asset else None, "duration_seconds": duration_seconds, "operation": payload.operation, "credits": amount, "currency": "internal_credits", "profile_source": "config/cost_profiles.json"}


@app.post("/api/jobs")
def jobs_create(payload: JobCreateRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize_job(create_job(db, user, payload.model_dump(), idempotency_key))


@app.get("/api/jobs")
def jobs_list(user: User = Depends(current_user), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).where(Job.user_id == user.id).order_by(Job.created_at.desc()).limit(50)).all()
    return [serialize_job(job) for job in jobs]


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    payload = serialize_job(job)
    payload["events"] = [{"state": event.state, "message": event.message, "metadata": json.loads(event.metadata_json), "created_at": event.created_at.isoformat()} for event in db.scalars(select(JobEvent).where(JobEvent.job_id == job.id).order_by(JobEvent.created_at.asc())).all()]
    payload["artifacts"] = [serialize_artifact(artifact) for artifact in db.scalars(select(JobArtifact).where(JobArtifact.job_id == job.id).order_by(JobArtifact.created_at.asc())).all()]
    return payload


@app.get("/api/jobs/{job_id}/artifacts")
def job_artifacts(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return [serialize_artifact(artifact) for artifact in db.scalars(select(JobArtifact).where(JobArtifact.job_id == job.id).order_by(JobArtifact.created_at.asc())).all()]


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}/download")
def job_artifact_download(job_id: str, artifact_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    artifact = db.scalar(select(JobArtifact).join(Job, Job.id == JobArtifact.job_id).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id, Job.user_id == user.id))
    if not artifact:
        raise HTTPException(status_code=404, detail="Artifact not found")
    store = object_store()
    presigned = getattr(store, "presigned_get", None)
    if presigned:
        return RedirectResponse(presigned(artifact.object_key), status_code=307)
    return FileResponse(store.path(artifact.object_key), media_type=artifact.content_type, filename=artifact.filename)


@app.get("/api/jobs/{job_id}/artifacts/{artifact_id}/preview")
def job_artifact_preview(job_id: str, artifact_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    artifact = db.scalar(select(JobArtifact).join(Job, Job.id == JobArtifact.job_id).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id, Job.user_id == user.id))
    if not artifact or not (artifact.content_type.startswith("text/") or "subrip" in artifact.content_type):
        raise HTTPException(status_code=404, detail="Text artifact not found")
    import tempfile
    from pathlib import Path
    store = object_store()
    with tempfile.NamedTemporaryFile(suffix=Path(artifact.filename).suffix, delete=False) as temp:
        temp_path = Path(temp.name)
    try:
        store.download(artifact.object_key, temp_path)
        text = temp_path.read_text(encoding="utf-8")
    finally:
        temp_path.unlink(missing_ok=True)
    return {"filename": artifact.filename, "text": text}


@app.post("/api/admin/jobs/{job_id}/retry")
def admin_retry_job(job_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state not in {JobState.FAILED.value, JobState.CANCELLED.value}:
        raise HTTPException(status_code=409, detail="Only failed or cancelled jobs can be retried")
    target_user = db.get(User, job.user_id)
    if not target_user or not target_user.is_active:
        raise HTTPException(status_code=409, detail="Job owner is unavailable")
    job.state = JobState.QUEUED.value
    job.error_code = None
    job.error_message = None
    job.retry_count = 0
    job.actual_credits = None
    reserve(db, target_user, job, job.estimate_credits)
    db.add(JobEvent(job_id=job.id, state=job.state, message="Retry requested by operator", metadata_json=json.dumps({"operator_id": admin.id})))
    db.commit()
    from .queueing import JobMessage, job_queue
    job_queue().send(JobMessage(job.id, job.operation))
    return serialize_job(job)


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    job = db.scalar(select(Job).where(Job.id == job_id, Job.user_id == user.id))
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state not in {JobState.QUEUED.value, JobState.PROVISIONING.value, JobState.DOWNLOADING.value}:
        raise HTTPException(status_code=409, detail="Job can no longer be cancelled")
    job.state = JobState.CANCELLED.value
    job.error_code = "USER_CANCELLED"
    job.error_message = "Cancelled by the user"
    # User cancellation is not an infrastructure failure; the reserved amount
    # remains accounted for rather than being silently refunded.
    finalize(db, job, job.reserved_credits)
    db.add(JobEvent(job_id=job.id, state=JobState.CANCELLED.value, message=job.error_message, metadata_json="{}"))
    db.commit()
    return serialize_job(job)


@app.post("/api/voices")
def voice_create(name: str = Form(...), declaration: str = Form(...), authorized: bool = Form(...), upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = create_voice_profile(db, user, name.strip(), declaration.strip(), authorized, upload)
    return {"id": profile.id, "name": profile.name, "status": profile.status, "consent_id": profile.consent_id, "created_at": profile.created_at.isoformat()}


@app.get("/api/voices")
def voices(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(VoiceProfile).where(VoiceProfile.user_id == user.id, VoiceProfile.deleted_at.is_(None)).order_by(VoiceProfile.created_at.desc())).all()
    return [{"id": row.id, "name": row.name, "status": row.status, "consent_id": row.consent_id, "created_at": row.created_at.isoformat()} for row in rows]


@app.post("/api/voices/{voice_id}/synthesize")
def voice_synthesize(voice_id: str, payload: VoiceSynthesisRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("voice-synthesis", 10))):
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == user.id, VoiceProfile.status == "active", VoiceProfile.deleted_at.is_(None)))
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    job = create_job(db, user, {"operation": "tts", "text": payload.text, "target_language": payload.language, "voice_profile_id": voice_id}, idempotency_key)
    return serialize_job(job)


@app.delete("/api/voices/{voice_id}")
def voice_revoke(voice_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.user_id == user.id, VoiceProfile.deleted_at.is_(None)))
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    profile.status = "revoked"
    profile.deleted_at = now()
    consent = db.get(VoiceConsent, profile.consent_id)
    if consent:
        consent.revoked_at = now()
    object_store().delete(profile.reference_object_key)
    db.add(AuditEvent(user_id=user.id, event_type="voice.consent.revoked", metadata_json=json.dumps({"voice_profile_id": profile.id, "consent_id": profile.consent_id})))
    db.commit()
    return {"id": profile.id, "status": profile.status, "deleted": True}
