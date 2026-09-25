"""Public mode: the server's own Telegram account reads PUBLIC_CHANNELS.

The account (TELEGRAM_SESSION, made once with tools/make_session.py) is stored
like any signed-in user, so the existing ingest/dedup/alert pipeline reads the
channels unchanged — it just becomes the single reader for everyone.
"""
from __future__ import annotations

import asyncio
import logging
import time

from .. import db
from ..config import settings
from . import mongo_store, telegram

log = logging.getLogger("dealradar.public")


async def bootstrap() -> int:
    """Register the reader account and track every public channel. Returns channels tracked."""
    from ..routers.channels import _deactivate_orphans, _register_channel

    client = telegram._new_client(settings.telegram_session)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            log.error("TELEGRAM_SESSION is not authorised — regenerate it with tools/make_session.py")
            return 0
        me = await client.get_me()
    finally:
        await client.disconnect()

    now = time.time()
    db.execute(
        "INSERT INTO users (telegram_id, username, first_name, phone, session_enc, created_at, last_login_at) "
        "VALUES (?, ?, ?, '', ?, ?, ?) ON CONFLICT(telegram_id) DO UPDATE SET "
        "session_enc = excluded.session_enc, last_login_at = excluded.last_login_at",
        (me.id, me.username or "", me.first_name or "DealRadar reader",
         telegram.encrypt_session(settings.telegram_session), now, now),
    )
    user_id = int(db.query_one("SELECT id FROM users WHERE telegram_id = ?", (me.id,))["id"])
    await telegram.drop_client(user_id)   # a stale cached client from an older session must not linger
    db.set_meta("reader_user_id", str(user_id))

    tracked = 0
    for username in settings.public_channels:
        known = db.query_one(
            "SELECT c.id FROM channels c JOIN user_channels uc ON uc.channel_id = c.id "
            "WHERE LOWER(c.username) = LOWER(?) AND uc.user_id = ? AND uc.enabled = 1",
            (username, user_id),
        )
        if known:
            tracked += 1
            continue
        try:
            info = await telegram.resolve_public_channel(user_id, username)
        except Exception as exc:  # noqa: BLE001 — one bad username must not stop the rest
            log.warning("Public channel @%s skipped: %s", username, exc)
            continue
        channel_id = _register_channel(info, user_id)
        db.execute(
            "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = 1",
            (user_id, channel_id, now),
        )
        tracked += 1
        await asyncio.sleep(2)   # joining channels back-to-back invites a Telegram flood-wait
    _deactivate_orphans()
    log.info("Public mode: joined %d/%d listed channels as @%s", tracked, len(settings.public_channels), me.username or me.id)
    try:
        await sync_followed(user_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("Initial follow-list sync failed: %s", exc)
    return tracked


_last_follow_sync = 0.0
FOLLOW_SYNC_SECONDS = 1800


async def sync_followed(user_id: int) -> dict:
    """Track every broadcast channel the reader account follows on Telegram.

    New follows are read automatically; channels the admin blocked (a
    user_channels row with enabled=0) stay blocked; channels the account has
    left are dropped. Blocks persist through MongoDB's user_channels collection.
    """
    global _last_follow_sync
    from ..routers.channels import _deactivate_orphans, _register_channel

    followed = await telegram.list_user_channels(user_id)
    now = time.time()
    added = 0
    keep_ids = []
    for info in followed:
        channel_id = _register_channel(info, user_id)
        keep_ids.append(channel_id)
        row = db.query_one("SELECT enabled FROM user_channels WHERE user_id = ? AND channel_id = ?",
                           (user_id, channel_id))
        if row is None:
            db.execute("INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, 1, ?)",
                       (user_id, channel_id, now))
            added += 1
    if keep_ids:
        marks = ",".join("?" for _ in keep_ids)
        removed = db.execute(f"DELETE FROM user_channels WHERE user_id = ? AND channel_id NOT IN ({marks})",
                             (user_id, *keep_ids)).rowcount or 0
        db.execute(f"UPDATE channels SET source_user_id = ? WHERE id IN ({marks})", (user_id, *keep_ids))
    else:
        removed = 0
    _deactivate_orphans()
    _last_follow_sync = now
    if mongo_store.is_enabled():
        loop = asyncio.get_event_loop()
        try:
            await loop.run_in_executor(None, mongo_store.sync_channels)
            await loop.run_in_executor(None, mongo_store.sync_user_channels)
        except Exception as exc:  # noqa: BLE001
            log.warning("Saving channel list to MongoDB failed: %s", exc)
    blocked = db.query_one("SELECT COUNT(*) AS c FROM user_channels WHERE user_id = ? AND enabled = 0", (user_id,))["c"]
    log.info("Reader follows %d channels: %d new, %d left, %d blocked", len(followed), added, removed, blocked)
    return {"followed": len(followed), "added": added, "removed": removed, "blocked": blocked}


async def maybe_sync_followed() -> None:
    """Called each ingest cycle; refreshes the follow list at most every 30 min."""
    reader = db.get_meta("reader_user_id")
    if not reader or time.time() - _last_follow_sync < FOLLOW_SYNC_SECONDS:
        return
    try:
        await sync_followed(int(reader))
    except Exception as exc:  # noqa: BLE001
        log.warning("Follow-list refresh failed: %s", exc)
