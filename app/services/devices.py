"""Visitor devices: push token, follows, daily digest and a notification feed.

No accounts — a device is a random id the app/site generates. Everything a
device should hear about (price drops, followed categories/brands, the daily
digest) goes into its feed; if it registered an Expo push token the same item
is also pushed with the product image (Myntra-style). The app polls the feed
in the background, so notifications work even before Firebase is configured.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from .. import db
from ..config import settings

log = logging.getLogger(__name__)

IST = timezone(timedelta(hours=5, minutes=30))
FOLLOW_KINDS = ("category", "brand", "store")
MAX_FOLLOWS = 30
# One follow push per device per window, so a busy channel can't spam a phone.
FOLLOW_COOLDOWN_SECONDS = 20 * 60
FEED_RETENTION_DAYS = 14


def absolute_image(image_url: Optional[str]) -> str:
    if not image_url:
        return ""
    if image_url.startswith("http"):
        return image_url
    if image_url.startswith("/") and settings.public_url:
        return settings.public_url + image_url
    return ""


def register(device_id: str, platform: str = "", push_token: Optional[str] = None,
             digest: Optional[bool] = None, digest_hour: Optional[int] = None,
             smart_schedule: Optional[bool] = None) -> Dict[str, Any]:
    now = time.time()
    db.execute(
        "INSERT INTO devices (device_id, platform, created_at, last_seen_at, turso_dirty) VALUES (?, ?, ?, ?, 1) "
        "ON CONFLICT(device_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, turso_dirty = 1, "
        "platform = COALESCE(NULLIF(excluded.platform, ''), devices.platform)",
        (device_id, platform or "", now, now),
    )
    if push_token is not None:
        db.execute("UPDATE devices SET push_token = ? WHERE device_id = ?", (push_token, device_id))
    if digest is not None:
        db.execute("UPDATE devices SET digest = ? WHERE device_id = ?", (1 if digest else 0, device_id))
    if digest_hour is not None:
        db.execute("UPDATE devices SET digest_hour = ? WHERE device_id = ?", (int(digest_hour) % 24, device_id))
    if smart_schedule is not None:
        db.execute("UPDATE devices SET smart_schedule = ? WHERE device_id = ?", (1 if smart_schedule else 0, device_id))
    return get(device_id)


def get(device_id: str) -> Dict[str, Any]:
    row = db.query_one("SELECT * FROM devices WHERE device_id = ?", (device_id,))
    if not row:
        return {"device_id": device_id, "digest": False, "digest_hour": 19, "has_push": False}
    return {"device_id": device_id, "digest": bool(row["digest"]), "digest_hour": row["digest_hour"],
            "has_push": bool(row["push_token"])}


def follows(device_id: str) -> List[Dict[str, Any]]:
    return [dict(r) for r in db.query(
        "SELECT id, kind, value, min_discount, created_at FROM device_follows WHERE device_id = ? ORDER BY created_at DESC",
        (device_id,))]


def add_follow(device_id: str, kind: str, value: str, min_discount: int = 0) -> Dict[str, Any]:
    if kind not in FOLLOW_KINDS:
        raise ValueError(f"kind must be one of {FOLLOW_KINDS}")
    value = value.strip()
    if not value:
        raise ValueError("Pick something to follow.")
    count = db.query_one("SELECT COUNT(*) AS c FROM device_follows WHERE device_id = ?", (device_id,))["c"]
    if count >= MAX_FOLLOWS:
        raise ValueError(f"You can follow at most {MAX_FOLLOWS} things.")
    register(device_id)
    existing = db.query_one("SELECT id FROM device_follows WHERE device_id = ? AND kind = ? AND LOWER(value) = LOWER(?)",
                            (device_id, kind, value))
    if existing:
        db.execute("UPDATE device_follows SET min_discount = ? WHERE id = ?", (int(min_discount or 0), existing["id"]))
        follow_id = existing["id"]
    else:
        follow_id = db.execute(
            "INSERT INTO device_follows (device_id, kind, value, min_discount, created_at) VALUES (?, ?, ?, ?, ?)",
            (device_id, kind, value, int(min_discount or 0), time.time()),
        ).lastrowid
    # Again after the write: follows travel inside the device row in Turso.
    db.execute("UPDATE devices SET turso_dirty = 1 WHERE device_id = ?", (device_id,))
    return dict(db.query_one("SELECT id, kind, value, min_discount, created_at FROM device_follows WHERE id = ?",
                             (follow_id,)))


def remove_follow(device_id: str, follow_id: int) -> bool:
    removed = bool(db.execute("DELETE FROM device_follows WHERE id = ? AND device_id = ?",
                              (follow_id, device_id)).rowcount)
    if removed:
        db.execute("UPDATE devices SET turso_dirty = 1 WHERE device_id = ?", (device_id,))
    return removed


BROADCAST_FEED_WINDOW = 3 * 86400  # a phone offline longer than this skips old broadcasts

# Deal fields ride along so the app can write its own copy ("{brand} at {discount}% off").
_FEED_COLS = (
    "n.id, n.kind, n.title, n.body, n.image_url, n.deal_id, n.url, n.expires_at, n.created_at, "
    "d.price, d.mrp, d.discount_pct, d.brand, d.category, d.subcategory, d.store, d.score, "
    "d.title AS deal_title"
)

# Kinds the user explicitly asked to hear about the moment they happen; every
# other kind waits for the phone's own schedule on smart_schedule devices.
INSTANT_KINDS = ("price_drop", "broadcast")


def feed(device_id: str, since: float = 0.0, limit: int = 20) -> List[Dict[str, Any]]:
    """The device's own items plus every broadcast (hot deals, admin messages).

    Broadcasts live in one shared table rather than a copy per device, so a
    phone that registered after the send — or was lost from `devices` in a
    restart — still gets them the next time it polls. Their ids carry a "b"
    prefix so they never collide with a device item's id (the app de-dupes
    on id).
    """
    own = [dict(r) for r in db.query(
        f"SELECT {_FEED_COLS} FROM device_notifications n LEFT JOIN deals d ON d.id = n.deal_id "
        "WHERE n.device_id = ? AND n.created_at > ? ORDER BY n.created_at DESC LIMIT ?",
        (device_id, since, limit))]
    floor = max(float(since or 0), time.time() - BROADCAST_FEED_WINDOW)
    shared = []
    for r in db.query(
        f"SELECT {_FEED_COLS} FROM broadcasts n LEFT JOIN deals d ON d.id = n.deal_id "
        "WHERE n.created_at > ? ORDER BY n.created_at DESC LIMIT ?", (floor, limit)
    ):
        item = dict(r)
        item["id"] = f"b{item['id']}"
        shared.append(item)
    items = sorted(own + shared, key=lambda i: float(i.get("created_at") or 0), reverse=True)
    return items[:limit]


def notify(device_id: str, kind: str, title: str, body: str, deal: Optional[Dict[str, Any]] = None,
           extra_token: str = "") -> None:
    """Add to the device's feed and push it (with the product image) if possible."""
    deal = deal or {}
    image = absolute_image(deal.get("image_url"))
    deal_id = deal.get("id") or ""
    url = f"/?deal={deal_id}" if deal_id else "/"
    expires_at = float(deal.get("expires_at") or 0)
    db.execute(
        "INSERT INTO device_notifications (device_id, kind, title, body, image_url, deal_id, url, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, kind, title, body, image, deal_id, url, expires_at, time.time()),
    )
    row = db.query_one("SELECT push_token, smart_schedule FROM devices WHERE device_id = ?", (device_id,))
    if row and row["smart_schedule"] and kind not in INSTANT_KINDS:
        return  # the phone reads it from its feed and picks the moment itself
    tokens = [t for t in {(row["push_token"] if row else ""), extra_token} if t]
    if not tokens:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no event loop (scripts/tests): the device reads it from its feed instead
    from . import push
    loop.create_task(push.send_push(tokens, title, body, url, deal_id or None, image=image, kind=kind,
                                     expires_at=expires_at))


