from __future__ import annotations

import json
import secrets

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CreditLedgerEntry, CreditReservation, Job, User, now


def balance(db: Session, user_id: str) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(CreditLedgerEntry.credits), 0)).where(CreditLedgerEntry.user_id == user_id)) or 0)


def grant(db: Session, user_id: str, credits: int, *, reference_key: str, entry_type: str = "grant", metadata: dict | None = None) -> CreditLedgerEntry:
    if credits <= 0:
        raise ValueError("credits must be positive")
    existing = db.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.reference_key == reference_key))
    if existing:
        return existing
    entry = CreditLedgerEntry(user_id=user_id, credits=credits, entry_type=entry_type, reference_key=reference_key, metadata_json=json.dumps(metadata or {}))
    db.add(entry)
    return entry


def adjust(db: Session, user_id: str, credits: int, *, reference_key: str, entry_type: str, metadata: dict | None = None) -> CreditLedgerEntry:
    if credits == 0:
        raise ValueError("credits must be non-zero")
    existing = db.scalar(select(CreditLedgerEntry).where(CreditLedgerEntry.reference_key == reference_key))
    if existing:
        return existing
    entry = CreditLedgerEntry(user_id=user_id, credits=credits, entry_type=entry_type, reference_key=reference_key, metadata_json=json.dumps(metadata or {}))
    db.add(entry)
    return entry


def reserve(db: Session, user: User, job: Job, amount: int) -> CreditReservation:
    # Serialize reservations for one account on PostgreSQL so concurrent requests
    # cannot both spend the same available balance. SQLite ignores FOR UPDATE.
    db.execute(select(User).where(User.id == user.id).with_for_update())
    existing = db.scalar(select(CreditReservation).where(CreditReservation.job_id == job.id))
    # A queued job is idempotent while its reservation is active. A finalized
    # cancelled job may be retried by an operator, which needs a fresh ledger
    # debit while reusing the job's reservation row.
    if existing and existing.status == "reserved":
        return existing
    if amount <= 0:
        raise ValueError("Reservation amount must be positive")
    if balance(db, user.id) < amount:
        raise HTTPException(status_code=402, detail="Insufficient credits")
    reference = f"reserve:{job.id}" if not existing else f"reserve:{job.id}:retry:{secrets.token_urlsafe(8)}"
    db.add(CreditLedgerEntry(user_id=user.id, job_id=job.id, credits=-amount, entry_type="reservation", reference_key=reference))
    if existing:
        existing.amount = amount
        existing.status = "reserved"
        existing.finalized_at = None
        reservation = existing
    else:
        reservation = CreditReservation(user_id=user.id, job_id=job.id, amount=amount)
        db.add(reservation)
    return reservation


def finalize(db: Session, job: Job, actual_amount: int | None = None) -> None:
    reservation = db.scalar(select(CreditReservation).where(CreditReservation.job_id == job.id))
    if not reservation or reservation.status == "finalized":
        return
    if reservation.status != "reserved":
        raise ValueError(f"Cannot finalize reservation in state {reservation.status}")
    reservation.status = "finalized"
    reservation.finalized_at = now()
    job.actual_credits = actual_amount if actual_amount is not None else reservation.amount


def release(db: Session, job: Job, reason: str = "infrastructure_failure") -> None:
    reservation = db.scalar(select(CreditReservation).where(CreditReservation.job_id == job.id))
    if not reservation or reservation.status == "released":
        return
    if reservation.status == "finalized":
        return
    db.add(CreditLedgerEntry(user_id=job.user_id, job_id=job.id, credits=reservation.amount, entry_type="release", reference_key=f"release:{job.id}", metadata_json=json.dumps({"reason": reason})))
    reservation.status = "released"
    reservation.finalized_at = now()


def ledger_rows(db: Session, user_id: str) -> list[CreditLedgerEntry]:
    return list(db.scalars(select(CreditLedgerEntry).where(CreditLedgerEntry.user_id == user_id).order_by(CreditLedgerEntry.created_at.desc()).limit(100)))
