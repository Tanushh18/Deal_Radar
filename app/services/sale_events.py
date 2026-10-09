"""Upcoming store sale events (Big Billion Days, Great Indian Festival, End of
Reason Sale…) — a curated, admin-editable calendar with an AI-written blurb.

Not scraped or guessed from channel chatter: dates for these mega-sales are
announced by the stores days to weeks ahead, and channel posts about them are
full of rumors and stale reposts — unreliable as a source of truth. Instead
this ships with a seed calendar of the recurring annual sales (approximate
dates, clearly marked, matching each store's usual month), editable from
/admin as real dates are confirmed.

Stored as one JSON blob in meta (mirrored to MongoDB like priority.py's
rule), not a table — a few dozen rows at most, read far more than written.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import httpx

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.sale_events")

META_KEY = "sale_events"
HEADS_UP_DAYS_BEFORE = 2   # how many days ahead of start the Telegram heads-up posts
_cache: Optional[List[Dict[str, Any]]] = None

# The yearly calendar of recurring Indian sales on stores this app tracks.
# Month/day follow each sale's usual slot in past years — genuinely
# approximate, and superseded as soon as an admin sets the real dates for a
# given year. Nothing here is stored: each read works out the next occurrence,
# so the calendar rolls forward on its own (BBD 2027 appears once BBD 2026 ends).
# `key` is permanent — it ties admin edits to a sale across renames.
_CALENDAR: List[Dict[str, Any]] = [
    {"key": "amazon-republic", "name": "Amazon Great Republic Day Sale", "store": "amazon", "month": 1, "day": 18, "duration_days": 5},
    {"key": "flipkart-republic", "name": "Flipkart Republic Day Sale", "store": "flipkart", "month": 1, "day": 13, "duration_days": 6},
    {"key": "flipkart-bsd-march", "name": "Flipkart Big Saving Days", "store": "flipkart", "month": 3, "day": 11, "duration_days": 4},
    {"key": "amazon-summer", "name": "Amazon Great Summer Sale", "store": "amazon", "month": 5, "day": 1, "duration_days": 5},
    {"key": "flipkart-summer", "name": "Flipkart Summer Big Saving Days", "store": "flipkart", "month": 5, "day": 2, "duration_days": 5},
    {"key": "myntra-eors-summer", "name": "Myntra End of Reason Sale (Summer)", "store": "myntra", "month": 6, "day": 1, "duration_days": 5},
    {"key": "amazon-prime-day", "name": "Amazon Prime Day", "store": "amazon", "month": 7, "day": 12, "duration_days": 3},
    {"key": "amazon-freedom", "name": "Amazon Great Freedom Festival", "store": "amazon", "month": 8, "day": 6, "duration_days": 5},
    {"key": "ajio-bbs", "name": "Ajio Big Bold Sale", "store": "ajio", "month": 8, "day": 15, "duration_days": 6},
    {"key": "myntra-bff", "name": "Myntra Big Fashion Festival", "store": "myntra", "month": 9, "day": 27, "duration_days": 5},
    {"key": "meesho-mbs", "name": "Meesho Mega Blockbuster Sale", "store": "meesho", "month": 9, "day": 27, "duration_days": 6},
    {"key": "amazon-gif", "name": "Amazon Great Indian Festival", "store": "amazon", "month": 10, "day": 3, "duration_days": 6},
    {"key": "flipkart-bbd", "name": "Flipkart Big Billion Days", "store": "flipkart", "month": 10, "day": 3, "duration_days": 6},
    {"key": "flipkart-diwali", "name": "Flipkart Big Diwali Sale", "store": "flipkart", "month": 10, "day": 22, "duration_days": 6},
    {"key": "nykaa-pink-friday", "name": "Nykaa Pink Friday Sale", "store": "nykaa", "month": 11, "day": 20, "duration_days": 8},
    {"key": "myntra-eors", "name": "Myntra End of Reason Sale", "store": "myntra", "month": 12, "day": 27, "duration_days": 5},
]
_BY_KEY = {t["key"]: t for t in _CALENDAR}

# Dates the stores have officially announced, keyed by (template key, year):
# (month, day, duration_days), starting at midnight IST. Beat the approximate
# slot above; the end is still an estimate until the store publishes one.
# Amazon Great Indian Festival 2026 starts 8 Oct; Flipkart Big Billion Days
# goes live for everyone 9 Oct 12 AM (Plus/Black early access from 8 Oct).
_CONFIRMED: Dict[tuple, tuple] = {
    ("amazon-gif", 2026): (10, 8, 10),
    ("flipkart-bbd", 2026): (10, 9, 9),
}
# Kept for older callers/tests that counted the seed.
_SEED_TEMPLATE = _CALENDAR

ADMIN_WINDOW_DAYS = 360   # admin sees (and can pre-confirm) about a year ahead — under 365 so a sale never shows twice
PUBLIC_MIN_DAYS = 90      # the app always shows at least this far ahead…
                          # …and otherwise everything up to 31 December


def _clean(event: Dict[str, Any]) -> Dict[str, Any]:
    name = str(event.get("name") or "").strip()[:80]
    store = str(event.get("store") or "").strip().lower()[:30]
    starts_at = float(event.get("starts_at") or 0) or None
    ends_at = float(event.get("ends_at") or 0) or None
    out = {
        "id": str(event.get("id") or uuid.uuid4().hex[:12]),
        "name": name,
        "store": store,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "approximate": bool(event.get("approximate", True)),  # False once an admin confirms real dates
        "hype": str(event.get("hype") or "")[:280],
        "hype_generated_at": float(event.get("hype_generated_at") or 0) or None,
        "heads_up_posted": bool(event.get("heads_up_posted", False)),
        "updated_at": time.time(),
    }
    # Calendar sales carry which template and year they are; custom ones don't.
    if event.get("template") in _BY_KEY and event.get("year"):
        out["template"] = event["template"]
        out["year"] = int(event["year"])
    if event.get("hidden"):
        out["hidden"] = True
    return out


def _occurrence_id(key: str, year: int) -> str:
    return f"{key}-{year}"


def _parse_occurrence_id(event_id: str) -> Optional[tuple]:
    """'flipkart-bbd-2026' -> ('flipkart-bbd', 2026); None for custom ids."""
    key, _, year = str(event_id).rpartition("-")
    if key in _BY_KEY and year.isdigit() and len(year) == 4:
        return key, int(year)
    return None


def _occurrence(tpl: Dict[str, Any], year: int) -> Dict[str, Any]:
    import calendar
    from datetime import datetime, timedelta, timezone

    month, day, duration = _CONFIRMED.get((tpl["key"], year), (tpl["month"], tpl["day"], tpl["duration_days"]))
    day = min(day, calendar.monthrange(year, month)[1])
    tz = timezone(timedelta(hours=5, minutes=30)) if (tpl["key"], year) in _CONFIRMED else timezone.utc
    start = datetime(year, month, day, tzinfo=tz)
    end = start + timedelta(days=duration)
    return _clean({
        "id": _occurrence_id(tpl["key"], year), "template": tpl["key"], "year": year,
        "name": tpl["name"], "store": tpl["store"],
        "starts_at": start.timestamp(), "ends_at": end.timestamp(), "approximate": True,
    })


def _seed(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """The calendar's next occurrences from `now` (no admin edits applied)."""
    return [e for e in _materialize([], now or time.time(), ADMIN_WINDOW_DAYS) if not e.get("hidden")]