def active_counts() -> Dict[str, Any]:
    """Real users for the admin panel: app installs (not website visitors)
    opened since ACTIVE_DEVICES_SINCE, and how many of those can get a push."""
    from . import fcm
    since = settings.active_devices_since
    rows = db.query(
        "SELECT push_token FROM devices WHERE last_seen_at >= ? AND platform IN ('android', 'ios')", (since,))
    return {
        "active_devices": len(rows),
        "reachable": sum(1 for r in rows if fcm.is_fcm_token(r["push_token"] or "")),
        "active_since": since,
    }


def _all_tokens(include_smart: bool = True) -> List[str]:
    smart = "" if include_smart else " AND COALESCE(smart_schedule, 0) = 0"
    rows = db.query(f"SELECT push_token FROM devices WHERE COALESCE(push_token, '') != ''{smart}")
    legacy = db.query("SELECT token FROM push_tokens")  # signed-in (older) app installs
    return list(dict.fromkeys([r["push_token"] for r in rows] + [r["token"] for r in legacy]))


async def broadcast(title: str, body: str, deal: Optional[Dict[str, Any]] = None,
                    kind: str = "broadcast") -> Dict[str, Any]:
    """Send one message to everyone: a shared feed row every device polls, plus
    a push to every registered token (batched, 100 per Expo request).

    Returns what actually happened, for the admin panel:
      {"devices", "tokens", "accepted", "failed", "errors": {code: n}, "broadcast_id"}
    """
    deal = deal or {}
    image = absolute_image(deal.get("image_url"))
    deal_id = deal.get("id") or ""
    url = f"/?deal={deal_id}" if deal_id else "/"
    expires_at = float(deal.get("expires_at") or 0)
    cur = db.execute(
        "INSERT INTO broadcasts (kind, title, body, image_url, deal_id, url, expires_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (kind, title, body, image, deal_id, url, expires_at, time.time()),
    )
    devices_count = db.query_one("SELECT COUNT(*) AS c FROM devices")["c"]
    from . import push
    tokens = _all_tokens(include_smart=kind in INSTANT_KINDS)
    report = await push.send_push_detailed(tokens, title, body, url, deal_id or None,
                                           image=image, kind=kind, expires_at=expires_at)
    log.info("Broadcast %r: %d devices, %d tokens, %d accepted, errors=%s",
             title, devices_count, report["tokens"], report["accepted"], report["errors"])
    return {"devices": devices_count, "broadcast_id": cur.lastrowid, **active_counts(), **report}


