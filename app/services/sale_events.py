"""Upcoming store sale events (Big Billion Days, Great Indian Festival, End of
Reason Sale…) — a curated, admin-editable calendar with an AI-written blurb.

Not scraped or guessed from channel chatter: dates for these mega-sales are
announced by the stores days to weeks ahead, and channel posts about them are
full of rumors and stale reposts — unreliable as a source of truth. Instead
this ships with a seed calendar of the recurring annual sales (approximate
dates, clearly marked, matching each store's usual month), editable from
/admin as real dates are confirmed.

Stored as one JSON blob in meta (mirrored to the Sheet like priority.py's
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

# Recurring annual sales on the stores this app already tracks. Month/day are
# the usual pattern in past years — genuinely approximate, always superseded
# once the real date is set in /admin. year is filled in relative to "now"
# each time the seed is used, so this never ships already stale.
_SEED_TEMPLATE: List[Dict[str, Any]] = [
    {"name": "Amazon Great Indian Festival", "store": "amazon", "month": 10, "day": 3, "duration_days": 6},
    {"name": "Flipkart Big Billion Days", "store": "flipkart", "month": 10, "day": 3, "duration_days": 6},
    {"name": "Myntra Big Fashion Festival", "store": "myntra", "month": 9, "day": 27, "duration_days": 5},
    {"name": "Flipkart Big Saving Days", "store": "flipkart", "month": 3, "day": 11, "duration_days": 4},
    {"name": "Amazon Great Republic Day Sale", "store": "amazon", "month": 1, "day": 18, "duration_days": 5},
    {"name": "Myntra End of Reason Sale", "store": "myntra", "month": 12, "day": 27, "duration_days": 5},
    {"name": "Ajio Big Bold Sale", "store": "ajio", "month": 8, "day": 15, "duration_days": 6},
]


def _clean(event: Dict[str, Any]) -> Dict[str, Any]:
    name = str(event.get("name") or "").strip()[:80]
    store = str(event.get("store") or "").strip().lower()[:30]
    starts_at = float(event.get("starts_at") or 0) or None
    ends_at = float(event.get("ends_at") or 0) or None
    return {
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


def _seed(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Next occurrence of each recurring sale from `now`, so a freshly-deployed
    server always starts with a genuinely *upcoming* calendar, not a stale one."""
    import calendar
    from datetime import datetime, timedelta, timezone

    now = now or time.time()
    today = datetime.fromtimestamp(now, tz=timezone.utc)
    out = []
    for tpl in _SEED_TEMPLATE:
        for year in (today.year, today.year + 1):
            day = min(tpl["day"], calendar.monthrange(year, tpl["month"])[1])
            start = datetime(year, tpl["month"], day, tzinfo=timezone.utc)
            if start.timestamp() >= now - 86400:  # allow "starting today"
                break
        end = start + timedelta(days=tpl["duration_days"])
        out.append(_clean({
            "name": tpl["name"], "store": tpl["store"],
            "starts_at": start.timestamp(), "ends_at": end.timestamp(),
            "approximate": True,
        }))
    return out


def _load() -> List[Dict[str, Any]]:
    global _cache
    if _cache is None:
        raw = db.get_meta(META_KEY)
        if raw:
            try:
                _cache = [_clean(e) for e in json.loads(raw)]
            except (ValueError, TypeError):
                _cache = None
        if _cache is None:
            _cache = _seed()
    return _cache


def _persist(events: List[Dict[str, Any]]) -> None:
    global _cache
    _cache = events
    db.set_meta(META_KEY, json.dumps(events))
    if settings.sheets_configured:
        from . import sheets
        if sheets.is_enabled():
            sheets.save_setting(META_KEY, json.dumps(events))


def reset_cache() -> None:
    global _cache
    _cache = None


def list_all(upcoming_only: bool = False, now: Optional[float] = None) -> List[Dict[str, Any]]:
    events = sorted(_load(), key=lambda e: e["starts_at"] or 0)
    if upcoming_only:
        now = now or time.time()
        events = [e for e in events if (e["ends_at"] or e["starts_at"] or 0) >= now]
    return events


def upsert(event: Dict[str, Any]) -> Dict[str, Any]:
    events = _load()
    cleaned = _clean(event)
    idx = next((i for i, e in enumerate(events) if e["id"] == cleaned["id"]), None)
    if idx is None:
        events = events + [cleaned]
    else:
        # A real date/name/store change invalidates the cached AI blurb and
        # the "already posted" flag — a materially different event needs a
        # fresh heads-up, not silence because the old id happened to post once.
        old = events[idx]
        if (cleaned["starts_at"], cleaned["name"], cleaned["store"]) != (old["starts_at"], old["name"], old["store"]):
            cleaned["hype"], cleaned["hype_generated_at"] = "", None
            cleaned["heads_up_posted"] = False
        events = [*events[:idx], cleaned, *events[idx + 1:]]
    _persist(events)
    return cleaned


def delete(event_id: str) -> bool:
    events = _load()
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
    return [e for e in list_all() if e["starts_at"] and not e["heads_up_posted"]
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
