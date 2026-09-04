from fastapi import HTTPException
from starlette.requests import Request
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base
from app.rate_limit import _client_key, _consume


def test_rate_limit_uses_viewer_ip_from_forwarded_chain():
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [(b"x-forwarded-for", b"203.0.113.8, 10.42.0.1")], "client": ("10.42.0.1", 1234), "scheme": "http", "server": ("test", 80), "query_string": b""}
    assert _client_key(Request(scope)) == "203.0.113.8"


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
