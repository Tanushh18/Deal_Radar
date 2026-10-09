"""Live sale banners read straight off each store's own home page.

The stores put the running sale (Big Billion Days, Great Indian Festival…) in
a hero banner on their home page. This fetches those pages, picks out sale
banner images (an <img> whose alt text names a sale) and returns them as sale
events with the store's own artwork. Best-effort: store pages change and may
block us, so any failure just yields nothing for that store and the curated
calendar in sale_events.py is shown instead. Results are cached in memory.
"""
from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from typing import Any, Dict, List
from urllib.parse import urljoin

import httpx

from .. import db

log = logging.getLogger("dealradar.live_banners")

PUSHED_KEY = "live_banners_pushed"
PUSHED_TTL = 12 * 3600     # banners pushed by tools/fetch_banners.py stay valid this long

CACHE_TTL = 30 * 60
STALE_TTL = 6 * 3600       # keep serving the last good fetch this long if a refresh fails
REQUEST_TIMEOUT = 12.0
MAX_PER_STORE = 3

STORES: Dict[str, str] = {
    "amazon": "https://www.amazon.in/",
    "flipkart": "https://www.flipkart.com/",
    "myntra": "https://www.myntra.com/",
}
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Mobile Safari/537.36",
    "Accept-Language": "en-IN,en;q=0.9",
}
_SALE_WORDS = re.compile(
    r"big billion|great indian|festival|sale|days\b|end of reason|prime day|diwali|mega|bonanza|offer", re.I)
_IMG = re.compile(r"<img\b[^>]*>", re.I)
_ATTR = re.compile(r'([a-zA-Z_:-]+)\s*=\s*("([^"]*)"|\'([^\']*)\')')

_cache: Dict[str, Any] = {"at": 0.0, "events": []}
_lock = asyncio.Lock()


def _attrs(tag: str) -> Dict[str, str]:
    return {m.group(1).lower(): html.unescape(m.group(3) if m.group(3) is not None else m.group(4))
            for m in _ATTR.finditer(tag)}


def parse_banners(store: str, base_url: str, page: str) -> List[Dict[str, Any]]:
    """Sale banners found in one store's home-page HTML."""
    out, seen = [], set()
    for tag in _IMG.findall(page):
        a = _attrs(tag)
        alt = (a.get("alt") or "").strip()
        src = a.get("src") or a.get("data-src") or ""
        if not src or src.startswith("data:") or len(alt) < 6 or not _SALE_WORDS.search(alt):
            continue
        src = urljoin(base_url, src)
        if not src.startswith("https://") or src in seen:
            continue
        seen.add(src)
        out.append({
            "id": f"live-{store}-{len(out)}", "name": alt[:80], "store": store,
            "starts_at": None, "ends_at": None, "approximate": False, "hype": "",
            "image_url": src, "url": base_url, "live": True,
        })
        if len(out) >= MAX_PER_STORE:
            break
    return out


async def _fetch_store(client: httpx.AsyncClient, store: str, url: str) -> List[Dict[str, Any]]:
    try:
        resp = await client.get(url, headers=_HEADERS, follow_redirects=True)
        if resp.status_code != 200:
            return []
        return parse_banners(store, url, resp.text)
    except Exception as exc:  # noqa: BLE001 — a store being down must never break the app
        log.info("Live banner fetch failed for %s: %s", store, exc)
        return []


async def refresh() -> List[Dict[str, Any]]:
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        results = await asyncio.gather(*(_fetch_store(client, s, u) for s, u in STORES.items()))
    return [b for r in results for b in r]


# --- exact sale dates read off the pages ---------------------------------

_MON = "jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec"
_MONTHS = {m: i + 1 for i, m in enumerate(_MON.split("|"))}
_ORD = r"(?:st|nd|rd|th)?"
_RANGE = re.compile(rf"(\d{{1,2}}){_ORD}\s*(?:({_MON})[a-z]*)?\s*(?:-|–|—|to)\s*(\d{{1,2}}){_ORD}\s*({_MON})[a-z]*", re.I)
_RANGE_MON_FIRST = re.compile(rf"({_MON})[a-z]*\s*(\d{{1,2}}){_ORD}\s*(?:-|–|—|to)\s*(?:({_MON})[a-z]*\s*)?(\d{{1,2}}){_ORD}", re.I)
_START = re.compile(rf"(?:start(?:s|ing)?|from|live(?: from)?|begins?)\s*(?:on\s*)?(\d{{1,2}}){_ORD}\s*({_MON})[a-z]*", re.I)
_START_MON_FIRST = re.compile(rf"(?:start(?:s|ing)?|from|live(?: from)?|begins?)\s*(?:on\s*)?({_MON})[a-z]*\s*(\d{{1,2}}){_ORD}", re.I)

# Which calendar sale a piece of page text is talking about.
SALE_KEYWORDS = [
    ("flipkart", "flipkart-bbd", re.compile(r"big billion", re.I)),
    ("flipkart", "flipkart-diwali", re.compile(r"big diwali", re.I)),
    ("amazon", "amazon-gif", re.compile(r"great indian festival", re.I)),
    ("amazon", "amazon-prime-day", re.compile(r"prime day", re.I)),
    ("amazon", "amazon-freedom", re.compile(r"freedom festival", re.I)),
    ("myntra", "myntra-eors", re.compile(r"end of reason", re.I)),
    ("myntra", "myntra-bff", re.compile(r"big fashion festival", re.I)),
]


