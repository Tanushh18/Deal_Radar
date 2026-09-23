"""Admin screen API: connect the reader Telegram account and manage channels.

Visitors never sign in; one "reader" account (connected here, or via
TELEGRAM_SESSION) reads every tracked public channel for everyone.
Every route needs the X-Admin-Token header (ADMIN_TOKEN).
"""
from __future__ import annotations

import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db
from ..services import ingest, priority, public_reader, sheets, taxonomy, telegram
from .channels import _deactivate_orphans, _register_channel

router = APIRouter(prefix="/api/admin/reader", tags=["admin"], dependencies=[Depends(auth.require_admin)])


class PhonePayload(BaseModel):
    phone: str


class CodePayload(BaseModel):
    login_id: str
    code: str


class PasswordPayload(BaseModel):
    login_id: str
    password: str


class ChannelPayload(BaseModel):
    username: str


class BlockPayload(BaseModel):
    blocked: bool


class PriorityPayload(BaseModel):
    preset: str = "custom"
    label: str = ""
    categories: list = []
    keywords: list = []
    stores: list = []


def reader_id() -> Optional[int]:
    value = db.get_meta("reader_user_id")
    if not value:
        return None
    row = db.query_one("SELECT id FROM users WHERE id = ?", (int(value),))
    return int(row["id"]) if row else None


def set_reader(user_id: int) -> None:
    """Make this account read every tracked channel (taking over from any previous reader)."""
    previous = reader_id()
    db.set_meta("reader_user_id", str(user_id))
    if previous and previous != user_id:
        db.execute(
            "INSERT OR IGNORE INTO user_channels (user_id, channel_id, enabled, added_at) "
            "SELECT ?, channel_id, enabled, added_at FROM user_channels WHERE user_id = ?",
            (user_id, previous),
        )
    db.execute("UPDATE channels SET source_user_id = ? WHERE active = 1", (user_id,))


def _reader_or_400() -> int:
    uid = reader_id()
    if uid is None:
        raise HTTPException(status_code=400, detail="Connect the reader Telegram account first.")
    return uid


def _session_string(user_id: int) -> str:
    row = db.query_one("SELECT session_enc FROM users WHERE id = ?", (user_id,))
    return telegram.decrypt_session(row["session_enc"]) if row and row["session_enc"] else ""


@router.get("")
async def status():
    uid = reader_id()
    account = None
    connected = False
    if uid:
        row = db.query_one("SELECT first_name, username, phone FROM users WHERE id = ?", (uid,))
        account = {"first_name": row["first_name"], "username": row["username"],
                   "phone": (row["phone"] or "")[-4:]}
        connected = await telegram.get_client(uid) is not None
    rows = db.query(
        "SELECT c.tg_id, c.username, c.title, c.participants, c.last_fetched_at, uc.enabled, "
        "(SELECT COUNT(*) FROM deals d WHERE d.channel_id = c.tg_id AND d.status = 'live') AS live_deals "
        "FROM channels c JOIN user_channels uc ON uc.channel_id = c.id "
        "WHERE uc.user_id = ? ORDER BY uc.enabled DESC, live_deals DESC, c.title",
        (uid or 0,),
    )
    return {"connected": connected, "account": account, "channels": db.rows_to_dicts(rows),
            "ingest": ingest.state(), "sync_paused": ingest.sync_paused()}


class PausePayload(BaseModel):
    paused: bool


@router.post("/pause")
async def pause_syncing(payload: PausePayload):
    """Stops the automatic 5-min ingest loop; "Sync now" still works as a manual override."""
    ingest.set_sync_paused(payload.paused)
    return {"status": "ok", "sync_paused": payload.paused}


class BroadcastPayload(BaseModel):
    title: str
    body: str
    deal_id: Optional[str] = None


