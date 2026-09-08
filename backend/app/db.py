from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import NullPool

from .config import settings


class Base(DeclarativeBase):
    pass


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
