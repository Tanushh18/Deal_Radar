"""Who's browsing the website right now — a tiny in-memory tracker.

Auto poll mode (autopoll.py) wants to know how many people are looking at
deals, so a busy evening gets fresher deals than a dead 4 a.m. App users are
already visible through the devices table (last_seen_at); website visitors
leave no trace anywhere, so this counts them.

Privacy: we never keep an IP. Each visitor is a truncated sha1 of
IP + user-agent — enough to tell "12 different people" from "one person
refreshing 12 times", useless for identifying anyone. Entries older than the
window are dropped, and the whole thing lives only in this process (it
resets on a restart, which is fine: it's a "right now" signal).

Only browser user-agents count: the Android/iOS app hits the same /api/deals
endpoints with okhttp/CFNetwork user-agents, and those people are already
counted as app users — counting them here too would double them.
"""
from __future__ import annotations

import hashlib
import time
from typing import Dict, Optional

from fastapi import Request

from .ratelimit import _client_ip

WINDOW_SECONDS = 900          # "active" = hit the deals feed in the last 15 min
MAX_TRACKED = 20000           # hard cap so a crawler storm can't grow this forever

_seen: Dict[str, float] = {}  # visitor key -> last seen timestamp


def _visitor_key(request: Request) -> str:
    raw = f"{_client_ip(request)}|{request.headers.get('user-agent', '')}"
    return hashlib.sha1(raw.encode("utf-8", "replace")).hexdigest()[:16]


def should_track(request: Request) -> bool:
    if request.method != "GET" or not request.url.path.startswith("/api/deals"):
        return False
    return "mozilla" in request.headers.get("user-agent", "").lower()


def record(request: Request, now: Optional[float] = None) -> None:
    """Note a website visitor. Cheap: one hash + one dict write."""
    if not should_track(request):
        return
    now = now or time.time()
    _seen[_visitor_key(request)] = now
    if len(_seen) > MAX_TRACKED:
        prune(now)
        if len(_seen) > MAX_TRACKED:  # still full of fresh keys: drop the oldest half
            for key, _ in sorted(_seen.items(), key=lambda kv: kv[1])[: len(_seen) // 2]:
                _seen.pop(key, None)


def prune(now: Optional[float] = None) -> int:
    now = now or time.time()
    stale = [key for key, ts in _seen.items() if now - ts > WINDOW_SECONDS]
    for key in stale:
        _seen.pop(key, None)
    return len(stale)


def active_web_visitors(now: Optional[float] = None) -> int:
    """Distinct website visitors in the last 15 minutes (prunes as it goes)."""
    prune(now)
    return len(_seen)


def reset() -> None:
    """Tests only."""
    _seen.clear()
