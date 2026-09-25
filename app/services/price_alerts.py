"""Price-drop alerts for visitors — tied to a device, no account.

A visitor asks "tell me when this drops below ₹X". Every time the server sees
a new price for that product (a channel post or a stock check), `check()`
fires matching alerts: the website/app polls for them, and the app also gets
an Expo push when it registered a token.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional

from .. import db

log = logging.getLogger(__name__)

MAX_PER_DEVICE = 50
_DEVICE_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")


def valid_device(device_id: str) -> bool:
    return bool(_DEVICE_RE.match(device_id or ""))


def create(device_id: str, deal: Dict[str, Any], target_price: float, push_token: str = "") -> Dict[str, Any]:
    count = db.query_one("SELECT COUNT(*) AS c FROM price_alerts WHERE device_id = ? AND triggered_at IS NULL",
                         (device_id,))["c"]
    if count >= MAX_PER_DEVICE:
        raise ValueError(f"You can watch at most {MAX_PER_DEVICE} prices at once.")
    now = time.time()
    db.execute(
        "INSERT INTO price_alerts (device_id, deal_id, product_key, title, target_price, start_price, "
        "push_token, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (device_id, deal["id"], deal.get("product_key") or "", deal.get("title") or "", float(target_price),
         deal.get("price"), push_token or "", now),
    )
    row = db.query_one("SELECT * FROM price_alerts WHERE rowid = last_insert_rowid()")
    alert = dict(row)
    # Already at or below target: tell them straight away rather than silently waiting.
    if deal.get("price") is not None and float(deal["price"]) <= float(target_price):
        _trigger([alert], float(deal["price"]))
        alert = dict(db.query_one("SELECT * FROM price_alerts WHERE id = ?", (alert["id"],)))
    return alert


def list_for(device_id: str) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT a.*, d.price AS current_price, d.image_url, d.store FROM price_alerts a "
        "LEFT JOIN deals d ON d.id = a.deal_id WHERE a.device_id = ? ORDER BY a.created_at DESC",
        (device_id,),
    )
    return [dict(r) for r in rows]


def delete(device_id: str, alert_id: int) -> bool:
    row = db.query_one("SELECT created_at FROM price_alerts WHERE id = ? AND device_id = ?",
                       (alert_id, device_id))
    if not row:
        return False
    db.execute("DELETE FROM price_alerts WHERE id = ? AND device_id = ?", (alert_id, device_id))
    db.turso_enqueue("DELETE FROM price_alerts WHERE device_id = ? AND created_at = ?",
                     (device_id, row["created_at"]))
    return True


def check(product_key: str, price: Optional[float]) -> int:
    """Fire every waiting alert for this product whose target the new price meets."""
    if not product_key or price is None:
        return 0
    rows = db.query(
        "SELECT * FROM price_alerts WHERE product_key = ? AND triggered_at IS NULL AND target_price >= ?",
        (product_key, float(price)),
    )
    if rows:
        _trigger([dict(r) for r in rows], float(price))
    return len(rows)


def _trigger(alerts: List[Dict[str, Any]], price: float) -> None:
    now = time.time()
    db.execute_many("UPDATE price_alerts SET triggered_at = ?, triggered_price = ?, turso_dirty = 1 WHERE id = ?",
                    [(now, price, a["id"]) for a in alerts])
    from . import devices
    for a in alerts:
        row = db.query_one("SELECT * FROM deals WHERE id = ?", (a["deal_id"],))
        deal = dict(row) if row else {"id": a["deal_id"]}
        devices.notify(
            a["device_id"], "price_drop", f"📉 Price drop: ₹{int(price):,}",
            f"{(a.get('title') or 'Your deal')[:90]} — below your ₹{int(a['target_price']):,} alert",
            deal, extra_token=a.get("push_token") or "",
        )
