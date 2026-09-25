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
from ..config import settings
from ..services import ingest, live, mongo_store, priority, public_reader, sale_events, taxonomy, telegram, tg_post
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


class PollIntervalPayload(BaseModel):
    seconds: int


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
        "(SELECT COUNT(*) FROM deals d WHERE d.channel_id = c.tg_id AND d.status = 'live') AS live_deals, "
        "(SELECT MAX(first_seen_at) FROM deals d WHERE d.channel_id = c.tg_id) AS last_new_deal_at "
        "FROM channels c JOIN user_channels uc ON uc.channel_id = c.id "
        "WHERE uc.user_id = ? ORDER BY uc.enabled DESC, live_deals DESC, c.title",
        (uid or 0,),
    )
    # A channel checked repeatedly but never producing a new card is either
    # dead or off-topic for this app's parser — surfaced so it can be dropped.
    STALE_DAYS = 10
    now = time.time()
    channels = db.rows_to_dicts(rows)
    for ch in channels:
        last = ch.get("last_new_deal_at")
        ch["stale"] = bool(ch["enabled"]) and (now - float(last or 0)) > STALE_DAYS * 86400
    return {"connected": connected, "account": account, "channels": channels,
            "ingest": ingest.state(), "sync_paused": ingest.sync_paused(),
            "live": {**live.status(), "posting_to": settings.tg_post_channel if settings.tg_post_configured else None,
                     "posting_paused": tg_post.posting_paused()}}


class PausePayload(BaseModel):
    paused: bool


@router.post("/pause")
async def pause_syncing(payload: PausePayload):
    """Stops the automatic 5-min ingest loop; "Sync now" still works as a manual override."""
    ingest.set_sync_paused(payload.paused)
    return {"status": "ok", "sync_paused": payload.paused}


@router.post("/telegram-pause")
async def pause_telegram_posting(payload: PausePayload):
    """Stops new deals (and sale-event heads-ups) going to the Telegram channel —
    ingest and the website/app keep working; only outgoing Telegram posts stop."""
    tg_post.set_posting_paused(payload.paused)
    return {"status": "ok", "posting_paused": payload.paused}


@router.get("/poll-interval")
async def get_poll_interval():
    return {
        "seconds": ingest.poll_interval_seconds(),
        "default_seconds": settings.poll_interval_seconds,
        "is_override": ingest.poll_interval_seconds() != settings.poll_interval_seconds,
        "min_seconds": ingest.MIN_POLL_INTERVAL_SECONDS,
        "max_seconds": ingest.MAX_POLL_INTERVAL_SECONDS,
    }


@router.post("/poll-interval")
async def set_poll_interval(payload: PollIntervalPayload):
    """Takes effect on the scheduler's next iteration — no redeploy, no restart."""
    if not (ingest.MIN_POLL_INTERVAL_SECONDS <= payload.seconds <= ingest.MAX_POLL_INTERVAL_SECONDS):
        raise HTTPException(
            status_code=400,
            detail=f"Must be between {ingest.MIN_POLL_INTERVAL_SECONDS} and {ingest.MAX_POLL_INTERVAL_SECONDS} seconds.",
        )
    seconds = ingest.set_poll_interval_seconds(payload.seconds)
    if mongo_store.is_enabled():
        mongo_store.save_setting(ingest.POLL_INTERVAL_META_KEY, str(seconds))
    return {"status": "ok", "seconds": seconds}


@router.post("/poll-interval/reset")
async def reset_poll_interval():
    seconds = ingest.reset_poll_interval_seconds()
    if mongo_store.is_enabled():
        mongo_store.save_setting(ingest.POLL_INTERVAL_META_KEY, "")
    return {"status": "ok", "seconds": seconds}


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
    report = await devices.broadcast(title, body, deal)
    return {"status": "ok", **report}


@router.get("/push-status")
async def push_status():
    """Who can be reached by push, what's queued this cycle, what went out."""
    from ..services import hot_push
    return hot_push.status()


