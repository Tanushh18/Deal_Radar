"""Deal persistence: dedup, price history, scoring, expiry.

The interesting logic lives in `save_deal`. The same product gets posted to a
dozen channels within minutes; collapsing those into one row (while counting
how many channels carried it) is both a storage win and the strongest quality
signal available — deals that many channels repost are usually the real ones.
"""
from __future__ import annotations

import json
import math
import re
import statistics
import time
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from .. import db
from ..config import settings

DEAL_COLUMNS = [
    "id", "title", "norm_title", "product_key", "price", "mrp", "discount_pct",
    "currency", "store", "url", "clean_url", "image_url", "coupon", "category",
    "subcategory", "brand", "sizes", "channel_id", "channel_title", "message_id",
    "posted_at", "first_seen_at", "last_seen_at", "expires_at", "repost_count",
    "channels_seen", "status", "score", "is_lowest", "flags", "raw_text", "search_blob",
]


def _encode(deal: Dict[str, Any]) -> Dict[str, Any]:
    row = {k: deal.get(k) for k in DEAL_COLUMNS}
    row["channels_seen"] = json.dumps(deal.get("channels_seen") or [])
    row["flags"] = json.dumps(deal.get("flags") or [])
    row["dirty"] = 1
    return row


def compute_score(deal: Dict[str, Any], now: Optional[float] = None) -> float:
    """0-100 ranking signal blending discount, corroboration, freshness, price history."""
    now = now or time.time()
    discount = min(float(deal.get("discount_pct") or 0), 90.0)
    reposts = min(int(deal.get("repost_count") or 1), 6)
    age_hours = max((now - float(deal.get("posted_at") or now)) / 3600.0, 0.0)

    score = (discount / 90.0) * 38.0                       # how good is the cut
    score += ((reposts - 1) / 5.0) * 22.0                  # how many channels agree
    score += math.exp(-age_hours / 36.0) * 25.0            # freshness, ~1.5 day half-life
    if deal.get("is_lowest"):
        score += 15.0                                       # cheapest we have ever seen
    flags = deal.get("flags") or []
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except json.JSONDecodeError:
            flags = []
    if "suspicious_mrp" in flags:
        score -= 12.0                                       # inflated "MRP" -> fake discount
    if not deal.get("price"):
        score -= 8.0
    if deal.get("status") != "live":
        score -= 40.0
    return round(max(score, 0.0), 2)


def record_price(product_key: str, price: Optional[float], store: str) -> None:
    if not product_key or not price:
        return
    last = db.query_one(
        "SELECT price FROM price_history WHERE product_key = ? ORDER BY seen_at DESC LIMIT 1",
        (product_key,),
    )
    # Only write when the price actually moved — keeps the table small.
    if last and abs(float(last["price"]) - float(price)) < 0.01:
        return
    db.execute(
        "INSERT INTO price_history (product_key, price, store, seen_at) VALUES (?, ?, ?, ?)",
        (product_key, float(price), store, time.time()),
    )


def price_stats(product_key: str) -> Dict[str, Any]:
    rows = db.query(
        "SELECT price FROM price_history WHERE product_key = ? ORDER BY seen_at DESC LIMIT 60",
        (product_key,),
    )
    prices = [float(r["price"]) for r in rows if r["price"]]
    if not prices:
        return {"min": None, "max": None, "median": None, "points": 0}
    return {
        "min": min(prices),
        "max": max(prices),
        "median": statistics.median(prices),
        "points": len(prices),
    }


# --- product identity -------------------------------------------------
# Pure string similarity is NOT usable here. Measured on real titles:
#   "rockerz 450 headphone" vs "rockerz 550 headphone"      -> 96  (different!)
#   "rockerz 450 headphone" vs "rockerz 450 bluetooth ..."  -> 76  (same!)
# Model numbers and capacities are the discriminator, so they gate the match
# and text similarity only breaks ties.
_NUM_TOKEN = re.compile(r"\b\d+(?:\.\d+)?\s*(?:mah|w|mm|cm|ml|l|gb|tb|kg|g|inch|in)?\b")
_MALE = {"men", "mens", "man", "male", "boy", "boys", "gents"}
_FEMALE = {"women", "womens", "woman", "female", "girl", "girls", "ladies"}

SIMILARITY_SHARED_MODEL = 62   # a shared model number carries most of the weight
SIMILARITY_NO_MODEL = 80       # nothing numeric to anchor on -> demand more overlap


