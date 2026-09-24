"""Storage layer.

When TURSO_DATABASE_URL/TURSO_AUTH_TOKEN are set, this connects to Turso
(hosted libSQL) as an embedded replica: a local file that syncs to the
remote database, so data survives Render redeploys/restarts on the free
plan without a paid disk. Without those env vars it falls back to a plain
local SQLite file (ephemeral on free hosting — fine for local dev).

Google Sheets remains an optional export/viewer, not the source of truth,
once Turso is configured.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

from .config import settings

log = logging.getLogger("dealradar.db")

try:
    import libsql_experimental as libsql
except ImportError:  # pragma: no cover - optional dep, only needed for Turso
    libsql = None

_lock = threading.RLock()
_conn: Optional[Any] = None
_using_turso = False
_last_sync = 0.0

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

CREATE TABLE IF NOT EXISTS push_tokens (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER,
    token        TEXT UNIQUE,
    platform     TEXT,
    created_at   REAL,
    last_seen_at REAL
);
CREATE INDEX IF NOT EXISTS idx_push_user ON push_tokens(user_id);

CREATE TABLE IF NOT EXISTS notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    deal_id    TEXT,
    title      TEXT,
    body       TEXT,
    url        TEXT,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, created_at);

CREATE TABLE IF NOT EXISTS price_alerts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id       TEXT,
    deal_id         TEXT,
    product_key     TEXT,
    title           TEXT,
    target_price    REAL,
    start_price     REAL,
    push_token      TEXT DEFAULT '',
    created_at      REAL,
    triggered_at    REAL,
    triggered_price REAL
);
CREATE INDEX IF NOT EXISTS idx_price_alerts_key ON price_alerts(product_key, triggered_at);
CREATE INDEX IF NOT EXISTS idx_price_alerts_device ON price_alerts(device_id);

CREATE TABLE IF NOT EXISTS devices (
    device_id           TEXT PRIMARY KEY,
    platform            TEXT DEFAULT '',
    push_token          TEXT DEFAULT '',
    digest              INTEGER DEFAULT 0,
    digest_hour         INTEGER DEFAULT 19,
    last_digest_day     TEXT DEFAULT '',
    last_follow_push_at REAL DEFAULT 0,
    created_at          REAL,
    last_seen_at        REAL
);

CREATE TABLE IF NOT EXISTS device_follows (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id    TEXT,
    kind         TEXT,
    value        TEXT,
    min_discount INTEGER DEFAULT 0,
    created_at   REAL
);
CREATE INDEX IF NOT EXISTS idx_follows_match ON device_follows(kind, value);
CREATE INDEX IF NOT EXISTS idx_follows_device ON device_follows(device_id);

CREATE TABLE IF NOT EXISTS device_notifications (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    device_id  TEXT,
    kind       TEXT,
    title      TEXT,
    body       TEXT,
    image_url  TEXT,
    deal_id    TEXT,
    url        TEXT,
    expires_at REAL DEFAULT 0,
    created_at REAL
);
CREATE INDEX IF NOT EXISTS idx_devnotif ON device_notifications(device_id, created_at);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One row per key per calendar day (UTC); hard usage ceiling for ai_enrich.
CREATE TABLE IF NOT EXISTS ai_usage (
    day         TEXT NOT NULL,
    key_label   TEXT NOT NULL,
    requests    INTEGER DEFAULT 0,
    tokens      INTEGER DEFAULT 0,
    PRIMARY KEY (day, key_label)
);

CREATE TABLE IF NOT EXISTS coupon_reports (
    deal_id    TEXT,
    device_id  TEXT,
    reported_at REAL,
    PRIMARY KEY (deal_id, device_id)
);
CREATE INDEX IF NOT EXISTS idx_coupon_reports_deal ON coupon_reports(deal_id);
"""


