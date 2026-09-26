"""BuyHatke price history for a deal, shown next to our own.

Read from BuyHatke's public product page (buyhatke.com/<store product url>),
which its robots.txt allows; its /api/ routes are disallowed there and never
touched. Amazon itself is never contacted: the Amazon URL is only the lookup
key BuyHatke's own page takes.

Nothing from BuyHatke is written to any of our databases. Results live in
process memory only (a compact, bounded cache for CACHE_TTL), so a restart
simply re-fetches on first view.

Polite by construction: one request at a time with MIN_GAP between them,
each product at most once per CACHE_TTL, and a full pause after any sign of
being blocked or throttled.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from array import array
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import httpx

from .. import db
from ..config import settings
from . import links

log = logging.getLogger("dealradar.buyhatke")

BASE = "https://buyhatke.com/"
CACHE_TTL = 3 * 86400          # found histories
MISS_TTL = 12 * 3600           # BuyHatke has no data for this product
CACHE_MAX = 1500               # products kept in memory
MAX_POINTS = 250               # per product, after downsampling
MIN_GAP = 3.0                  # seconds between two BuyHatke requests
PAUSE_AFTER_BLOCK = 3600
TIMEOUT = 12.0
MAX_BYTES = 2_500_000
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_ENTRY = re.compile(r'\{from:"([^"]+)",to:"([^"]+)",price:([\d.]+)\}')
_ASIN = re.compile(r"^[A-Z0-9]{10}$")
_BLOCK_MARKERS = ("cf-chl", "just a moment...", "attention required", "cf_chl_opt")
IST = timezone(timedelta(hours=5, minutes=30))

Point = Tuple[float, float]

# key -> (expires_at, flat array [t0, p0, t1, p1, ...] or None for "no data")
_cache: "OrderedDict[str, Tuple[float, Optional[array]]]" = OrderedDict()
_inflight: Dict[str, "asyncio.Task[Optional[array]]"] = {}
_lock = asyncio.Lock()
_last_request = 0.0
_paused_until = 0.0
_warming = False
_stats: Dict[str, Any] = {"requests": 0, "found": 0, "no_data": 0, "blocked": 0, "errors": 0,
                          "last_ok_at": None, "last_error": None}


def enabled() -> bool:
    return settings.buyhatke_enabled


def status() -> Dict[str, Any]:
    now = time.time()
    return {"enabled": enabled(), "cached_products": len(_cache),
            "paused_for_seconds": max(0, int(_paused_until - now)), **_stats}


def store_url(deal: Dict[str, Any]) -> Optional[str]:
    """The store product URL BuyHatke should look up, or None if we don't have one."""
    key = str(deal.get("product_key") or "")
    if key.startswith("amazon:"):
        asin = key.split(":", 1)[1]
        # Canonical form: BuyHatke recognises it reliably, whatever link the post used.
        return f"https://www.amazon.in/dp/{asin}" if _ASIN.match(asin) else None
    for candidate in (deal.get("resolved_url"), deal.get("clean_url"), deal.get("url")):
        if candidate and links.is_store_site(candidate):
            return candidate.split("#")[0].split("?")[0]
    return None


def _key(deal: Dict[str, Any], url: str) -> str:
    return str(deal.get("product_key") or url)


def _to_points(flat: Optional[array]) -> List[Point]:
    if not flat:
        return []
    return [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]


def cached(deal: Dict[str, Any]) -> List[Point]:
    """Points already in memory, never triggering a fetch."""
    url = store_url(deal)
    if not url or not enabled():
        return []
    hit = _cache.get(_key(deal, url))
    return _to_points(hit[1]) if hit and hit[0] > time.time() else []


async def history(deal: Dict[str, Any], wait: float = 8.0) -> List[Point]:
    """BuyHatke's points for this deal, fetching if needed — but waiting at most
    `wait` seconds; a slower fetch finishes in the background for next time."""
    url = store_url(deal)
    if not url or not enabled():
        return []
    key = _key(deal, url)
    hit = _cache.get(key)
    if hit and hit[0] > time.time():
        _cache.move_to_end(key)
        return _to_points(hit[1])
    if time.time() < _paused_until:
        return []
    task = _inflight.get(key)
    if task is None:
        task = asyncio.get_running_loop().create_task(_fetch(key, url))
        _inflight[key] = task
        task.add_done_callback(lambda _t, k=key: _inflight.pop(k, None))
    try:
        return _to_points(await asyncio.wait_for(asyncio.shield(task), wait))
    except asyncio.TimeoutError:
        return []
    except Exception:  # noqa: BLE001 - a BuyHatke problem must never break our own history
        return []


