from __future__ import annotations

from collections import defaultdict, deque
from threading import Lock
from time import monotonic

from fastapi import HTTPException, Request

from .config import settings

_buckets: dict[str, deque[float]] = defaultdict(deque)
_lock = Lock()


def rate_limited(bucket: str, limit: int | None = None):
    max_requests = limit or settings.rate_limit_per_minute

    def dependency(request: Request) -> None:
        key = f"{bucket}:{request.client.host if request.client else 'unknown'}"
        now = monotonic()
        with _lock:
            entries = _buckets[key]
            while entries and now - entries[0] >= 60:
                entries.popleft()
            if len(entries) >= max_requests:
                raise HTTPException(status_code=429, detail="Rate limit exceeded; try again shortly")
            entries.append(now)

    return dependency
