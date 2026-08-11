"""SQLite hot cache.

Google Sheets is the durable source of truth; this file is a fast local index
that gets rebuilt from Sheets on boot. Render's free disk is ephemeral, so
never treat this as permanent storage.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .config import settings

_lock = threading.RLock()
_conn: Optional[sqlite3.Connection] = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id              INTEGER PRIMARY KEY,
    telegram_id     INTEGER UNIQUE,
    username        TEXT,
    first_name      TEXT,
    phone           TEXT,
    session_enc     TEXT,
    created_at      REAL,
    last_login_at   REAL
);

CREATE TABLE IF NOT EXISTS channels (
    id                INTEGER PRIMARY KEY,
    tg_id             INTEGER UNIQUE,
    username          TEXT,
    title             TEXT,
    participants      INTEGER DEFAULT 0,
    last_message_id   INTEGER DEFAULT 0,
    last_fetched_at   REAL DEFAULT 0,
    source_user_id    INTEGER,
    active            INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS user_channels (
    user_id    INTEGER,
    channel_id INTEGER,
    enabled    INTEGER DEFAULT 1,
    added_at   REAL,
    PRIMARY KEY (user_id, channel_id)
);

CREATE TABLE IF NOT EXISTS deals (
    id              TEXT PRIMARY KEY,
    title           TEXT,
    norm_title      TEXT,
    product_key     TEXT,
    price           REAL,
    mrp             REAL,
    discount_pct    INTEGER,
    currency        TEXT DEFAULT 'INR',
    store           TEXT,
    url             TEXT,
    clean_url       TEXT,
    image_url       TEXT,
    coupon          TEXT,
    category        TEXT,
    subcategory     TEXT,
    brand           TEXT,
    sizes           TEXT,
    channel_id      INTEGER,
    channel_title   TEXT,
    message_id      INTEGER,
    posted_at       REAL,
    first_seen_at   REAL,
    last_seen_at    REAL,
    expires_at      REAL,
    repost_count    INTEGER DEFAULT 1,
    channels_seen   TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'live',
    score           REAL DEFAULT 0,
    is_lowest       INTEGER DEFAULT 0,
    flags           TEXT DEFAULT '[]',
    raw_text        TEXT,
    search_blob     TEXT,
    dirty           INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_deals_status   ON deals(status);
CREATE INDEX IF NOT EXISTS idx_deals_category ON deals(category);
CREATE INDEX IF NOT EXISTS idx_deals_channel  ON deals(channel_id);
CREATE INDEX IF NOT EXISTS idx_deals_score    ON deals(score DESC);
CREATE INDEX IF NOT EXISTS idx_deals_posted   ON deals(posted_at DESC);
CREATE INDEX IF NOT EXISTS idx_deals_pkey     ON deals(product_key);
CREATE INDEX IF NOT EXISTS idx_deals_dirty    ON deals(dirty);

CREATE TABLE IF NOT EXISTS price_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    product_key TEXT,
    price       REAL,
    store       TEXT,
    seen_at     REAL
);
CREATE INDEX IF NOT EXISTS idx_ph_key ON price_history(product_key, seen_at DESC);

CREATE TABLE IF NOT EXISTS watchlists (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id          INTEGER,
    query            TEXT,
    filters          TEXT DEFAULT '{}',
    notify           INTEGER DEFAULT 1,
    created_at       REAL,
    last_notified_at REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_watch_user ON watchlists(user_id);

CREATE TABLE IF NOT EXISTS notified (
    watchlist_id INTEGER,
    deal_id      TEXT,
    sent_at      REAL,
    PRIMARY KEY (watchlist_id, deal_id)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        directory = os.path.dirname(settings.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA synchronous=NORMAL")
        _conn.executescript(SCHEMA)
        _conn.commit()
        return _conn


def query(sql: str, params: Iterable[Any] = ()) -> List[sqlite3.Row]:
    with _lock:
        cur = connect().execute(sql, tuple(params))
        return cur.fetchall()


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[sqlite3.Row]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur


def execute_many(sql: str, seq: Iterable[Iterable[Any]]) -> None:
    with _lock:
        conn = connect()
        conn.executemany(sql, [tuple(p) for p in seq])
        conn.commit()


def upsert(table: str, row: Dict[str, Any], conflict: str = "id") -> None:
    """INSERT ... ON CONFLICT(conflict) DO UPDATE for every supplied column."""
    cols = list(row.keys())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != conflict)
    sql = (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({conflict}) DO UPDATE SET {updates}"
    )
    execute(sql, [row[c] for c in cols])


def get_meta(key: str, default: Optional[str] = None) -> Optional[str]:
    row = query_one("SELECT value FROM meta WHERE key = ?", (key,))
    return row["value"] if row else default


def set_meta(key: str, value: str) -> None:
    execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def row_to_dict(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for json_field in ("channels_seen", "flags", "filters"):
        if json_field in data and isinstance(data[json_field], str):
            try:
                data[json_field] = json.loads(data[json_field])
            except (json.JSONDecodeError, TypeError):
                data[json_field] = []
    return data


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [d for d in (row_to_dict(r) for r in rows) if d is not None]


def stats() -> Dict[str, Any]:
    now = time.time()
    total = query_one("SELECT COUNT(*) AS c FROM deals")
    live = query_one("SELECT COUNT(*) AS c FROM deals WHERE status='live' AND expires_at > ?", (now,))
    channels = query_one("SELECT COUNT(*) AS c FROM channels WHERE active=1")
    users = query_one("SELECT COUNT(*) AS c FROM users")
    fresh = query_one("SELECT COUNT(*) AS c FROM deals WHERE first_seen_at > ?", (now - 86400,))
    return {
        "deals_total": total["c"] if total else 0,
        "deals_live": live["c"] if live else 0,
        "deals_today": fresh["c"] if fresh else 0,
        "channels": channels["c"] if channels else 0,
        "users": users["c"] if users else 0,
    }
