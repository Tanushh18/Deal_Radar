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
_cost_buckets: Dict[Tuple[str, str], Deque[Tuple[float, int]]] = defaultdict(deque)  # (timestamp, cost) pairs


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


def cost_limit(bucket: str, max_cost: int, window_seconds: int):
    """Rate limit by cost (e.g. DB rows processed) instead of request count.

    Useful for endpoints with variable complexity: /sparklines with 100 deals
    costs more than with 1 deal. Tracks (timestamp, cost) pairs and rejects
    if the sum of costs in the window exceeds max_cost.
    """

    async def checker(request: Request, cost: int) -> None:
        key = (bucket, _client_ip(request))
        now = time.time()
        window = _cost_buckets[key]

        # Drop expired entries
        while window and now - window[0][0] > window_seconds:
            window.popleft()

        # Sum cost in the current window
        current_cost = sum(c for _, c in window)
        if current_cost + cost > max_cost:
            retry_after = int(window_seconds - (now - window[0][0])) + 1 if window else 1
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded (cost: {cost}, limit: {max_cost}). Try again in {retry_after}s.",
                headers={"Retry-After": str(retry_after)},
            )
        window.append((now, cost))

    return checker


def prune(max_age_seconds: int = 3600) -> int:
    """Drop buckets with no recent activity so long-lived deployments don't
    accumulate one deque per distinct IP forever. Safe to call periodically."""
    now = time.time()
    stale = [
        key for key, window in _buckets.items()
        if not window or now - window[-1] > max_age_seconds
    ]
    stale_cost = [
        key for key, window in _cost_buckets.items()
        if not window or now - window[-1][0] > max_age_seconds
    ]
    for key in stale:
        _buckets.pop(key, None)
    for key in stale_cost:
        _cost_buckets.pop(key, None)
    return len(stale) + len(stale_cost)
