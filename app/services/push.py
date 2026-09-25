"""App notifications: a per-user feed in SQLite plus Expo push delivery.

The feed row is written first and is the source of truth; push is a
best-effort nudge on top. Nothing here raises into the caller.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from .. import db

log = logging.getLogger(__name__)

EXPO_PUSH_URL = "https://exp.host/--/api/v2/push/send"
BATCH_SIZE = 100
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
TOKEN_PREFIXES = ("ExponentPushToken[", "ExpoPushToken[")
RETENTION_DAYS = 30


def is_expo_token(token: str) -> bool:
    return bool(token) and token.startswith(TOKEN_PREFIXES) and token.endswith("]") and len(token) <= 200


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


async def _post_batch(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One HTTP call to Expo; returns the ticket list. Tests monkeypatch this."""
    async with httpx.AsyncClient(timeout=6.0) as client:
        resp = await client.post(
            EXPO_PUSH_URL,
            json=messages,
            headers={"Accept": "application/json", "Content-Type": "application/json"},
        )
        resp.raise_for_status()
        data = resp.json().get("data") or []
        return data if isinstance(data, list) else []


async def send_push_detailed(
    tokens: List[str], title: str, body: str, url: str, deal_id: Optional[str] = None,
    image: str = "", kind: str = "", expires_at: float = 0,
) -> Dict[str, Any]:
    """Send to every token (batched, 100 per Expo request). Never raises.

    Returns {"tokens", "accepted", "failed", "errors": {expo error code: count}}
    so a caller (the admin panel) can say *why* nothing arrived — e.g.
    InvalidCredentials means the EAS project has no FCM key uploaded.
    """
    tokens = [t for t in dict.fromkeys(tokens) if is_expo_token(t)]
    report: Dict[str, Any] = {"tokens": len(tokens), "accepted": 0, "failed": 0, "errors": {}}
    if not tokens:
        return report

    def _error(code: str) -> None:
        report["failed"] += 1
        report["errors"][code] = report["errors"].get(code, 0) + 1

    messages = [
        {
            "to": token,
            "title": title,
            "body": body,
            "sound": "default",
            "priority": "high",
            "channelId": CHANNEL_BY_KIND.get(kind, CHANNEL_ID),
            "data": {"url": url, "deal_id": deal_id, "image_url": image, "kind": kind, "expires_at": expires_at},
            # Product photo on the notification (thumbnail on Android, like Myntra).
            **({"richContent": {"image": image}} if image else {}),
        }
        for token in tokens
    ]
    dead: List[str] = []
    for i in range(0, len(messages), BATCH_SIZE):
        batch = messages[i : i + BATCH_SIZE]
        try:
            tickets = await _post_batch(batch)
        except Exception as exc:  # noqa: BLE001
            log.warning("Expo push failed for %d message(s): %s", len(batch), exc)
            for _ in batch:
                _error("RequestFailed")
            continue
        # Expo returns tickets in the same order as the messages sent.
        for message, ticket in zip(batch, tickets):
            if not isinstance(ticket, dict):
                _error("BadTicket")
                continue
            if ticket.get("status") == "ok":
                report["accepted"] += 1
                continue
            code = (ticket.get("details") or {}).get("error") or "Unknown"
            _error(code)
            if code == "DeviceNotRegistered":
                dead.append(message["to"])
            else:
                log.info("Expo push error %s: %s", code, ticket.get("message"))
    for token in dead:
        try:
            db.execute("DELETE FROM push_tokens WHERE token = ?", (token,))
            db.execute("UPDATE devices SET push_token = '', turso_dirty = 1 WHERE push_token = ?", (token,))
        except Exception as exc:  # noqa: BLE001
            log.warning("Couldn't drop dead push token: %s", exc)
    return report


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
