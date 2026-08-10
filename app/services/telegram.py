"""Telethon (MTProto) client management.

Why MTProto and not the Bot API: a bot can only read a channel where it is an
admin. Public deal channels are not ours, so the only way to read the channels
a user already follows is to act as that user.

Security posture:
  * The phone code / 2FA password are never stored — they exist only for the
    seconds it takes to complete a sign-in.
  * The resulting Telethon session string is encrypted with Fernet (key derived
    from SECRET_KEY) before it touches SQLite or Sheets.
  * Sessions are read-only in practice: we fetch history and send messages to
    the user's own Saved Messages, nothing else.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from cryptography.fernet import Fernet, InvalidToken
from telethon import TelegramClient, functions
from telethon.errors import (
    ApiIdInvalidError,
    AuthKeyUnregisteredError,
    FloodWaitError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SessionPasswordNeededError,
)
from telethon.sessions import StringSession
from telethon.tl.types import Channel, Message

from .. import db
from ..config import settings

log = logging.getLogger(__name__)

# login_id -> {"client": TelegramClient, "phone": str, "hash": str, "created": float}
_pending: Dict[str, Dict[str, Any]] = {}
_pending_lock = asyncio.Lock()

# user_id -> connected TelegramClient
_clients: Dict[int, TelegramClient] = {}
_clients_lock = asyncio.Lock()

PENDING_TTL = 600  # a login attempt is good for 10 minutes


class TelegramError(Exception):
    """User-facing Telegram failure."""


# --- session encryption ------------------------------------------------
def encrypt_session(session_string: str) -> str:
    return Fernet(settings.fernet_key).encrypt(session_string.encode("utf-8")).decode("utf-8")


def decrypt_session(blob: str) -> Optional[str]:
    try:
        return Fernet(settings.fernet_key).decrypt(blob.encode("utf-8")).decode("utf-8")
    except (InvalidToken, AttributeError, ValueError):
        log.warning("Could not decrypt a stored session (SECRET_KEY changed?)")
        return None


def _new_client(session: Optional[str] = None) -> TelegramClient:
    if not settings.telegram_configured:
        raise TelegramError(
            "Telegram API credentials are missing. Set TELEGRAM_API_ID and "
            "TELEGRAM_API_HASH from https://my.telegram.org."
        )
    return TelegramClient(
        StringSession(session) if session else StringSession(),
        settings.telegram_api_id,
        settings.telegram_api_hash,
        device_model="DealRadar Web",
        system_version="1.0",
        app_version="1.0.0",
    )


async def _purge_pending() -> None:
    now = time.time()
    stale = [k for k, v in _pending.items() if now - v["created"] > PENDING_TTL]
    for key in stale:
        entry = _pending.pop(key, None)
        if entry:
            try:
                await entry["client"].disconnect()
            except Exception:  # noqa: BLE001
                pass


# --- login flow --------------------------------------------------------
async def start_login(phone: str) -> Dict[str, Any]:
    """Step 1: ask Telegram to send a login code."""
    await _purge_pending()
    phone = phone.strip().replace(" ", "")
    if not phone.startswith("+"):
        phone = "+" + phone.lstrip("0")

    client = _new_client()
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        await client.disconnect()
        raise TelegramError("That phone number isn't valid. Include the country code, e.g. +91…")
    except ApiIdInvalidError:
        await client.disconnect()
        raise TelegramError("TELEGRAM_API_ID / TELEGRAM_API_HASH are invalid.")
    except FloodWaitError as exc:
        await client.disconnect()
        raise TelegramError(f"Too many attempts. Try again in {exc.seconds} seconds.")
    except Exception as exc:  # noqa: BLE001
        await client.disconnect()
        raise TelegramError(f"Could not send the code: {exc}")

    login_id = uuid.uuid4().hex
    async with _pending_lock:
        _pending[login_id] = {
            "client": client,
            "phone": phone,
            "hash": sent.phone_code_hash,
            "created": time.time(),
        }
    return {"login_id": login_id, "phone": phone}


async def verify_code(login_id: str, code: str) -> Dict[str, Any]:
    """Step 2: submit the SMS/app code. May ask for a 2FA password next."""
    entry = _pending.get(login_id)
    if not entry:
        raise TelegramError("This login attempt expired. Please start again.")
    client: TelegramClient = entry["client"]
    try:
        await client.sign_in(
            phone=entry["phone"], code=code.strip(), phone_code_hash=entry["hash"]
        )
    except SessionPasswordNeededError:
        return {"status": "password_required", "login_id": login_id}
    except PhoneCodeInvalidError:
        raise TelegramError("That code is incorrect.")
    except PhoneCodeExpiredError:
        await _drop_pending(login_id)
        raise TelegramError("That code expired. Please request a new one.")
    except FloodWaitError as exc:
        raise TelegramError(f"Too many attempts. Try again in {exc.seconds} seconds.")
    return await _finish_login(login_id)


async def verify_password(login_id: str, password: str) -> Dict[str, Any]:
    """Step 3 (optional): two-factor password."""
    entry = _pending.get(login_id)
    if not entry:
        raise TelegramError("This login attempt expired. Please start again.")
    client: TelegramClient = entry["client"]
    try:
        await client.sign_in(password=password)
    except Exception as exc:  # noqa: BLE001
        raise TelegramError("That two-step password is incorrect.") from exc
    return await _finish_login(login_id)


async def _drop_pending(login_id: str) -> None:
    async with _pending_lock:
        entry = _pending.pop(login_id, None)
    if entry:
        try:
            await entry["client"].disconnect()
        except Exception:  # noqa: BLE001
            pass


async def _finish_login(login_id: str) -> Dict[str, Any]:
    entry = _pending.get(login_id)
    if not entry:
        raise TelegramError("This login attempt expired. Please start again.")
    client: TelegramClient = entry["client"]
    me = await client.get_me()
    session_string = client.session.save()

    now = time.time()
    existing = db.query_one("SELECT id, created_at FROM users WHERE telegram_id = ?", (me.id,))
    row = {
        "telegram_id": me.id,
        "username": me.username or "",
        "first_name": me.first_name or "",
        "phone": entry["phone"],
        "session_enc": encrypt_session(session_string),
        "created_at": existing["created_at"] if existing else now,
        "last_login_at": now,
    }
    if existing:
        row["id"] = existing["id"]
    db.upsert("users", row, conflict="telegram_id")
    user = db.query_one("SELECT * FROM users WHERE telegram_id = ?", (me.id,))

    async with _pending_lock:
        _pending.pop(login_id, None)
    # Keep this connection hot for the session instead of reconnecting.
    async with _clients_lock:
        old = _clients.pop(user["id"], None)
        if old:
            try:
                await old.disconnect()
            except Exception:  # noqa: BLE001
                pass
        _clients[user["id"]] = client

    return {
        "status": "ok",
        "user": {
            "id": user["id"],
            "telegram_id": me.id,
            "username": me.username or "",
            "first_name": me.first_name or "",
        },
    }


# --- client pool -------------------------------------------------------
async def get_client(user_id: int) -> Optional[TelegramClient]:
    """Return a connected client for a user, reviving it from storage if needed."""
    async with _clients_lock:
        client = _clients.get(user_id)
    if client is not None and client.is_connected():
        return client

    row = db.query_one("SELECT session_enc FROM users WHERE id = ?", (user_id,))
    if not row or not row["session_enc"]:
        return None
    session_string = decrypt_session(row["session_enc"])
    if not session_string:
        return None

    client = _new_client(session_string)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            log.info("Stored session for user %s is no longer authorized", user_id)
            await client.disconnect()
            return None
    except (AuthKeyUnregisteredError, Exception) as exc:  # noqa: BLE001
        log.warning("Could not restore client for user %s: %s", user_id, exc)
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass
        return None

    async with _clients_lock:
        _clients[user_id] = client
    return client


async def drop_client(user_id: int) -> None:
    async with _clients_lock:
        client = _clients.pop(user_id, None)
    if client:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


async def shutdown_all() -> None:
    async with _clients_lock:
        clients = list(_clients.values())
        _clients.clear()
    async with _pending_lock:
        clients += [e["client"] for e in _pending.values()]
        _pending.clear()
    for client in clients:
        try:
            await client.disconnect()
        except Exception:  # noqa: BLE001
            pass


# --- data access -------------------------------------------------------
async def list_user_channels(user_id: int) -> List[Dict[str, Any]]:
    """Every broadcast channel the user follows — the pool they can track."""
    client = await get_client(user_id)
    if client is None:
        raise TelegramError("Your Telegram session expired. Please sign in again.")

    channels: List[Dict[str, Any]] = []
    try:
        async for dialog in client.iter_dialogs(limit=400):
            entity = dialog.entity
            if not isinstance(entity, Channel) or not getattr(entity, "broadcast", False):
                continue
            channels.append({
                "tg_id": entity.id,
                "username": getattr(entity, "username", "") or "",
                "title": entity.title or "",
                "participants": getattr(entity, "participants_count", 0) or 0,
            })
    except FloodWaitError as exc:
        raise TelegramError(f"Telegram rate limit — retry in {exc.seconds}s.")
    return sorted(channels, key=lambda c: c["title"].lower())


async def fetch_messages(
    user_id: int, tg_channel_id: int, min_id: int = 0, limit: int = 60
) -> List[Message]:
    """Pull messages newer than min_id. Watermarking keeps this cheap."""
    client = await get_client(user_id)
    if client is None:
        raise TelegramError("Telegram session unavailable.")
    try:
        entity = await client.get_entity(tg_channel_id)
        return [
            msg
            async for msg in client.iter_messages(entity, limit=limit, min_id=min_id or 0)
        ]
    except FloodWaitError as exc:
        log.warning("Flood wait %ss on channel %s", exc.seconds, tg_channel_id)
        raise TelegramError(f"Rate limited for {exc.seconds}s")


async def resolve_public_channel(user_id: int, username: str) -> Dict[str, Any]:
    """Look up (and join) a public channel by @username or t.me link."""
    client = await get_client(user_id)
    if client is None:
        raise TelegramError("Your Telegram session expired. Please sign in again.")
    handle = username.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if handle.lower().startswith(prefix.lower()):
            handle = handle[len(prefix):]
    handle = handle.split("/")[0].split("?")[0]
    if not handle:
        raise TelegramError("Enter a channel username like @dealschannel.")
    try:
        entity = await client.get_entity(handle)
    except Exception as exc:  # noqa: BLE001
        raise TelegramError(f"Couldn't find @{handle} on Telegram.") from exc
    if not isinstance(entity, Channel) or not getattr(entity, "broadcast", False):
        raise TelegramError(f"@{handle} isn't a broadcast channel.")
    try:
        await client(functions.channels.JoinChannelRequest(entity))
    except Exception:  # noqa: BLE001
        pass  # already a member, or joining isn't required to read it
    return {
        "tg_id": entity.id,
        "username": getattr(entity, "username", "") or handle,
        "title": entity.title or handle,
        "participants": getattr(entity, "participants_count", 0) or 0,
    }


async def notify_user(user_id: int, text: str) -> bool:
    """Deliver an alert to the user's own Saved Messages."""
    client = await get_client(user_id)
    if client is None:
        return False
    try:
        await client.send_message("me", text, link_preview=False)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("Notify failed for user %s: %s", user_id, exc)
        return False
