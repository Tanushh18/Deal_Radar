"""Check price: paste (or share into the app) any product link."""
from __future__ import annotations

import time
from urllib.parse import urljoin

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from .. import db
from ..services import ingest, parser, ratelimit, search, store
from .deals import price_verdict

router = APIRouter(prefix="/api", tags=["lookup"])
_limit = ratelimit.limit("lookup", max_requests=30, window_seconds=300)
_HEADERS = {"User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Mobile Safari/537.36"}


async def _resolve(url: str) -> str:
    """Follow shortlinks (amzn.to, fkrt.cc, cuttli…) hop by hop, SSRF-checked at each hop."""
    async with httpx.AsyncClient(follow_redirects=False, timeout=6.0, headers=_HEADERS) as client:
        for _ in range(ingest.MAX_REDIRECT_HOPS):
            if not await ingest._resolve_is_safe(url):
                break
            try:
                resp = await client.head(url)
                if resp.status_code in (405, 403):
                    resp = await client.get(url)
            except httpx.HTTPError:
                break
            if resp.status_code in (301, 302, 303, 307, 308) and resp.headers.get("location"):
                url = urljoin(url, resp.headers["location"])
                continue
            break
    return url


@router.get("/lookup", dependencies=[Depends(_limit)])
async def lookup(url: str = Query(..., min_length=8, max_length=2000)):
    found = parser.URL_RE.search(url)
    if not found:
        raise HTTPException(status_code=400, detail="Paste a product link (https://…).")
    original = found.group(0)
    resolved = await _resolve(original)
    shop = parser.detect_store(resolved)
    key = parser.product_key(resolved, shop, "")
    cleaned = parser.clean_url(resolved)
    rows = db.query(
        "SELECT * FROM deals WHERE (product_key = ? OR clean_url = ? OR url = ? OR resolved_url = ?) "
        "AND flags NOT LIKE '%not_a_deal%' ORDER BY (status = 'live' AND expires_at > ?) DESC, last_seen_at DESC LIMIT 20",
        (key, cleaned, original, resolved, time.time()),
    )
    deals = db.rows_to_dicts(rows)
    now = time.time()
    live = [search.shape(d) for d in deals if d.get("status") == "live" and float(d.get("expires_at") or 0) > now]
    archive = [search.shape(d) for d in deals if not (d.get("status") == "live" and float(d.get("expires_at") or 0) > now)]
    product_key = (deals[0]["product_key"] if deals else key) or key
    stats = store.price_stats(product_key)
    points = db.query("SELECT price, seen_at FROM price_history WHERE product_key = ? ORDER BY seen_at ASC LIMIT 120",
                      (product_key,))
    current = (live or archive or [{}])[0].get("price")
    return {
        "resolved_url": resolved,
        "store": shop,
        "product_key": product_key,
        "deals": live,
        "archive": archive,
        "price_stats": stats,
        "history": [{"price": p["price"], "at": p["seen_at"]} for p in points],
        "price_history_url": search.price_history_url({"resolved_url": resolved, "url": original}),
        "verdict": price_verdict(current, stats),
    }
