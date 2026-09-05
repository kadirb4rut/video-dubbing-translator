from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from contextlib import asynccontextmanager
from datetime import timedelta, timezone
from io import BytesIO
from urllib.parse import urlencode

from fastapi import (
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Request,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .billing import (
    PLANS,
    BillingNotConfigured,
    StripeBillingProvider,
    billing_provider,
    plan,
    process_stripe_event,
)
from .config import settings
from .db import create_tables, get_db
from .domain import JobState
from .google_auth import (
    GoogleAuthError,
    GoogleIdentity,
    authorization_url,
    exchange_code,
    google_auth_configured,
)
from .ledger import adjust, balance, finalize, grant, ledger_rows, reserve
from .mail import mail_provider
from .models import (
    AbuseEvent,
    AuditEvent,
    AuthIdentity,
    CreditPurchase,
    GpuCostProfile,
    Job,
    JobArtifact,
    JobEvent,
    JobStageMetric,
    MediaAsset,
    ModelVersion,
    OAuthLoginState,
    PasswordResetToken,
    Project,
    SessionToken,
    Subscription,
    UsageRecord,
    User,
    VoiceConsent,
    VoiceProfile,
    WorkerLease,
    now,
)
from .providers import provider_registry
from .queueing import job_queue
from .rate_limit import rate_limited
from .schemas import (
    AbuseReportRequest,
    AccountUpdateRequest,
    ArtifactTextUpdateRequest,
    CheckoutRequest,
    EstimateRequest,
    GpuProfileRequest,
    JobCreateRequest,
    LoginRequest,
    MediaPresignRequest,
    ModelVersionRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    ProjectRequest,
    SignupRequest,
    VoiceSynthesisRequest,
)
from .security import (
    current_user,
    hash_password,
    new_session,
    require_admin,
    revoke_session,
    verify_password,
)
from .services import (
    asset_for_user,
    complete_presigned_asset,
    create_job,
    create_voice_profile,
    estimate_for_duration,
    presign_asset,
    serialize_artifact,
    serialize_asset,
    serialize_job,
    upload_asset,
)
from .storage import object_store

GOOGLE_OAUTH_STATE_COOKIE = "lingowave_google_oauth_state"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if settings.database_url.startswith("sqlite"):
        create_tables()
    yield


app = FastAPI(title="LingoWave API", version="0.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[settings.frontend_origin], allow_credentials=True, allow_methods=["*"], allow_headers=["*"])


def user_payload(user: User) -> dict:
    return {"id": user.id, "email": user.email, "display_name": user.display_name, "role": user.role, "plan": plan(user.plan_key).key}


def set_session(response, token: str):
    response.set_cookie(settings.session_cookie_name, token, max_age=settings.session_ttl_days * 86400, httponly=True, secure=settings.cookie_secure, samesite="lax")
    return response


def _oauth_value_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _oauth_failure(reason: str) -> RedirectResponse:
    location = f"{settings.frontend_origin.rstrip('/')}/?{urlencode({'auth_error': reason})}"
    response = RedirectResponse(url=location, status_code=303)
    response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/api/auth/google")
    return response


def _oauth_state_expired(row: OAuthLoginState) -> bool:
    expires_at = row.expires_at.replace(tzinfo=timezone.utc) if row.expires_at.tzinfo is None else row.expires_at
    return expires_at <= now()


def _user_for_google_identity(db: Session, identity: GoogleIdentity) -> User:
    subject = identity.subject.strip()
    email = identity.email.strip().lower()
    if not identity.email_verified or not subject or not email:
        raise GoogleAuthError("Google identity is not verified")

    linked = db.scalar(select(AuthIdentity).where(AuthIdentity.provider == "google", AuthIdentity.provider_subject == subject))
    if linked:
        user = db.get(User, linked.user_id)
        if not user or not user.is_active or user.deleted_at:
            raise GoogleAuthError("Linked LingoWave account is unavailable")
        return user

    user = db.scalar(select(User).where(User.email == email))
    if user:
        if not user.is_active or user.deleted_at:
            raise GoogleAuthError("LingoWave account is unavailable")
    else:
        user = User(email=email, password_hash=hash_password(secrets.token_urlsafe(48)), display_name=identity.display_name)
        db.add(user)
        db.flush()
        grant(db, user.id, plan(user.plan_key).monthly_credits, reference_key=f"signup:{user.id}", metadata={"plan": user.plan_key, "provider": "google"})

    db.add(AuthIdentity(provider="google", provider_subject=subject, provider_email=email, user_id=user.id))
    return user


@app.get("/api/auth/google/config")
def google_config() -> dict:
    return {"enabled": google_auth_configured(settings)}


@app.get("/api/auth/google/login")
@app.get("/api/auth/google/start", include_in_schema=False)
def google_start(db: Session = Depends(get_db)):
    if not google_auth_configured(settings):
        raise HTTPException(status_code=503, detail="Google authentication is not configured")
    state = secrets.token_urlsafe(32)
    nonce = secrets.token_urlsafe(32)
    db.add(
        OAuthLoginState(
            provider="google",
            state_hash=_oauth_value_hash(state),
            nonce_hash=_oauth_value_hash(nonce),
            redirect_uri=settings.google_redirect_uri,
            expires_at=now() + timedelta(seconds=settings.google_state_ttl_seconds),
        )
    )
    db.commit()
    response = RedirectResponse(authorization_url(settings, state=state, nonce=nonce), status_code=303)
    response.set_cookie(
        GOOGLE_OAUTH_STATE_COOKIE,
        state,
        max_age=settings.google_state_ttl_seconds,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/api/auth/google",
    )
    return response


@app.get("/api/auth/google/callback")
def google_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: Session = Depends(get_db),
):
    browser_state = request.cookies.get(GOOGLE_OAUTH_STATE_COOKIE)
    if error:
        if state:
            pending = db.scalar(select(OAuthLoginState).where(OAuthLoginState.provider == "google", OAuthLoginState.state_hash == _oauth_value_hash(state), OAuthLoginState.consumed_at.is_(None)).with_for_update())
            if pending and browser_state and hmac.compare_digest(_oauth_value_hash(browser_state), pending.state_hash):
                pending.consumed_at = now()
                db.commit()
        return _oauth_failure("google_cancelled" if error == "access_denied" else "google_provider_error")
    if not code or not state:
        return _oauth_failure("google_invalid_callback")

    pending = db.scalar(select(OAuthLoginState).where(OAuthLoginState.provider == "google", OAuthLoginState.state_hash == _oauth_value_hash(state), OAuthLoginState.consumed_at.is_(None)).with_for_update())
    if (
        not pending
        or not browser_state
        or not hmac.compare_digest(_oauth_value_hash(browser_state), pending.state_hash)
        or _oauth_state_expired(pending)
        or pending.redirect_uri != settings.google_redirect_uri
    ):
        return _oauth_failure("google_invalid_state")
    pending.consumed_at = now()
    db.commit()

    try:
        identity = exchange_code(settings, code=code)
        if not hmac.compare_digest(_oauth_value_hash(identity.nonce), pending.nonce_hash):
            raise GoogleAuthError("Google nonce validation failed")
        user = _user_for_google_identity(db, identity)
        db.commit()
        db.refresh(user)
    except GoogleAuthError:
        db.rollback()
        return _oauth_failure("google_identity_invalid")
    except IntegrityError:
        db.rollback()
        return _oauth_failure("google_identity_conflict")

    response = RedirectResponse(url=f"{settings.frontend_origin.rstrip('/')}/", status_code=303)
    response.delete_cookie(GOOGLE_OAUTH_STATE_COOKIE, path="/api/auth/google")
    return set_session(response, new_session(db, user))


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


@app.get("/api/billing")
def billing_summary(user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    subscriptions = db.scalars(select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.updated_at.desc())).all()
    purchases = db.scalars(select(CreditPurchase).where(CreditPurchase.user_id == user.id).order_by(CreditPurchase.created_at.desc()).limit(20)).all()
    return {
        "provider": "stripe" if settings.stripe_secret_key else "disabled",
        "checkout_enabled": bool(settings.stripe_secret_key),
        "plan": plan(user.plan_key).__dict__,
        "credits": balance(db, user.id),
        "subscriptions": [{"plan": row.plan_key, "status": row.status, "current_period_end": row.current_period_end.isoformat() if row.current_period_end else None, "cancel_at_period_end": row.cancel_at_period_end} for row in subscriptions],
        "purchases": [{"pack_key": row.pack_key, "credits": row.credits, "refunded_credits": row.refunded_credits, "status": row.status, "amount_minor": row.amount_minor, "currency": row.currency, "created_at": row.created_at.isoformat()} for row in purchases],
    }


@app.post("/api/billing/checkout")
def billing_checkout(payload: CheckoutRequest, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict:
    if payload.kind == "subscription" and payload.key not in {"creator", "pro", "studio"}:
        raise HTTPException(status_code=422, detail="Choose a paid subscription plan")
    if payload.kind == "credits" and payload.key not in {"starter", "growth", "scale"}:
        raise HTTPException(status_code=422, detail="Choose a valid credit pack")
    provider = billing_provider()
    if not isinstance(provider, StripeBillingProvider):
        raise HTTPException(status_code=503, detail="Billing is not configured")
    try:
        session = provider.create_checkout_session(db, user, kind=payload.kind, key=payload.key)
        db.commit()
        return {"session_id": getattr(session, "id", None) or session.get("id"), "url": getattr(session, "url", None) or session.get("url")}
    except BillingNotConfigured as exc:
        db.rollback()
        raise HTTPException(status_code=503, detail="Billing is not configured") from exc
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=502, detail="Stripe could not create the checkout session") from exc


@app.post("/api/billing/portal")
def billing_portal(user: User = Depends(current_user)) -> dict:
    provider = billing_provider()
    if not isinstance(provider, StripeBillingProvider):
        raise HTTPException(status_code=503, detail="Billing is not configured")
    try:
        session = provider.create_billing_portal_session(user)
        return {"url": getattr(session, "url", None) or session.get("url")}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Stripe could not create the billing portal session") from exc


@app.post("/api/billing/webhook")
async def billing_webhook(request: Request, stripe_signature: str | None = Header(default=None, alias="Stripe-Signature"), db: Session = Depends(get_db)) -> dict:
    if not stripe_signature:
        raise HTTPException(status_code=400, detail="Stripe-Signature header is required")
    provider = billing_provider()
    if not isinstance(provider, StripeBillingProvider):
        raise HTTPException(status_code=503, detail="Billing webhook is not configured")
    payload = await request.body()
    try:
        event = provider.verify_webhook(payload, stripe_signature)
        return process_stripe_event(db, event)
    except BillingNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Billing webhook is not configured") from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook") from exc


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
        try:
            mail_provider().send_password_reset(user.email, raw)
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail="Password reset mail is not configured") from exc
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


