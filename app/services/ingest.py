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
import ipaddress
import json
import logging
import re
import socket
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urljoin, urlparse

import httpx

from .. import db
from ..config import settings
from . import parser, push, ratelimit, search, sheets, store, telegram

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


async def _resolve_reader(channel: Dict[str, Any]) -> Optional[int]:
    """Pick a user whose Telegram session can actually read this channel.

    The recorded source_user_id may be stale — most commonly right after a
    Render restart, where Sheets restored the channel and its tracking links
    but sessions can't be restored (they're a deliberate secret, never written
    to Sheets) and that user hasn't signed back in yet. Rather than stall the
    channel until that specific person returns, fall back to any other user
    who tracks it and already has a live session, and adopt them as the new
    source going forward.
    """
    source_user = channel.get("source_user_id")
    if source_user and await telegram.get_client(int(source_user)) is not None:
        return int(source_user)

    candidates = db.query(
        "SELECT uc.user_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
        "WHERE c.tg_id = ? AND uc.enabled = 1 AND uc.user_id != ?",
        (channel["tg_id"], source_user or 0),
    )
    for row in candidates:
        candidate_id = int(row["user_id"])
        if await telegram.get_client(candidate_id) is not None:
            db.execute(
                "UPDATE channels SET source_user_id = ? WHERE tg_id = ?",
                (candidate_id, channel["tg_id"]),
            )
            log.info(
                "Channel %s: switched reader to user %s (previous reader unavailable)",
                channel.get("title"), candidate_id,
            )
            return candidate_id
    return None


# --- step 1-3: fetch, parse, store -------------------------------------
def with_hidden_links(text: str, message: Any) -> str:
    """Append URLs that Telegram keeps outside the visible text.

    Deal channels mostly post "🛒 Buy Now" as a text link (the URL lives in a
    MessageEntityTextUrl) or as an inline button — plain `message.message`
    has no URL at all, which left ~12% of deals without a buy link.
    """
    urls: List[str] = []
    for entity in getattr(message, "entities", None) or []:
        url = getattr(entity, "url", None)
        if url:
            urls.append(url)
    for row in getattr(getattr(message, "reply_markup", None), "rows", None) or []:
        for button in getattr(row, "buttons", None) or []:
            url = getattr(button, "url", None)
            if url:
                urls.append(url)
    extra = [u for u in dict.fromkeys(urls) if u.startswith(("http://", "https://")) and u not in text]
    return f"{text}\n" + "\n".join(extra) if extra and text else text


async def ingest_channel(channel: Dict[str, Any]) -> Dict[str, int]:
    """Pull and store new deals from one channel."""
    result = {"fetched": 0, "new": 0, "merged": 0, "skipped": 0}
    source_user = await _resolve_reader(channel)
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
        text = with_hidden_links(message.message or getattr(message, "raw_text", "") or "", message)
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


MAX_PROBE_BYTES = 200_000   # enough to see the out-of-stock markers, not a whole page
MAX_REDIRECT_HOPS = 4


