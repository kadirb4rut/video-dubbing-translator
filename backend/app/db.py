from __future__ import annotations

import os
import time
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings


class Base(DeclarativeBase):
    pass


def _aurora_resume_error(exc: BaseException) -> bool:
    """Return true only for transient Data API wake-up errors.

    Aurora Serverless v2 Express can auto-pause. The first Data API transaction
    after that pause may fail while the HTTP endpoint or database instance is
    resuming. Retrying these two provider errors is safe; retrying arbitrary
    database errors could duplicate writes or hide a real outage.
    """

    current: BaseException | None = exc
    seen: set[int] = set()
    retryable_codes = {"DatabaseResumingException", "HttpEndpointNotEnabledException"}
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        response = getattr(current, "response", None)
        if isinstance(response, dict):
            code = ((response.get("Error") or {}).get("Code"))
            if code in retryable_codes:
                return True
        if any(code in str(current) for code in retryable_codes):
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


def _install_aurora_resume_retry() -> None:
    """Wrap the Data API DB-API connection's transaction start with bounded retry."""

    if not settings.database_url.startswith("postgresql+auroradataapi://"):
        return

    import aurora_data_api

    original_client = aurora_data_api.AuroraDataAPIClient

    class ResilientAuroraDataAPIClient(original_client):
        def cursor(self):
            attempts = max(1, settings.aurora_resume_retry_attempts)
            for attempt in range(attempts):
                try:
                    return super().cursor()
                except Exception as exc:
                    if not _aurora_resume_error(exc) or attempt == attempts - 1:
                        raise
                    time.sleep(settings.aurora_resume_retry_base_seconds * (2**attempt))
            raise RuntimeError("Aurora Data API retry loop exited unexpectedly")

    def resilient_connect(
        aurora_cluster_arn=None,
        secret_arn=None,
        rds_data_client=None,
        database=None,
        host=None,
        port=None,
        username=None,
        password=None,
        charset=None,
        continue_after_timeout=None,
    ):
        # Keep the DB-API connect signature used by the SQLAlchemy dialect;
        # AuroraDataAPIClient itself calls the database name ``dbname``.
        return ResilientAuroraDataAPIClient(
            dbname=database,
            aurora_cluster_arn=aurora_cluster_arn,
            secret_arn=secret_arn,
            rds_data_client=rds_data_client,
            charset=charset,
            continue_after_timeout=continue_after_timeout,
        )

    aurora_data_api.connect = resilient_connect


_install_aurora_resume_retry()


if settings.database_url.startswith("sqlite"):
    Path(settings.database_url.removeprefix("sqlite:///").strip()).parent.mkdir(parents=True, exist_ok=True)
    connect_args = {"check_same_thread": False}
else:
    connect_args = {}

engine_kwargs = {"future": True, "connect_args": connect_args}
if settings.db_pool_mode == "null" or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    # Lambda execution environments can stay warm. A persistent SQLAlchemy connection
    # would keep Aurora Serverless from auto-pausing and can exhaust connections during
    # concurrent cold starts, so Data API/Lambda uses one connection per request.
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs["pool_pre_ping"] = True
engine = create_engine(settings.database_url, **engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_tables() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(engine)
