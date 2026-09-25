"""Price history and tracking reads, served from Turso.

Turso's price_points table is keyed (product_key, seen_at), so one product's
recent history is a single range scan that reads only the rows returned —
and a whole grid's sparklines go in one HTTP round trip (one statement per
product in a single pipeline). Results are cached briefly in memory.

Points recorded locally but not yet uploaded (the upload thread runs every
few minutes) are merged in, so a price seen seconds ago shows up right away.
When Turso isn't configured or doesn't answer, the local SQLite history is
used instead — the view degrades, it never breaks.
"""
from __future__ import annotations

import logging
import statistics
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .. import db
from ..config import settings
from . import turso_backup

log = logging.getLogger("dealradar.price_store")

HISTORY_LIMIT = 365     # points returned for one product's chart
STATS_WINDOW = 60       # recent points behind min/max/median (matches store.price_stats)
CACHE_TTL_SECONDS = 120
CACHE_MAX_ENTRIES = 5000
REQUEST_TIMEOUT = 4.0

Point = Tuple[float, float]  # (seen_at, price)
_cache: Dict[Tuple[str, int], Tuple[float, List[Point]]] = {}


async def _from_turso(keys: List[str], limit: int) -> Dict[str, List[Point]]:
    statements = [{
        "sql": "SELECT seen_at, price FROM price_points WHERE product_key = ? ORDER BY seen_at DESC LIMIT ?",
        "args": [turso_backup.arg(k), turso_backup.arg(limit)],
    } for k in keys]
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        resp = await client.post(f"{turso_backup.http_url()}/v2/pipeline",
                                 headers=turso_backup.auth_headers(),
                                 json=turso_backup.pipeline_body(statements))
    resp.raise_for_status()
    results = turso_backup.check_results(resp.json())
    return {k: [(float(r["seen_at"]), float(r["price"])) for r in turso_backup.rows_to_dicts(res)]
            for k, res in zip(keys, results)}


def _from_local(keys: List[str], limit: int, unsynced_only: bool) -> Dict[str, List[Point]]:
    out: Dict[str, List[Point]] = {}
    extra = " AND turso_synced = 0" if unsynced_only else ""
    for k in keys:
        rows = db.query(
            f"SELECT seen_at, price FROM price_history WHERE product_key = ?{extra} "
            "ORDER BY seen_at DESC LIMIT ?", (k, limit),
        )
        out[k] = [(float(r["seen_at"]), float(r["price"])) for r in rows]
    return out


async def history_many(keys: List[str], limit: int = HISTORY_LIMIT) -> Dict[str, List[Point]]:
    """Newest `limit` points per product, returned oldest-first."""
    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}
    if not settings.turso_configured:
        found = _from_local(keys, limit, unsynced_only=False)
    else:
        now = time.time()
        found = {}
        missing = []
        for k in keys:
            hit = _cache.get((k, limit))
            if hit and now - hit[0] < CACHE_TTL_SECONDS:
                found[k] = hit[1]
            else:
                missing.append(k)
        if missing:
            try:
                fetched = await _from_turso(missing, limit)
                if len(_cache) > CACHE_MAX_ENTRIES:
                    _cache.clear()
                for k, pts in fetched.items():
                    _cache[(k, limit)] = (now, pts)
                found.update(fetched)
            except Exception as exc:  # noqa: BLE001 - Turso down: fall back to local copy
                log.warning("Turso price read failed, using local history: %s", exc)
                found.update(_from_local(missing, limit, unsynced_only=False))
        # Fresh points the upload thread hasn't sent yet.
        pending = _from_local(keys, limit, unsynced_only=True)
        for k, pts in pending.items():
            if pts:
                merged = {int(t): (t, p) for t, p in found.get(k, [])}
                merged.update({int(t): (t, p) for t, p in pts})
                found[k] = sorted(merged.values(), reverse=True)[:limit]
    return {k: sorted(found.get(k, [])) for k in keys}


async def history(product_key: str, limit: int = HISTORY_LIMIT) -> List[Point]:
    return (await history_many([product_key], limit)).get(product_key, [])


def stats(points: List[Point]) -> Dict[str, Any]:
    """min/max/median over the most recent STATS_WINDOW points."""
    prices = [p for _, p in points[-STATS_WINDOW:] if p]
    if not prices:
        return {"min": None, "max": None, "median": None, "points": 0}
    return {"min": min(prices), "max": max(prices),
            "median": statistics.median(prices), "points": len(prices)}


def as_json(points: List[Point]) -> List[Dict[str, float]]:
    return [{"price": p, "at": t} for t, p in points]


async def tracked_product(product_key: str) -> Optional[Dict[str, Any]]:
    """The products row (title, all-time min/max, last price) — None if untracked
    or Turso is unavailable. Survives the deal itself leaving the local cache."""
    if not settings.turso_configured or not product_key:
        return None
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            resp = await client.post(
                f"{turso_backup.http_url()}/v2/pipeline", headers=turso_backup.auth_headers(),
                json=turso_backup.pipeline_body([{
                    "sql": "SELECT * FROM products WHERE product_key = ?",
                    "args": [turso_backup.arg(product_key)],
                }]),
            )
        resp.raise_for_status()
        rows = turso_backup.rows_to_dicts(turso_backup.check_results(resp.json())[0])
    except Exception as exc:  # noqa: BLE001
        log.warning("Turso product read failed: %s", exc)
        return None
    return rows[0] if rows else None
