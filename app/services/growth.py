"""Promotion counters: which shared links and "get the app" links get opened, and where from.

First-party and anonymous, to match the privacy policy ("no analytics SDKs, no ad trackers"):
only totals are kept — per day, per event, per source label (e.g. "telegram", "share_app",
"web_banner") — never an IP address, device id, account or user agent. A user agent is looked at
once, in memory, to pick where /get sends the visitor (Android -> Play Store) and to skip link-preview
bots; it is not stored. Events:

  deal_open   a shared deal link (/d/<id>) opened by a person
  get_click   the "get the app" link (/get) opened by a person
  share       a share tapped in the app or on the site        (sent by the client)
  invite      "invite a friend" tapped in the app             (sent by the client)

Installs themselves are counted by Play Console: /get sends Android visitors to the store with a
referrer (utm_source = the label), so installs show up there by source.

Counts live in memory and are written to the `meta` table (mirrored to MongoDB like other admin
settings, so they survive a restart) every few minutes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.growth")

STATE_KEY = "growth_counts"
SERVER_EVENTS = ("deal_open", "get_click")
CLIENT_EVENTS = ("share", "invite")      # the only ones the app/site may report
EVENTS = SERVER_EVENTS + CLIENT_EVENTS
MAX_KEYS_PER_DAY = 200                   # (event, source) pairs a day; the rest fall into "other" (at most one such row per event)
KEEP_DAYS = 90
FLUSH_SECONDS = 5 * 60
IST = timezone(timedelta(hours=5, minutes=30))
BOT_MARKERS = ("bot", "crawler", "spider", "preview", "whatsapp", "telegram", "facebookexternalhit", "slurp",
               "discord", "slack", "linkedin", "pinterest", "embedly", "skypeuri", "headless")

_counts: Optional[Dict[str, Dict[str, int]]] = None
_dirty = False


def clean_src(raw: Any, default: str = "direct") -> str:
    """A source label: lowercase letters, digits, _ and - only, short."""
    return re.sub(r"[^a-z0-9_-]", "", str(raw or "").lower())[:24] or default


def is_bot(user_agent: str) -> bool:
    ua = (user_agent or "").lower()
    return not ua or any(m in ua for m in BOT_MARKERS)


def is_android(user_agent: str) -> bool:
    return "android" in (user_agent or "").lower()


def play_url(src: str) -> str:
    """The Play Store page, with the source as the install referrer (shows up in Play Console)."""
    referrer = quote(f"utm_source={clean_src(src)}&utm_medium=link&utm_campaign=promo", safe="")
    base = settings.play_store_url or f"https://play.google.com/store/apps/details?id={settings.play_store_package}"
    return f"{base}{'&' if '?' in base else '?'}referrer={referrer}"


def _today(now: Optional[float] = None) -> str:
    return datetime.fromtimestamp(now or time.time(), IST).strftime("%Y-%m-%d")


def _load() -> Dict[str, Dict[str, int]]:
    global _counts
    if _counts is not None:
        return _counts
    try:
        parsed = json.loads(db.get_meta(STATE_KEY) or "{}")
        if not isinstance(parsed, dict):
            parsed = {}
    except Exception as exc:  # noqa: BLE001 — no database yet, or junk: start empty, don't cache it
        log.debug("Growth counts unreadable: %s", exc)
        return {}
    _counts = {d: {k: int(v) for k, v in day.items() if isinstance(v, (int, float))}
               for d, day in parsed.items() if isinstance(day, dict)}
    return _counts


def reset_cache() -> None:
    """Forget the in-memory copy (tests; after a restore from MongoDB)."""
    global _counts, _dirty
    _counts, _dirty = None, False


def record(event: str, src: str = "direct", now: Optional[float] = None) -> bool:
    """Count one event. Never raises; unknown events are ignored."""
    global _dirty
    if event not in EVENTS:
        return False
    try:
        counts = _load()
        day = counts.setdefault(_today(now), {})
        key = f"{event}|{clean_src(src)}"
        if key not in day and len(day) >= MAX_KEYS_PER_DAY:
            key = f"{event}|other"
        day[key] = day.get(key, 0) + 1
        _dirty = True
        return True
    except Exception as exc:  # noqa: BLE001 — a counter is never worth an error
        log.debug("Growth count failed: %s", exc)
        return False


def flush(force: bool = False) -> None:
    """Write the counts to the database (and MongoDB) if anything changed."""
    global _dirty
    if _counts is None or not (_dirty or force):
        return
    cutoff = _today(time.time() - KEEP_DAYS * 86400)
    for day in [d for d in _counts if d < cutoff]:
        del _counts[day]
    blob = json.dumps(_counts)
    try:
        db.set_meta(STATE_KEY, blob)
        _dirty = False
    except Exception as exc:  # noqa: BLE001
        log.warning("Couldn't save growth counts: %s", exc)
        return
    try:
        from . import mongo_store
        if mongo_store.is_enabled():
            mongo_store.save_setting(STATE_KEY, blob)
    except Exception as exc:  # noqa: BLE001 — the local copy is saved
        log.warning("Couldn't mirror growth counts to MongoDB: %s", exc)


def summary(days: int = 30, now: Optional[float] = None) -> Dict[str, Any]:
    """Totals for the admin panel: per event, per (event, source), and per day."""
    counts = _load()
    since = _today((now or time.time()) - (days - 1) * 86400)
    totals = {e: 0 for e in EVENTS}
    by_src: Dict[str, Dict[str, int]] = {}
    series: List[Dict[str, Any]] = []
    for day in sorted(d for d in counts if d >= since):
        row: Dict[str, Any] = {"day": day, **{e: 0 for e in EVENTS}}
        for key, n in counts[day].items():
            event, _, src = key.partition("|")
            if event not in totals:
                continue
            totals[event] += n
            row[event] += n
            by_src.setdefault(src, {e: 0 for e in EVENTS})[event] += n
        series.append(row)
    sources = [{"src": s, **v, "total": sum(v.values())} for s, v in by_src.items()]
    sources.sort(key=lambda x: (-x["get_click"] - x["deal_open"], -x["total"], x["src"]))
    return {"days": days, "totals": totals, "sources": sources[:40], "daily": series[-14:],
            "play_store": play_url("example"), "note": "Totals only: no IP, device, account or user agent is stored."}


async def loop() -> None:
    """Started from app/main.py: save the counts every few minutes."""
    while True:
        try:
            await asyncio.sleep(FLUSH_SECONDS)
            flush()
        except asyncio.CancelledError:
            flush()
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Growth flush failed: %s", exc)
