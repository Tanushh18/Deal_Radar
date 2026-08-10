"""Query engine over the cached deals.

Two-stage: SQL narrows by hard filters (price, store, category, the user's own
channels), then Python ranks what survives. At a few thousand live deals this
is far simpler than a search index and still sub-100ms.

Relevance blends exact-phrase hits, synonym hits (so "kurta" finds "kurti"),
and a fuzzy fallback for typos, then folds in the stored deal score so a
mediocre-but-relevant match ranks below a great one.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from .. import db
from . import taxonomy

SORTS = {
    "relevance": None,
    "best": "score DESC",
    "newest": "posted_at DESC",
    "discount": "discount_pct DESC",
    "price_low": "price ASC",
    "price_high": "price DESC",
}


def _relevance(deal: Dict[str, Any], raw_query: str, terms: List[str]) -> float:
    blob = (deal.get("search_blob") or "").lower()
    title = (deal.get("title") or "").lower()
    if not blob:
        return 0.0

    score = 0.0
    if raw_query and raw_query in title:
        score += 60.0                     # exact phrase in the product name
    elif raw_query and raw_query in blob:
        score += 40.0

    hits = sum(1 for term in terms if term in blob)
    if hits:
        score += min(hits, 5) * 12.0      # synonym / token overlap

    if score == 0.0:
        # Nothing matched literally — allow a typo-tolerant fallback.
        fuzzy = fuzz.partial_ratio(raw_query, title)
        if fuzzy >= 82:
            score += (fuzzy - 82) * 1.6

    if deal.get("brand") and raw_query and deal["brand"].lower() in raw_query:
        score += 15.0
    return score


def search(
    *,
    q: str = "",
    category: str = "",
    subcategory: str = "",
    store: str = "",
    brand: str = "",
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_discount: int = 0,
    channel_ids: Optional[List[int]] = None,
    include_expired: bool = False,
    only_lowest: bool = False,
    sort: str = "relevance",
    limit: int = 48,
    offset: int = 0,
) -> Dict[str, Any]:
    now = time.time()
    where: List[str] = []
    params: List[Any] = []

    if include_expired:
        where.append("status != 'dead'")
    else:
        where.append("status = 'live'")
        where.append("expires_at > ?")
        params.append(now)

    if category:
        where.append("category = ?")
        params.append(category)
    if subcategory:
        where.append("subcategory = ?")
        params.append(subcategory)
    if store:
        where.append("store = ?")
        params.append(store.lower())
    if brand:
        where.append("LOWER(brand) = ?")
        params.append(brand.lower())
    if min_price is not None:
        where.append("price >= ?")
        params.append(min_price)
    if max_price is not None:
        where.append("price IS NOT NULL AND price <= ?")
        params.append(max_price)
    if min_discount:
        where.append("discount_pct >= ?")
        params.append(min_discount)
    if only_lowest:
        where.append("is_lowest = 1")
    if channel_ids:
        placeholders = ",".join("?" for _ in channel_ids)
        where.append(f"channel_id IN ({placeholders})")
        params.extend(channel_ids)

    sql = f"SELECT * FROM deals WHERE {' AND '.join(where)}"

    raw_query = (q or "").strip().lower()
    if raw_query:
        # Cheap SQL prefilter on any expanded term, exact ranking happens below.
        terms = sorted(taxonomy.expand_query(raw_query), key=len, reverse=True)[:24]
        likes = " OR ".join("search_blob LIKE ?" for _ in terms) if terms else "1=1"
        sql += f" AND ({likes} OR search_blob LIKE ?)"
        params.extend([f"%{t}%" for t in terms])
        params.append(f"%{raw_query}%")
    else:
        terms = []

    order = SORTS.get(sort) or SORTS["best"]
    sql += f" ORDER BY {order} LIMIT 2000"

    rows = db.rows_to_dicts(db.query(sql, params))

    if raw_query and sort == "relevance":
        ranked = []
        for deal in rows:
            rel = _relevance(deal, raw_query, terms)
            if rel <= 0:
                continue
            deal["_relevance"] = round(rel, 1)
            deal["_rank"] = rel + float(deal.get("score") or 0) * 0.45
            ranked.append(deal)
        ranked.sort(key=lambda d: d["_rank"], reverse=True)
        rows = ranked

    total = len(rows)
    page = rows[offset: offset + limit]
    return {
        "total": total,
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "results": [shape(d) for d in page],
    }


def shape(deal: Dict[str, Any]) -> Dict[str, Any]:
    """Trim a DB row down to what the UI needs."""
    price = deal.get("price")
    mrp = deal.get("mrp")
    saving = None
    if price and mrp and mrp > price:
        saving = round(mrp - price)
    return {
        "id": deal.get("id"),
        "title": deal.get("title"),
        "price": price,
        "mrp": mrp,
        "saving": saving,
        "discount_pct": deal.get("discount_pct") or 0,
        "currency": deal.get("currency") or "INR",
        "store": deal.get("store"),
        "url": deal.get("url"),
        "image_url": deal.get("image_url"),
        "coupon": deal.get("coupon"),
        "category": deal.get("category"),
        "subcategory": deal.get("subcategory"),
        "brand": deal.get("brand"),
        "sizes": deal.get("sizes"),
        "channel_title": deal.get("channel_title"),
        "posted_at": deal.get("posted_at"),
        "expires_at": deal.get("expires_at"),
        "repost_count": deal.get("repost_count") or 1,
        "status": deal.get("status"),
        "score": deal.get("score"),
        "is_lowest": bool(deal.get("is_lowest")),
        "flags": deal.get("flags") or [],
        "relevance": deal.get("_relevance"),
    }


def facets(channel_ids: Optional[List[int]] = None) -> Dict[str, Any]:
    """Counts for the filter sidebar, scoped to what the user can see."""
    now = time.time()
    where = ["status = 'live'", "expires_at > ?"]
    params: List[Any] = [now]
    if channel_ids:
        where.append(f"channel_id IN ({','.join('?' for _ in channel_ids)})")
        params.extend(channel_ids)
    clause = " AND ".join(where)

    def group(column: str) -> List[Dict[str, Any]]:
        rows = db.query(
            f"SELECT {column} AS key, COUNT(*) AS n FROM deals WHERE {clause} "
            f"AND {column} IS NOT NULL AND {column} != '' "
            f"GROUP BY {column} ORDER BY n DESC LIMIT 30",
            params,
        )
        return [{"key": r["key"], "count": r["n"]} for r in rows]

    price_row = db.query_one(
        f"SELECT MIN(price) AS lo, MAX(price) AS hi FROM deals WHERE {clause} AND price IS NOT NULL",
        params,
    )
    return {
        "categories": group("category"),
        "stores": group("store"),
        "brands": group("brand"),
        "channels": group("channel_title"),
        "price_range": {
            "min": price_row["lo"] if price_row and price_row["lo"] else 0,
            "max": price_row["hi"] if price_row and price_row["hi"] else 0,
        },
    }


def trending(channel_ids: Optional[List[int]] = None, limit: int = 12) -> List[Dict[str, Any]]:
    """Deals many channels reposted in the last day — the strongest signal we have."""
    now = time.time()
    where = ["status = 'live'", "expires_at > ?", "first_seen_at > ?", "repost_count > 1"]
    params: List[Any] = [now, now - 86400 * 2]
    if channel_ids:
        where.append(f"channel_id IN ({','.join('?' for _ in channel_ids)})")
        params.extend(channel_ids)
    rows = db.query(
        f"SELECT * FROM deals WHERE {' AND '.join(where)} "
        f"ORDER BY repost_count DESC, score DESC LIMIT ?",
        params + [limit],
    )
    return [shape(d) for d in db.rows_to_dicts(rows)]
