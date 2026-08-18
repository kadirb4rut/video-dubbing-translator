from __future__ import annotations

import json

from fastapi import Depends, File, Form, Header, HTTPException, Request, FastAPI, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .billing import PLANS, plan
from .config import settings
from .db import create_tables, get_db
from .domain import JobState
from .ledger import balance, finalize, grant, ledger_rows
from .models import AuditEvent, Job, JobEvent, UsageRecord, User, VoiceConsent, VoiceProfile, now
from .providers import provider_registry
from .schemas import EstimateRequest, JobCreateRequest, LoginRequest, SignupRequest
from .security import current_user, hash_password, new_session, require_admin, revoke_session, verify_password
from .services import asset_for_user, create_job, create_voice_profile, estimate_for_duration, serialize_asset, serialize_job, upload_asset
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


@app.post("/api/auth/signup")
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
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
def login(payload: LoginRequest, db: Session = Depends(get_db)):
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
    response = RedirectResponse(url="/", status_code=303)
    response.delete_cookie(settings.session_cookie_name)
    return response


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


@app.get("/api/admin/metrics")
def admin_metrics(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.execute(select(Job.state, func.count(Job.id)).group_by(Job.state)).all()
    usage_count = db.scalar(select(func.count(UsageRecord.id))) or 0
    return {"job_counts": {state: count for state, count in rows}, "usage_records": usage_count, "operator_id": admin.id}


@app.get("/api/admin/jobs")
def admin_jobs(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    jobs = db.scalars(select(Job).order_by(Job.created_at.desc()).limit(100)).all()
    return [serialize_job(job) | {"user_id": job.user_id} for job in jobs]


@app.get("/api/admin/audit")
def admin_audit(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(200)).all()
    return [{"id": event.id, "user_id": event.user_id, "event_type": event.event_type, "metadata": json.loads(event.metadata_json), "created_at": event.created_at.isoformat()} for event in events]


@app.post("/api/media/upload")
def media_upload(upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    return serialize_asset(upload_asset(db, user, upload))


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
    asset = asset_for_user(db, user, payload.media_asset_id)
    amount = estimate_for_duration(asset.duration_seconds or 0, payload.operation, lip_sync=payload.lip_sync, quality=payload.quality)
    return {"media_asset_id": asset.id, "duration_seconds": asset.duration_seconds, "operation": payload.operation, "credits": amount, "currency": "internal_credits", "profile_source": "config/cost_profiles.json"}


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
    return payload


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
