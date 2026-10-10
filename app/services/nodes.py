"""Node registry for the ingest / validator / user split.

Every server heartbeats its role and own address into MongoDB, so the others
find a healthy user server without anyone hand-copying URLs, and a user server
that goes away drops out of the pool by itself. A failed forward puts that
server in a short penalty box so requests move to the next one.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from typing import Dict, List

from ..config import settings
from . import mongo_store

log = logging.getLogger("dealradar.nodes")

HEARTBEAT_SECONDS = 15
ALIVE_SECONDS = 60       # a node silent for longer than this is treated as gone
PENALTY_SECONDS = 30     # after a failed forward, skip that node for a while
_COLL = "nodes"

_cache: List[str] = []
_cache_at = 0.0
_penalty: Dict[str, float] = {}
_rr = itertools.count()


def _beat() -> None:
    mongo_store.connect()
    mongo_store._db()[_COLL].update_one(
        {"_id": settings.self_url},
        {"$set": {"role": settings.node_role, "url": settings.self_url, "seen_at": time.time()}},
        upsert=True)


def _read_user_urls() -> List[str]:
    mongo_store.connect()
    cutoff = time.time() - ALIVE_SECONDS
    rows = mongo_store._db()[_COLL].find({"role": "user", "seen_at": {"$gt": cutoff}})
    return sorted(r["url"] for r in rows if r.get("url"))


async def heartbeat_loop() -> None:
    """Announce this node and keep a fresh view of the user servers."""
    global _cache, _cache_at
    if not (settings.sharded and mongo_store.is_enabled() and settings.self_url):
        return
    loop = asyncio.get_event_loop()
    while True:
        try:
            await loop.run_in_executor(None, _beat)
            _cache = await loop.run_in_executor(None, _read_user_urls)
            _cache_at = time.time()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a Mongo blip must not stop the node
            log.warning("Node heartbeat failed: %s", exc)
        await asyncio.sleep(HEARTBEAT_SECONDS)


def user_urls() -> List[str]:
    """Healthy user servers, registry first, USER_NODE_URL as the static fallback."""
    urls = [u for u in _cache if u.rstrip("/").lower() != settings.self_url.rstrip("/").lower()]
    if not urls and settings.user_node_url:
        urls = [settings.user_node_url]
    return urls


def pick_user_url() -> str:
    """Round-robin over healthy user servers, skipping any in the penalty box."""
    urls = user_urls()
    if not urls:
        return ""
    now = time.time()
    ok = [u for u in urls if _penalty.get(u, 0) < now] or urls
    return ok[next(_rr) % len(ok)]


def mark_failed(url: str) -> None:
    _penalty[url] = time.time() + PENALTY_SECONDS


def snapshot() -> dict:
    return {"role": settings.node_role or "auto", "self_url": settings.self_url,
            "user_nodes": user_urls(), "penalised": [u for u, t in _penalty.items() if t > time.time()]}