async def _resolve_is_safe(url: str) -> bool:
    """SSRF guard: reject URLs that resolve to a non-public address.

    Deal links come from channel posts we don't control — a malicious or
    compromised channel could point a domain at an internal address and have
    this server fetch it. This blocks the direct case (a domain resolving to
    a private/loopback/link-local/reserved address). It does not defend
    against DNS rebinding (resolve-safe, then reconnect-elsewhere), which
    would require pinning the connection to the resolved IP; that's more than
    a deal-liveness prober warrants — its worst case here is bad ranking
    data, not remote code execution.
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    try:
        loop = asyncio.get_event_loop()
        infos = await loop.getaddrinfo(parsed.hostname, None)
    except (socket.gaierror, UnicodeError, OSError):
        return False
    if not infos:
        return False
    for info in infos:
        raw_addr = info[4][0].split("%")[0]  # strip an IPv6 zone id if present
        try:
            addr = ipaddress.ip_address(raw_addr)
        except ValueError:
            return False
        if (
            addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified
        ):
            return False
    return True


_AVAILABILITY_RE = re.compile(r'"availability"\s*:\s*"(?:https?://schema\.org/)?([A-Za-z]+)"', re.I)
_LD_PRICE_RE = re.compile(r'"(?:price|lowPrice)"\s*:\s*"?([0-9][0-9,]*(?:\.[0-9]+)?)"?', re.I)
_BLOCK_MARKERS = ("captcha", "robot check", "are you a human", "access denied", "unusual traffic")


def read_product_page(html: str) -> Dict[str, Any]:
    """Stock + price from the page's schema.org product data (what search engines read).

    Returns {"stock": "in"|"out"|None, "price": float|None, "blocked": bool}.
    Structured data beats text matching: "out of stock" text also appears on
    in-stock pages (other sizes, related items).
    """
    low = html.lower()
    stock = None
    match = _AVAILABILITY_RE.search(html)
    if match:
        value = match.group(1).lower()
        if value in ("instock", "limitedavailability", "preorder", "onlineonly", "backorder"):
            stock = "in"
        elif value in ("outofstock", "soldout", "discontinued"):
            stock = "out"
    price = None
    price_match = _LD_PRICE_RE.search(html)
    if price_match:
        try:
            price = float(price_match.group(1).replace(",", "")) or None
        except ValueError:
            price = None
    blocked = stock is None and any(m in low for m in _BLOCK_MARKERS)
    return {"stock": stock, "price": price, "blocked": blocked}


def _set_flag(deal_id: str, flag: str, on: bool) -> None:
    row = db.query_one("SELECT flags FROM deals WHERE id = ?", (deal_id,))
    flags = set((db.row_to_dict(row) or {}).get("flags") or []) if row else set()
    new = (flags | {flag}) if on else (flags - {flag})
    if new != flags:
        db.execute("UPDATE deals SET flags = ?, dirty = 1 WHERE id = ?", (json.dumps(sorted(new)), deal_id))


# --- step 5: link liveness ---------------------------------------------
async def verify_links(batch: int = 40) -> Dict[str, int]:
    """Probe the least-recently-checked live deals; retire dead links.

    This is what lets deals outlive a fixed TTL when they're still in stock,
    and die early when they aren't.
    """
    if not settings.liveness_check_enabled:
        return {"checked": 0, "dead": 0}

    rows = db.query(
        "SELECT id, url, clean_url, product_key, store FROM deals WHERE status='live' AND url != '' "
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
    # Redirects are followed manually (not via follow_redirects=True) so each
    # hop can be re-validated by _resolve_is_safe before it's fetched.
    async with httpx.AsyncClient(follow_redirects=False, timeout=8.0, headers=headers) as client:

        async def probe(row) -> None:
            nonlocal checked, dead
            url = row["url"] or row["clean_url"]
            if not url:
                return

            for _ in range(MAX_REDIRECT_HOPS):
                if not await _resolve_is_safe(url):
                    return
                try:
                    # client.stream(), not client.get(): get() buffers the
                    # full body before we ever see it, which would make the
                    # MAX_PROBE_BYTES cap below pointless — the download
                    # already happened. stream() lets us stop mid-download.
                    async with client.stream("GET", url) as resp:
                        if (
                            resp.status_code in (301, 302, 303, 307, 308)
                            and resp.headers.get("location")
                        ):
                            url = urljoin(url, resp.headers["location"])
                            continue

                        checked += 1
                        original = row["url"] or row["clean_url"]
                        if url != original:
                            db.execute("UPDATE deals SET resolved_url = ? WHERE id = ?", (url, row["id"]))
                        if resp.status_code in (404, 410):
                            store.mark_dead(row["id"], "dead_link")
                            dead += 1
                            return
                        if resp.status_code in (403, 429, 503):
                            _set_flag(row["id"], "stock_unknown", True)
                            return
                        if resp.status_code < 400:
                            body = b""
                            async for chunk in resp.aiter_bytes():
                                body += chunk
                                if len(body) >= MAX_PROBE_BYTES:
                                    break
                            html = body.decode("utf-8", errors="ignore")
                            page = read_product_page(html)
                            if page["stock"] == "out" or (
                                page["stock"] is None and not page["blocked"]
                                and any(marker in html.lower() for marker in DEAD_MARKERS)
                            ):
                                store.mark_dead(row["id"], "out_of_stock")
                                dead += 1
                                return
                            _set_flag(row["id"], "stock_unknown", page["blocked"])
                            if page["stock"] == "in" and page["price"] and row["product_key"]:
                                # Every successful check adds a real price point, so
                                # our own history (and all-time lows) grows between posts.
                                store.record_price(row["product_key"], page["price"], row["store"] or "")
                            # Alive and still selling — extend past the base TTL.
                            db.execute(
                                "UPDATE deals SET last_seen_at = ?, "
                                "expires_at = MAX(expires_at, ?), dirty = 1 WHERE id = ?",
                                (time.time(), time.time() + 86400, row["id"]),
                            )
                        return
                except (httpx.HTTPError, asyncio.TimeoutError):
                    return  # a transient network error is not evidence the deal is dead
            # too many redirect hops — give up quietly, not evidence of anything

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


def _app_alert(query: str, deal: Dict[str, Any]) -> Dict[str, str]:
    name = (deal.get("title") or "").strip()
    if len(name) > 60:
        name = name[:59].rstrip() + "…"
    price = f"₹{int(deal['price']):,} " if deal.get("price") else ""
    body_bits = []
    if deal.get("store"):
        body_bits.append(str(deal["store"]).title())
    if deal.get("discount_pct"):
        body_bits.append(f"{deal['discount_pct']}% off")
    if deal.get("mrp") and deal.get("price"):
        body_bits.append(f"was ₹{int(deal['mrp']):,}")
    if deal.get("is_lowest"):
        body_bits.append("lowest price we've seen")
    if deal.get("coupon"):
        body_bits.append(f"code {deal['coupon']}")
    return {
        "title": f"🔔 \"{query}\": {price}{name}".strip(),
        "body": " · ".join(body_bits) or "New deal matching your alert",
        "url": f"/?deal={deal['id']}",
    }


async def run_watchlist_alerts(max_per_watchlist: int = 3) -> Dict[str, int]:
    watchlists = db.rows_to_dicts(db.query("SELECT * FROM watchlists WHERE notify = 1"))
    sent = 0
    app_sent = 0
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
            # Mark first so a failure below can never cause a repeat alert.
            db.execute(
                "INSERT OR REPLACE INTO notified (watchlist_id, deal_id, sent_at) VALUES (?, ?, ?)",
                (watch["id"], deal["id"], time.time()),
            )
            try:
                alert = _app_alert(watch.get("query") or "", deal)
                await push.notify(watch["user_id"], alert["title"], alert["body"], alert["url"], deal["id"])
                app_sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("App notification failed for watchlist %s: %s", watch["id"], exc)
            try:
                ok = await telegram.notify_user(watch["user_id"], _format_alert(deal))
            except Exception as exc:  # noqa: BLE001
                log.warning("Telegram alert failed for watchlist %s: %s", watch["id"], exc)
                ok = False
            if ok:
                sent += 1
        if fresh:
            db.execute(
                "UPDATE watchlists SET last_notified_at = ? WHERE id = ?",
                (time.time(), watch["id"]),
            )
    return {"alerts_sent": sent, "app_notifications": app_sent, "watchlists": len(watchlists)}


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
            from . import public_reader  # local: public_reader imports routers that import ingest
            await public_reader.maybe_sync_followed()
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
            purged_ids = store.purge_ancient()
            store.purge_housekeeping()
            push.prune_notifications()
            ratelimit.prune()

            flushed = {"updated": 0, "appended": 0}
            if sheets.is_enabled():
                loop = asyncio.get_event_loop()
                # Push every changed deal each cycle (batches of 400), so the
                # Sheet always holds everything fetched from Telegram.
                flushed = {"updated": 0, "appended": 0}
                for _ in range(25):
                    batch = await loop.run_in_executor(None, sheets.flush_deals)
                    flushed["updated"] += batch.get("updated", 0)
                    flushed["appended"] += batch.get("appended", 0)
                    if batch.get("skipped") or not (batch.get("updated") or batch.get("appended")):
                        break
                    # Each batch is ≤2 Google write calls; pacing keeps a big
                    # backlog under Sheets' 60 writes/minute quota.
                    await asyncio.sleep(1.5)
                # Channel watermarks move every cycle; without this a restart
                # would re-backfill instead of resuming from where it left off.
                await loop.run_in_executor(None, sheets.sync_channels)
                await loop.run_in_executor(None, sheets.flush_price_history)
                # The Sheet is the permanent archive of every deal ever seen:
                # the local purge only trims the fast SQLite cache, never Sheets.

            result = {
                **totals,
                "expired": expired,
                "purged": len(purged_ids),
                "purged_from_sheets": 0,
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
        started = time.time()
        try:
            from . import public_reader  # local: avoids an import cycle via routers
            await public_reader.maybe_sync_followed()
            has_channels = db.query_one("SELECT COUNT(*) AS c FROM channels WHERE active = 1")
            if has_channels and has_channels["c"]:
                result = await run_cycle("scheduled")
                log.info("Ingest cycle: %s", result)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Scheduler iteration failed: %s", exc)
        # Fixed cadence: a new cycle starts every POLL_INTERVAL_SECONDS (5 min),
        # however long the last one took.
        await asyncio.sleep(max(30, settings.poll_interval_seconds - (time.time() - started)))


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
