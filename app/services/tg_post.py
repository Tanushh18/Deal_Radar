"""Post hot deals to your own Telegram channel, through a bot.

Called the moment a deal is saved (live.py, seconds after the source channel
posts it) and, as a safety net, from the poll cycle for anything fresh the
live listener missed. A product is posted once: again only after
TG_REPOST_COOLDOWN_HOURS, or sooner if its price dropped further.

"Hot" means any of: a new all-time low, a big discount, or several channels
posting the same product. A deal flagged with a fake MRP never qualifies.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

import httpx

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.tg_post")

API = "https://api.telegram.org"
CAPTION_LIMIT = 1024          # Telegram's photo caption limit
PRICE_DROP_REPOST = 0.97      # re-post inside the cooldown if >=3% cheaper
_lock = asyncio.Lock()        # one post at a time: keeps dedupe + hourly cap exact

SCHEMA = """CREATE TABLE IF NOT EXISTS tg_posts (
    product_key TEXT PRIMARY KEY,
    deal_id     TEXT,
    price       REAL,
    posted_at   REAL,
    message_id  INTEGER
)"""
_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        db.execute(SCHEMA)
        db.execute("CREATE INDEX IF NOT EXISTS idx_tg_posts_at ON tg_posts(posted_at)")
        _schema_ready = True


def hot_reason(deal: Dict[str, Any]) -> Optional[str]:
    """Why this deal is worth posting, or None."""
    flags = set(deal.get("flags") or [])
    if flags & {"suspicious_mrp", "not_a_deal", "dead_link", "out_of_stock"}:
        return None
    if deal.get("status") not in (None, "live") or not deal.get("price"):
        return None
    if not (deal.get("resolved_url") or deal.get("clean_url") or deal.get("url")):
        return None
    if deal.get("is_lowest"):
        return "lowest"
    if int(deal.get("discount_pct") or 0) >= settings.tg_hot_min_discount:
        return "discount"
    if int(deal.get("repost_count") or 1) >= settings.tg_hot_min_reposts:
        return "trending"
    return None


def _inr(value: Any) -> str:
    return f"₹{int(round(float(value))):,}"


def format_post(deal: Dict[str, Any], reason: str) -> str:
    """HTML caption for the channel post."""
    title = html.escape((deal.get("title") or "Deal")[:200])
    head = {"lowest": "📉 LOWEST PRICE EVER", "discount": f"🔥 {int(deal.get('discount_pct') or 0)}% OFF",
            "trending": f"📣 TRENDING · {int(deal.get('repost_count') or 1)} channels"}[reason]
    lines = [f"<b>{head}</b>", "", f"<b>{title}</b>", ""]
    price = f"💰 <b>{_inr(deal['price'])}</b>"
    if deal.get("mrp") and float(deal["mrp"]) > float(deal["price"]):
        price += f"  <s>{_inr(deal['mrp'])}</s>"
        if deal.get("discount_pct"):
            price += f"  ({int(deal['discount_pct'])}% off)"
    lines.append(price)
    if deal.get("is_lowest") and reason != "lowest":
        lines.append("📉 Lowest price we've recorded")
    if deal.get("coupon"):
        lines.append(f"🏷 Code: <code>{html.escape(str(deal['coupon'])[:40])}</code>")
    if deal.get("store"):
        lines.append(f"🛒 {html.escape(str(deal['store']).title())}")
    if deal.get("ai_hook"):
        lines += ["", f"<i>{html.escape(str(deal['ai_hook'])[:200])}</i>"]
    text = "\n".join(lines)
    return text[:CAPTION_LIMIT]


def _buttons(deal: Dict[str, Any]) -> Dict[str, Any]:
    buy = deal.get("resolved_url") or deal.get("clean_url") or deal.get("url")
    row = [{"text": "🛒 Buy now", "url": buy}]
    if settings.public_url:
        row.append({"text": "📈 Price history", "url": f"{settings.public_url}/?deal={deal['id']}"})
    return {"inline_keyboard": [row]}


def _should_post(deal: Dict[str, Any], now: float) -> bool:
    last = db.query_one("SELECT price, posted_at FROM tg_posts WHERE product_key = ?", (deal["product_key"],))
    if last:
        cooled = now - float(last["posted_at"] or 0) >= settings.tg_repost_cooldown_hours * 3600
        cheaper = last["price"] and float(deal["price"]) <= float(last["price"]) * PRICE_DROP_REPOST
        if not (cooled or cheaper):
            return False
    recent = db.query_one("SELECT COUNT(*) AS c FROM tg_posts WHERE posted_at > ?", (now - 3600,))["c"]
    if recent >= settings.tg_max_posts_per_hour:
        log.info("Telegram hourly cap (%d) reached — skipping %s", settings.tg_max_posts_per_hour, deal["id"])
        return False
    return True


async def _send(method: str, data: Dict[str, Any], files: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    url = f"{API}/bot{settings.tg_bot_token}/{method}"
    async with httpx.AsyncClient(timeout=20.0) as client:
        for attempt in range(2):
            resp = await client.post(url, data=data, files=files) if files else await client.post(url, json=data)
            body = resp.json()
            if body.get("ok"):
                return body["result"]
            retry = (body.get("parameters") or {}).get("retry_after")
            if resp.status_code == 429 and retry and attempt == 0:
                await asyncio.sleep(min(float(retry), 30))
                continue
            raise RuntimeError(f"Telegram {method} failed: {body.get('description')}")
    raise RuntimeError(f"Telegram {method} failed")


PhotoLoader = Callable[[], Awaitable[Optional[bytes]]]


async def maybe_publish(deal: Dict[str, Any], photo: Optional[PhotoLoader] = None) -> bool:
    """Post `deal` to the channel if it's hot and not already posted. Never raises."""
    if not settings.tg_post_configured or not deal or not deal.get("product_key"):
        return False
    reason = hot_reason(deal)
    if not reason:
        return False
    try:
        async with _lock:
            _ensure_schema()
            now = time.time()
            if not _should_post(deal, now):
                return False
            caption = format_post(deal, reason)
            markup = _buttons(deal)
            result = None
            image = deal.get("image_url") or ""
            base = {"chat_id": settings.tg_post_channel, "caption": caption, "parse_mode": "HTML"}
            try:
                if image.startswith(("http://", "https://")):
                    result = await _send("sendPhoto", {**base, "photo": image, "reply_markup": markup})
                elif photo is not None:
                    raw = await photo()
                    if raw:
                        # multipart: reply_markup has to be a JSON string here
                        result = await _send("sendPhoto", {**base, "reply_markup": json.dumps(markup)},
                                             files={"photo": ("deal.jpg", raw, "image/jpeg")})
            except Exception as exc:  # noqa: BLE001 - a bad image must not cost the post
                log.info("Photo post failed for %s (%s) — sending as text", deal["id"], exc)
                result = None
            if result is None:
                result = await _send("sendMessage", {
                    "chat_id": settings.tg_post_channel, "text": caption, "parse_mode": "HTML",
                    "reply_markup": markup, "disable_web_page_preview": False,
                })
            db.execute(
                "INSERT INTO tg_posts (product_key, deal_id, price, posted_at, message_id) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(product_key) DO UPDATE SET deal_id = excluded.deal_id, price = excluded.price, "
                "posted_at = excluded.posted_at, message_id = excluded.message_id",
                (deal["product_key"], deal["id"], float(deal["price"]), now, (result or {}).get("message_id")),
            )
            log.info("Posted to Telegram (%s): %s", reason, (deal.get("title") or "")[:60])
            return True
    except Exception as exc:  # noqa: BLE001 - posting must never break ingestion
        log.warning("Telegram post failed for %s: %s", deal.get("id"), exc)
        return False


def prune(days: int = 30) -> None:
    """Forget posts older than any cooldown could care about."""
    if settings.tg_post_configured:
        _ensure_schema()
        db.execute("DELETE FROM tg_posts WHERE posted_at < ?", (time.time() - days * 86400,))
