"""Real-time listener: handle a deal the moment a source channel posts it.

The poll cycle (ingest.run_cycle) reads every channel in turn every
POLL_INTERVAL_SECONDS, and with many channels plus link checks and Sheets
writes a new post can wait minutes before it's seen. This instead registers a
Telethon NewMessage handler on the reader account, so Telegram pushes each
post to us as it's published: it's parsed, saved (the website and app see it
immediately too) and, if hot, posted to your channel (tg_post) — seconds end
to end. The poll cycle keeps running as the safety net for anything missed
while the connection was down.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

from telethon import events

from .. import db
from ..config import settings
from . import links, parser, price_store, quality, store, telegram, tg_post

log = logging.getLogger("dealradar.live")

SUPERVISE_SECONDS = 30
_bound_client: Any = None
_stats = {"received": 0, "deals": 0, "posted": 0, "last_at": 0.0}


def status() -> Dict[str, Any]:
    return {"enabled": settings.live_listener, "connected": _bound_client is not None, **_stats}


def _is_output_channel(channel: Dict[str, Any], tg_id: int) -> bool:
    """Never re-ingest our own posts if the reader account follows the output channel."""
    target = settings.tg_post_channel.lstrip("@").lower()
    if not target:
        return False
    return target == (channel.get("username") or "").lower() or target in (str(tg_id), f"-100{tg_id}")


def _photo_loader(client: Any, message: Any):
    async def load() -> Optional[bytes]:
        if not getattr(message, "photo", None):
            return None
        return await client.download_media(message, file=bytes)
    return load


async def handle_message(client: Any, message: Any) -> Optional[str]:
    """Parse, save and maybe post one channel message. Returns the save outcome."""
    from .ingest import with_hidden_links  # local: ingest imports this module's siblings

    tg_id = getattr(getattr(message, "peer_id", None), "channel_id", None)
    if not tg_id:
        return None
    channel = db.query_one("SELECT * FROM channels WHERE tg_id = ? AND active = 1", (tg_id,))
    if not channel or _is_output_channel(channel, tg_id):
        return None
    _stats["received"] += 1
    text = with_hidden_links(message.message or getattr(message, "raw_text", "") or "", message)
    if not text:
        return None
    deal = parser.parse_message(
        text,
        channel_id=int(tg_id),
        channel_title=channel.get("title") or "",
        message_id=int(message.id),
        posted_at=message.date.timestamp() if message.date else time.time(),
        ttl_hours=settings.deal_ttl_hours,
    )
    if deal is None:
        return None
    # Same steps as the poll cycle (ingest.ingest_channel), so a post is
    # judged identically whichever path sees it first.
    gate_on = settings.quality_filter
    if gate_on and quality.text_reason(deal):
        return "filtered"
    try:
        await links.resolve_deals([deal])
    except Exception as exc:  # noqa: BLE001 — resolution only improves dedup
        log.info("Shortlink resolution failed: %s", exc)
    if getattr(message, "photo", None):
        deal["image_url"] = store.telegram_image_url(deal["id"], int(tg_id), int(message.id))

    # Full Turso history first, so the all-time-low and fake-MRP checks are right.
    await price_store.prefetch([deal["product_key"]] if deal.get("product_key") else [])
    outcome = store.save_deal(deal, gate=quality.reject_reason if gate_on else None)
    if outcome == "filtered":
        return outcome
    store.remember_resolved_url(deal)
    _stats["deals"] += 1
    _stats["last_at"] = time.time()

    saved = db.query_one("SELECT * FROM deals WHERE product_key = ? ORDER BY last_seen_at DESC LIMIT 1",
                         (deal["product_key"],)) if deal.get("product_key") else None
    if saved and await tg_post.maybe_publish(db.row_to_dict(saved) or {}, _photo_loader(client, message)):
        _stats["posted"] += 1

    if outcome == "new" and settings.ai_enrich_enabled:
        from . import ai_enrich
        asyncio.create_task(ai_enrich.enrich_new_deals([deal["id"]]))
    return outcome


async def _on_new_message(event) -> None:
    try:
        await handle_message(event.client, event.message)
    except Exception as exc:  # noqa: BLE001 - one bad post must never kill the listener
        log.warning("Live message failed: %s", exc)


async def run() -> None:
    """Keep the handler bound to the reader's live client, rebinding if it's replaced."""
    global _bound_client
    if not settings.live_listener:
        return
    await asyncio.sleep(20)  # let public_reader.bootstrap register the reader first
    while True:
        try:
            reader = db.get_meta("reader_user_id")
            client = await telegram.get_client(int(reader)) if reader else None
            if client is not None and client is not _bound_client:
                client.add_event_handler(_on_new_message, events.NewMessage(incoming=True))
                _bound_client = client
                try:
                    await client.catch_up()  # ask Telegram to start pushing channel updates
                except Exception as exc:  # noqa: BLE001
                    log.info("catch_up skipped: %s", exc)
                log.info("Live listener bound to the reader account — new posts arrive in real time")
            elif client is None and _bound_client is not None:
                _bound_client = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Live listener supervise failed: %s", exc)
        await asyncio.sleep(SUPERVISE_SECONDS)
