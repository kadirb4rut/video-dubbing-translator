from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.rate_limit import _consume


def test_rate_limit_window_is_persisted_and_shared_between_sessions():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)

    with Session(engine) as first:
        _consume(first, bucket="test", key="client-1", limit=2)

    with Session(engine) as second:
        _consume(second, bucket="test", key="client-1", limit=2)

    with Session(engine) as third:
        try:
            _consume(third, bucket="test", key="client-1", limit=2)
        except HTTPException as exc:
            assert exc.status_code == 429
            assert exc.headers["Retry-After"] == "60"
        else:
            raise AssertionError("the persisted rate-limit window should reject the third request")
