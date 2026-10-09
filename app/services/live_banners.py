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
import logging
import re
import time
from typing import Any, Dict, List
from urllib.parse import urljoin

import httpx

log = logging.getLogger("dealradar.live_banners")

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


async def get_live(now: float | None = None) -> List[Dict[str, Any]]:
    """Cached live banners; refreshes at most every CACHE_TTL."""
    now = now or time.time()
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