@router.post("/broadcast")
async def broadcast(payload: BroadcastPayload):
    from ..services import devices

    title, body = payload.title.strip(), payload.body.strip()
    if not title or not body:
        raise HTTPException(status_code=400, detail="Write a title and a message.")
    deal = None
    if payload.deal_id:
        row = db.query_one("SELECT * FROM deals WHERE id = ?", (payload.deal_id,))
        if not row:
            raise HTTPException(status_code=404, detail="That deal id doesn't exist.")
        deal = dict(row)
    sent = devices.broadcast(title, body, deal)
    return {"status": "ok", "devices": sent}


async def _finish(result: dict) -> dict:
    if result.get("status") != "ok":
        return result
    user_id = int(result["user"]["id"])
    set_reader(user_id)
    try:
        await public_reader.sync_followed(user_id)
    except Exception:  # noqa: BLE001 — the login itself succeeded; the list refreshes next cycle
        pass
    # Shown once so it can be saved as TELEGRAM_SESSION on Render: the SQLite
    # copy is wiped whenever the free instance restarts.
    return {**result, "session": _session_string(user_id)}


@router.post("/send-code")
async def send_code(payload: PhonePayload):
    try:
        return await telegram.start_login(payload.phone)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/verify-code")
async def verify_code(payload: CodePayload):
    try:
        return await _finish(await telegram.verify_code(payload.login_id, payload.code))
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/verify-password")
async def verify_password(payload: PasswordPayload):
    try:
        return await _finish(await telegram.verify_password(payload.login_id, payload.password))
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/channels")
async def add_channel(payload: ChannelPayload):
    uid = _reader_or_400()
    try:
        info = await telegram.resolve_public_channel(uid, payload.username)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    channel_id = _register_channel(info, uid)
    db.execute(
        "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = 1",
        (uid, channel_id, time.time()),
    )
    db.execute("UPDATE channels SET source_user_id = ?, active = 1 WHERE id = ?", (uid, channel_id))
    return {"status": "ok", "channel": info}


@router.delete("/channels/{tg_id}")
async def remove_channel(tg_id: int):
    uid = _reader_or_400()
    db.execute(
        "DELETE FROM user_channels WHERE user_id = ? AND channel_id = (SELECT id FROM channels WHERE tg_id = ?)",
        (uid, tg_id),
    )
    _deactivate_orphans()
    return {"status": "ok"}


@router.post("/sync")
async def sync():
    _reader_or_400()
    result = await ingest.run_cycle("admin")
    if result.get("status") == "already_running":
        raise HTTPException(status_code=409, detail="A sync is already running.")
    return result


@router.post("/refresh")
async def refresh_followed():
    """Re-read the channels the reader account follows on Telegram."""
    uid = _reader_or_400()
    try:
        return await public_reader.sync_followed(uid)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/channels/{tg_id}/block")
async def block_channel(tg_id: int, payload: BlockPayload):
    """Blocked channels stay followed on Telegram but are never read for deals."""
    uid = _reader_or_400()
    cur = db.execute(
        "UPDATE user_channels SET enabled = ? WHERE user_id = ? "
        "AND channel_id = (SELECT id FROM channels WHERE tg_id = ?)",
        (0 if payload.blocked else 1, uid, tg_id),
    )
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Channel not found.")
    _deactivate_orphans()
    if sheets.is_enabled():
        try:
            sheets.sync_channels()
            sheets.sync_user_channels()
        except Exception:  # noqa: BLE001
            pass
    return {"status": "ok", "blocked": payload.blocked}


@router.get("/priority")
async def get_priority():
    """The "show on top" rule, the presets and the category list for the admin form."""
    return {"rule": priority.get(),
            "presets": {k: v["label"] for k, v in priority.PRESETS.items()},
            "categories": [c["name"] for c in taxonomy.category_list()]}


@router.post("/priority")
async def set_priority(payload: PriorityPayload):
    import json
    rule = priority.save(payload.model_dump())
    if sheets.is_enabled():
        sheets.save_setting(priority.META_KEY, json.dumps(rule))
    return {"status": "ok", "rule": rule}
