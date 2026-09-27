""""More offers": channel posts that didn't become a card, still findable by search.

The noise gate (quality.py) keeps the feed to one-product-one-price cards with
a photo, which throws away a lot a searcher would still want — "Upto 70% off
Levi's", "Kurta starting 229", a text-only deal with no picture. Those posts
are kept here for OFFER_DAYS and only ever surface under a typed search, below
the real deals.

Only what a result row shows is stored: a title, price (if any), store and the
link. Never the post text, never which channel posted it.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any, Dict, List, Set

from .. import db
from . import links, taxonomy

log = logging.getLogger(__name__)

OFFER_DAYS = 7
# Never worth keeping, even for search: referral/loan/crypto spam and posts
# with nothing to click.
_SKIP_REASONS = {"promo", "no_link"}
_KNOWN_STORES: Set[str] = set(taxonomy.STORE_DOMAINS)
_KEY_RE = re.compile(r"[^a-z0-9]+")
# "starting 229", "Starts @ ₹1,049", "from Rs.192", "Under ₹399" — a round-up's floor price.
_FROM_PRICE_RE = re.compile(
    r"\b(?:start(?:s|ing)?|from|under)\s*(?:at|from|@)?\s*(?:@|₹|rs\.?|inr)?\s*(\d{1,3}(?:,\d{2,3})+|\d{2,6})\b",
    re.IGNORECASE,
)
_pending: set = set()   # strong refs to background save tasks


def _title_key(title: str) -> str:
    return _KEY_RE.sub(" ", (title or "").lower()).strip()[:120]


def keep(deal: Dict[str, Any], reason: str) -> bool:
    return bool(deal and reason not in _SKIP_REASONS and deal.get("url") and len(_title_key(deal.get("title") or "")) >= 4)


def _from_price(title: str) -> float:
    match = _FROM_PRICE_RE.search(title or "")
    return float(match.group(1).replace(",", "")) if match else 0.0


def _row(deal: Dict[str, Any]) -> Dict[str, Any]:
    store = (deal.get("store") or "").lower()
    title = deal.get("title") or ""
    # A round-up's "starting 229" beats whatever single number the parser guessed.
    floor = _from_price(title)
    price = floor or (float(deal["price"]) if deal.get("price") else None)
    return {
        "id": f"{int(deal.get('channel_id') or 0)}:{int(deal.get('message_id') or 0)}",
        "title": title[:200],
        "price": price,
        "price_from": 1 if floor else 0,
        "store": store if store in _KNOWN_STORES else "",
        "url": deal.get("url") or "",
        "brand": deal.get("brand") or "",
        "category": deal.get("category") or "",
        "subcategory": deal.get("subcategory") or "",
        "title_key": _title_key(deal.get("title") or ""),
        "posted_at": float(deal.get("posted_at") or time.time()),
    }


async def save(deals: List[Dict[str, Any]]) -> int:
    """Store posts the gate turned away. Shortlinks are resolved first, only to learn the store."""
    todo = [d for d in deals if d]
    if not todo:
        return 0
    try:
        await links.resolve_deals(todo)
    except Exception as exc:  # noqa: BLE001 — an unknown store is fine; the link still works
        log.info("Offer link resolution failed: %s", exc)
    for deal in todo:
        db.upsert("offers", _row(deal), conflict="id")
    return len(todo)


def save_later(deals: List[Dict[str, Any]]) -> None:
    """Fire-and-forget save, so resolving links never slows ingest down."""
    deals = [d for d in deals if d]
    if not deals:
        return
    task = asyncio.get_running_loop().create_task(save(deals))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


def prune(days: int = OFFER_DAYS) -> int:
    return db.execute("DELETE FROM offers WHERE posted_at < ?", (time.time() - days * 86400,)).rowcount or 0


def search(q: str, limit: int = 12, exclude_titles: Set[str] = frozenset()) -> List[Dict[str, Any]]:
    """Recent offers matching `q`, best match first, one row per distinct title."""
    from . import search as deal_search  # local: search is heavy and imports taxonomy too

    plan = deal_search._Plan(q)
    if not plan.tokens:
        return []
    rows = db.rows_to_dicts(db.query(
        "SELECT * FROM offers WHERE posted_at >= ? ORDER BY posted_at DESC LIMIT 5000",
        (time.time() - OFFER_DAYS * 86400,),
    ))
    for row in rows:
        row["search_blob"] = " ".join(filter(None, (row.get("title"), row.get("brand"), row.get("store"),
                                                    row.get("category"), row.get("subcategory"))))
    matched = deal_search._match(rows, plan)
    matched.sort(key=lambda r: (-r["_relevance"], -r["posted_at"]))
    out, seen = [], set(exclude_titles)
    for row in matched:
        if row["title_key"] in seen:
            continue
        seen.add(row["title_key"])
        out.append({
            "id": row["id"],
            "title": row["title"],
            "price": row["price"],
            "price_from": bool(row.get("price_from")),
            "store": row["store"],
            "url": row["url"],
            "posted_at": row["posted_at"],
        })
        if len(out) >= limit:
            break
    return out
