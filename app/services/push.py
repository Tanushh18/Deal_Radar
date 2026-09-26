"""App notifications: a per-user feed in SQLite plus push delivery via FCM.

The feed row is written first and is the source of truth; push is a
best-effort nudge on top, sent straight to Firebase Cloud Messaging
(services/fcm.py). Nothing here raises into the caller.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

from .. import db
from . import fcm

log = logging.getLogger(__name__)

# Mirrors mobile/src/native/notifications.ts CHANNELS — keep both in sync.
CHANNEL_ID = "deal-alerts"  # legacy fallback for pre-channel-split installs
CHANNEL_BY_KIND = {
    "price_drop": "price-drops",
    "digest": "daily-deals",
    "weekly_pick": "daily-deals",
    "follow": "flash-sales",
    "hot_deal": "flash-sales",
    "broadcast": "flash-sales",
}
RETENTION_DAYS = 30


def is_push_token(token: str) -> bool:
    """A raw FCM device token — the only kind we send to."""
    return fcm.is_fcm_token(token)


def register_token(user_id: int, token: str, platform: str) -> None:
    now = time.time()
    db.execute(
        "INSERT INTO push_tokens (user_id, token, platform, created_at, last_seen_at) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(token) DO UPDATE SET user_id=excluded.user_id, "
        "platform=excluded.platform, last_seen_at=excluded.last_seen_at",
        (user_id, token, platform, now, now),
    )


def unregister_token(user_id: int, token: str) -> None:
    db.execute("DELETE FROM push_tokens WHERE user_id = ? AND token = ?", (user_id, token))


def user_tokens(user_id: int) -> List[str]:
    rows = db.query("SELECT token FROM push_tokens WHERE user_id = ?", (user_id,))
    return [r["token"] for r in rows]


def create_notification(
    user_id: int, title: str, body: str, url: str, deal_id: Optional[str] = None
) -> Dict[str, Any]:
    now = time.time()
    cur = db.execute(
        "INSERT INTO notifications (user_id, deal_id, title, body, url, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, deal_id, title, body, url, now),
    )
    return {
        "id": cur.lastrowid,
        "deal_id": deal_id,
        "title": title,
        "body": body,
        "url": url,
        "created_at": now,
    }


def list_notifications(user_id: int, since: float = 0.0, limit: int = 20) -> List[Dict[str, Any]]:
    rows = db.query(
        "SELECT id, deal_id, title, body, url, created_at FROM notifications "
        "WHERE user_id = ? AND created_at > ? ORDER BY created_at DESC, id DESC LIMIT ?",
        (user_id, since, limit),
    )
    return [dict(r) for r in rows]


def prune_notifications(days: int = RETENTION_DAYS) -> int:
    cutoff = time.time() - days * 86400
    return db.execute("DELETE FROM notifications WHERE created_at < ?", (cutoff,)).rowcount or 0


async def send_push_detailed(
    tokens: List[str], title: str, body: str, url: str, deal_id: Optional[str] = None,
    image: str = "", kind: str = "", expires_at: float = 0,
) -> Dict[str, Any]:
    """Send to every FCM token. Never raises.

    Returns {"tokens", "accepted", "failed", "errors": {code: count}} so a
    caller (the admin panel) can say *why* nothing arrived — e.g.
    FcmNotConfigured means FCM_SERVICE_ACCOUNT_JSON isn't set on the server.
    Tokens from old app builds (Expo tokens) are skipped.
    """
    fcm_tokens = [t for t in dict.fromkeys(tokens) if fcm.is_fcm_token(t)]
    report = await fcm.send(
        fcm_tokens, title, body,
        {"url": url, "deal_id": deal_id, "image_url": image, "kind": kind, "expires_at": expires_at},
        CHANNEL_BY_KIND.get(kind, CHANNEL_ID),
    )
    _forget(report.pop("dead", []))
    return report


def _forget(dead: List[str]) -> None:
    """Drop tokens the push service says belong to an uninstalled app."""
    for token in dead:
        try:
            db.execute("DELETE FROM push_tokens WHERE token = ?", (token,))
            db.execute("UPDATE devices SET push_token = '', turso_dirty = 1 WHERE push_token = ?", (token,))
        except Exception as exc:  # noqa: BLE001
            log.warning("Couldn't drop dead push token: %s", exc)


async def send_push(
    tokens: List[str], title: str, body: str, url: str, deal_id: Optional[str] = None,
    image: str = "", kind: str = "", expires_at: float = 0,
) -> int:
    """Send to every token; returns how many Expo accepted. Never raises."""
    report = await send_push_detailed(tokens, title, body, url, deal_id, image=image, kind=kind,
                                      expires_at=expires_at)
    return report["accepted"]


async def notify(
    user_id: int, title: str, body: str, url: str, deal_id: Optional[str] = None
) -> Dict[str, Any]:
    """Record a feed entry and push it. Returns the notification plus push_sent."""
    note = create_notification(user_id, title, body, url, deal_id)
    try:
        pushed = await send_push(user_tokens(user_id), title, body, url, deal_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Push for user %s failed: %s", user_id, exc)
        pushed = 0
    return {"notification": note, "push_sent": pushed}
