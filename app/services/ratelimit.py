"""In-memory rate limiting.

Single-process sliding-window counters, keyed by (bucket, client IP). This is
enough for a single-worker deployment — which this app already requires,
since Telethon's stateful connections rule out multiple workers anyway. Not
suitable for a multi-instance deployment; there's no shared state to enforce
limits across processes.

Without this, /api/auth/send-code in particular is an open abuse vector: any
caller can trigger a real Telegram login code to any phone number through the
app's own API credentials, with no cost to them.
"""
from __future__ import annotations

import time
from collections import defaultdict, deque
from typing import Deque, Dict, Tuple

from fastapi import HTTPException, Request

_buckets: Dict[Tuple[str, str], Deque[float]] = defaultdict(deque)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def limit(bucket: str, max_requests: int, window_seconds: int):
    """FastAPI dependency factory, e.g. Depends(limit("send-code", 5, 900))."""

    async def checker(request: Request) -> None:
        key = (bucket, _client_ip(request))
        now = time.time()
        window = _buckets[key]
        while window and now - window[0] > window_seconds:
            window.popleft()
        if len(window) >= max_requests:
            retry_after = int(window_seconds - (now - window[0])) + 1
            raise HTTPException(
                status_code=429,
                detail=f"Too many requests. Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        window.append(now)

    return checker


def prune(max_age_seconds: int = 3600) -> int:
    """Drop buckets with no recent activity so long-lived deployments don't
    accumulate one deque per distinct IP forever. Safe to call periodically."""
    now = time.time()
    stale = [
        key for key, window in _buckets.items()
        if not window or now - window[-1] > max_age_seconds
    ]
    for key in stale:
        _buckets.pop(key, None)
    return len(stale)
