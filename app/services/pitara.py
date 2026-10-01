"""Notification copy from the pitara (app/content/notification_pitara*.json).

Every notification the server writes itself — hot deals, crazy deals, nudges,
price drops, follows, digests, weekly picks, watchlist alerts — picks its
title/body here instead of having text hard-coded at each call site. Callers
keep their old text as a fallback: pick() returns None when nothing fits (no
eligible line, files missing or broken, or NOTIFICATION_PITARA_ENABLED=false),
and a notification must never fail to go out over copy.

A line is eligible only when everything it claims is true for this deal
(needs: price/mrp/discount/brand/store/lowest/endsSoon, min_discount, category,
time of day, day of week, and every context placeholder it uses). Lines meant
for the phone alone (personal) never run here. The same file is served to the
app by routers/pitara.py, so both sides speak with one voice.
"""
from __future__ import annotations

import hashlib
import json
import logging
import random
import re
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Deque, Dict, Iterable, List, NamedTuple, Optional, Tuple, Union

from ..config import settings

log = logging.getLogger(__name__)

CONTENT_DIR = Path(__file__).resolve().parent.parent / "content"
IST = timezone(timedelta(hours=5, minutes=30))
PLACEHOLDER = re.compile(r"\{(\w+)\}")
CONTEXT_KEYS = ("target", "value", "count", "query", "coupon", "sale", "when")
CATEGORY_ALIAS = {"Appliances": "Electronics"}
DEAL_KIND = "hot_deal"          # lines with no `kinds` are ordinary deal copy
CATEGORY_BOOST = 2.0            # a line written for this category beats a generic one
RECENT_PER_KIND = 12            # don't repeat any of the last N lines of a kind
ENDS_SOON_SECONDS = 6 * 3600
TITLE_MAX, BODY_MAX = 90, 160
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class Pick(NamedTuple):
    title: str
    body: str
    id: str


_cache: Optional[Tuple[List[Dict[str, Any]], str]] = None
_recent: Dict[str, Deque[str]] = {}


# --- loading -----------------------------------------------------------------

def _valid(t: Any) -> bool:
    return (isinstance(t, dict) and isinstance(t.get("id"), str) and isinstance(t.get("title"), str)
            and isinstance(t.get("body"), str) and t["title"] and t["body"])


def load(force: bool = False) -> Tuple[List[Dict[str, Any]], str]:
    """(templates, version). Version is a hash of the content, so the app can
    tell cheaply whether its cached copy is current. Never raises."""
    global _cache
    if _cache is not None and not force:
        return _cache
    templates: List[Dict[str, Any]] = []
    digest = hashlib.sha1()
    seen = set()
    try:
        files = sorted(CONTENT_DIR.glob("notification_pitara*.json"))
    except OSError as exc:
        log.warning("Pitara directory unreadable: %s", exc)
        files = []
    for path in files:
        try:
            raw = path.read_bytes()
            doc = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("Skipping pitara file %s: %s", path.name, exc)
            continue
        digest.update(path.name.encode() + raw)
        for t in doc.get("templates", []):
            if _valid(t) and t["id"] not in seen:
                seen.add(t["id"])
                templates.append(t)
    # Lines the model drafted and a person approved, and any line the admin switched off.
    # (Imported here: pitara_writer imports this module.)
    try:
        from . import pitara_writer
        off, extra, signature = pitara_writer.disabled_ids(), pitara_writer.approved_lines(), pitara_writer.signature()
    except Exception as exc:  # noqa: BLE001 — the shipped lines alone are always enough
        log.debug("Pitara drafts unavailable: %s", exc)
        off, extra, signature = set(), [], ""
    templates = [t for t in templates if t["id"] not in off]
    for t in extra:
        if _valid(t) and t["id"] not in seen and t["id"] not in off:
            seen.add(t["id"])
            templates.append(t)
    digest.update(signature.encode())
    _cache = (templates, digest.hexdigest()[:16])
    log.info("Pitara loaded: %d lines (version %s)", len(templates), _cache[1])
    return _cache


def reset() -> None:
    """Forget the loaded files and repeat memory (tests)."""
    global _cache
    _cache = None
    _recent.clear()


def version() -> str:
    return load()[1]


def enabled() -> bool:
    return bool(getattr(settings, "notification_pitara_enabled", True))


# --- eligibility ---------------------------------------------------------------