def _remember(key: str, flat: Optional[array], ttl: float) -> Optional[array]:
    _cache[key] = (time.time() + ttl, flat)
    _cache.move_to_end(key)
    while len(_cache) > CACHE_MAX:
        _cache.popitem(last=False)
    return flat


def parse(html: str) -> List[Point]:
    """Price-change points from a BuyHatke product page, oldest first."""
    points: Dict[int, float] = {}
    for start, end, price in _ENTRY.findall(html):
        value = float(price)
        if value <= 0:
            continue
        for stamp in (start, end):
            try:
                ts = datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST).timestamp()
            except ValueError:
                continue
            points[int(ts)] = value
    return [(float(t), p) for t, p in sorted(points.items())]


def _downsample(points: List[Point]) -> List[Point]:
    if len(points) <= MAX_POINTS:
        return points
    step = len(points) / MAX_POINTS
    keep = {int(i * step) for i in range(MAX_POINTS)}
    # Never lose the extremes: the lowest and highest prices are the whole point.
    keep.add(min(range(len(points)), key=lambda i: points[i][1]))
    keep.add(max(range(len(points)), key=lambda i: points[i][1]))
    keep.add(len(points) - 1)
    return [points[i] for i in sorted(keep)]


async def _fetch(key: str, url: str) -> Optional[array]:
    global _last_request, _paused_until
    async with _lock:
        wait = MIN_GAP - (time.time() - _last_request)
        if wait > 0:
            await asyncio.sleep(wait)
        if time.time() < _paused_until:
            return None
        _last_request = time.time()
        _stats["requests"] += 1
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=True,
                                         headers={"User-Agent": _UA}) as client:
                resp = await client.get(BASE + url)
        except (httpx.HTTPError, asyncio.TimeoutError) as exc:
            _stats["errors"] += 1
            _stats["last_error"] = f"{type(exc).__name__}"
            return None  # transient: not cached, retried on the next view

    html = resp.text[:MAX_BYTES] if resp.status_code == 200 else ""
    blocked = resp.status_code in (403, 429, 503) or any(m in html[:5000].lower() for m in _BLOCK_MARKERS)
    if blocked:
        _paused_until = time.time() + PAUSE_AFTER_BLOCK
        _stats["blocked"] += 1
        _stats["last_error"] = f"blocked (HTTP {resp.status_code}) — paused for an hour"
        log.warning("BuyHatke blocked us (HTTP %s); pausing for an hour", resp.status_code)
        return None
    if urlparse(str(resp.url)).hostname not in ("buyhatke.com", "www.buyhatke.com") or resp.status_code != 200:
        _stats["errors"] += 1
        _stats["last_error"] = f"HTTP {resp.status_code}"
        return None
    points = _downsample(parse(html))
    if len(points) < 2:
        _stats["no_data"] += 1
        return _remember(key, None, MISS_TTL)
    _stats["found"] += 1
    _stats["last_ok_at"] = time.time()
    flat = array("d", [v for pt in points for v in pt])
    return _remember(key, flat, CACHE_TTL)


async def warm(limit: int) -> int:
    """Fetch history for the best live deals nobody has opened yet, so it's
    ready on first view. Sequential and rate-limited like everything else."""
    global _warming
    if not enabled() or limit <= 0 or _warming or time.time() < _paused_until:
        return 0
    _warming = True
    fetched = 0
    try:
        rows = db.query(
            "SELECT product_key, url, clean_url, resolved_url FROM deals WHERE status = 'live' "
            "AND expires_at > ? AND COALESCE(image_url, '') != '' ORDER BY score DESC LIMIT 200",
            (time.time(),),
        )
        for row in rows:
            if fetched >= limit or time.time() < _paused_until:
                break
            deal = dict(row)
            url = store_url(deal)
            if not url:
                continue
            key = _key(deal, url)
            hit = _cache.get(key)
            if (hit and hit[0] > time.time()) or key in _inflight:
                continue
            await _fetch(key, url)
            fetched += 1
    except Exception as exc:  # noqa: BLE001 - warming is best-effort
        log.warning("BuyHatke warm-up failed: %s", exc)
    finally:
        _warming = False
    return fetched
