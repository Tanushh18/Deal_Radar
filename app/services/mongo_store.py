"""MongoDB: durable store for everything Google Sheets used to mirror —
users, channels, channel-tracking links, watchlists, and admin settings.

Deals, price history and price alerts are Turso's job (turso_backup.py) and
never touch MongoDB. This module exists purely so the small "who's signed
in, which channels are tracked, what's the admin's priority rule" state
survives a Render restart, same as Sheets did — but as real upserts against
indexed collections instead of clear-and-rewrite whole tabs against a
60-writes/minute quota.

pymongo is a synchronous driver — every function here blocks, exactly like
gspread did. Call sites that already wrapped Sheets calls in
run_in_executor keep doing the same for these; ones that called Sheets
directly from an async request handler (the write is a single small
upsert, same as it was) do the same here, unchanged in shape.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.mongo_store")

try:
    import pymongo
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover - optional dep, only needed when configured
    pymongo = None
    PyMongoError = Exception

_client: Optional[Any] = None
_last_error: Optional[str] = None


def is_enabled() -> bool:
    return settings.mongo_configured


def status() -> Dict[str, Any]:
    return {"configured": settings.mongo_configured, "connected": _client is not None,
            "last_error": _last_error}


def connect() -> bool:
    """Open (and cache) the client, creating indexes once. Idempotent."""
    global _client, _last_error
    if not settings.mongo_configured:
        _last_error = "MongoDB not configured"
        return False
    if _client is not None:
        return True
    if pymongo is None:
        _last_error = "pymongo is not installed"
        return False
    try:
        client = pymongo.MongoClient(settings.mongo_uri, serverSelectionTimeoutMS=8000)
        client.admin.command("ping")
        database = client[settings.mongo_db_name]
        database["users"].create_index("telegram_id", unique=True)
        database["channels"].create_index("tg_id", unique=True)
        database["user_channels"].create_index([("user_telegram_id", 1), ("channel_tg_id", 1)], unique=True)
        database["watchlists"].create_index([("user_telegram_id", 1), ("query", 1)], unique=True)
        database["settings"].create_index("key", unique=True)
        _client = client
        _last_error = None
        log.info("MongoDB connected: %s", settings.mongo_db_name)
        return True
    except PyMongoError as exc:
        _client = None
        _last_error = f"{type(exc).__name__}: {exc}"
        log.warning("MongoDB connect failed: %s", _last_error)
        return False


def _db():
    return _client[settings.mongo_db_name]


def _bool(value: Any) -> bool:
    return bool(value)


# --- push (sync_*) -------------------------------------------------------

def sync_users() -> int:
    if not connect():
        return 0
    rows = db.query("SELECT * FROM users ORDER BY created_at")
    if not rows:
        return 0
    ops = [pymongo.UpdateOne(
        {"telegram_id": r["telegram_id"]},
        {"$set": {"telegram_id": r["telegram_id"], "phone": r["phone"] or "",
                   "username": r["username"] or "", "first_name": r["first_name"] or "",
                   "created_at": r["created_at"], "last_login_at": r["last_login_at"]}},
        upsert=True,
    ) for r in rows]
    try:
        _db()["users"].bulk_write(ops, ordered=False)
    except PyMongoError as exc:
        log.warning("User sync failed: %s", exc)
        return 0
    return len(ops)


def sync_channels() -> int:
    if not connect():
        return 0
    rows = db.query(
        "SELECT c.*, u.telegram_id AS source_telegram_id FROM channels c "
        "LEFT JOIN users u ON u.id = c.source_user_id ORDER BY c.title"
    )
    if not rows:
        return 0
    ops = [pymongo.UpdateOne(
        {"tg_id": r["tg_id"]},
        {"$set": {"tg_id": r["tg_id"], "username": r["username"] or "", "title": r["title"] or "",
                   "participants": r["participants"] or 0, "last_message_id": r["last_message_id"] or 0,
                   "last_fetched_at": r["last_fetched_at"] or 0, "active": _bool(r["active"]),
                   "source_user_telegram_id": r["source_telegram_id"]}},
        upsert=True,
    ) for r in rows]
    try:
        _db()["channels"].bulk_write(ops, ordered=False)
    except PyMongoError as exc:
        log.warning("Channel sync failed: %s", exc)
        return 0
    return len(ops)


def sync_user_channels() -> int:
    """Mirror who-tracks-what, keyed by stable Telegram ids."""
    if not connect():
        return 0
    rows = db.query(
        "SELECT u.telegram_id AS user_tid, c.tg_id AS channel_tid, uc.enabled, uc.added_at "
        "FROM user_channels uc "
        "JOIN users u ON u.id = uc.user_id JOIN channels c ON c.id = uc.channel_id"
    )
    if not rows:
        return 0
    ops = [pymongo.UpdateOne(
        {"user_telegram_id": r["user_tid"], "channel_tg_id": r["channel_tid"]},
        {"$set": {"user_telegram_id": r["user_tid"], "channel_tg_id": r["channel_tid"],
                   "enabled": _bool(r["enabled"]), "added_at": r["added_at"] or 0}},
        upsert=True,
    ) for r in rows]
    try:
        _db()["user_channels"].bulk_write(ops, ordered=False)
    except PyMongoError as exc:
        log.warning("UserChannels sync failed: %s", exc)
        return 0
    return len(ops)


def sync_watchlists() -> int:
    if not connect():
        return 0
    rows = db.query(
        "SELECT w.*, u.telegram_id AS user_tid FROM watchlists w JOIN users u ON u.id = w.user_id"
    )
    if not rows:
        return 0
    ops = []
    for r in rows:
        try:
            filters = json.loads(r["filters"] or "{}")
        except (json.JSONDecodeError, TypeError):
            filters = {}
        query = r["query"] or ""
        ops.append(pymongo.UpdateOne(
            {"user_telegram_id": r["user_tid"], "query": query},
            {"$set": {"user_telegram_id": r["user_tid"], "query": query,
                       "category": filters.get("category") or "", "store": filters.get("store") or "",
                       "max_price": filters.get("max_price"), "min_discount": filters.get("min_discount") or 0,
                       "notify": _bool(r["notify"]), "created_at": r["created_at"] or 0}},
            upsert=True,
        ))
    try:
        _db()["watchlists"].bulk_write(ops, ordered=False)
    except PyMongoError as exc:
        log.warning("Watchlist sync failed: %s", exc)
        return 0
    return len(ops)


def sync_all_meta() -> Dict[str, int]:
    """Flush every small table. Cheap enough to call once per ingest cycle."""
    return {
        "users": sync_users(),
        "channels": sync_channels(),
        "user_channels": sync_user_channels(),
        "watchlists": sync_watchlists(),
    }


# --- pull (restore_*) -----------------------------------------------------

def restore_users() -> int:
    """Recreate user shells from MongoDB. Sessions are never stored there
    (by design — they're a secret), so restored users must sign in again;
    what this buys is that their *local id* is stable once they do, so
    anything restored below that references them (channels, alerts) still
    lines up. See telegram._finish_login, which updates in place when
    telegram_id already exists instead of minting a new id.
    """
    if not connect():
        return 0
    restored = 0
    try:
        for doc in _db()["users"].find():
            tid = doc.get("telegram_id")
            if not tid:
                continue
            db.execute(
                "INSERT INTO users (telegram_id, username, first_name, phone, session_enc, "
                "created_at, last_login_at) VALUES (?, ?, ?, ?, '', ?, ?) "
                "ON CONFLICT(telegram_id) DO NOTHING",
                (int(tid), str(doc.get("username") or ""), str(doc.get("first_name") or ""),
                 str(doc.get("phone") or ""), float(doc.get("created_at") or 0),
                 float(doc.get("last_login_at") or 0)),
            )
            restored += 1
    except PyMongoError as exc:
        log.warning("Users restore failed: %s", exc)
        return 0
    log.info("Restored %d user shells from MongoDB (re-login required for each)", restored)
    return restored


def restore_channels() -> int:
    """Recreate the channel registry, resolving source_user by telegram_id."""
    if not connect():
        return 0
    restored = 0
    try:
        for doc in _db()["channels"].find():
            tg_id = doc.get("tg_id")
            if not tg_id:
                continue
            source_tid = doc.get("source_user_telegram_id")
            source_user_id = None
            if source_tid:
                row = db.query_one("SELECT id FROM users WHERE telegram_id = ?", (source_tid,))
                source_user_id = row["id"] if row else None
            db.upsert("channels", {
                "tg_id": int(tg_id), "username": str(doc.get("username") or ""),
                "title": str(doc.get("title") or ""), "participants": int(doc.get("participants") or 0),
                "last_message_id": int(doc.get("last_message_id") or 0),
                "last_fetched_at": float(doc.get("last_fetched_at") or 0),
                "source_user_id": source_user_id, "active": 1 if doc.get("active") else 0,
            }, conflict="tg_id")
            restored += 1
    except PyMongoError as exc:
        log.warning("Channels restore failed: %s", exc)
        return 0
    log.info("Restored %d channels from MongoDB", restored)
    return restored


def restore_user_channels() -> int:
    if not connect():
        return 0
    restored = 0
    try:
        for doc in _db()["user_channels"].find():
            user_row = db.query_one("SELECT id FROM users WHERE telegram_id = ?", (doc.get("user_telegram_id"),))
            channel_row = db.query_one("SELECT id FROM channels WHERE tg_id = ?", (doc.get("channel_tg_id"),))
            if not user_row or not channel_row:
                continue
            db.execute(
                "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = excluded.enabled",
                (user_row["id"], channel_row["id"], 1 if doc.get("enabled") else 0,
                 float(doc.get("added_at") or 0) or time.time()),
            )
            restored += 1
    except PyMongoError as exc:
        log.warning("UserChannels restore failed: %s", exc)
        return 0
    log.info("Restored %d channel-tracking links from MongoDB", restored)
    return restored


def restore_watchlists() -> int:
    if not connect():
        return 0
    restored = 0
    try:
        for doc in _db()["watchlists"].find():
            user_row = db.query_one("SELECT id FROM users WHERE telegram_id = ?", (doc.get("user_telegram_id"),))
            query = str(doc.get("query") or "").strip()
            if not user_row or not query:
                continue
            already = db.query_one("SELECT id FROM watchlists WHERE user_id = ? AND query = ?",
                                   (user_row["id"], query))
            if already:
                continue
            filters = {"category": doc.get("category") or "", "store": doc.get("store") or "",
                       "max_price": doc.get("max_price"), "min_discount": int(doc.get("min_discount") or 0)}
            db.execute(
                "INSERT INTO watchlists (user_id, query, filters, notify, created_at, last_notified_at) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (user_row["id"], query, json.dumps(filters), 1 if doc.get("notify") else 0,
                 float(doc.get("created_at") or 0) or time.time()),
            )
            restored += 1
    except PyMongoError as exc:
        log.warning("Watchlists restore failed: %s", exc)
        return 0
    log.info("Restored %d watchlists from MongoDB", restored)
    return restored


def restore_all_meta() -> Dict[str, int]:
    """Full cold-start recovery, in dependency order: users -> channels -> links."""
    return {
        "users": restore_users(),
        "channels": restore_channels(),
        "user_channels": restore_user_channels(),
        "watchlists": restore_watchlists(),
    }


# --- key/value settings (priority rule, sale-events calendar, poll interval) --

def save_setting(key: str, value: str) -> bool:
    """Upsert one admin setting so it survives restarts."""
    if not connect():
        return False
    try:
        _db()["settings"].update_one(
            {"key": key}, {"$set": {"key": key, "value": value, "updated_at": time.time()}}, upsert=True,
        )
        return True
    except PyMongoError as exc:
        log.warning("Saving setting %s failed: %s", key, exc)
        return False


def restore_settings() -> int:
    if not connect():
        return 0
    restored = 0
    try:
        for doc in _db()["settings"].find():
            key = doc.get("key")
            if key:
                db.set_meta(str(key), str(doc.get("value") or ""))
                restored += 1
    except PyMongoError as exc:
        log.warning("Settings restore failed: %s", exc)
        return 0
    return restored
