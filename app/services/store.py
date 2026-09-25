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
import time
from typing import Any, Dict, List, Optional

from rapidfuzz import fuzz

from .. import db
from ..config import settings
from . import parser

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
    from . import price_store  # local: price_store -> turso_backup -> db only
    points = price_store.cached_points(product_key)
    # Only write when the price actually moved — keeps the table small.
    if points and abs(points[-1][1] - float(price)) < 0.01:
        return
    db.execute(
        "INSERT INTO price_history (product_key, price, store, seen_at, turso_synced) VALUES (?, ?, ?, ?, ?)",
        (product_key, float(price), store, time.time(), 0 if settings.turso_configured else 1),
    )
    from . import price_alerts  # local: price_alerts -> push -> db only, but keep store import-light
    price_alerts.check(product_key, price)


def price_stats(product_key: str) -> Dict[str, Any]:
    """min/max/median of the recent history — powers the ALL-TIME LOW badge
    and the fake-MRP check. Local SQLite only holds a few days, so this uses
    the Turso history ingest prefetched (price_store.prefetch) plus local points."""
    from . import price_store
    return price_store.stats(price_store.cached_points(product_key))


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

    Matches purely on product identity (same_product's model-number-gated
    check), not price: a genuine price drop is exactly the case this needs to
    still catch, so requiring the price to also stay close would defeat the
    purpose — two posts of the same listing a week apart at very different
    prices are still the same listing.
    """
    norm = deal.get("norm_title")
    if not norm:
        return None

    rows = db.query(
        "SELECT * FROM deals WHERE store = ? AND expires_at > ? AND product_key != ? "
        "ORDER BY last_seen_at DESC LIMIT 25",
        (deal.get("store", ""), now, deal.get("product_key") or ""),
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

    # Any live deal sharing this product_key IS this listing, at whatever price
    # it's now at — canonical_product_key already vetted true product identity
    # (via same_product's model-number gating) when the key came from a title
    # hash, and an ASIN/pid key match is trivially the same listing regardless.
    # A price-proximity check here would only block the case that matters most:
    # catching a genuine price drop as an update to the same card instead of a
    # brand new one.
    existing = None
    if pkey:
        existing = db.query_one(
            "SELECT * FROM deals WHERE product_key = ? AND expires_at > ? "
            "ORDER BY last_seen_at DESC LIMIT 1",
            (pkey, now),
        )

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
        from . import devices  # local: keeps store importable without the device layer
        devices.match_follows(deal)
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
    # The existing row can be hours or days old by the time a new price comes
    # in (that's the whole point of merging on identity, not price proximity)
    # — posted_at needs to track the latest sighting or both the freshness
    # score and the "posted X ago" display go stale on an actively-tracked deal.
    merged["posted_at"] = max(float(existing["posted_at"] or 0), float(deal.get("posted_at") or now))
    merged["expires_at"] = max(
        float(existing["expires_at"] or 0),
        (deal.get("posted_at") or now) + settings.deal_ttl_hours * 3600,
    )
    merged["status"] = "live"
    merged["flags"] = deal["flags"]
    merged["is_lowest"] = deal.get("is_lowest", existing["is_lowest"])

    # The current price always wins — this card must show what the product
    # costs *now*, with price_history (already recorded above) carrying the
    # trend. MRP prefers the fresh value too, falling back to the last known
    # one; discount_pct is recomputed against the numbers that actually apply
    # after the update; without them, the freshly-parsed value is used.
    if price is not None:
        merged["price"] = price
    if deal.get("mrp") is not None:
        merged["mrp"] = deal["mrp"]
    if merged.get("price") and merged.get("mrp") and float(merged["mrp"]) > float(merged["price"]):
        merged["discount_pct"] = int(round((float(merged["mrp"]) - float(merged["price"])) / float(merged["mrp"]) * 100))
    elif deal.get("discount_pct"):
        merged["discount_pct"] = deal["discount_pct"]
    # A real marketplace id beats a title hash — upgrade if the new post has one,
    # and carry the price history across so the all-time-low flag stays correct.
    old_key = str(existing["product_key"] or "")
    new_key = str(deal.get("product_key") or "")
    if ":t:" in old_key and new_key and ":t:" not in new_key:
        merged["product_key"] = new_key
        db.execute(
            "UPDATE price_history SET product_key = ? WHERE product_key = ?", (new_key, old_key)
        )
        db.turso_enqueue(
            "UPDATE OR IGNORE price_points SET product_key = ? WHERE product_key = ?", (new_key, old_key),
            target="prices",
        )
        db.turso_enqueue("DELETE FROM price_points WHERE product_key = ?", (old_key,), target="prices")
        db.turso_enqueue("DELETE FROM products WHERE product_key = ?", (old_key,))
        db.execute(
            "UPDATE deals SET product_key = ?, dirty = 1 WHERE product_key = ?", (new_key, old_key)
        )

    # Backfill anything the earlier post was missing (price/mrp/discount_pct
    # are handled explicitly above, not here — they update, they don't just fill gaps).
    for field in ("image_url", "coupon", "sizes", "brand", "url", "clean_url"):
        if not merged.get(field) and deal.get(field):
            merged[field] = deal[field]
    merged["score"] = compute_score(merged, now)

    db.upsert("deals", _encode(merged), conflict="id")
    return "merged" if is_new_channel else "updated"


PRICE_HISTORY_FULL_RES_DAYS = 10  # keep every observation this recent


def rollup_price_history() -> int:
    """Collapse price_history older than 10 days to one row/product/day.

    The tracker only needs daily resolution to answer "lowest in N days" —
    keeping every poll's observation forever is pure row growth with no
    feature benefit. Rows within the full-resolution window are untouched.
    Returns how many rows were removed.
    """
    cutoff = time.time() - PRICE_HISTORY_FULL_RES_DAYS * 86400
    rows = db.query(
        "SELECT product_key, store, price, seen_at FROM price_history WHERE seen_at < ? AND turso_synced = 1",
        (cutoff,),
    )
    if not rows:
        return 0

    daily: Dict[tuple, Dict[str, Any]] = {}
    for r in rows:
        day = int(r["seen_at"] // 86400)
        key = (r["product_key"], r["store"], day)
        existing = daily.get(key)
        # keep the lowest price seen that day, with its own seen_at
        if existing is None or r["price"] < existing["price"]:
            daily[key] = {"product_key": r["product_key"], "store": r["store"],
                          "price": r["price"], "seen_at": r["seen_at"]}

    db.execute("DELETE FROM price_history WHERE seen_at < ? AND turso_synced = 1", (cutoff,))
    # Each kept row is an original observation already in Sheets and Turso —
    # re-flagging it unsynced would append a duplicate row to the Sheet.
    db.execute_many(
        "INSERT INTO price_history (product_key, price, store, seen_at, synced, turso_synced) "
        "VALUES (?, ?, ?, ?, 1, 1)",
        [(d["product_key"], d["price"], d["store"], d["seen_at"]) for d in daily.values()],
    )
    removed = len(rows) - len(daily)
    return max(removed, 0)


def expire_stale() -> int:
    """Mark deals past their TTL as expired. Returns how many changed."""
    now = time.time()
    cur = db.execute(
        "UPDATE deals SET status='expired', dirty=1 WHERE status='live' AND expires_at <= ?",
        (now,),
    )
    return cur.rowcount or 0


NO_IMAGE_MAX_SHARE = 0.20  # at most 1 in 5 live deals may be missing a photo


def enforce_image_ratio() -> int:
    """Keep live deals with vs without a photo at roughly 80:20.

    Deals without an image convert far worse on a visual grid — this caps
    how many can crowd out the ones with a real photo. Excess is expired
    (never deleted; archive search and the Sheet still see it), oldest
    first, down to NO_IMAGE_MAX_SHARE of the deals that do have an image.
    """
    with_image = db.query_one(
        "SELECT COUNT(*) AS c FROM deals WHERE status = 'live' AND COALESCE(image_url, '') != ''"
    )["c"]
    without_image = db.query_one(
        "SELECT COUNT(*) AS c FROM deals WHERE status = 'live' AND COALESCE(image_url, '') = ''"
    )["c"]
    allowed = int(with_image * NO_IMAGE_MAX_SHARE / (1 - NO_IMAGE_MAX_SHARE))
    excess = without_image - allowed
    if excess <= 0:
        return 0
    rows = db.query(
        "SELECT id FROM deals WHERE status = 'live' AND COALESCE(image_url, '') = '' "
        "ORDER BY last_seen_at ASC LIMIT ?",
        (excess,),
    )
    ids = [r["id"] for r in rows]
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    db.execute(
        f"UPDATE deals SET status = 'expired', dirty = 1 WHERE id IN ({placeholders})", ids
    )
    return len(ids)


def purge_ancient(days: int = 400) -> List[str]:
    """Every deal is kept — past ones power archive search and mirror the Sheet.

    Only price points older than `days` are trimmed. Returns [] (kept for the
    caller's signature).
    """
    db.execute("DELETE FROM price_history WHERE seen_at < ?", (time.time() - days * 86400,))
    return []


def purge_housekeeping(notified_days: int = 30, coupon_report_days: int = 90) -> Dict[str, int]:
    """Clear tables that otherwise grow forever on a long-running deployment.

    A sent-alert record in `notified` exists purely to dedupe future alerts
    and is worthless once old enough that the same deal would have expired
    anyway. (Web sessions used to need pruning here too, but they're now a
    stateless signed cookie — see auth.py — with nothing stored server-side.)
    A coupon report past this window is stale feedback on a deal that has
    long since expired; the count reaching COUPON_DEAD_THRESHOLD already
    suppressed the coupon while it mattered.
    """
    notified_cutoff = time.time() - notified_days * 86400
    notified = db.execute(
        "DELETE FROM notified WHERE sent_at < ?", (notified_cutoff,)
    ).rowcount or 0
    coupon_cutoff = time.time() - coupon_report_days * 86400
    coupon_reports = db.execute(
        "DELETE FROM coupon_reports WHERE reported_at < ?", (coupon_cutoff,)
    ).rowcount or 0
    return {"notified": notified, "coupon_reports": coupon_reports}


def purge_local_cache() -> Dict[str, int]:
    """Keep local SQLite to the last LOCAL_CACHE_DAYS (default 15) of data.

    Local SQLite is only the fast cache the app and website read from; the
    permanent copy of every deal and price point is in Turso (or, without
    Turso, the Google Sheet). A row is dropped only once it's safely there —
    a deal Turso hasn't received yet stays until it has. Deals that are still
    live are kept whatever their age, so the feed never loses them.
    """
    uploaded = {"deals": 0, "prices": 0}
    if settings.turso_configured:
        deal_safe = "turso_dirty = 0"
    elif settings.sheets_configured:
        deal_safe = "dirty = 0"
    else:
        return uploaded  # local SQLite is the only copy — never delete
    now = time.time()
    cutoff = now - settings.local_cache_days * 86400
    uploaded["deals"] = db.execute(
        f"DELETE FROM deals WHERE last_seen_at < ? AND {deal_safe} "
        "AND NOT (status = 'live' AND expires_at > ?)",
        (cutoff, now),
    ).rowcount or 0
    if settings.turso_configured:
        # Price stats read the full history from Turso (price_store.cached_points).
        uploaded["prices"] = db.execute(
            "DELETE FROM price_history WHERE seen_at < ? AND turso_synced = 1", (cutoff,)
        ).rowcount or 0
    return uploaded


def backfill_channel_ids() -> int:
    """Repair deals restored from Sheets before the Deals tab tracked
    channel_tg_id (they came back with channel_id=0, invisible to any
    channel-scoped query — a signed-in browsing their own tracked channels
    would see nothing at all, since 0 never matches a real channel's id).

    Resolves each affected deal's channel by matching its stored
    channel_title text against the known channels table, marks it dirty so
    the fix reaches Sheets on the next flush, and is safe to call
    repeatedly — deals with no title match, or already carrying a real
    channel_id, are left untouched.
    """
    rows = db.query(
        "SELECT d.id, c.tg_id FROM deals d JOIN channels c ON c.title = d.channel_title "
        "WHERE d.channel_id = 0 AND d.channel_title != ''"
    )
    if not rows:
        return 0
    db.execute_many(
        "UPDATE deals SET channel_id = ?, dirty = 1 WHERE id = ?",
        [(r["tg_id"], r["id"]) for r in rows],
    )
    return len(rows)


_PARSER_FLAGS = {"price_from", "upto_discount"}


def reparse_stored_deals() -> int:
    """Re-read every stored deal's price/MRP/discount from its raw post text.

    Deals are parsed once, when first saved — so rows written (or restored
    from Sheets) by an older parser keep its mistakes forever, e.g. a ₹300
    watch stored as ₹91. Runs once per parser.PARSER_VERSION. Corrected rows
    are marked dirty so the fix also reaches Sheets, and the wrong price is
    dropped from price history so it can't masquerade as an all-time low.
    """
    done = db.get_meta("parser_version")
    if done == str(parser.PARSER_VERSION):
        return 0

    fixed = 0
    now = time.time()
    for row in db.query("SELECT * FROM deals WHERE raw_text IS NOT NULL AND raw_text != ''"):
        old = db.row_to_dict(row) or {}
        fresh = parser.parse_message(
            old["raw_text"], channel_id=int(old.get("channel_id") or 0),
            channel_title=old.get("channel_title") or "", message_id=int(old.get("message_id") or 0),
            posted_at=float(old.get("posted_at") or now),
        )
        if fresh is None:
            if old.get("status") == "live":
                flags = sorted(set(list(old.get("flags") or []) + ["not_a_deal"]))
                db.execute("UPDATE deals SET status = 'dead', flags = ?, dirty = 1 WHERE id = ?",
                           (json.dumps(flags), old["id"]))
                fixed += 1
            continue
        parser_flags = {f for f in fresh.get("flags") or [] if f in _PARSER_FLAGS or f.startswith("min_buy_")}
        kept_flags = {f for f in old.get("flags") or [] if f not in _PARSER_FLAGS and not f.startswith("min_buy_")}
        new_flags = sorted(kept_flags | parser_flags)
        if new_flags != sorted(old.get("flags") or []) or fresh["title"] != old.get("title"):
            db.execute(
                "UPDATE deals SET title = ?, norm_title = ?, search_blob = ?, flags = ?, dirty = 1 WHERE id = ?",
                (fresh["title"], fresh["norm_title"], fresh["search_blob"], json.dumps(new_flags), old["id"]),
            )
            fixed += 1
        if fresh.get("price") is None:
            continue
        changes = {k: fresh.get(k) for k in ("price", "mrp", "discount_pct")
                   if fresh.get(k) != old.get(k)}
        if not changes:
            continue
        wrong_price = old.get("price")
        updated = {**old, **changes}
        if "price" in changes and wrong_price is not None and old.get("product_key"):
            db.execute(
                "DELETE FROM price_history WHERE product_key = ? AND ABS(price - ?) < 0.01",
                (old["product_key"], float(wrong_price)),
            )
            db.turso_enqueue(
                "DELETE FROM price_points WHERE product_key = ? AND ABS(price - ?) < 0.01",
                (old["product_key"], float(wrong_price)), target="prices",
            )
            record_price(old["product_key"], updated["price"], old.get("store") or "")
            stats = price_stats(old["product_key"])
            updated["is_lowest"] = int(bool(stats.get("points", 0) >= 2 and stats.get("min") is not None
                                            and float(updated["price"]) <= float(stats["min"])))
        updated["score"] = compute_score(updated, now)
        db.execute(
            "UPDATE deals SET price = ?, mrp = ?, discount_pct = ?, is_lowest = ?, score = ?, dirty = 1 "
            "WHERE id = ?",
            (updated["price"], updated.get("mrp"), int(updated.get("discount_pct") or 0),
             int(updated.get("is_lowest") or 0), updated["score"], old["id"]),
        )
        fixed += 1

    db.set_meta("parser_version", str(parser.PARSER_VERSION))
    return fixed


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
