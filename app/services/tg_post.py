"""Post verified, genuinely good deals to your own Telegram channel, via a bot.

Called the moment a deal is saved (live.py, seconds after the source channel
posts it) and, as a safety net, from the poll cycle for anything fresh the
live listener missed.

Every deal goes through assess() before anything is sent, in three stages:

  1. Hard rejects — anything that looks fake or vague never gets further:
     a fake-MRP flag, "starting from ₹X" / "up to N% off" / "buy 3" posts, an
     unknown store, a claimed discount above 95%, a dead or out-of-stock link.
  2. Is it actually good? Thresholds are lower for women's deals, which the
     channel is focused on, and highest for everything else. "Good" can
     mean a big MRP discount, but also a real drop against our own recorded
     price history, a new all-time low, or several channels posting the same
     product — MRPs are easy to inflate, those aren't.
  3. Verified right now — the product page is opened: dead or out of stock
     is rejected (and the deal retired), a page price more than 5% above the
     posted one means the deal is over, and poor ratings are rejected. If the
     store blocks the check, the deal is only posted when other channels or
     our own history corroborate it.

A product is posted once; again only after TG_REPOST_COOLDOWN_HOURS, or
sooner if its price drops a further 3%. Posts are capped per hour, and
non-women deals only get TG_OTHER_MAX_PER_HOUR of those slots.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import statistics
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx

from .. import db
from ..config import settings
from . import taxonomy

log = logging.getLogger("dealradar.tg_post")

API = "https://api.telegram.org"
CAPTION_LIMIT = 1024          # Telegram's photo caption limit
PRICE_DROP_REPOST = 0.97      # re-post inside the cooldown if >=3% cheaper
MAX_BELIEVABLE_DISCOUNT = 95  # beyond this the MRP is invented
PAGE_PRICE_TOLERANCE = 1.05   # page may be up to 5% above the post (rounding, coupons)
MIN_RATING = 3.8              # when the page shows a rating with enough reviews
MIN_RATING_REVIEWS = 10
_lock = asyncio.Lock()        # one post at a time: keeps dedupe + hourly cap exact

VAGUE_FLAGS = {"price_from", "upto_discount"}          # "from ₹99", "up to 80% off"
FAKE_FLAGS = {"suspicious_mrp", "not_a_deal", "dead_link", "out_of_stock"}
TRUSTED_STORES = set(taxonomy.STORE_DOMAINS)
_WOMEN_WORDS = re.compile(r"\b(women|womens|women's|woman|ladies|lady|girls|female|her)\b", re.I)

SCHEMA = """CREATE TABLE IF NOT EXISTS tg_posts (
    product_key TEXT PRIMARY KEY,
    deal_id     TEXT,
    price       REAL,
    posted_at   REAL,
    message_id  INTEGER,
    audience    TEXT DEFAULT 'other'
)"""
_schema_ready = False


def _ensure_schema() -> None:
    global _schema_ready
    if not _schema_ready:
        db.execute(SCHEMA)
        cols = {r["name"] for r in db.query("PRAGMA table_info(tg_posts)")}
        if "audience" not in cols:
            db.execute("ALTER TABLE tg_posts ADD COLUMN audience TEXT DEFAULT 'other'")
        db.execute("CREATE INDEX IF NOT EXISTS idx_tg_posts_at ON tg_posts(posted_at)")
        _schema_ready = True


# --- who is it for -----------------------------------------------------------

def audience(deal: Dict[str, Any]) -> str:
    """'women_acc' (jewellery, handbags, watches…), 'women', or 'other'."""
    category, sub = deal.get("category") or "", deal.get("subcategory") or ""
    if category == "Women Accessories":
        return "women_acc"
    if category == "Women Fashion" or sub == "Heels" or (category == "Beauty" and sub == "Makeup"):
        return "women"
    if _WOMEN_WORDS.search(deal.get("title") or ""):
        return "women"
    return "other"


# --- the quality gate --------------------------------------------------------

@dataclass
class Verdict:
    ok: bool
    why: str                              # reject reason, or the headline reason
    audience: str = "other"
    crazy: bool = False
    verified: str = ""                    # "live" | "corroborated" | "history"
    price: Optional[float] = None         # price to show (page-checked when possible)
    rating: Optional[float] = None
    reviews: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    store_pending: bool = False


def _store_of(url: Optional[str]) -> str:
    from . import parser
    return parser.detect_store(url) if url else ""


def _trusted_store(store: Optional[str]) -> bool:
    return (store or "") in TRUSTED_STORES


def _history(deal: Dict[str, Any]) -> Dict[str, Any]:
    """This product's own recorded prices (Turso history prefetched by ingest)."""
    from . import price_store  # local: price_store -> turso_backup -> db only
    points = price_store.cached_points(deal.get("product_key") or "")
    price = float(deal["price"])
    others = [p for _, p in points if p and abs(p - price) > 0.01]
    out = {"points": len(points), "median": None, "drop": 0.0}
    if len(others) >= 3:
        median = statistics.median(others)
        out["median"] = median
        out["drop"] = max(0.0, 1 - price / median) if median else 0.0
    return out