def prune_broadcasts() -> None:
    db.execute("DELETE FROM broadcasts WHERE created_at < ?", (time.time() - FEED_RETENTION_DAYS * 86400,))


def _money(value: Any) -> str:
    return f"₹{int(float(value)):,}" if value not in (None, "") else ""


def match_follows(deal: Dict[str, Any]) -> int:
    """A brand-new deal arrived: tell every device following its category/brand/store."""
    checks = [("category", deal.get("category")), ("brand", deal.get("brand")), ("store", deal.get("store"))]
    clauses, params = [], []
    for kind, value in checks:
        if value:
            clauses.append("(kind = ? AND LOWER(value) = LOWER(?))")
            params.extend([kind, value])
    if not clauses:
        return 0
    rows = db.query(
        f"SELECT f.id, f.device_id, f.kind, f.value, f.min_discount, d.last_follow_push_at FROM device_follows f "
        f"JOIN devices d ON d.device_id = f.device_id WHERE ({' OR '.join(clauses)})",
        params,
    )
    now = time.time()
    sent = 0
    seen_devices = set()
    for r in rows:
        if r["device_id"] in seen_devices:
            continue
        if int(deal.get("discount_pct") or 0) < int(r["min_discount"] or 0):
            continue
        if now - float(r["last_follow_push_at"] or 0) < FOLLOW_COOLDOWN_SECONDS:
            continue
        seen_devices.add(r["device_id"])
        off = f" · {deal['discount_pct']}% off" if deal.get("discount_pct") else ""
        notify(r["device_id"], "follow", f"New {r['value']} deal {_money(deal.get('price'))}".strip(),
               f"{(deal.get('title') or '')[:90]}{off}", deal)
        db.execute("UPDATE devices SET last_follow_push_at = ? WHERE device_id = ?", (now, r["device_id"]))
        sent += 1
    return sent