@app.get("/api/usage")
def usage_history(user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        select(UsageRecord, Job.operation, Job.state, Job.actual_credits)
        .join(Job, Job.id == UsageRecord.job_id)
        .where(UsageRecord.user_id == user.id)
        .order_by(UsageRecord.created_at.desc())
        .limit(100)
    ).all()
    return [
        {
            "job_id": record.job_id,
            "operation": operation,
            "state": state,
            "actual_credits": actual_credits,
            "input_duration_seconds": record.input_duration_seconds,
            "output_duration_seconds": record.output_duration_seconds,
            "input_bytes": record.input_bytes,
            "output_bytes": record.output_bytes,
            "wall_clock_seconds": record.wall_clock_seconds,
            "model_seconds": record.model_seconds,
            "worker_type": record.worker_type,
            "gpu_type": record.gpu_type,
            "model_version": record.model_version,
            "source_language": record.source_language,
            "target_language": record.target_language,
            "models": json.loads(record.models_json or "{}"),
            "queue_wait_seconds": record.queue_wait_seconds,
            "compute_startup_seconds": record.compute_startup_seconds,
            "model_load_seconds": record.model_load_seconds,
            "real_time_factor": record.real_time_factor,
            "peak_vram_mb": record.peak_vram_mb,
            "peak_ram_mb": record.peak_ram_mb,
            "cpu_utilization_percent": record.cpu_utilization_percent,
            "compute_cost_per_input_minute_usd": record.compute_cost_per_input_minute_usd,
            "estimated_cost_usd": record.estimated_cost_usd,
            "actual_cost_usd": record.actual_cost_usd,
            "retry_count": record.retry_count,
            "created_at": record.created_at.isoformat(),
        }
        for record, operation, state, actual_credits in rows
    ]


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
    operation_rows = db.execute(select(Job.operation, Job.retry_count)).all()
    operation_counts: dict[str, dict[str, int]] = {}
    for operation, retry_count in operation_rows:
        entry = operation_counts.setdefault(operation, {"jobs": 0, "retry_attempts": 0})
        entry["jobs"] += 1
        entry["retry_attempts"] += max(0, int(retry_count or 0) - 1)
    usage_count = db.scalar(select(func.count(UsageRecord.id))) or 0
    processed_seconds = db.scalar(select(func.coalesce(func.sum(UsageRecord.input_duration_seconds), 0))) or 0
    processed_by_kind = db.execute(
        select(MediaAsset.media_kind, func.coalesce(func.sum(UsageRecord.input_duration_seconds), 0))
        .join(Job, Job.media_asset_id == MediaAsset.id)
        .join(UsageRecord, UsageRecord.job_id == Job.id)
        .group_by(MediaAsset.media_kind)
    ).all()
    processed_minutes_by_kind = {kind: float(seconds or 0) / 60 for kind, seconds in processed_by_kind}
    credits_used = db.scalar(select(func.coalesce(func.sum(Job.actual_credits), 0)).where(Job.state == JobState.COMPLETED.value)) or 0
    measured_cost = db.scalar(select(func.coalesce(func.sum(UsageRecord.actual_cost_usd), 0))) or 0
    measured_cost_records = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.actual_cost_usd.is_not(None))) or 0
    estimated_cost = db.scalar(select(func.coalesce(func.sum(UsageRecord.estimated_cost_usd), 0))) or 0
    estimated_cost_records = db.scalar(select(func.count(UsageRecord.id)).where(UsageRecord.estimated_cost_usd.is_not(None))) or 0
    operation_cost_rows = db.execute(select(Job.operation, func.sum(UsageRecord.estimated_cost_usd), func.sum(UsageRecord.actual_cost_usd)).join(UsageRecord, UsageRecord.job_id == Job.id).group_by(Job.operation)).all()
    worker_cost_rows = db.execute(select(UsageRecord.worker_type, UsageRecord.gpu_type, func.sum(UsageRecord.estimated_cost_usd), func.sum(UsageRecord.actual_cost_usd)).group_by(UsageRecord.worker_type, UsageRecord.gpu_type)).all()
    model_cost_rows = db.execute(select(UsageRecord.model_version, func.sum(UsageRecord.estimated_cost_usd), func.sum(UsageRecord.actual_cost_usd)).group_by(UsageRecord.model_version)).all()
    try:
        queue = job_queue().stats()
    except Exception:  # noqa: BLE001 - metrics must fail closed when queue telemetry is unavailable
        queue = {"visible": None, "in_flight": None}
    total_processed_minutes = float(processed_seconds) / 60
    active_workers = db.scalar(select(func.count(WorkerLease.id)).where(WorkerLease.expires_at > now())) or 0
    return {"job_counts": {state: count for state, count in rows}, "operation_counts": operation_counts, "cost_by_tool": [{"operation": operation, "estimated_cost_usd": float(estimated or 0) if estimated is not None else None, "actual_cost_usd": float(actual or 0) if actual is not None else None} for operation, estimated, actual in operation_cost_rows], "cost_by_worker": [{"worker_type": worker_type, "gpu_type": gpu_type, "estimated_cost_usd": float(estimated or 0) if estimated is not None else None, "actual_cost_usd": float(actual or 0) if actual is not None else None} for worker_type, gpu_type, estimated, actual in worker_cost_rows], "cost_by_model": [{"model_version": model_version, "estimated_cost_usd": float(estimated or 0) if estimated is not None else None, "actual_cost_usd": float(actual or 0) if actual is not None else None} for model_version, estimated, actual in model_cost_rows], "usage_records": usage_count, "processed_minutes": total_processed_minutes, "processed_audio_minutes": processed_minutes_by_kind.get("audio", 0), "processed_video_minutes": processed_minutes_by_kind.get("video", 0), "credits_used": int(credits_used), "estimated_compute_cost_usd": float(estimated_cost), "estimated_cost_available": bool(estimated_cost_records), "measured_compute_cost_usd": float(measured_cost), "measured_cost_available": bool(measured_cost_records), "cost_per_processed_minute_usd": round(float(measured_cost) / total_processed_minutes, 6) if measured_cost_records and total_processed_minutes > 0 else None, "gross_margin_estimate_available": False, "active_workers": int(active_workers), "active_workers_available": True, "queue": queue, "operator_id": admin.id}


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
    return [serialize_job(job, include_internal_error=True) | {"user_id": job.user_id} for job in jobs]


