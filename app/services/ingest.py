"""The automation loop.

One cycle does, in order:

  1. fetch  — pull only messages newer than each channel's watermark
  2. parse  — structure them, drop chatter
  3. store  — dedup against existing deals, update price history + scores
  4. expire — retire deals past their TTL
  5. verify — probe a slice of live links and kill the dead ones
  6. alert  — notify watchlist owners in their Telegram Saved Messages
  7. flush  — batch-write everything dirty to Google Sheets

Watermarking (step 1) is what keeps this viable on a free tier: each channel
remembers its last seen message id, so a poll costs one small request per
channel instead of re-reading history.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .. import db
from ..config import settings
from . import parser, search, sheets, store, telegram

log = logging.getLogger(__name__)

_state: Dict[str, Any] = {
    "running": False,
    "last_run": 0.0,
    "last_duration": 0.0,
    "cycles": 0,
    "last_result": {},
    "last_error": None,
}
_cycle_lock = asyncio.Lock()

DEAD_MARKERS = (
    "currently unavailable",
    "out of stock",
    "page not found",
    "sorry, we couldn",
    "product not found",
    "no longer available",
    "this item is not available",
)


def state() -> Dict[str, Any]:
    return dict(_state)


# --- step 1-3: fetch, parse, store -------------------------------------
async def ingest_channel(channel: Dict[str, Any]) -> Dict[str, int]:
    """Pull and store new deals from one channel."""
    result = {"fetched": 0, "new": 0, "merged": 0, "skipped": 0}
    source_user = channel.get("source_user_id")
    if not source_user:
        return result

    watermark = int(channel.get("last_message_id") or 0)
    limit = settings.incremental_limit if watermark else settings.backfill_limit

    try:
        messages = await telegram.fetch_messages(
            source_user, int(channel["tg_id"]), min_id=watermark, limit=limit
        )
    except telegram.TelegramError as exc:
        log.info("Channel %s skipped: %s", channel.get("title"), exc)
        return result
    except Exception as exc:  # noqa: BLE001
        log.warning("Channel %s failed: %s", channel.get("title"), exc)
        return result

    highest = watermark
    for message in messages:
        highest = max(highest, int(message.id or 0))
        text = message.message or getattr(message, "raw_text", "") or ""
        if not text:
            continue
        result["fetched"] += 1

        deal = parser.parse_message(
            text,
            channel_id=int(channel["tg_id"]),
            channel_title=channel.get("title") or "",
            message_id=int(message.id),
            posted_at=message.date.timestamp() if message.date else time.time(),
            ttl_hours=settings.deal_ttl_hours,
        )
        if deal is None:
            result["skipped"] += 1
            continue

        # Telegram-hosted photos are fetched lazily through our own endpoint.
        if getattr(message, "photo", None):
            deal["image_url"] = f"/api/deals/{deal['id']}/image"

        outcome = store.save_deal(deal)
        if outcome == "new":
            result["new"] += 1
        elif outcome == "merged":
            result["merged"] += 1

    if highest > watermark:
        db.execute(
            "UPDATE channels SET last_message_id = ?, last_fetched_at = ? WHERE tg_id = ?",
            (highest, time.time(), channel["tg_id"]),
        )
    else:
        db.execute(
            "UPDATE channels SET last_fetched_at = ? WHERE tg_id = ?",
            (time.time(), channel["tg_id"]),
        )
    return result


# --- step 5: link liveness ---------------------------------------------
async def verify_links(batch: int = 40) -> Dict[str, int]:
    """Probe the least-recently-checked live deals; retire dead links.

    This is what lets deals outlive a fixed TTL when they're still in stock,
    and die early when they aren't.
    """
    if not settings.liveness_check_enabled:
        return {"checked": 0, "dead": 0}

    rows = db.query(
        "SELECT id, url, clean_url FROM deals WHERE status='live' AND url != '' "
        "ORDER BY last_seen_at ASC LIMIT ?",
        (batch,),
    )
    if not rows:
        return {"checked": 0, "dead": 0}

    checked = dead = 0
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        )
    }
    async with httpx.AsyncClient(
        follow_redirects=True, timeout=8.0, headers=headers
    ) as client:

        async def probe(row) -> None:
            nonlocal checked, dead
            url = row["url"] or row["clean_url"]
            if not url:
                return
            try:
                resp = await client.get(url)
                checked += 1
                if resp.status_code in (404, 410):
                    store.mark_dead(row["id"], "dead_link")
                    dead += 1
                    return
                if resp.status_code < 400:
                    body = resp.text[:60000].lower()
                    if any(marker in body for marker in DEAD_MARKERS):
                        store.mark_dead(row["id"], "out_of_stock")
                        dead += 1
                        return
                    # Alive and still selling — extend its life past the base TTL.
                    db.execute(
                        "UPDATE deals SET last_seen_at = ?, expires_at = MAX(expires_at, ?), "
                        "dirty = 1 WHERE id = ?",
                        (time.time(), time.time() + 86400, row["id"]),
                    )
            except (httpx.HTTPError, asyncio.TimeoutError):
                pass  # a transient network error is not evidence the deal is dead

        # Bounded concurrency — free tier has one small CPU.
        semaphore = asyncio.Semaphore(8)

        async def guarded(row):
            async with semaphore:
                await probe(row)

        await asyncio.gather(*(guarded(r) for r in rows), return_exceptions=True)

    return {"checked": checked, "dead": dead}


# --- step 6: watchlist alerts ------------------------------------------
def _format_alert(deal: Dict[str, Any]) -> str:
    bits = [f"🔔 {deal['title']}"]
    if deal.get("price"):
        line = f"₹{int(deal['price']):,}"
        if deal.get("mrp"):
            line += f"  (was ₹{int(deal['mrp']):,}, {deal.get('discount_pct', 0)}% off)"
        bits.append(line)
    if deal.get("is_lowest"):
        bits.append("📉 Lowest price we've recorded")
    if deal.get("coupon"):
        bits.append(f"🏷 Code: {deal['coupon']}")
    if deal.get("store"):
        bits.append(f"🛒 {deal['store'].title()}")
    if deal.get("url"):
        bits.append(deal["url"])
    return "\n".join(bits)


async def run_watchlist_alerts(max_per_watchlist: int = 3) -> Dict[str, int]:
    watchlists = db.rows_to_dicts(db.query("SELECT * FROM watchlists WHERE notify = 1"))
    sent = 0
    for watch in watchlists:
        filters = watch.get("filters") or {}
        if isinstance(filters, str):
            try:
                filters = json.loads(filters)
            except json.JSONDecodeError:
                filters = {}
        if isinstance(filters, list):
            filters = {}

        channel_ids = user_channel_ids(watch["user_id"])
        try:
            found = search.search(
                q=watch.get("query") or "",
                category=filters.get("category", ""),
                store=filters.get("store", ""),
                max_price=filters.get("max_price"),
                min_discount=int(filters.get("min_discount") or 0),
                channel_ids=channel_ids or None,
                sort="newest",
                limit=20,
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("Watchlist %s search failed: %s", watch["id"], exc)
            continue

        fresh = []
        for deal in found["results"]:
            already = db.query_one(
                "SELECT 1 FROM notified WHERE watchlist_id = ? AND deal_id = ?",
                (watch["id"], deal["id"]),
            )
            if not already:
                fresh.append(deal)
            if len(fresh) >= max_per_watchlist:
                break

        for deal in fresh:
            ok = await telegram.notify_user(watch["user_id"], _format_alert(deal))
            db.execute(
                "INSERT OR REPLACE INTO notified (watchlist_id, deal_id, sent_at) VALUES (?, ?, ?)",
                (watch["id"], deal["id"], time.time()),
            )
            if ok:
                sent += 1
        if fresh:
            db.execute(
                "UPDATE watchlists SET last_notified_at = ? WHERE id = ?",
                (time.time(), watch["id"]),
            )
    return {"alerts_sent": sent, "watchlists": len(watchlists)}


def user_channel_ids(user_id: int) -> List[int]:
    rows = db.query(
        "SELECT c.tg_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
        "WHERE uc.user_id = ? AND uc.enabled = 1",
        (user_id,),
    )
    return [int(r["tg_id"]) for r in rows]


# --- the cycle ---------------------------------------------------------
async def run_cycle(reason: str = "scheduled") -> Dict[str, Any]:
    if _cycle_lock.locked():
        return {"status": "already_running"}

    async with _cycle_lock:
        started = time.time()
        _state["running"] = True
        totals = {"fetched": 0, "new": 0, "merged": 0, "skipped": 0, "channels": 0}
        try:
            channels = db.rows_to_dicts(
                db.query("SELECT * FROM channels WHERE active = 1 ORDER BY last_fetched_at ASC")
            )
            for channel in channels:
                result = await ingest_channel(channel)
                totals["channels"] += 1
                for key in ("fetched", "new", "merged", "skipped"):
                    totals[key] += result[key]
                await asyncio.sleep(0.4)  # be polite to Telegram between channels

            expired = store.expire_stale()
            store.rescore_all()
            liveness = await verify_links(settings.liveness_batch)
            alerts = await run_watchlist_alerts()
            store.purge_ancient()

            flushed = {"updated": 0, "appended": 0}
            if sheets.is_enabled():
                flushed = await asyncio.get_event_loop().run_in_executor(None, sheets.flush_deals)

            result = {
                **totals,
                "expired": expired,
                "liveness": liveness,
                "alerts": alerts,
                "sheets": flushed,
                "reason": reason,
            }
            _state["last_result"] = result
            _state["last_error"] = None
            _state["cycles"] += 1
            return result
        except Exception as exc:  # noqa: BLE001
            log.exception("Ingest cycle failed")
            _state["last_error"] = f"{type(exc).__name__}: {exc}"
            return {"error": _state["last_error"]}
        finally:
            _state["running"] = False
            _state["last_run"] = time.time()
            _state["last_duration"] = round(time.time() - started, 2)


async def scheduler_loop() -> None:
    """Background poller. Started on app startup, cancelled on shutdown."""
    await asyncio.sleep(15)  # let the app finish booting first
    while True:
        try:
            has_channels = db.query_one("SELECT COUNT(*) AS c FROM channels WHERE active = 1")
            if has_channels and has_channels["c"]:
                result = await run_cycle("scheduled")
                log.info("Ingest cycle: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Scheduler iteration failed: %s", exc)
        await asyncio.sleep(settings.poll_interval_seconds)


async def keepalive_loop() -> None:
    """Ping our own /api/ping so Render's free tier doesn't idle out.

    A self-ping only helps while the instance is awake; pair it with an external
    cron (see README) to cover the window after it has already slept.
    """
    if not settings.keepalive_enabled or not settings.public_url:
        log.info("Keepalive disabled (set PUBLIC_URL to enable self-ping)")
        return
    url = f"{settings.public_url}/api/ping"
    await asyncio.sleep(60)
    while True:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(url)
                log.debug("Keepalive ping -> %s", resp.status_code)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.debug("Keepalive ping failed: %s", exc)
        await asyncio.sleep(settings.keepalive_seconds)
