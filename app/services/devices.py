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
             digest: Optional[bool] = None, digest_hour: Optional[int] = None) -> Dict[str, Any]:
    now = time.time()
    db.execute(
        "INSERT INTO devices (device_id, platform, created_at, last_seen_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(device_id) DO UPDATE SET last_seen_at = excluded.last_seen_at, "
        "platform = COALESCE(NULLIF(excluded.platform, ''), devices.platform)",
        (device_id, platform or "", now, now),
    )
    if push_token is not None:
        db.execute("UPDATE devices SET push_token = ? WHERE device_id = ?", (push_token, device_id))
    if digest is not None:
        db.execute("UPDATE devices SET digest = ? WHERE device_id = ?", (1 if digest else 0, device_id))
    if digest_hour is not None:
        db.execute("UPDATE devices SET digest_hour = ? WHERE device_id = ?", (int(digest_hour) % 24, device_id))
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
    return dict(db.query_one("SELECT id, kind, value, min_discount, created_at FROM device_follows WHERE id = ?",
                             (follow_id,)))


def remove_follow(device_id: str, follow_id: int) -> bool:
    return bool(db.execute("DELETE FROM device_follows WHERE id = ? AND device_id = ?",
                           (follow_id, device_id)).rowcount)


def feed(device_id: str, since: float = 0.0, limit: int = 20) -> List[Dict[str, Any]]:
    return [dict(r) for r in db.query(
        "SELECT id, kind, title, body, image_url, deal_id, url, expires_at, created_at FROM device_notifications "
        "WHERE device_id = ? AND created_at > ? ORDER BY created_at DESC LIMIT ?",
        (device_id, since, limit))]


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
    row = db.query_one("SELECT push_token FROM devices WHERE device_id = ?", (device_id,))
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


def broadcast(title: str, body: str, deal: Optional[Dict[str, Any]] = None) -> int:
    """Admin-triggered: send one message to every device that has ever registered."""
    ids = [r["device_id"] for r in db.query("SELECT device_id FROM devices")]
    for device_id in ids:
        notify(device_id, "broadcast", title, body, deal)
    return len(ids)


def broadcast_best(candidate_ids: List[str], min_score: float) -> Optional[Dict[str, Any]]:
    """Auto-triggered once per ingest cycle: the single standout new deal, to
    every device — no digest toggle, no follow match required to receive it.

    `candidate_ids` is this cycle's own new_deal_ids, never "best deal we
    have," so the same favourite never gets re-broadcast cycle after cycle.
    Only fires when something actually crosses `min_score` — most cycles find
    nothing that good, and this must not become a notification every 40 min
    regardless of quality. Picked after liveness/scoring so a dead or
    since-retired card is never the one pushed.
    """
    if not candidate_ids:
        return None
    marks = ",".join("?" for _ in candidate_ids)
    row = db.query_one(
        f"SELECT * FROM deals WHERE id IN ({marks}) AND status = 'live' "
        f"AND COALESCE(image_url, '') != '' ORDER BY score DESC LIMIT 1",
        candidate_ids,
    )
    if not row or float(row["score"] or 0) < min_score:
        return None
    deal = dict(row)
    bits = [_money(deal.get("price"))] if deal.get("price") else []
    if deal.get("discount_pct"):
        bits.append(f"{deal['discount_pct']}% off")
    if deal.get("store"):
        bits.append(str(deal["store"]).title())
    if deal.get("is_lowest"):
        bits.append("lowest we've ever seen")
    name = (deal.get("title") or "").strip()
    if len(name) > 60:
        name = name[:59].rstrip() + "…"
    title = f"🔥 {name}" if name else "🔥 A deal just landed"
    body = " · ".join(bits) or "Worth a look"
    sent = broadcast(title, body, deal)
    return {"deal_id": deal["id"], "score": deal["score"], "devices": sent}


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
