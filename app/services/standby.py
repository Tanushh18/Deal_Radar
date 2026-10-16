"""Standby server for the ingest / validator / user split (NODE_ROLE=standby).

The spare serves deal browsing like every node and watches the node registry.
When one of the three roles has had no live heartbeat for a while, it takes
that role over; when the real server heartbeats again, it hands the role back
(the returning server waits for that, see nodes.wait_for_standby_release, so
the Telegram session never runs in two places). It covers one role at a time,
most important first. If all three are down it covers nothing and stays in
its lightest mode: reads and user requests served locally, no background jobs,
so the one server left keeps the app and website up.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable, Dict, Optional

from ..config import settings
from . import mongo_store, nodes

log = logging.getLogger("dealradar.standby")

PRIORITY = ("ingest", "user", "validator")
CHECK_SECONDS = 15
GONE_CHECKS = 3   # consecutive misses (on top of nodes.ALIVE_SECONDS) before taking over


def _can_cover(role: str) -> bool:
    if role == "ingest":
        return settings.telegram_configured and bool(settings.reader_session)
    return True


async def _set_role(role: str) -> None:
    settings.node_role = role
    settings.is_standby = role == "standby"
    try:
        await asyncio.get_event_loop().run_in_executor(None, nodes.beat_now)
    except Exception as exc:  # noqa: BLE001
        log.warning("Heartbeat after role change failed: %s", exc)


async def run(start: Callable[[str], Awaitable[None]], stop: Callable[[], Awaitable[None]]) -> None:
    if not (mongo_store.is_enabled() and settings.self_url):
        log.warning("Standby needs MongoDB and SELF_URL; serving reads only")
        return
    loop = asyncio.get_event_loop()
    covering: Optional[str] = None
    misses: Dict[str, int] = {r: 0 for r in PRIORITY}

    async def release(why: str) -> None:
        nonlocal covering
        log.info("Standby handing back %s (%s)", covering, why)
        covering = None
        await stop()
        await _set_role("standby")

    while True:
        await asyncio.sleep(CHECK_SECONDS)
        try:
            live = await loop.run_in_executor(None, nodes.live_nodes)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — a Mongo blip is not a dead server
            log.warning("Standby registry read failed: %s", exc)
            continue
        up = {n.get("role") for n in live if not n.get("standby")}
        for r in PRIORITY:
            misses[r] = 0 if r in up else misses[r] + 1

        try:
            if covering == "all" and up:
                await release("a peer server returned; resuming standby")
                continue
            if covering and covering in up:
                await release("its server is back")
                continue
            if not up:
                # No peers remain: use the spare as the full application, not a read-only shell.
                if covering != "all" and _can_cover("ingest"):
                    log.warning("No peer servers remain; standby switching to all-in-one mode")
                    covering = "all"
                    await _set_role("all")
                    await start("all")
                continue
            if covering:
                continue
            for r in PRIORITY:
                if misses[r] >= GONE_CHECKS and _can_cover(r):
                    log.warning("No live %s server; standby taking over that role", r)
                    covering = r
                    await _set_role(r)
                    await start(r)
                    break
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the standby loop itself must never die
            log.exception("Standby step failed: %s", exc)
