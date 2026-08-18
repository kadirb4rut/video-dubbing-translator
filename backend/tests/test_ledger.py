from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.ledger import balance, finalize, grant, release, reserve
from app.models import Job, User


def test_reservation_finalize_and_release_are_ledgered():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(email="ledger@example.com", password_hash="hash")
        db.add(user)
        db.flush()
        grant(db, user.id, 20, reference_key="test-grant")
        job = Job(user_id=user.id, media_asset_id="asset", operation="noise", idempotency_key="job-1", estimate_credits=7, reserved_credits=7)
        db.add(job)
        db.flush()
        reserve(db, user, job, 7)
        db.commit()
        assert balance(db, user.id) == 13
        finalize(db, job, 5)
        db.commit()
        assert balance(db, user.id) == 13
        assert job.actual_credits == 5

        job2 = Job(user_id=user.id, media_asset_id="asset", operation="noise", idempotency_key="job-2", estimate_credits=4, reserved_credits=4)
        db.add(job2)
        db.flush()
        reserve(db, user, job2, 4)
        db.commit()
        release(db, job2)
        db.commit()
        assert balance(db, user.id) == 13
