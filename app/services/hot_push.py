"""Hot-deal pushes — the main reason anyone opens the app.

Each ingest cycle schedules PUSHES_PER_CYCLE (default 2) pushes, each fired
at a random moment inside the cycle: one somewhere in the first half, one in
the second — say 11 and 27 minutes in, the next cycle 6 and 31. Never a
fixed clock, so they don't read as a timer.

What gets pushed is decided when the push *fires*, not when it's scheduled:
the deal must still be live then, and the first push of a cycle is already in
push_log so the second never repeats it.

Ranking favours women's items (the women preset, whatever the admin's "show
on top" rule is), then fresh, genuinely discounted, lowest-ever deals. The
same product is never pushed twice within REPEAT_WINDOW, and nothing goes out
in quiet hours (PUSH_QUIET_HOURS, IST) — a 2am buzz is how apps get
uninstalled.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.hot_push")

IST = timezone(timedelta(hours=5, minutes=30))
FRESH_WINDOW = 12 * 3600        # candidates: live deals seen in the last 12h
REPEAT_WINDOW = 48 * 3600       # the same product never goes out twice within this
MIN_GAP_SECONDS = 5 * 60        # two pushes never land closer than this
WOMEN_BONUS = 30.0              # on the 0-100 deal score: women's items lead
NEW_BONUS = 12.0                # posted in the cycle that scheduled this push
LOWEST_BONUS = 10.0             # lowest price we've recorded

_pending: Set[asyncio.Task] = set()
_plan: List[Dict[str, Any]] = []


# --------------------------------------------------------------- timing

def is_quiet(now: Optional[datetime] = None) -> bool:
    """Inside PUSH_QUIET_HOURS ("23-8" = 11pm to 8am IST)? Empty = never quiet."""
    spec = settings.push_quiet_hours
    if not spec or "-" not in spec:
        return False
    try:
        start, end = (int(x) % 24 for x in spec.split("-", 1))
    except ValueError:
        return False
    hour = (now or datetime.now(IST)).hour
    if start == end:
        return False
    return (hour >= start or hour < end) if start > end else (start <= hour < end)


def random_delays(n: int, window: float) -> List[float]:
    """n moments spread across `window` seconds: one random point per equal
    slice, kept off the slice edges so two pushes never bunch together."""
    if n <= 0:
        return []
    window = max(float(window), 120.0)
    slice_len = window / n
    delays = []
    for i in range(n):
        lo = i * slice_len + slice_len * 0.15
        hi = (i + 1) * slice_len - slice_len * 0.10
        delays.append(max(60.0, random.uniform(lo, max(lo, hi))))
    return delays


# --------------------------------------------------------------- choosing

def _rank(deal: Dict[str, Any], women: bool, fresh_ids: Set[str]) -> float:
    rank = float(deal.get("score") or 0)
    if women:
        rank += WOMEN_BONUS
    if deal.get("id") in fresh_ids:
        rank += NEW_BONUS
    if deal.get("is_lowest"):
        rank += LOWEST_BONUS
    rank += min(float(deal.get("discount_pct") or 0), 80.0) / 8.0
    return rank


def candidates(fresh_ids: Iterable[str] = (), limit: int = 20) -> List[Tuple[Dict[str, Any], bool, float]]:
    """Live, photographed, priced deals not pushed recently — best first.

    Returns (deal, is_women, rank) tuples.
    """
    from . import priority

    now = time.time()
    fresh = set(fresh_ids or ())
    rows = db.query(
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND COALESCE(image_url, '') != '' "
        "AND COALESCE(price, 0) > 0 AND last_seen_at > ? AND score >= ? ORDER BY score DESC LIMIT 300",
        (now, now - FRESH_WINDOW, settings.broadcast_min_score),
    )
    recent = {r["product_key"] for r in db.query(
        "SELECT product_key FROM push_log WHERE sent_at > ?", (now - REPEAT_WINDOW,))}
    ranked = []
    seen_keys: Set[str] = set()
    for row in rows:
        deal = db.row_to_dict(row) or {}
        key = deal.get("product_key") or deal.get("id")
        if key in recent or key in seen_keys:
            continue
        seen_keys.add(key)
        women = priority.is_women(deal)
        ranked.append((deal, women, _rank(deal, women, fresh)))
    ranked.sort(key=lambda t: t[2], reverse=True)
    return ranked[:limit]


# --------------------------------------------------------------- copy

def _money(value: Any) -> str:
    return f"₹{int(float(value)):,}" if value not in (None, "", 0) else ""


# Channel titles carry their own price and hype ("Loot : Kurta Set @ ₹649 (MRP
# ₹2,199)"). The push states those itself, so they're stripped from the name.
_PRICE_BITS = re.compile(
    r"\(?\s*(?:\b(?:mrp|was|at|for|only|just)\b|[@:\-–])?\s*(?:₹|\brs\b\.?|\binr\b)\s*[\d,]+(?:\.\d+)?\s*/?-?\s*\)?"
    r"|@\s*[\d,]+(?:\.\d+)?/?-?"
    r"|\+\s*\d*\s*(?:sc|supercoins?)\b",
    re.IGNORECASE,
)
_DANGLING = re.compile(r"\s+\b(?:under|at|for|from|starting|starts|only|just|@)\s*$", re.IGNORECASE)
_HYPE_PREFIX = re.compile(
    r"^(?:(?:\d{1,2}\s*%\s*off|loot|lut|grab|steal|fast|lowest|rush hour deal|deal|hot deal|mega deal|amazon|flipkart|"
    r"myntra|ajio|meesho|shopsy|nykaa)\s*[:|\-–]\s*)+",
    re.IGNORECASE,
)


def clean_title(title: str) -> str:
    text = " ".join((title or "").split())
    text = _HYPE_PREFIX.sub("", text)
    text = _PRICE_BITS.sub(" ", text)
    text = re.sub(r"\s*\((?:effective(?:ly)?|using [^)]*)\)?", "", text, flags=re.IGNORECASE)
    text = " ".join(text.split()).strip(" ,-|:.–")
    text = _DANGLING.sub("", text).strip(" ,-|:.–")
    return text or " ".join((title or "").split())


def _short(title: str, room: int) -> str:
    title = clean_title(title)
    return title if len(title) <= room else title[: room - 1].rstrip(" ,-|:") + "…"


_CLOSERS = [
    "Tap before it's gone ⏳",
    "Selling fast — grab yours 🛒",
    "Limited stock, don't sleep on it 👀",
    "Price like this won't last ⚡",
    "Found it first on DealRadar 💜",
]


def compose(deal: Dict[str, Any], women: bool) -> Tuple[str, str]:
    """An attractive, varied title + body: a random pick among the strongest
    two or three openers that fit this deal (lowest-ever, big discount,
    women's fashion/beauty), so a stream of pushes never reads like one
    repeated line."""
    disc = int(deal.get("discount_pct") or 0)
    category = deal.get("category") or ""
    title_room = 44

    heads = []
    if deal.get("is_lowest"):
        heads.append("📉 Lowest price ever")
    if disc >= 40:
        heads.append(f"🔥 {disc}% OFF")
    if women and category == "Women Fashion":
        heads += ["👗 Just dropped for you", "✨ Your next favourite"]
    elif women and category == "Beauty":
        heads += ["💄 Beauty steal", "✨ Glow-up deal"]
    elif women:
        heads += ["💖 Picked for you", "✨ Trending right now"]
    heads += ["⚡ Price crash", "🛍️ Hot right now"]
    head = random.choice(heads[:3])
    title = f"{head}: {_short(deal.get('title') or 'A deal worth a look', title_room)}"

    price, mrp = deal.get("price"), deal.get("mrp")
    bits = []
    if price:
        now_part = f"Now {_money(price)}"
        if mrp and float(mrp) > float(price):
            now_part += f" (was {_money(mrp)})"
        bits.append(now_part)
    if deal.get("store") and deal.get("store") != "unknown":
        bits.append(str(deal["store"]).title())
    hook = (deal.get("ai_hook") or "").strip()
    line = " · ".join(bits)
    body = f"{hook} — {line}" if hook and line else (hook or line)
    body = f"{body}. {random.choice(_CLOSERS)}" if body else random.choice(_CLOSERS)
    return title[:90], body[:160]


# --------------------------------------------------------------- sending

async def send_best(fresh_ids: Iterable[str] = (), force: bool = False, reason: str = "") -> Dict[str, Any]:
    """Pick the best eligible deal right now and push it to everyone.

    `force` (admin "send now") ignores quiet hours and the minimum gap.
    """
    from . import devices

    if not force and is_quiet():
        return {"status": "skipped", "why": "quiet hours"}
    last = float(db.get_meta("last_hot_push_at") or 0)
    if not force and time.time() - last < MIN_GAP_SECONDS:
        return {"status": "skipped", "why": "too soon after the last push"}
    ranked = candidates(fresh_ids, limit=1)
    if not ranked:
        return {"status": "skipped", "why": "no eligible deal (live, with photo, above the score bar, not pushed in 48h)"}
    deal, women, rank = ranked[0]
    title, body = compose(deal, women)
    report = await devices.broadcast(title, body, deal, kind="hot_deal")
    now = time.time()
    db.execute(
        "INSERT INTO push_log (product_key, deal_id, title, women, tokens, accepted, sent_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (deal.get("product_key") or deal["id"], deal["id"], title, int(women),
         report.get("tokens", 0), report.get("accepted", 0), now),
    )
    db.set_meta("last_hot_push_at", str(now))
    log.info("Hot push (%s): %r women=%s rank=%.1f -> %s", reason or "manual", title, women, rank, report)
    return {"status": "sent", "deal_id": deal["id"], "title": title, "body": body, "women": women, **report}


def _cancel_pending() -> None:
    for task in list(_pending):
        task.cancel()
    _pending.clear()
    _plan.clear()


async def _fire_later(delay: float, fresh_ids: List[str], slot: int) -> None:
    try:
        await asyncio.sleep(delay)
        result = await send_best(fresh_ids, reason=f"cycle push {slot + 1}")
        for p in _plan:
            if p["slot"] == slot:
                p["result"] = result.get("status")
                p["why"] = result.get("why", "")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed push must never take anything else down
        log.warning("Hot push slot %d failed: %s", slot + 1, exc)


def schedule_cycle(fresh_ids: Iterable[str], window_seconds: float) -> List[Dict[str, Any]]:
    """Called at the end of each ingest cycle: plan this window's pushes.

    Anything still pending from the previous window is cancelled first, so an
    admin shortening the cycle can never stack pushes up.
    """
    if not settings.broadcast_hot_deal_enabled or settings.pushes_per_cycle <= 0:
        return []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return []
    _cancel_pending()
    ids = list(fresh_ids or [])
    now = time.time()
    for slot, delay in enumerate(random_delays(settings.pushes_per_cycle, window_seconds)):
        task = loop.create_task(_fire_later(delay, ids, slot))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        _plan.append({"slot": slot, "fires_at": now + delay, "result": "pending", "why": ""})
    return [dict(p) for p in _plan]


def status() -> Dict[str, Any]:
    """For the admin panel: who can be reached, what's queued, what went out."""
    devices_total = db.query_one("SELECT COUNT(*) AS c FROM devices")["c"]
    with_token = db.query_one("SELECT COUNT(*) AS c FROM devices WHERE COALESCE(push_token, '') != ''")["c"]
    recent = [dict(r) for r in db.query(
        "SELECT deal_id, title, women, tokens, accepted, sent_at FROM push_log ORDER BY sent_at DESC LIMIT 10")]
    return {
        "enabled": settings.broadcast_hot_deal_enabled,
        "pushes_per_cycle": settings.pushes_per_cycle,
        "min_score": settings.broadcast_min_score,
        "quiet_hours": settings.push_quiet_hours,
        "quiet_now": is_quiet(),
        "devices": devices_total,
        "devices_with_push": with_token,
        "planned": [dict(p) for p in _plan],
        "recent": recent,
    }
