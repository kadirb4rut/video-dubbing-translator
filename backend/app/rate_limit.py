from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import Depends, HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .models import RateLimitBucket, now


def _window_start() -> datetime:
    return now().replace(second=0, microsecond=0)


def _consume(db: Session, *, bucket: str, key: str, limit: int) -> None:
    window_start = _window_start()

    # The unique window key makes the insert race-safe on PostgreSQL. If two
    # replicas create the same new bucket concurrently, retry after the loser
    # rolls back and then lock/increment the row that won the race.
    for attempt in range(2):
        try:
            row = db.scalar(
                select(RateLimitBucket)
                .where(
                    RateLimitBucket.bucket == bucket,
                    RateLimitBucket.key == key,
                    RateLimitBucket.window_start == window_start,
                )
                .with_for_update()
            )
            if row is None:
                row = RateLimitBucket(bucket=bucket, key=key, window_start=window_start, request_count=0)
                db.add(row)
                db.flush()
            if row.request_count >= limit:
                db.rollback()
                raise HTTPException(status_code=429, detail="Rate limit exceeded; try again shortly", headers={"Retry-After": "60"})
            row.request_count += 1
            # Operational buckets are not user data. Prune old windows during
            # normal traffic so the table cannot grow without bound.
            db.execute(
                delete(RateLimitBucket)
                .where(RateLimitBucket.window_start < window_start - timedelta(minutes=2))
                .execution_options(synchronize_session=False)
            )
            db.commit()
            return
        except IntegrityError:
            db.rollback()
            if attempt == 1:
                raise HTTPException(status_code=429, detail="Rate limit temporarily unavailable; try again shortly", headers={"Retry-After": "5"}) from None


def _client_key(request: Request) -> str:
    # The API is reachable through the CloudFront/ALB path, whose security
    # group admits CloudFront origin traffic only. Use the first viewer IP
    # from the proxy-preserved X-Forwarded-For chain instead of rate-limiting
    # every public user by the ALB task's private address.
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        viewer_ip = forwarded.split(",", 1)[0].strip()
        if viewer_ip:
            return viewer_ip[:255]
    return request.client.host if request.client else "unknown"


def rate_limited(bucket: str, limit: int | None = None):
    max_requests = limit or settings.rate_limit_per_minute

    def dependency(request: Request, db: Session = Depends(get_db)) -> None:
        _consume(db, bucket=bucket, key=_client_key(request), limit=max_requests)

    return dependency
