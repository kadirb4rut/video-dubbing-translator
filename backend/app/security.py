from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import Cookie, Depends, HTTPException, Request, status
from pwdlib import PasswordHash
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import SessionToken, User, now

password_hash = PasswordHash.recommended()


def hash_password(password: str) -> str:
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    return password_hash.hash(password)


def verify_password(password: str, encoded: str) -> bool:
    return password_hash.verify(password, encoded)


def new_session(db: Session, user: User) -> str:
    raw = secrets.token_urlsafe(32)
    token = SessionToken(
        user_id=user.id,
        token_hash=hashlib.sha256(raw.encode()).hexdigest(),
        expires_at=now() + timedelta(days=settings.session_ttl_days),
    )
    db.add(token)
    db.commit()
    return raw


def revoke_session(db: Session, raw: str | None) -> None:
    if not raw:
        return
    token = db.scalar(select(SessionToken).where(SessionToken.token_hash == hashlib.sha256(raw.encode()).hexdigest()))
    if token:
        token.revoked_at = now()
        db.commit()


def current_user(
    request: Request,
    session_cookie: str | None = Cookie(default=None, alias=settings.session_cookie_name),
    db: Session = Depends(get_db),
) -> User:
    raw = session_cookie or request.headers.get("x-session-token")
    if not raw:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
    token = db.scalar(select(SessionToken).where(SessionToken.token_hash == hashlib.sha256(raw.encode()).hexdigest()))
    expires_at = token.expires_at.replace(tzinfo=timezone.utc) if token and token.expires_at.tzinfo is None else (token.expires_at if token else None)
    if not token or token.revoked_at or expires_at <= now():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expired")
    user = db.get(User, token.user_id)
    if not user or not user.is_active or user.deleted_at:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    return user


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role not in {"admin", "operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator access required")
    return user
