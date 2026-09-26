"""Hot-deal pushes — the main reason anyone opens the app.

Manual notification mode (notify_auto.mode() == "manual"): each ingest cycle
schedules PUSHES_PER_CYCLE (default 2) pushes, each fired at a random moment
inside the window: one somewhere in the first half, one in the second — say
11 and 27 minutes in, the next time 6 and 31. Never a fixed clock, so they
don't read as a timer.

The window is at least an hour, and nothing new is scheduled while earlier
pushes are still pending. Poll intervals can be as short as a minute now; if
each cycle cancelled and re-planned (as it once did), pushes planned >= 60 s
out would be cancelled by the next 60 s cycle and never fire at all. So with
short cycles PUSHES_PER_CYCLE effectively means "per hour at most".

In auto mode notify_auto.py paces everything from its own loop and
schedule_cycle does nothing; it still calls send_best() for its slots.

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

MIN_WINDOW_SECONDS = 3600       # a push window is never shorter than this
FRESH_ID_TTL = 3600             # new-deal ids keep their NEW_BONUS this long

_pending: Set[asyncio.Task] = set()
_plan: List[Dict[str, Any]] = []
_fresh: Dict[str, float] = {}   # deal id -> when a cycle reported it new


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


def candidates(fresh_ids: Iterable[str] = (), limit: int = 20,
               now: Optional[float] = None) -> List[Tuple[Dict[str, Any], bool, float]]:
    """Live, photographed, priced deals not pushed recently — best first.

    Returns (deal, is_women, rank) tuples.
    """
    from . import priority

    now = now or time.time()
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

def log_push(kind: str, deal: Optional[Dict[str, Any]], title: str, women: bool, report: Dict[str, Any],
             reach: int, now: float) -> None:
    """One push_log row per send. product_key stays empty for sends that
    aren't "this deal to everyone" (nudges), so they never block the deal
    from a later hot push via REPEAT_WINDOW."""
    deal = deal or {}
    key = (deal.get("product_key") or deal.get("id") or "") if kind != "nudge" else ""
    db.execute(
        "INSERT INTO push_log (product_key, deal_id, title, women, tokens, accepted, sent_at, kind, reach) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (key, deal.get("id") or "", title, int(women), report.get("tokens", 0), report.get("accepted", 0),
         now, kind, int(reach)),
    )


async def send_best(fresh_ids: Iterable[str] = (), force: bool = False, reason: str = "",
                    kind: Optional[str] = None, now: Optional[float] = None,
                    check_guards: Optional[bool] = None) -> Dict[str, Any]:
    """Pick the best eligible deal right now and push it to everyone.

    `force` (admin "send now") ignores quiet hours and the minimum gap and
    goes out as kind "broadcast" (instant on every phone). `kind` overrides
    the kind; `check_guards=False` skips quiet hours / gap for a caller that
    has applied its own (notify_auto), without making the push instant.
    """
    from . import devices

    now = now or time.time()
    guards = (not force) if check_guards is None else check_guards
    if guards and is_quiet(datetime.fromtimestamp(now, IST)):
        return {"status": "skipped", "why": "quiet hours"}
    last = float(db.get_meta("last_hot_push_at") or 0)
    min_gap = max(MIN_GAP_SECONDS, settings.hot_push_min_gap_minutes * 60)
    if guards and now - last < min_gap:
        return {"status": "skipped", "why": "too soon after the last push"}
    ranked = candidates(fresh_ids, limit=1, now=now)
    if not ranked:
        return {"status": "skipped", "why": "no eligible deal (live, with photo, above the score bar, not pushed in 48h)"}
    deal, women, rank = ranked[0]
    title, body = compose(deal, women)
    # An admin pressing "send now" means now, on every phone — including the ones
    # that otherwise pick their own moment (they treat "broadcast" as instant).
    kind = kind or ("broadcast" if force else "hot_deal")
    report = await devices.broadcast(title, body, deal, kind=kind)
    log_push(kind, deal, title, women, report, report.get("devices", 0), now)
    db.set_meta("last_hot_push_at", str(now))
    log.info("Hot push (%s): %r women=%s rank=%.1f -> %s", reason or "manual", title, women, rank, report)
    return {"status": "sent", "deal_id": deal["id"], "title": title, "body": body, "women": women, **report}


def _cancel_pending() -> None:
    for task in list(_pending):
        task.cancel()
    _pending.clear()
    _plan.clear()


def _note_fresh(ids: Iterable[str], now: float) -> List[str]:
    """Remember new-deal ids for an hour, so a push planned in an earlier
    cycle still favours what later cycles found."""
    for deal_id in ids or ():
        _fresh[deal_id] = now
    for deal_id in [k for k, at in _fresh.items() if now - at > FRESH_ID_TTL]:
        del _fresh[deal_id]
    return list(_fresh)


async def _fire_later(delay: float, slot: int) -> None:
    try:
        await asyncio.sleep(delay)
        result = await send_best(_note_fresh((), time.time()), reason=f"cycle push {slot + 1}")
        for p in _plan:
            if p["slot"] == slot:
                p["result"] = result.get("status")
                p["why"] = result.get("why", "")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — a failed push must never take anything else down
        log.warning("Hot push slot %d failed: %s", slot + 1, exc)


def schedule_cycle(fresh_ids: Iterable[str], window_seconds: float) -> List[Dict[str, Any]]:
    """Called at the end of each ingest cycle: plan the next window's pushes
    (manual notification mode only — auto mode paces itself).

    Pending pushes are never cancelled and never doubled: while any is still
    waiting, this only notes the new deals (so the pending push can favour
    them) and returns the current plan. The window is at least an hour, so
    a 1-minute poll interval still gets its pushes out.
    """
    from . import notify_auto

    if not settings.broadcast_hot_deal_enabled or settings.pushes_per_cycle <= 0:
        return []
    if notify_auto.mode() == "auto":
        return []
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return []
    now = time.time()
    _note_fresh(fresh_ids, now)
    if any(not t.done() for t in _pending):
        return [dict(p) for p in _plan]
    _plan.clear()
    window = max(float(window_seconds or 0), MIN_WINDOW_SECONDS)
    for slot, delay in enumerate(random_delays(settings.pushes_per_cycle, window)):
        task = loop.create_task(_fire_later(delay, slot))
        _pending.add(task)
        task.add_done_callback(_pending.discard)
        _plan.append({"slot": slot, "fires_at": now + delay, "result": "pending", "why": ""})
    return [dict(p) for p in _plan]


def status() -> Dict[str, Any]:
    """For the admin panel: who can be reached, what's queued, what went out."""
    from . import devices
    recent = [dict(r) for r in db.query(
        "SELECT deal_id, title, women, tokens, accepted, sent_at, kind FROM push_log "
        "WHERE COALESCE(kind, 'hot_deal') != 'nudge' ORDER BY sent_at DESC LIMIT 10")]
    return {
        "enabled": settings.broadcast_hot_deal_enabled,
        "pushes_per_cycle": settings.pushes_per_cycle,
        "min_score": settings.broadcast_min_score,
        "quiet_hours": settings.push_quiet_hours,
        "quiet_now": is_quiet(),
        **devices.active_counts(),
        "planned": [dict(p) for p in _plan],
        "recent": recent,
    }