def digest_tick() -> int:
    """Once per ingest cycle: send the daily digest to devices whose hour it is (IST)."""
    now_ist = datetime.now(IST)
    today = now_ist.strftime("%Y-%m-%d")
    rows = db.query("SELECT device_id FROM devices WHERE digest = 1 AND digest_hour = ? "
                    "AND COALESCE(last_digest_day, '') != ?", (now_ist.hour, today))
    if not rows:
        return 0
    top = db.query_one(
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND first_seen_at > ? "
        "AND COALESCE(image_url, '') != '' ORDER BY score DESC LIMIT 1", (time.time(), time.time() - 86400))
    count = db.query_one("SELECT COUNT(*) AS c FROM deals WHERE status = 'live' AND first_seen_at > ?",
                         (time.time() - 86400,))["c"]
    if not top:
        return 0
    top = dict(top)
    for r in rows:
        notify(r["device_id"], "digest", f"🔥 Today's best: {_money(top.get('price'))} {(top.get('title') or '')[:40]}",
               f"{count} new deals today — tap to see the top picks", top)
        db.execute("UPDATE devices SET last_digest_day = ? WHERE device_id = ?", (today, r["device_id"]))
    return len(rows)


WEEKLY_DIGEST_SECONDS = 7 * 86400


def _best_for_follows(device_id: str) -> Optional[Dict[str, Any]]:
    """Best live deal matching what this device follows (category/brand/store)."""
    rows = db.query("SELECT kind, value FROM device_follows WHERE device_id = ?", (device_id,))
    if not rows:
        return None
    clauses, params = [], []
    for r in rows:
        clauses.append(f"LOWER({r['kind']}) = LOWER(?)")
        params.append(r["value"])
    sql = (
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND COALESCE(image_url, '') != '' "
        "AND (" + " OR ".join(clauses) +
        ") ORDER BY score DESC LIMIT 1"
    )
    row = db.query_one(sql, [time.time()] + params)
    return dict(row) if row else None


def weekly_digest_tick() -> int:
    """Once a week: the best deal in each device's own followed categories/brands/stores.

    Opt-in and first-party only — built from what the device explicitly follows,
    never from any inferred trait. Devices with no follows get nothing (there is
    nothing "personal" to show yet).
    """
    cutoff = time.time() - WEEKLY_DIGEST_SECONDS
    rows = db.query(
        "SELECT DISTINCT d.device_id FROM devices d JOIN device_follows f ON f.device_id = d.device_id "
        "WHERE COALESCE(d.last_weekly_digest_at, 0) < ?", (cutoff,))
    sent = 0
    now = time.time()
    for r in rows:
        deal = _best_for_follows(r["device_id"])
        db.execute("UPDATE devices SET last_weekly_digest_at = ? WHERE device_id = ?", (now, r["device_id"]))
        if not deal:
            continue
        off = f" · {deal['discount_pct']}% off" if deal.get("discount_pct") else ""
        notify(r["device_id"], "weekly_pick", f"⭐ Picked for you: {_money(deal.get('price'))}",
               f"{(deal.get('title') or '')[:90]}{off}", deal)
        sent += 1
    return sent


def prune() -> None:
    db.execute("DELETE FROM device_notifications WHERE created_at < ?", (time.time() - FEED_RETENTION_DAYS * 86400,))
    prune_broadcasts()