def connect() -> Any:
    global _conn, _using_turso, _last_sync
    with _lock:
        if _conn is not None:
            return _conn
        directory = os.path.dirname(settings.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        if settings.turso_configured and libsql is not None:
            try:
                _conn = libsql.connect(
                    settings.db_path,
                    sync_url=settings.turso_url,
                    auth_token=settings.turso_auth_token,
                )
                _conn.sync()
                _using_turso = True
                _last_sync = time.time()
                for statement in _split_statements(SCHEMA):
                    _conn.execute(statement)
            except Exception as exc:  # noqa: BLE001 - a bad Turso handshake must never take the app down
                log.warning("Turso connect/sync failed (%s) — falling back to local SQLite", exc)
                _conn = None
                _using_turso = False

        if _conn is None:
            _conn = sqlite3.connect(settings.db_path, check_same_thread=False)
            _conn.row_factory = sqlite3.Row
            _conn.execute("PRAGMA journal_mode=WAL")
            _conn.execute("PRAGMA synchronous=NORMAL")
            _conn.executescript(SCHEMA)
        # Additive migration: the final store URL behind cuttli/bitli-style redirects.
        cols = {r[1] for r in _conn.execute("PRAGMA table_info(deals)")}
        if "resolved_url" not in cols:
            _conn.execute("ALTER TABLE deals ADD COLUMN resolved_url TEXT DEFAULT ''")
        ph_cols = {r[1] for r in _conn.execute("PRAGMA table_info(price_history)")}
        if "synced" not in ph_cols:
            _conn.execute("ALTER TABLE price_history ADD COLUMN synced INTEGER DEFAULT 0")
        device_cols = {r[1] for r in _conn.execute("PRAGMA table_info(devices)")}
        if "last_weekly_digest_at" not in device_cols:
            _conn.execute("ALTER TABLE devices ADD COLUMN last_weekly_digest_at REAL DEFAULT 0")
        notif_cols = {r[1] for r in _conn.execute("PRAGMA table_info(device_notifications)")}
        if "expires_at" not in notif_cols:
            _conn.execute("ALTER TABLE device_notifications ADD COLUMN expires_at REAL DEFAULT 0")
        deal_cols = {r[1] for r in _conn.execute("PRAGMA table_info(deals)")}
        if "ai_hook" not in deal_cols:
            _conn.execute("ALTER TABLE deals ADD COLUMN ai_hook TEXT DEFAULT ''")
        if "ai_mrp_reason" not in deal_cols:
            _conn.execute("ALTER TABLE deals ADD COLUMN ai_mrp_reason TEXT DEFAULT ''")
        if not _using_turso:
            _conn.commit()
        return _conn


def _split_statements(script: str) -> List[str]:
    """libsql's execute() takes one statement at a time (no executescript)."""
    return [s.strip() for s in script.split(";") if s.strip()]


def _row_to_plain_dict(row: Any, columns: Optional[List[str]] = None) -> Dict[str, Any]:
    if isinstance(row, sqlite3.Row):
        return dict(row)
    if isinstance(row, dict):
        return dict(row)
    if columns is not None:
        return dict(zip(columns, row))
    return dict(row)


def _maybe_sync() -> None:
    """Pull remote changes into the local replica, throttled."""
    global _last_sync
    if not _using_turso:
        return
    now = time.time()
    if now - _last_sync < settings.turso_sync_seconds:
        return
    try:
        _conn.sync()
        _last_sync = now
    except Exception:  # noqa: BLE001 - never let a sync hiccup break a request
        pass


def query(sql: str, params: Iterable[Any] = ()) -> List[Dict[str, Any]]:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        rows = cur.fetchall()
        if _using_turso:
            columns = [d[0] for d in cur.description] if cur.description else []
            return [_row_to_plain_dict(r, columns) for r in rows]
        return [_row_to_plain_dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def execute(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        conn = connect()
        cur = conn.execute(sql, tuple(params))
        if not _using_turso:
            conn.commit()
        else:
            _maybe_sync()
        return cur


def execute_many(sql: str, seq: Iterable[Iterable[Any]]) -> None:
    with _lock:
        conn = connect()
        rows = [tuple(p) for p in seq]
        if not rows:
            return
        if _using_turso:
            # libsql_experimental has no executemany; loop instead.
            for row in rows:
                conn.execute(sql, row)
            _maybe_sync()
        else:
            conn.executemany(sql, rows)
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
