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
from . import telegram

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
    log.info("Public mode: reading %d/%d channels as @%s", tracked, len(settings.public_channels), me.username or me.id)
    return tracked
