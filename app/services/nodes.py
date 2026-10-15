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
_ingest_last = 0.0       # newest ingest cycle reported by a live ingest server
_penalty: Dict[str, float] = {}
_rr = itertools.count()


def _beat() -> None:
    from . import ingest  # lazy: ingest is heavy and nodes is imported early

    mongo_store.connect()
    doc = {"role": settings.node_role, "url": settings.self_url, "seen_at": time.time(),
           "standby": settings.is_standby}
    if settings.is_ingest_node:
        # Only the ingest server knows when it last synced; share it so every
        # node's /api/stats (served by the user server) shows the real time.
        doc["last_ingest_at"] = ingest.state().get("last_run") or 0.0
    mongo_store._db()[_COLL].update_one({"_id": settings.self_url}, {"$set": doc}, upsert=True)


def beat_now() -> None:
    """Heartbeat immediately (after a role change), instead of waiting for the next tick."""
    if settings.sharded and mongo_store.is_enabled() and settings.self_url:
        _beat()


def live_nodes() -> List[dict]:
    """Every other server that heartbeated within ALIVE_SECONDS."""
    mongo_store.connect()
    cutoff = time.time() - ALIVE_SECONDS
    rows = mongo_store._db()[_COLL].find({"seen_at": {"$gt": cutoff}})
    me = settings.self_url.rstrip("/").lower()
    return [r for r in rows if str(r.get("url", "")).rstrip("/").lower() != me]


async def wait_for_standby_release(role: str, max_wait: float = 150.0) -> None:
    """Before a returning server starts its role, wait until a standby covering it has let go.

    Matters most for ingest: Telegram invalidates a session used from two places at
    once. Our own heartbeat (already running) is what tells the standby to hand back.
    """
    if not (mongo_store.is_enabled() and settings.self_url) or settings.is_standby:
        return
    loop = asyncio.get_event_loop()
    deadline = time.time() + max_wait
    while time.time() < deadline:
        try:
            covering = [n for n in await loop.run_in_executor(None, live_nodes)
                        if n.get("standby") and n.get("role") == role]
        except Exception as exc:  # noqa: BLE001 — never block startup on a Mongo blip
            log.warning("Standby check failed: %s", exc)
            return
        if not covering:
            return
        log.info("Standby %s is covering %s; waiting for it to hand back", covering[0].get("url"), role)
        await asyncio.sleep(5)
    log.warning("Standby did not release %s within %ss; starting anyway", role, int(max_wait))


def _read_registry() -> tuple:
    """(live user server URLs, newest ingest cycle time from a live ingest server)."""
    mongo_store.connect()
    cutoff = time.time() - ALIVE_SECONDS
    rows = list(mongo_store._db()[_COLL].find({"seen_at": {"$gt": cutoff}}))
    users = sorted(r["url"] for r in rows if r.get("role") == "user" and r.get("url"))
    last = max([float(r.get("last_ingest_at") or 0) for r in rows if r.get("role") == "ingest"] or [0.0])
    return users, last


def ingest_last_run(local: float) -> float:
    """This server's own last ingest cycle, or the ingest server's when this one doesn't ingest."""
    return local or (_ingest_last if settings.sharded else 0.0)


async def heartbeat_loop() -> None:
    """Announce this node and keep a fresh view of the user servers."""
    global _cache, _cache_at, _ingest_last
    if not (settings.sharded and mongo_store.is_enabled() and settings.self_url):
        return
    loop = asyncio.get_event_loop()
    while True:
        try:
            await loop.run_in_executor(None, _beat)
            _cache, _ingest_last = await loop.run_in_executor(None, _read_registry)
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
    return {"role": settings.node_role or "auto", "standby": settings.is_standby, "self_url": settings.self_url,
            "user_nodes": user_urls(), "penalised": [u for u, t in _penalty.items() if t > time.time()]}