@app.get("/api/admin/queue")
def admin_queue(admin: User = Depends(require_admin)):
    try:
        return job_queue().stats()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Queue metrics unavailable") from exc


@app.get("/api/admin/users")
def admin_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(User).order_by(User.created_at.desc()).limit(200)).all()
    return [{"id": row.id, "email": row.email, "display_name": row.display_name, "role": row.role, "plan": row.plan_key, "is_active": row.is_active, "created_at": row.created_at.isoformat()} for row in rows]


@app.get("/api/admin/voices")
def admin_voices(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    rows = db.scalars(select(VoiceProfile).order_by(VoiceProfile.created_at.desc()).limit(200)).all()
    return [{"id": row.id, "user_id": row.user_id, "name": row.name, "status": row.status, "created_at": row.created_at.isoformat()} for row in rows]


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
def media_upload(upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("media-upload", 20))):
    return serialize_asset(upload_asset(db, user, upload))


@app.post("/api/media/presign")
def media_presign(payload: MediaPresignRequest, user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("media-presign", 20))):
    asset, url = presign_asset(db, user, payload.filename, payload.content_type, payload.size_bytes)
    return {"asset": serialize_asset(asset), "upload_url": url, "method": "PUT", "headers": {"Content-Type": payload.content_type, "x-amz-server-side-encryption": "AES256", "x-amz-tagging": "lingowave-category=media"}}