def _thresholds(aud: str) -> Dict[str, float]:
    if aud in ("women_acc", "women"):
        return {"discount": settings.tg_women_min_discount, "drop": 0.20, "reposts": 2}
    return {"discount": settings.tg_hot_min_discount, "drop": 0.25, "reposts": settings.tg_hot_min_reposts}


def precheck(deal: Dict[str, Any]) -> Verdict:
    """Stages 1–2: everything that needs no network. Cheap, run first."""
    aud = audience(deal)
    flags = set(deal.get("flags") or [])
    if flags & FAKE_FLAGS:
        return Verdict(False, "flagged: " + ",".join(sorted(flags & FAKE_FLAGS)), aud)
    if flags & VAGUE_FLAGS or any(f.startswith("min_buy_") for f in flags):
        return Verdict(False, "vague sale post (from / up-to / buy-N price)", aud)
    if deal.get("status") not in (None, "live"):
        return Verdict(False, f"status {deal.get('status')}", aud)
    if not deal.get("price") or float(deal["price"]) <= 0:
        return Verdict(False, "no price", aud)
    if not (deal.get("resolved_url") or deal.get("clean_url") or deal.get("url")):
        return Verdict(False, "no link", aud)
    store_ok = _trusted_store(deal.get("store")) or _trusted_store(_store_of(deal.get("resolved_url")))
    discount = int(deal.get("discount_pct") or 0)
    if discount > MAX_BELIEVABLE_DISCOUNT:
        return Verdict(False, f"{discount}% off is not believable", aud)

    t = _thresholds(aud)
    hist = _history(deal)
    reposts = int(deal.get("repost_count") or 1)
    lowest = bool(deal.get("is_lowest")) and hist["points"] >= 3
    reasons = []
    if lowest:
        reasons.append("lowest")
    if hist["drop"] >= t["drop"]:
        reasons.append("drop")
    if discount >= t["discount"]:
        reasons.append("discount")
    if reposts >= t["reposts"]:
        reasons.append("trending")
    if not reasons:
        return Verdict(False, "not good enough", aud)
    v = Verdict(True, reasons[0], aud, price=float(deal["price"]))
    v.notes = reasons
    # A shortlink (cutt.ly, bit.ly…) hides the store; verify() opens it and
    # rejects it unless it lands on a store we know.
    v.store_pending = not store_ok
    # Crazy = a women's accessory at a throwaway price, or far below its own history.
    v.crazy = aud == "women_acc" and (
        (float(deal["price"]) <= settings.tg_crazy_max_price and discount >= 70) or hist["drop"] >= 0.40)
    v.verified = "history" if (lowest or hist["drop"] >= t["drop"]) else ""
    return v


async def verify(deal: Dict[str, Any], v: Verdict) -> Verdict:
    """Stage 3: open the product page right now."""
    from . import store
    url = deal.get("resolved_url") or deal.get("clean_url") or deal.get("url")
    page = await probe_product(url)
    if v.store_pending and not _trusted_store(_store_of(page.get("url"))):
        return Verdict(False, f"link doesn't lead to a known store ({page.get('url')})", v.audience)
    v.rating, v.reviews = page.get("rating"), page.get("reviews")
    if page["status"] == "dead":
        store.mark_dead(deal["id"], "out_of_stock")
        return Verdict(False, "dead or out of stock right now", v.audience)
    page_price = page.get("price")
    if page_price and float(page_price) > float(deal["price"]) * PAGE_PRICE_TOLERANCE:
        return Verdict(False, f"page says ₹{page_price:,.0f} — deal is over", v.audience)
    if v.rating is not None and (v.reviews or 0) >= MIN_RATING_REVIEWS and v.rating < MIN_RATING:
        return Verdict(False, f"poorly rated ({v.rating}★)", v.audience)
    if page["status"] == "live":
        v.verified = "live"
        if page_price and float(page_price) >= float(deal["price"]) * 0.5:
            v.price = min(float(deal["price"]), float(page_price))
        return v
    # The store wouldn't show us the page. Post only with independent proof.
    if int(deal.get("repost_count") or 1) >= 2:
        v.verified = v.verified or "corroborated"
        return v
    if v.verified == "history":
        return v
    return Verdict(False, "couldn't verify (store blocked the check, no corroboration)", v.audience)


async def probe_product(url: str) -> Dict[str, Any]:
    from . import ingest
    return await ingest.probe_product(url)


async def assess(deal: Dict[str, Any]) -> Verdict:
    v = precheck(deal)
    if not v.ok:
        return v
    return await verify(deal, v)


# --- the post ---------------------------------------------------------------

def _inr(value: Any) -> str:
    return f"₹{int(round(float(value))):,}"


