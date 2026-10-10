"""Single-leader election, so identical servers can share one config safely.

Telegram invalidates a session used from two places at once, and ingest, the
live listener and push must not run twice. Every server holds the same env
vars, so they compete for a short lease in MongoDB: the holder runs those
singletons; the others serve reads from their own cache and forward everything
else to the holder. If the leader stops renewing, another server takes over
within LEASE_SECONDS. With no MongoDB (or ROLE set explicitly) there is no
election: the server behaves as before.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from typing import Awaitable, Callable, Optional

from ..config import settings
from . import mongo_store

log = logging.getLogger("dealradar.leader")

LEASE_SECONDS = 90
RENEW_SECONDS = 20
_ME = uuid.uuid4().hex
_leader = True              # until an election says otherwise (no Mongo => behave as before)
_leader_url = ""            # public URL of whoever holds the lease
_last_renewed = 0.0


def enabled() -> bool:
    return settings.role not in ("primary", "replica") and mongo_store.is_enabled()


def is_leader() -> bool:
    return _leader


def leader_url() -> str:
    return _leader_url


def _try_acquire() -> tuple[bool, str]:
    """One lease attempt. Returns (we hold it, URL of the holder)."""
    import pymongo.errors as pe

    mongo_store.connect()
    coll = mongo_store._db()["leases"]
    now = time.time()
    me = {"holder": _ME, "url": settings.self_url, "expires_at": now + LEASE_SECONDS}
    try:
        coll.find_one_and_update(
            {"_id": "ingest", "$or": [{"holder": _ME}, {"expires_at": {"$lt": now}}]},
            {"$set": me}, upsert=True)
        return True, settings.self_url
    except pe.DuplicateKeyError:           # live lease belongs to someone else
        doc = coll.find_one({"_id": "ingest"}) or {}
        return False, doc.get("url", "")


def release() -> None:
    try:
        mongo_store._db()["leases"].delete_one({"_id": "ingest", "holder": _ME})
    except Exception:  # noqa: BLE001
        pass


async def run(on_gain: Callable[[], Awaitable[None]], on_loss: Callable[[], Awaitable[None]]) -> None:
    """Keep the lease fresh and call on_gain/on_loss as leadership changes."""
    global _leader, _leader_url, _last_renewed
    if not enabled():
        await on_gain()
        return
    _leader = False
    loop = asyncio.get_event_loop()
    while True:
        try:
            won, url = await loop.run_in_executor(None, _try_acquire)
            _leader_url = url
            if won:
                _last_renewed = time.time()
            if won and not _leader:
                _leader = True
                log.info("This server is now the leader (runs ingest, Telegram, pushes)")
                await on_gain()
            elif not won and _leader:
                _leader = False
                log.warning("Lost the lease to %s — standing down", url)
                await on_loss()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — Mongo blip: keep going, but never outlive our lease
            log.warning("Leader election failed: %s", exc)
            if _leader and time.time() - _last_renewed > LEASE_SECONDS - RENEW_SECONDS:
                _leader = False
                await on_loss()
        await asyncio.sleep(RENEW_SECONDS)