@app.post("/api/media/{asset_id}/complete")
def media_complete(asset_id: str, user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("media-complete", 20))):
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
def job_estimate(payload: EstimateRequest, user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("job-estimate", 30))):
    if payload.operation in {"dubbing", "subtitle_translation"} and not payload.target_language:
        raise HTTPException(status_code=422, detail="target_language is required for this operation")
    asset = asset_for_user(db, user, payload.media_asset_id) if payload.media_asset_id else None
    if not asset and payload.operation != "tts":
        raise HTTPException(status_code=422, detail="media_asset_id is required for this operation")
    if payload.operation == "dubbing" and asset and asset.media_kind != "video":
        raise HTTPException(status_code=422, detail="Dubbing requires a video asset")
    duration_seconds = asset.duration_seconds if asset else max(1.0, len(payload.text or "") / 12)
    amount = estimate_for_duration(duration_seconds or 0, payload.operation, lip_sync=payload.lip_sync, quality=payload.quality)
    return {"media_asset_id": asset.id if asset else None, "duration_seconds": duration_seconds, "operation": payload.operation, "credits": amount, "currency": "internal_credits", "profile_source": "config/cost_profiles.json"}


@app.post("/api/jobs")
def jobs_create(payload: JobCreateRequest, idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"), user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("job-create", 20))):
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
    payload = serialize_job(job, usage=db.scalar(select(UsageRecord).where(UsageRecord.job_id == job.id)))
    payload["success"] = job.state == JobState.COMPLETED.value
    payload["stages"] = [
        {
            "stage": metric.stage,
            "started_at": metric.started_at.isoformat(),
            "finished_at": metric.finished_at.isoformat() if metric.finished_at else None,
            "wall_clock_seconds": metric.wall_clock_seconds,
            "model_seconds": metric.model_seconds,
            "metadata": json.loads(metric.metadata_json or "{}"),
        }
        for metric in db.scalars(select(JobStageMetric).where(JobStageMetric.job_id == job.id).order_by(JobStageMetric.started_at.asc())).all()
    ]
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


@app.patch("/api/jobs/{job_id}/artifacts/{artifact_id}")
def job_artifact_update(job_id: str, artifact_id: str, payload: ArtifactTextUpdateRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    artifact = db.scalar(select(JobArtifact).join(Job, Job.id == JobArtifact.job_id).where(JobArtifact.id == artifact_id, JobArtifact.job_id == job_id, Job.user_id == user.id))
    if not artifact or not (artifact.content_type.startswith("text/") or "subrip" in artifact.content_type):
        raise HTTPException(status_code=404, detail="Text artifact not found")
    encoded = payload.text.encode("utf-8")
    if len(encoded) > settings.max_upload_bytes:
        raise HTTPException(status_code=413, detail="Text artifact is too large")
    try:
        size = object_store().put(artifact.object_key, BytesIO(encoded), content_type=artifact.content_type, max_bytes=settings.max_upload_bytes)
    except (RuntimeError, OSError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Text artifact could not be saved") from exc
    artifact.size_bytes = size
    db.add(AuditEvent(user_id=user.id, event_type="artifact.text.updated", metadata_json=json.dumps({"job_id": job_id, "artifact_id": artifact_id, "size_bytes": size})))
    db.commit()
    db.refresh(artifact)
    return serialize_artifact(artifact)


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


@app.post("/api/admin/jobs/{job_id}/cancel")
def admin_cancel_job(job_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.state in {JobState.COMPLETED.value, JobState.FAILED.value, JobState.CANCELLED.value}:
        raise HTTPException(status_code=409, detail="Job is already terminal")
    job.state = JobState.CANCELLED.value
    job.error_code = "OPERATOR_CANCELLED"
    job.error_message = "Cancelled by an operator"
    finalize(db, job, job.reserved_credits)
    db.add(JobEvent(job_id=job.id, state=job.state, message=job.error_message, metadata_json=json.dumps({"operator_id": admin.id})))
    db.commit()
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
def voice_create(name: str = Form(...), declaration: str = Form(...), authorized: bool = Form(...), upload: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db), _limit: None = Depends(rate_limited("voice-create", 10))):
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


@app.post("/api/admin/voices/{voice_id}/revoke")
def admin_voice_revoke(voice_id: str, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    profile = db.scalar(select(VoiceProfile).where(VoiceProfile.id == voice_id, VoiceProfile.deleted_at.is_(None)))
    if not profile:
        raise HTTPException(status_code=404, detail="Voice profile not found")
    profile.status = "revoked"
    profile.deleted_at = now()
    consent = db.get(VoiceConsent, profile.consent_id)
    if consent:
        consent.revoked_at = now()
    object_store().delete(profile.reference_object_key)
    db.add(AuditEvent(user_id=admin.id, event_type="voice.consent.revoked_by_operator", metadata_json=json.dumps({"voice_profile_id": profile.id, "owner_id": profile.user_id})))
    db.commit()
    return {"id": profile.id, "status": profile.status, "deleted": True}