def parse_dates(snippet: str, year: int):
    """(start, end) as (month, day) pairs from text like '8-14 Oct', 'Oct 8 – 14'
    or 'starts 9th October'. end is None when only a start is stated; None if no date."""
    m = _RANGE.search(snippet)
    if m:
        d1, m1, d2, m2 = int(m.group(1)), m.group(2) or m.group(4), int(m.group(3)), m.group(4)
        return (_MONTHS[m1[:3].lower()], d1), (_MONTHS[m2[:3].lower()], d2)
    m = _RANGE_MON_FIRST.search(snippet)
    if m:
        m1, d1, m2, d2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4))
        return (_MONTHS[m1[:3].lower()], d1), (_MONTHS[m2[:3].lower()], d2)
    m = _START.search(snippet)
    if m:
        return (_MONTHS[m.group(2)[:3].lower()], int(m.group(1))), None
    m = _START_MON_FIRST.search(snippet)
    if m:
        return (_MONTHS[m.group(1)[:3].lower()], int(m.group(2))), None
    return None


def extract_sales(text: str, year: int) -> List[Dict[str, Any]]:
    """Exact dates for known sales mentioned in a page's text: a window around
    each mention is searched for a date range or 'starts <date>'."""
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    out, done = [], set()
    for _store, key, pat in SALE_KEYWORDS:
        if key in done:
            continue
        for m in pat.finditer(text):
            found = parse_dates(text[max(0, m.start() - 120): m.end() + 220], year)
            if not found:
                continue
            (sm, sd), end = found
            try:
                start = datetime(year, sm, sd, tzinfo=ist)
                stop = datetime(year, end[0], end[1], tzinfo=ist) + timedelta(days=1) if end else None
            except ValueError:
                continue
            if stop and stop <= start:
                continue
            done.add(key)
            out.append({"key": key, "starts_at": start.timestamp(), "ends_at": stop.timestamp() if stop else None})
            break
    return out


def apply_sales(sales: List[Dict[str, Any]]) -> int:
    """Write scraped exact dates into the calendar as that year's confirmed edit."""
    from datetime import datetime, timezone
    from . import sale_events as se

    n = 0
    for sale in sales[:20]:
        tpl = se._BY_KEY.get(str(sale.get("key")))
        start = float(sale.get("starts_at") or 0)
        if not tpl or not start:
            continue
        end = float(sale.get("ends_at") or 0) or start + tpl["duration_days"] * 86400
        year = datetime.fromtimestamp(start, tz=timezone.utc).year
        cur = se._find(se._occurrence_id(tpl["key"], year)) or {}
        if cur.get("starts_at") == start and cur.get("ends_at") == end and not cur.get("approximate"):
            continue
        se.upsert({**cur, "id": se._occurrence_id(tpl["key"], year), "name": tpl["name"], "store": tpl["store"],
                   "starts_at": start, "ends_at": end, "approximate": not sale.get("ends_at")})
        n += 1
    return n


def needs_refresh(now: float | None = None) -> Dict[str, Any]:
    """For the CI gate: is a sale live or starting within 2 days, and are the
    pushed banners older than 6h? Banners are only worth scraping then."""
    from . import sale_events as se

    now = now or time.time()
    try:
        data = json.loads(db.get_meta(PUSHED_KEY) or "{}")
    except ValueError:
        data = {}
    age = now - float(data.get("at") or 0)
    active = [e["name"] for e in se.list_all(upcoming_only=True, now=now)
              if e["starts_at"] and e["starts_at"] - 2 * 86400 <= now <= (e["ends_at"] or e["starts_at"])]
    sales = [{"name": e["name"], "store": e["store"]} for e in se.list_all(upcoming_only=True, now=now)
             if e["name"] in active]
    return {"active": active, "sales": sales, "needed": bool(active) and age > 6 * 3600}


def set_pushed(banners: List[Dict[str, Any]], now: float | None = None) -> int:
    """Store banners scraped elsewhere (a headless browser in CI) — the server
    itself can't render the JavaScript-built home pages. Only https images."""
    clean = []
    for b in banners[:30]:
        img, store = str(b.get("image_url") or ""), str(b.get("store") or "").lower()[:30]
        name = str(b.get("name") or "").strip()[:80]
        if not (img.startswith("https://") and store and name):
            continue
        url = str(b.get("url") or "")
        clean.append({
            "id": f"live-{store}-{len(clean)}", "name": name, "store": store,
            "starts_at": None, "ends_at": None, "approximate": False, "hype": "",
            "image_url": img[:500], "url": url[:500] if url.startswith("https://") else STORES.get(store, ""),
            "live": True, "credit": str(b.get("credit") or "")[:80],
        })
    db.set_meta(PUSHED_KEY, json.dumps({"at": now or time.time(), "banners": clean}))
    return len(clean)


def _pushed(now: float) -> List[Dict[str, Any]]:
    try:
        data = json.loads(db.get_meta(PUSHED_KEY) or "{}")
    except ValueError:
        return []
    if now - float(data.get("at") or 0) > PUSHED_TTL:
        return []
    return data.get("banners") or []


async def get_live(now: float | None = None) -> List[Dict[str, Any]]:
    """Cached live banners; refreshes at most every CACHE_TTL."""
    now = now or time.time()
    pushed = _pushed(now)
    if pushed:
        return pushed
    if _cache["events"] and now - _cache["at"] < CACHE_TTL:
        return _cache["events"]
    if not _cache["events"] and _cache["at"] and now - _cache["at"] < 300:
        return []   # recently found nothing: don't hammer the stores on every request
    async with _lock:
        if now - _cache["at"] < CACHE_TTL and _cache["at"]:
            return _cache["events"]
        events = await refresh()
        if events or now - _cache["at"] > STALE_TTL:
            _cache["events"] = events
        _cache["at"] = now
        return _cache["events"]