def _num(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _time_of_day(hour: int) -> str:
    if 5 <= hour < 12:
        return "morning"
    if 12 <= hour < 17:
        return "afternoon"
    if 17 <= hour < 21:
        return "evening"
    return "night"


def _has(need: str, deal: Dict[str, Any], now: float) -> bool:
    price, mrp = _num(deal.get("price")), _num(deal.get("mrp"))
    if need == "price":
        return price > 0
    if need == "mrp":
        return mrp > price > 0
    if need == "discount":
        return int(_num(deal.get("discount_pct"))) > 0
    if need == "brand":
        return bool(str(deal.get("brand") or "").strip())
    if need == "store":
        store = str(deal.get("store") or "").strip().lower()
        return bool(store) and store != "unknown"
    if need == "lowest":
        return bool(deal.get("is_lowest"))
    if need == "endsSoon":
        expires = _num(deal.get("expires_at"))
        return now < expires <= now + ENDS_SOON_SECONDS
    return False  # a need we don't know can't be promised


def _eligible(t: Dict[str, Any], kind: str, deal: Dict[str, Any], ctx: Dict[str, str], moods: Optional[Iterable[str]],
              now: float, category: str) -> bool:
    kinds = t.get("kinds") or []
    if (kind not in kinds) if kinds else (kind != DEAL_KIND):
        return False
    if t.get("personal"):
        return False
    if moods is not None and t.get("mood") not in moods:
        return False
    cat = t.get("category") or "Any"
    if cat != "Any" and cat != category:
        return False
    when = datetime.fromtimestamp(now, IST)
    if t.get("time") and _time_of_day(when.hour) not in t["time"]:
        return False
    if t.get("day") and t["day"] != ("weekend" if when.weekday() >= 5 else "weekday"):
        return False
    if t.get("weekdays") and (when.weekday() + 1) % 7 not in t["weekdays"]:
        return False
    if not all(_has(n, deal, now) for n in (t.get("needs") or [])):
        return False
    min_discount = t.get("min_discount")
    if min_discount and int(_num(deal.get("discount_pct"))) < int(min_discount):
        return False
    used = set(PLACEHOLDER.findall(t["title"] + " " + t["body"]))
    return all(ctx.get(k) for k in used & set(CONTEXT_KEYS))


# --- rendering ------------------------------------------------------------------

def _money(value: Any) -> str:
    return f"₹{int(_num(value)):,}"


def _fields(deal: Dict[str, Any], ctx: Dict[str, str], now: float) -> Dict[str, str]:
    from . import hot_push  # lazy: hot_push imports us

    price, mrp = _num(deal.get("price")), _num(deal.get("mrp"))
    store = str(deal.get("store") or "").strip()
    out = {
        "name": hot_push._short(deal.get("title") or "", 34) if deal.get("title") else "",
        "price": _money(price) if price > 0 else "",
        "mrp": _money(mrp) if mrp > price > 0 else "",
        "save": _money(mrp - price) if mrp > price > 0 else "",
        "discount": str(int(_num(deal.get("discount_pct")))) if _num(deal.get("discount_pct")) >= 1 else "",
        "store": store.title() if store and store.lower() != "unknown" else "",
        "brand": str(deal.get("brand") or "").strip(),
        "category": str(deal.get("category") or "").strip(),
        "day": DAYS[datetime.fromtimestamp(now, IST).weekday()],
    }
    out.update({k: str(v) for k, v in ctx.items() if v not in (None, "")})
    return out


def _render(text: str, fields: Dict[str, str]) -> Optional[str]:
    def sub(m: "re.Match[str]") -> str:
        return fields.get(m.group(1), "")

    used = PLACEHOLDER.findall(text)
    if any(not fields.get(p) for p in used):
        return None  # something the line promised isn't there: skip the line
    return " ".join(PLACEHOLDER.sub(sub, text).split())


def pick(kind: str, deal: Optional[Dict[str, Any]] = None, ctx: Optional[Dict[str, Any]] = None,
         mood: Union[None, str, Iterable[str]] = None, now: Optional[float] = None) -> Optional[Pick]:
    """One title/body for `kind`, or None (caller uses its own text)."""
    if not enabled():
        return None
    try:
        now = now or time.time()
        deal = deal or {}
        ctx = {k: str(v) for k, v in (ctx or {}).items() if v not in (None, "")}
        moods = None if mood is None else ([mood] if isinstance(mood, str) else list(mood))
        category = CATEGORY_ALIAS.get(str(deal.get("category") or ""), str(deal.get("category") or ""))
        templates, _ = load()
        pool = [t for t in templates if _eligible(t, kind, deal, ctx, moods, now, category)]
        if not pool:
            return None
        recent = _recent.setdefault(kind, deque(maxlen=RECENT_PER_KIND))
        fresh = [t for t in pool if t["id"] not in recent] or pool
        weights = [float(t.get("weight", 1)) * (CATEGORY_BOOST if (t.get("category") or "Any") != "Any" else 1.0)
                   for t in fresh]
        fields = _fields(deal, ctx, now)
        for _ in range(min(len(fresh), 6)):  # a line can still fail to render; try a few
            t = random.choices(fresh, weights=weights, k=1)[0]
            title, body = _render(t["title"], fields), _render(t["body"], fields)
            if title and body:
                recent.append(t["id"])
                return Pick(title[:TITLE_MAX], body[:BODY_MAX], t["id"])
        return None
    except Exception as exc:  # noqa: BLE001 — copy must never stop a notification
        log.warning("Pitara pick failed for %s: %s", kind, exc)
        return None


# --- for the app ------------------------------------------------------------------

def public() -> Dict[str, Any]:
    """Everything the phone may use: the shared lines minus server-only ones
    (those needing context only a server notification has are filtered by the
    phone itself, which knows what it can fill)."""
    templates, ver = load()
    return {"version": ver, "count": len(templates), "templates": templates}