def _numeric_tokens(text: str) -> set:
    return {m.group(0).replace(" ", "") for m in _NUM_TOKEN.finditer(text or "")}


def _gender_conflict(a: str, b: str) -> bool:
    ta, tb = set((a or "").split()), set((b or "").split())
    return bool((ta & _MALE and tb & _FEMALE) or (ta & _FEMALE and tb & _MALE))


def same_product(a: str, b: str) -> bool:
    """Are two normalised titles the same product?"""
    if not a or not b:
        return False
    na, nb = _numeric_tokens(a), _numeric_tokens(b)
    # Conflicting model numbers / capacities => different products, full stop.
    if na and nb and not (na <= nb or nb <= na):
        return False
    if _gender_conflict(a, b):
        return False
    threshold = SIMILARITY_SHARED_MODEL if (na & nb) else SIMILARITY_NO_MODEL
    return fuzz.token_set_ratio(a, b) >= threshold


def canonical_product_key(deal: Dict[str, Any]) -> str:
    """Resolve a title-hash key onto an existing product's key.

    A shortlink post and a /dp/ASIN post for the same item produce different
    keys, which would split the price history in two and break the all-time-low
    flag. When our key is only a title hash, adopt the key of any sufficiently
    similar product we already know — regardless of price, because tracking a
    price *drop* is exactly the point.
    """
    key = str(deal.get("product_key") or "")
    norm = deal.get("norm_title")
    if ":t:" not in key or not norm:
        return key

    rows = db.query(
        "SELECT product_key, norm_title FROM deals WHERE store = ? AND last_seen_at > ? "
        "ORDER BY last_seen_at DESC LIMIT 40",
        (deal.get("store", ""), time.time() - 30 * 86400),
    )
    best, best_score = key, 0.0
    for row in rows:
        candidate = row["norm_title"] or ""
        if not same_product(norm, candidate):
            continue
        ratio = fuzz.token_set_ratio(norm, candidate)
        if ratio > best_score:
            best, best_score = str(row["product_key"]), ratio
    return best


def _match_by_title(deal: Dict[str, Any], now: float) -> Optional[Any]:
    """Fallback dedup when product ids don't line up.

    Channels post the same product with wildly different links — a bare
    amzn.to shortlink in one, a full /dp/ASIN URL in another. Those produce
    different product keys, so without this the same headphone shows up twice.
    Matching on normalised title + near-identical price catches it.
    """
    norm = deal.get("norm_title")
    price = deal.get("price")
    if not norm or not price:
        return None

    lo, hi = float(price) * 0.98, float(price) * 1.02
    rows = db.query(
        "SELECT * FROM deals WHERE store = ? AND expires_at > ? AND price BETWEEN ? AND ? "
        "AND product_key != ? ORDER BY last_seen_at DESC LIMIT 25",
        (deal.get("store", ""), now, lo, hi, deal.get("product_key") or ""),
    )
    best, best_score = None, 0.0
    for row in rows:
        candidate = row["norm_title"] or ""
        if not same_product(norm, candidate):
            continue
        ratio = fuzz.token_set_ratio(norm, candidate)
        if ratio > best_score:
            best, best_score = row, ratio
    return best


