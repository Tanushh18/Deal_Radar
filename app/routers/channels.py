"""Channel discovery and tracking."""
from __future__ import annotations

import time
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .. import auth, db
from ..config import settings
from ..services import ingest, sheets, telegram

router = APIRouter(prefix="/api/channels", tags=["channels"])


class TrackPayload(BaseModel):
    tg_ids: List[int]


class PublicChannelPayload(BaseModel):
    username: str


class TogglePayload(BaseModel):
    enabled: bool


def _register_channel(info: dict, user_id: int) -> int:
    """Insert/refresh the global channel row, return its local id."""
    existing = db.query_one("SELECT * FROM channels WHERE tg_id = ?", (info["tg_id"],))
    row = {
        "tg_id": info["tg_id"],
        "username": info.get("username", ""),
        "title": info.get("title", ""),
        "participants": info.get("participants", 0),
        "active": 1,
        # Whoever tracks it first becomes the session used to read it. Channels
        # are read once globally, not once per interested user.
        "source_user_id": existing["source_user_id"] if existing and existing["source_user_id"] else user_id,
    }
    if existing:
        row["id"] = existing["id"]
        row["last_message_id"] = existing["last_message_id"]
        row["last_fetched_at"] = existing["last_fetched_at"]
    else:
        row["last_message_id"] = 0
        row["last_fetched_at"] = 0
    db.upsert("channels", row, conflict="tg_id")
    channel = db.query_one("SELECT id FROM channels WHERE tg_id = ?", (info["tg_id"],))
    return int(channel["id"])


@router.get("/available")
async def available_channels(user=Depends(auth.current_user)):
    """Every broadcast channel this user follows, flagged with what's tracked."""
    try:
        channels = await telegram.list_user_channels(user["id"])
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    tracked = {
        int(r["tg_id"])
        for r in db.query(
            "SELECT c.tg_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
            "WHERE uc.user_id = ? AND uc.enabled = 1",
            (user["id"],),
        )
    }
    for channel in channels:
        channel["tracked"] = channel["tg_id"] in tracked
    return {"channels": channels, "tracked_count": len(tracked)}


@router.get("")
async def my_channels(user=Depends(auth.current_user)):
    rows = db.query(
        "SELECT c.*, uc.enabled FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
        "WHERE uc.user_id = ? ORDER BY c.title",
        (user["id"],),
    )
    channels = []
    for row in rows:
        deals = db.query_one(
            "SELECT COUNT(*) AS c FROM deals WHERE channel_id = ? AND status = 'live'",
            (row["tg_id"],),
        )
        channels.append({
            "tg_id": row["tg_id"],
            "username": row["username"],
            "title": row["title"],
            "participants": row["participants"],
            "enabled": bool(row["enabled"]),
            "last_message_id": row["last_message_id"],
            "last_fetched_at": row["last_fetched_at"],
            "live_deals": deals["c"] if deals else 0,
        })
    return {"channels": channels}


@router.post("/track")
async def track_channels(payload: TrackPayload, user=Depends(auth.current_user)):
    """Track a set of channels the user already follows."""
    if len(payload.tg_ids) > settings.max_channels_per_user:
        raise HTTPException(
            status_code=400,
            detail=f"You can track at most {settings.max_channels_per_user} channels.",
        )
    try:
        available = {c["tg_id"]: c for c in await telegram.list_user_channels(user["id"])}
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    wanted = set(payload.tg_ids)
    added = 0
    for tg_id in wanted:
        info = available.get(tg_id)
        if not info:
            continue
        channel_id = _register_channel(info, user["id"])
        db.execute(
            "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, 1, ?) "
            "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = 1",
            (user["id"], channel_id, time.time()),
        )
        added += 1

    # Anything the user deselected stops being tracked for them.
    current = db.query(
        "SELECT c.tg_id, uc.channel_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
        "WHERE uc.user_id = ?",
        (user["id"],),
    )
    for row in current:
        if int(row["tg_id"]) not in wanted:
            db.execute(
                "UPDATE user_channels SET enabled = 0 WHERE user_id = ? AND channel_id = ?",
                (user["id"], row["channel_id"]),
            )

    _deactivate_orphans()
    _sync_meta_safely()
    return {"status": "ok", "tracked": added}


def _sync_meta_safely() -> None:
    """Best-effort push of channels + tracking links so a Render restart
    doesn't lose the user's channel selection. Never let a Sheets hiccup
    fail the request that triggered it."""
    if not sheets.is_enabled():
        return
    try:
        sheets.sync_channels()
        sheets.sync_user_channels()
    except Exception:  # noqa: BLE001
        pass


@router.post("/add-public")
async def add_public_channel(payload: PublicChannelPayload, user=Depends(auth.current_user)):
    """Track a public channel by @username, joining it if needed."""
    try:
        info = await telegram.resolve_public_channel(user["id"], payload.username)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    channel_id = _register_channel(info, user["id"])
    db.execute(
        "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = 1",
        (user["id"], channel_id, time.time()),
    )
    _sync_meta_safely()
    return {"status": "ok", "channel": info}


@router.delete("/{tg_id}")
async def untrack_channel(tg_id: int, user=Depends(auth.current_user)):
    channel = db.query_one("SELECT id FROM channels WHERE tg_id = ?", (tg_id,))
    if not channel:
        raise HTTPException(status_code=404, detail="Channel not found.")
    db.execute(
        "DELETE FROM user_channels WHERE user_id = ? AND channel_id = ?",
        (user["id"], channel["id"]),
    )
    _deactivate_orphans()
    _sync_meta_safely()
    return {"status": "ok"}


def _deactivate_orphans() -> None:
    """Stop polling channels nobody tracks any more."""
    db.execute(
        "UPDATE channels SET active = 0 WHERE id NOT IN "
        "(SELECT channel_id FROM user_channels WHERE enabled = 1)"
    )
    db.execute(
        "UPDATE channels SET active = 1 WHERE id IN "
        "(SELECT channel_id FROM user_channels WHERE enabled = 1)"
    )


@router.post("/sync")
async def sync_now(user=Depends(auth.current_user)):
    """Manual 'fetch deals now' from the UI."""
    result = await ingest.run_cycle("manual")
    if result.get("status") == "already_running":
        raise HTTPException(status_code=409, detail="A sync is already running.")
    return result