def format_post(deal: Dict[str, Any], v: Verdict) -> str:
    """HTML caption for the channel post."""
    title = html.escape((deal.get("title") or "Deal")[:200])
    price = v.price or float(deal["price"])
    discount = int(deal.get("discount_pct") or 0)
    if deal.get("mrp") and float(deal["mrp"]) > price:
        discount = int(round((float(deal["mrp"]) - price) / float(deal["mrp"]) * 100))
    head = {
        "lowest": "📉 LOWEST PRICE EVER",
        "drop": "📉 PRICE CRASH",
        "discount": f"🔥 {discount}% OFF",
        "trending": f"📣 TRENDING · {int(deal.get('repost_count') or 1)} channels",
    }[v.why]
    if v.crazy:
        head = f"🤯 CRAZY DEAL · {head}"
    lines = [f"<b>{head}</b>"]
    if v.audience == "women_acc":
        lines.append(f"👜 Women's {html.escape((deal.get('subcategory') or 'accessories').lower())}")
    elif v.audience == "women":
        lines.append("👗 For her")
    lines += ["", f"<b>{title}</b>", ""]
    line = f"💰 <b>{_inr(price)}</b>"
    if deal.get("mrp") and float(deal["mrp"]) > price:
        line += f"  <s>{_inr(deal['mrp'])}</s>  ({discount}% off)"
    lines.append(line)
    if "lowest" in v.notes and v.why != "lowest":
        lines.append("📉 Lowest price we've recorded")
    if v.rating:
        stars = f"⭐ {v.rating:.1f}"
        if v.reviews:
            stars += f" ({v.reviews:,} ratings)"
        lines.append(stars)
    if deal.get("coupon"):
        lines.append(f"🏷 Code: <code>{html.escape(str(deal['coupon'])[:40])}</code>")
    if deal.get("store"):
        lines.append(f"🛒 {html.escape(str(deal['store']).title())}")
    lines.append({
        "live": "✅ Verified in stock just now",
        "corroborated": f"✅ Confirmed by {int(deal.get('repost_count') or 1)} channels",
        "history": "✅ Checked against our price history",
    }.get(v.verified, ""))
    if deal.get("ai_hook"):
        lines += ["", f"<i>{html.escape(str(deal['ai_hook'])[:200])}</i>"]
    return "\n".join(x for x in lines if x is not None)[:CAPTION_LIMIT]


def _buttons(deal: Dict[str, Any]) -> Dict[str, Any]:
    buy = deal.get("resolved_url") or deal.get("clean_url") or deal.get("url")
    row = [{"text": "🛒 Buy now", "url": buy}]
    if settings.public_url:
        row.append({"text": "📈 Price history", "url": f"{settings.public_url}/?deal={deal['id']}"})
    return {"inline_keyboard": [row]}


def _already_posted(deal: Dict[str, Any], now: float) -> bool:
    last = db.query_one("SELECT price, posted_at FROM tg_posts WHERE product_key = ?", (deal["product_key"],))
    if not last:
        return False
    cooled = now - float(last["posted_at"] or 0) >= settings.tg_repost_cooldown_hours * 3600
    cheaper = last["price"] and float(deal["price"]) <= float(last["price"]) * PRICE_DROP_REPOST
    return not (cooled or cheaper)


def _over_cap(aud: str, now: float) -> bool:
    hour_ago = now - 3600
    total = db.query_one("SELECT COUNT(*) AS c FROM tg_posts WHERE posted_at > ?", (hour_ago,))["c"]
    if total >= settings.tg_max_posts_per_hour:
        return True
    if aud == "other":
        others = db.query_one("SELECT COUNT(*) AS c FROM tg_posts WHERE posted_at > ? AND audience = 'other'",
                              (hour_ago,))["c"]
        return others >= settings.tg_other_max_per_hour
    return False


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
    """Post `deal` if it passes assess() and wasn't posted already. Never raises."""
    if not settings.tg_post_configured or not deal or not deal.get("product_key"):
        return False
    try:
        _ensure_schema()
        pre = precheck(deal)
        if not pre.ok:
            return False
        if _already_posted(deal, time.time()) or _over_cap(pre.audience, time.time()):
            return False
        v = await verify(deal, pre)  # the page check runs outside the lock
        if not v.ok:
            log.info("Not posting %s: %s", (deal.get("title") or "")[:50], v.why)
            return False
        async with _lock:
            now = time.time()
            if _already_posted(deal, now) or _over_cap(v.audience, now):
                return False
            caption = format_post(deal, v)
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
                "INSERT INTO tg_posts (product_key, deal_id, price, posted_at, message_id, audience) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(product_key) DO UPDATE SET deal_id = excluded.deal_id, "
                "price = excluded.price, posted_at = excluded.posted_at, message_id = excluded.message_id, "
                "audience = excluded.audience",
                (deal["product_key"], deal["id"], float(deal["price"]), now, (result or {}).get("message_id"),
                 v.audience),
            )
            log.info("Posted to Telegram (%s%s, %s, %s): %s", v.why, " crazy" if v.crazy else "",
                     v.audience, v.verified, (deal.get("title") or "")[:60])
            return True
    except Exception as exc:  # noqa: BLE001 - posting must never break ingestion
        log.warning("Telegram post failed for %s: %s", deal.get("id"), exc)
        return False


def prune(days: int = 30) -> None:
    """Forget posts older than any cooldown could care about."""
    if settings.tg_post_configured:
        _ensure_schema()
        db.execute("DELETE FROM tg_posts WHERE posted_at < ?", (time.time() - days * 86400,))