def save_deal(deal: Dict[str, Any]) -> str:
    """Insert or merge a parsed deal. Returns 'new' | 'merged' | 'updated'."""
    now = time.time()
    # Fold shortlink/full-URL variants of the same product onto one key first,
    # so price history (and the all-time-low flag) accumulate correctly.
    pkey = canonical_product_key(deal)
    deal["product_key"] = pkey
    price = deal.get("price")

    existing = None
    if pkey:
        # Match the same product at a comparable price within the TTL window.
        rows = db.query(
            "SELECT * FROM deals WHERE product_key = ? AND expires_at > ? "
            "ORDER BY last_seen_at DESC LIMIT 5",
            (pkey, now),
        )
        for row in rows:
            row_price = row["price"]
            if price and row_price:
                # Within 2% -> same offer reposted, not a new price.
                if abs(float(row_price) - float(price)) / max(float(row_price), 1.0) <= 0.02:
                    existing = row
                    break
            elif not price and not row_price:
                existing = row
                break

    if existing is None:
        existing = _match_by_title(deal, now)

    history = price_stats(pkey) if pkey else {"min": None, "median": None, "points": 0}
    record_price(pkey, price, deal.get("store", ""))

    flags: List[str] = list(deal.get("flags") or [])
    mrp, deal_price = deal.get("mrp"), price
    if mrp and deal_price and history.get("median") and history["points"] >= 3:
        # An "MRP" far above every price we have ever recorded is inflated.
        if float(mrp) > float(history["median"]) * 2.5:
            flags.append("suspicious_mrp")
    if deal_price and history.get("min") and history["points"] >= 2:
        if float(deal_price) < float(history["min"]):
            deal["is_lowest"] = 1
    elif deal_price and history["points"] == 0:
        deal["is_lowest"] = 0
    deal["flags"] = sorted(set(flags))

    if existing is None:
        deal["score"] = compute_score(deal, now)
        db.upsert("deals", _encode(deal), conflict="id")
        return "new"

    # --- merge into the existing row --------------------------------
    try:
        seen = json.loads(existing["channels_seen"] or "[]")
    except (json.JSONDecodeError, TypeError):
        seen = []
    channel_id = deal.get("channel_id")
    is_new_channel = channel_id not in seen
    if is_new_channel and channel_id:
        seen.append(channel_id)

    merged = dict(existing)
    merged["channels_seen"] = seen
    merged["repost_count"] = len(seen) or int(existing["repost_count"] or 1)
    merged["last_seen_at"] = now
    merged["expires_at"] = max(
        float(existing["expires_at"] or 0),
        (deal.get("posted_at") or now) + settings.deal_ttl_hours * 3600,
    )
    merged["status"] = "live"
    merged["flags"] = deal["flags"]
    merged["is_lowest"] = deal.get("is_lowest", existing["is_lowest"])
    # A real marketplace id beats a title hash — upgrade if the new post has one,
    # and carry the price history across so the all-time-low flag stays correct.
    old_key = str(existing["product_key"] or "")
    new_key = str(deal.get("product_key") or "")
    if ":t:" in old_key and new_key and ":t:" not in new_key:
        merged["product_key"] = new_key
        db.execute(
            "UPDATE price_history SET product_key = ? WHERE product_key = ?", (new_key, old_key)
        )
        db.execute(
            "UPDATE deals SET product_key = ?, dirty = 1 WHERE product_key = ?", (new_key, old_key)
        )

    # Backfill anything the earlier post was missing.
    for field in ("image_url", "coupon", "mrp", "sizes", "brand", "url", "clean_url"):
        if not merged.get(field) and deal.get(field):
            merged[field] = deal[field]
    if deal.get("discount_pct") and not merged.get("discount_pct"):
        merged["discount_pct"] = deal["discount_pct"]
    merged["score"] = compute_score(merged, now)

    db.upsert("deals", _encode(merged), conflict="id")
    return "merged" if is_new_channel else "updated"


def expire_stale() -> int:
    """Mark deals past their TTL as expired. Returns how many changed."""
    now = time.time()
    cur = db.execute(
        "UPDATE deals SET status='expired', dirty=1 WHERE status='live' AND expires_at <= ?",
        (now,),
    )
    return cur.rowcount or 0


def purge_ancient(days: int = 14) -> int:
    """Drop rows well past usefulness so the cache stays small."""
    cutoff = time.time() - days * 86400
    cur = db.execute("DELETE FROM deals WHERE last_seen_at < ?", (cutoff,))
    db.execute("DELETE FROM price_history WHERE seen_at < ?", (time.time() - 90 * 86400,))
    return cur.rowcount or 0


def rescore_all() -> int:
    """Recompute scores so recency decay stays honest between polls."""
    rows = db.query("SELECT * FROM deals WHERE status = 'live'")
    now = time.time()
    updates = []
    for row in rows:
        deal = db.row_to_dict(row) or {}
        updates.append((compute_score(deal, now), deal["id"]))
    if updates:
        db.execute_many("UPDATE deals SET score = ? WHERE id = ?", updates)
    return len(updates)


def mark_dead(deal_id: str, reason: str = "dead_link") -> None:
    row = db.query_one("SELECT flags FROM deals WHERE id = ?", (deal_id,))
    flags = []
    if row:
        try:
            flags = json.loads(row["flags"] or "[]")
        except (json.JSONDecodeError, TypeError):
            flags = []
    if reason not in flags:
        flags.append(reason)
    db.execute(
        "UPDATE deals SET status='dead', flags=?, dirty=1 WHERE id = ?",
        (json.dumps(flags), deal_id),
    )
