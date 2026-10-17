"""The ingest server stands in for the validator and user roles while their servers are down.

The ingest server is the one that always runs (it holds the Telegram session).
When no live server holds the validator or user role, it runs that role's
background work itself, so with the other three servers off it does
everything alone. When a server for the role heartbeats again (its own
server, or the standby covering it) the ingest server stops its copy and
hands the role back. Covered roles go out in this server's heartbeat
("covers"), so the standby never covers the same role a second time.

It waits longer than the standby does (COVER_CHECKS vs standby.GONE_CHECKS),
so a live standby gets the first pick and the ingest server takes what's left.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Callable, Dict, List, Set

from ..config import settings
from . import mongo_store, nodes

log = logging.getLogger("dealradar.cover")

ROLES = ("user", "validator")
CHECK_SECONDS = 15
COVER_CHECKS = 6   # consecutive misses before taking over (the standby waits 3)


def up_roles(live: List[dict]) -> Set[str]:
    """Roles some other live server holds, as its own role or as one it covers."""
    up: Set[str] = set()
    for node in live:
        role = node.get("role")
        if role and role != "standby":
            up.add(role)
        up.update(node.get("covers") or [])
    return up


async def run(start: Callable[[str], List[asyncio.Task]]) -> None:
    if not (mongo_store.is_enabled() and settings.self_url):
        log.warning("Covering other roles needs MongoDB and SELF_URL; ingest only")
        return
    loop = asyncio.get_event_loop()
    tasks: Dict[str, List[asyncio.Task]] = {}
    misses: Dict[str, int] = {r: 0 for r in ROLES}

    async def release(role: str) -> None:
        log.info("Handing the %s role back to its server", role)
        nodes.covering.discard(role)
        running = tasks.pop(role, [])
        for t in running:
            t.cancel()
        await asyncio.gather(*running, return_exceptions=True)

    try:
        while True:
            await asyncio.sleep(CHECK_SECONDS)
            try:
                live = await loop.run_in_executor(None, nodes.live_nodes)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — a Mongo blip is not a dead server
                log.warning("Cover registry read failed: %s", exc)
                continue
            up = up_roles(live)
            for role in ROLES:
                if role in up:
                    misses[role] = 0
                    if role in tasks:
                        await release(role)
                    continue
                misses[role] += 1
                if role not in tasks and misses[role] >= COVER_CHECKS:
                    log.warning("No live %s server; the ingest server is running that role too", role)
                    nodes.covering.add(role)
                    tasks[role] = start(role)
                    try:
                        await loop.run_in_executor(None, nodes.beat_now)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Heartbeat after covering %s failed: %s", role, exc)
    finally:
        for role in list(tasks):
            await release(role)