@router.post("/push-now")
async def push_now():
    """Push the best eligible deal right now (ignores quiet hours and the gap)."""
    from ..services import hot_push
    return await hot_push.send_best(force=True, reason="admin")


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
    if mongo_store.is_enabled():
        try:
            mongo_store.sync_channels()
            mongo_store.sync_user_channels()
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
    if mongo_store.is_enabled():
        mongo_store.save_setting(priority.META_KEY, json.dumps(rule))
    return {"status": "ok", "rule": rule}


class StorageModePayload(BaseModel):
    mode: str  # "turso" | "sqlite" | "auto"


@router.get("/data-source")
async def get_data_source():
    """Which DB backend is live — for the admin panel's testing toggle."""
    return {"storage_mode": db.storage_mode()}


@router.post("/data-source/storage")
async def set_storage_mode(payload: StorageModePayload):
    """Switch the live DB backend between Turso and local SQLite (or back to auto)."""
    try:
        mode = db.switch_storage(payload.mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"status": "ok", "storage_mode": mode}


@router.post("/telegram-test")
async def telegram_test():
    """Post a test message to TG_POST_CHANNEL — proves the bot token, the
    channel id and the bot's admin rights in one tap."""
    if not settings.tg_post_configured:
        raise HTTPException(status_code=400, detail="Set TG_BOT_TOKEN and TG_POST_CHANNEL in Render first.")
    try:
        result = await tg_post._send("sendMessage", {
            "chat_id": settings.tg_post_channel,
            "text": "✅ DealRadar test post — the bot can post here. Verified deals will appear automatically.",
        })
    except Exception as exc:  # noqa: BLE001 - Telegram's own reason is the useful part
        raise HTTPException(status_code=400, detail=f"Telegram refused: {exc}")
    return {"status": "ok", "channel": settings.tg_post_channel, "message_id": result.get("message_id"),
            "live": live.status()}


class SaleEventPayload(BaseModel):
    id: Optional[str] = None
    name: str
    store: str = ""
    starts_at: Optional[float] = None
    ends_at: Optional[float] = None
    approximate: bool = True
    hype: str = ""


@router.get("/sale-events")
async def list_sale_events():
    """Full calendar (past + upcoming) for the admin editor."""
    return {"events": sale_events.list_all()}


@router.post("/sale-events")
async def upsert_sale_event(payload: SaleEventPayload):
    event = sale_events.upsert(payload.model_dump())
    if not event.get("hype"):
        event = await sale_events.ensure_hype(event)
    return {"status": "ok", "event": event}


@router.delete("/sale-events/{event_id}")
async def delete_sale_event(event_id: str):
    if not sale_events.delete(event_id):
        raise HTTPException(status_code=404, detail="Event not found.")
    return {"status": "ok"}


@router.post("/sale-events/{event_id}/hype")
async def regenerate_sale_event_hype(event_id: str):
    """Force a fresh AI blurb, e.g. after the dates were confirmed."""
    events = sale_events.list_all()
    event = next((e for e in events if e["id"] == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    hype = await sale_events.generate_hype(event)
    if not hype:
        raise HTTPException(status_code=400, detail="AI enrichment is off or the daily budget is used up.")
    event = sale_events.upsert({**event, "hype": hype, "hype_generated_at": __import__("time").time()})
    return {"status": "ok", "event": event}


@router.post("/sale-events/{event_id}/post-now")
async def post_sale_event_now(event_id: str):
    """Send the Telegram heads-up immediately, skipping the days-before window."""
    events = sale_events.list_all()
    event = next((e for e in events if e["id"] == event_id), None)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found.")
    if not settings.tg_post_configured:
        raise HTTPException(status_code=400, detail="Set TG_BOT_TOKEN and TG_POST_CHANNEL first.")
    ok = await sale_events.post_heads_up(event)
    if not ok:
        raise HTTPException(status_code=400, detail="Telegram post failed — check the bot/channel setup.")
    return {"status": "ok"}
