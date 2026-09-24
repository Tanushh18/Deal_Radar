"""Admin testing toggle: serve /api/deals from the Google Sheet directly
instead of the DB, and back again.

For testing only — this reads the whole 'Deals' tab into memory (cached
briefly by sheets.fetch_deals_raw) and filters/sorts/paginates in Python.
No indexes, no fuzzy search, nowhere near what search.py does over the DB.
It exists purely so the live catalog can be inspected/served even when the
DB is empty or suspect, and switched back once the DB is trusted again.
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from . import sheets
from .search import shape

_mode = "db"  # "db" | "sheet"


def get_mode() -> str:
    return _mode


def set_mode(mode: str) -> str:
    global _mode
    if mode not in ("db", "sheet"):
        raise ValueError("mode must be 'db' or 'sheet'")
    _mode = mode
    return _mode


_SORT_KEYS = {
    "newest": lambda d: d.get("posted_at") or 0,
    "best": lambda d: d.get("score") or 0,
    "discount": lambda d: d.get("discount_pct") or 0,
    "price_low": lambda d: d.get("price") if d.get("price") is not None else float("inf"),
    "price_high": lambda d: d.get("price") if d.get("price") is not None else float("-inf"),
    "ending": lambda d: d.get("expires_at") or float("inf"),
}
_SORT_REVERSE = {"newest", "best", "discount", "price_high"}


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
    include_expired: bool = False,
    only_lowest: bool = False,
    sort: str = "newest",
    limit: int = 48,
    offset: int = 0,
    has_coupon: bool = False,
    **_ignored: Any,
) -> Dict[str, Any]:
    now = time.time()
    rows = sheets.fetch_deals_raw()

    if not include_expired:
        rows = [d for d in rows if d.get("status") == "live" and (d.get("expires_at") or 0) > now]
    if q:
        needle = q.strip().lower()
        rows = [d for d in rows if needle in (d.get("search_blob") or d.get("title", "").lower())]
    if category:
        rows = [d for d in rows if d.get("category") == category]
    if subcategory:
        rows = [d for d in rows if d.get("subcategory") == subcategory]
    if store:
        rows = [d for d in rows if (d.get("store") or "").lower() == store.lower()]
    if brand:
        rows = [d for d in rows if (d.get("brand") or "").lower() == brand.lower()]
    if min_price is not None:
        rows = [d for d in rows if d.get("price") is not None and d["price"] >= min_price]
    if max_price is not None:
        rows = [d for d in rows if d.get("price") is not None and d["price"] <= max_price]
    if min_discount:
        rows = [d for d in rows if (d.get("discount_pct") or 0) >= min_discount]
    if only_lowest:
        rows = [d for d in rows if d.get("is_lowest")]
    if has_coupon:
        rows = [d for d in rows if d.get("coupon")]

    key = _SORT_KEYS.get(sort, _SORT_KEYS["newest"])
    rows = sorted(rows, key=key, reverse=sort in _SORT_REVERSE)

    total = len(rows)
    page = rows[offset: offset + limit]
    return {
        "total": total,
        "count": len(page),
        "offset": offset,
        "limit": limit,
        "results": [shape(d) for d in page],
        "categories": [],
        "source": "sheet",
    }