def _migrate(stored: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Rows saved before the calendar had keys: a row whose name matches a
    calendar sale becomes that sale's edit for its year, so dates an admin
    already confirmed are kept rather than shown twice."""
    by_name = {t["name"].lower(): t for t in _CALENDAR}
    taken = {e["id"] for e in stored}
    out = []
    for e in stored:
        tpl = None if e.get("template") else by_name.get((e.get("name") or "").lower())
        if tpl and e.get("starts_at"):
            from datetime import datetime, timezone
            year = datetime.fromtimestamp(e["starts_at"], tz=timezone.utc).year
            occ = _occurrence_id(tpl["key"], year)
            if occ not in taken:
                taken.add(occ)
                e = _clean({**e, "id": occ, "template": tpl["key"], "year": year})
        out.append(e)
    return out


def _load() -> List[Dict[str, Any]]:
    """Stored rows only: admin edits of calendar sales, hidden ones, and custom sales."""
    global _cache
    if _cache is None:
        _cache = []
        raw = db.get_meta(META_KEY)
        if raw:
            try:
                _cache = _migrate([_clean(e) for e in json.loads(raw)])
            except (ValueError, TypeError):
                _cache = []
    return _cache


def _materialize(stored: List[Dict[str, Any]], now: float, window_days: float) -> List[Dict[str, Any]]:
    """Calendar occurrences (with any admin edit for that year applied) that
    haven't ended and start within the window, plus every custom sale."""
    from datetime import datetime, timezone

    edits = {e["id"]: e for e in stored if e.get("template")}
    horizon = now + window_days * 86400
    this_year = datetime.fromtimestamp(now, tz=timezone.utc).year
    out = []
    for tpl in _CALENDAR:
        for year in (this_year - 1, this_year, this_year + 1):
            occ_id = _occurrence_id(tpl["key"], year)
            e = edits.get(occ_id) or _occurrence(tpl, year)
            end = e["ends_at"] or e["starts_at"] or 0
            if end >= now and (e["starts_at"] or 0) <= horizon:
                out.append(e)
    out += [e for e in stored if not e.get("template")]
    return out


def _persist(events: List[Dict[str, Any]]) -> None:
    global _cache
    _cache = events
    db.set_meta(META_KEY, json.dumps(events))
    if settings.mongo_configured:
        from . import mongo_store
        if mongo_store.is_enabled():
            mongo_store.save_setting(META_KEY, json.dumps(events))


def reset_cache() -> None:
    global _cache
    _cache = None


def _public_window_days(now: float) -> float:
    from datetime import datetime, timezone
    today = datetime.fromtimestamp(now, tz=timezone.utc)
    year_end = datetime(today.year, 12, 31, 23, 59, tzinfo=timezone.utc).timestamp()
    return max((year_end - now) / 86400, PUBLIC_MIN_DAYS)


def list_all(upcoming_only: bool = False, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """upcoming_only=True is the app/website view: live and upcoming sales
    through December. Otherwise the admin view: a full year ahead, plus past
    custom sales, including hidden ones (so they can be restored)."""
    now = now or time.time()
    window = _public_window_days(now) if upcoming_only else ADMIN_WINDOW_DAYS
    events = _materialize(_load(), now, window)
    if upcoming_only:
        events = [e for e in events if not e.get("hidden") and (e["ends_at"] or e["starts_at"] or 0) >= now]
    return sorted(events, key=lambda e: e["starts_at"] or 0)


def _find(event_id: str) -> Optional[Dict[str, Any]]:
    for e in _load():
        if e["id"] == event_id:
            return e
    parsed = _parse_occurrence_id(event_id)
    if parsed:
        return _occurrence(_BY_KEY[parsed[0]], parsed[1])
    return None


def upsert(event: Dict[str, Any]) -> Dict[str, Any]:
    events = _load()
    if not event.get("id") and event.get("starts_at"):
        # "Adding" a sale the calendar already has (same name) means new dates
        # for that year's occurrence, not a second copy of it.
        tpl = next((t for t in _CALENDAR if t["name"].lower() == str(event.get("name") or "").strip().lower()), None)
        if tpl:
            from datetime import datetime, timezone
            year = datetime.fromtimestamp(float(event["starts_at"]), tz=timezone.utc).year
            event = {**event, "id": _occurrence_id(tpl["key"], year)}
    parsed = _parse_occurrence_id(event.get("id") or "")
    if parsed:  # editing a calendar sale: store it as that year's edit
        event = {**event, "template": parsed[0], "year": parsed[1]}
    cleaned = _clean(event)
    old = _find(cleaned["id"])
    if old:
        # A real date/name/store change invalidates the cached AI blurb and
        # the "already posted" flag — a materially different event needs a
        # fresh heads-up, not silence because the old id happened to post once.
        if (cleaned["starts_at"], cleaned["name"], cleaned["store"]) != (old["starts_at"], old["name"], old["store"]):
            cleaned["hype"], cleaned["hype_generated_at"] = "", None
            cleaned["heads_up_posted"] = False
    idx = next((i for i, e in enumerate(events) if e["id"] == cleaned["id"]), None)
    events = events + [cleaned] if idx is None else [*events[:idx], cleaned, *events[idx + 1:]]
    _persist(events)
    return cleaned


def delete(event_id: str) -> bool:
    """Custom sales are removed; a calendar sale is hidden for that year only
    (it comes back next year on its own)."""
    events = _load()
    parsed = _parse_occurrence_id(event_id)
    if parsed:
        base = _find(event_id)
        upsert({**base, "hidden": True})
        return True
    kept = [e for e in events if e["id"] != event_id]
    if len(kept) == len(events):
        return False
    _persist(kept)
    return True


# --- AI hype copy -------------------------------------------------------

_HYPE_SYSTEM_PROMPT = (
    "You write a one-sentence, exciting-but-honest hype line for an upcoming Indian "
    "e-commerce sale event, for a deals app/channel. Under 25 words. No emoji at the "
    "start. Mention the store by name naturally. No fake urgency about stock; the "
    "urgency is just the sale's dates. Reply with ONLY the sentence, no quotes."
)


async def generate_hype(event: Dict[str, Any]) -> Optional[str]:
    """Best-effort AI blurb — reuses ai_enrich's Groq client/budget. None on any failure."""
    if not settings.ai_enrich_enabled:
        return None
    from . import ai_enrich

    if not ai_enrich._budget_ok():  # same daily cap as deal enrichment; this is a tiny extra draw on it
        return None
    from datetime import datetime, timezone
    when = ""
    if event.get("starts_at"):
        when = datetime.fromtimestamp(event["starts_at"], tz=timezone.utc).strftime("%d %B")
    prompt = f"Event: {event['name']} on {event.get('store', '')}. Starts around {when or 'soon'}."
    try:
        async with httpx.AsyncClient(timeout=ai_enrich.REQUEST_TIMEOUT) as client:
            resp = await client.post(
                ai_enrich.GROQ_URL,
                headers={"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"},
                json={"model": settings.groq_model, "max_tokens": 60, "temperature": 0.6,
                      "messages": [{"role": "system", "content": _HYPE_SYSTEM_PROMPT},
                                   {"role": "user", "content": prompt}]},
            )
        if resp.status_code != 200:
            return None
        body = resp.json()
        text = (body["choices"][0]["message"]["content"] or "").strip().strip('"')
        usage = body.get("usage") or {}
        ai_enrich._record_usage(int(usage.get("total_tokens") or 0))
        return text[:280] or None
    except Exception as exc:  # noqa: BLE001 — a missing hype line is never worth breaking anything
        log.info("Sale-event hype generation failed for %s: %s", event.get("name"), exc)
        return None


async def ensure_hype(event: Dict[str, Any]) -> Dict[str, Any]:
    """Fill in the AI blurb if this event doesn't have one yet, and persist it."""
    if event.get("hype"):
        return event
    hype = await generate_hype(event)
    if hype:
        event = upsert({**event, "hype": hype, "hype_generated_at": time.time()})
    return event


# --- the Telegram heads-up ------------------------------------------------

def due_for_heads_up(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Events starting within HEADS_UP_DAYS_BEFORE that haven't been announced yet."""
    now = now or time.time()
    window = now + HEADS_UP_DAYS_BEFORE * 86400
    return [e for e in list_all(upcoming_only=True, now=now) if e["starts_at"] and not e["heads_up_posted"]
            and now <= e["starts_at"] <= window]


async def post_heads_up(event: Dict[str, Any]) -> bool:
    """Announce an upcoming sale on the Telegram channel. Never raises."""
    from . import tg_post
    if not settings.tg_post_configured or tg_post.posting_paused():
        return False
    event = await ensure_hype(event)
    try:
        from datetime import datetime, timezone
        start = datetime.fromtimestamp(event["starts_at"], tz=timezone.utc)
        days = max(0, round((event["starts_at"] - time.time()) / 86400))
        when = f"in {days} day{'s' if days != 1 else ''}" if days else "today"
        lines = [f"<b>📅 Coming up: {tg_post.html.escape(event['name'])}</b>", "",
                 f"🗓 Starts {when} ({start.strftime('%d %B')})"]
        if event.get("store"):
            lines.append(f"🛒 {tg_post.html.escape(event['store'].title())}")
        if event.get("approximate"):
            lines.append("<i>Date is approximate — official dates confirm closer to the sale.</i>")
        if event.get("hype"):
            lines += ["", tg_post.html.escape(event["hype"])]
        lines.append("")
        lines.append("We'll post the best verified deals here the moment they go live.")
        await tg_post._send("sendMessage", {
            "chat_id": settings.tg_post_channel, "text": "\n".join(lines), "parse_mode": "HTML",
        })
    except Exception as exc:  # noqa: BLE001
        log.warning("Sale-event heads-up post failed for %s: %s", event.get("name"), exc)
        return False
    upsert({**event, "heads_up_posted": True})
    return True


async def run_heads_up_check() -> int:
    """Called once per ingest cycle. Posts (and best-effort AI-blurbs) any due events."""
    posted = 0
    for event in due_for_heads_up():
        if await post_heads_up(event):
            posted += 1
    return posted
