"""Storage layer — the live database is local SQLite, a fast short-term cache.

It holds only the last few days of deals (store.purge_local_cache). Every
deal, its full price history and price alerts are kept permanently in Turso
(turso_backup.py writes, price_store.py reads); channels, users and settings
are mirrored to Google Sheets. Render's free disk is wiped on restart either way.
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

-- Deletes/renames waiting to be replayed on Turso (see turso_enqueue).
CREATE TABLE IF NOT EXISTS turso_outbox (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    sql  TEXT,
    args TEXT
);
"""


class _EmptyCursor:
    """Stand-in for a libsql cursor when the library's own call errored spuriously."""
    rowcount = 0
    description = None

    def fetchall(self) -> List[Any]:
        return []


def _safe_exec(conn: Any, sql: str, params: Iterable[Any] = ()) -> Any:
    """conn.execute(), tolerating libsql_experimental's `ValueError: not an error`.

    That specific message is a known response-handling bug in the library —
    it's raised on statements that succeeded but returned no rows (CREATE
    INDEX, some ALTERs, PRAGMAs with no matching column). Anything else
    still raises normally.
    """
    try:
        return conn.execute(sql, tuple(params)) if params else conn.execute(sql)
    except ValueError as exc:
        if str(exc) == "not an error":
            return _EmptyCursor()
        raise


# None = auto (Turso if configured, else local SQLite); "turso"/"sqlite" = admin-forced.
_forced_mode: Optional[str] = None


def storage_mode() -> str:
    """The backend actually in use right now: 'turso' or 'sqlite'."""
    return "turso" if _using_turso else "sqlite"


def switch_storage(mode: str) -> str:
    """Admin-only runtime switch between Turso and local SQLite.

    Each backend keeps its own local file (db_path vs db_path + '.local'),
    so switching never lets the sqlite3 module write into a file libsql's
    embedded replica manages, or vice versa. Returns the mode now active.

    A failed switch (e.g. forcing Turso when it isn't configured) leaves
    the previously-working connection untouched instead of tearing it down
    and then discovering the new one doesn't work — that would take the
    whole site down until someone noticed and switched back.
    """
    global _conn, _using_turso, _last_sync, _forced_mode
    if mode not in ("turso", "sqlite", "auto"):
        raise ValueError("mode must be 'turso', 'sqlite', or 'auto'")
    if mode == "turso":
        # The Turso database now holds only price history and alerts in its
        # own schema (turso_backup.py); mirroring the whole app DB into it
        # would clash with that.
        raise ValueError("Turso stores only price history and alerts now; the live DB stays local SQLite.")
    with _lock:
        previous_conn, previous_using_turso, previous_forced = _conn, _using_turso, _forced_mode
        _forced_mode = None if mode == "auto" else mode
        _conn = None
        try:
            connect()
        except Exception:
            # Roll back to the connection that was working before this call.
            _conn, _using_turso, _forced_mode = previous_conn, previous_using_turso, previous_forced
            raise
    return storage_mode()


def _connect_turso() -> Any:
    if not settings.turso_configured or libsql is None:
        raise RuntimeError(
            "Turso mode needs TURSO_DATABASE_URL and TURSO_AUTH_TOKEN set "
            "(and libsql-experimental installed)."
        )
    conn = libsql.connect(
        settings.db_path,
        sync_url=settings.turso_url,
        auth_token=settings.turso_auth_token,
    )
    conn.sync()
    return conn


def _connect_sqlite() -> Any:
    path = settings.db_path + ".local"
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def connect() -> Any:
    global _conn, _using_turso, _last_sync
    with _lock:
        if _conn is not None:
            return _conn
        directory = os.path.dirname(settings.db_path)
        if directory:
            os.makedirs(directory, exist_ok=True)

        # Local SQLite is the default live DB. Turso's embedded replica makes
        # every write (and each periodic sync) a blocking network round-trip,
        # and these db.* calls run directly on the event loop — an ingest
        # cycle of hundreds of writes froze the loop long enough for Render's
        # health check to fail and restart the instance in a loop. Turso is
        # only used when the admin explicitly forces it.
        want_turso = _forced_mode == "turso"
        if want_turso:
            try:
                _conn = _connect_turso()
                _using_turso = True
                _last_sync = time.time()
            except Exception as exc:  # noqa: BLE001 - a bad Turso connect must never crash boot
                log.warning("Turso connect/sync failed (%s) — falling back to local SQLite", exc)
                _conn = None
        if _conn is None:
            _conn = _connect_sqlite()
            _using_turso = False

        for statement in _split_statements(SCHEMA):
            _safe_exec(_conn, statement)
        # Additive migration: the final store URL behind cuttli/bitli-style redirects.
        cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(deals)").fetchall()}
        if "resolved_url" not in cols:
            _safe_exec(_conn, "ALTER TABLE deals ADD COLUMN resolved_url TEXT DEFAULT ''")
        ph_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(price_history)").fetchall()}
        if "synced" not in ph_cols:
            _safe_exec(_conn, "ALTER TABLE price_history ADD COLUMN synced INTEGER DEFAULT 0")
        # Separate from `synced` (Sheets): each destination tracks its own
        # progress, or whichever flushes first hides rows from the other.
        # Defaults to 1 so restored/rolled-up rows never re-upload; only
        # store.record_price() inserts 0.
        if "turso_synced" not in ph_cols:
            _safe_exec(_conn, "ALTER TABLE price_history ADD COLUMN turso_synced INTEGER DEFAULT 1")
        _safe_exec(_conn, "CREATE INDEX IF NOT EXISTS idx_ph_turso ON price_history(id) WHERE turso_synced = 0")
        # Change counter for the Turso deal upload: bumped by these triggers on
        # every write that marks a deal dirty (the same writes that feed the
        # Sheet), cleared by turso_backup only if unchanged since it read it.
        deal_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(deals)").fetchall()}
        if "turso_dirty" not in deal_cols:
            _safe_exec(_conn, "ALTER TABLE deals ADD COLUMN turso_dirty INTEGER DEFAULT 0")
        _safe_exec(_conn, "CREATE INDEX IF NOT EXISTS idx_deals_turso ON deals(id) WHERE turso_dirty > 0")
        _safe_exec(_conn, "CREATE INDEX IF NOT EXISTS idx_deals_last_seen ON deals(last_seen_at)")
        _safe_exec(_conn, (
            "CREATE TRIGGER IF NOT EXISTS trg_deals_turso_ins AFTER INSERT ON deals WHEN NEW.dirty = 1 "
            "BEGIN UPDATE deals SET turso_dirty = turso_dirty + 1 WHERE id = NEW.id; END"
        ))
        _safe_exec(_conn, (
            "CREATE TRIGGER IF NOT EXISTS trg_deals_turso_upd AFTER UPDATE OF dirty ON deals WHEN NEW.dirty = 1 "
            "BEGIN UPDATE deals SET turso_dirty = turso_dirty + 1 WHERE id = NEW.id; END"
        ))
        alert_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(price_alerts)").fetchall()}
        if "turso_dirty" not in alert_cols:
            _safe_exec(_conn, "ALTER TABLE price_alerts ADD COLUMN turso_dirty INTEGER DEFAULT 1")
        device_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(devices)").fetchall()}
        if "last_weekly_digest_at" not in device_cols:
            _safe_exec(_conn, "ALTER TABLE devices ADD COLUMN last_weekly_digest_at REAL DEFAULT 0")
        notif_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(device_notifications)").fetchall()}
        if "expires_at" not in notif_cols:
            _safe_exec(_conn, "ALTER TABLE device_notifications ADD COLUMN expires_at REAL DEFAULT 0")
        deal_cols = {r[1] for r in _safe_exec(_conn, "PRAGMA table_info(deals)").fetchall()}
        if "ai_hook" not in deal_cols:
            _safe_exec(_conn, "ALTER TABLE deals ADD COLUMN ai_hook TEXT DEFAULT ''")
        if "ai_mrp_reason" not in deal_cols:
            _safe_exec(_conn, "ALTER TABLE deals ADD COLUMN ai_mrp_reason TEXT DEFAULT ''")
        _conn.commit()
        return _conn


def _split_statements(script: str) -> List[str]:
    """libsql's execute() takes one statement at a time (no executescript)."""
    # Drop "--" comment lines first: a ";" inside a comment would otherwise
    # split one statement in two.
    code = "\n".join(line for line in script.splitlines() if not line.strip().startswith("--"))
    return [s.strip() for s in code.split(";") if s.strip()]


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
        cur = _safe_exec(conn, sql, params)
        rows = cur.fetchall()
        if _using_turso:
            columns = [d[0] for d in cur.description] if cur.description else []
            return [_row_to_plain_dict(r, columns) for r in rows]
        return [_row_to_plain_dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, params)
    return rows[0] if rows else None


def _is_disk_full(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "disk" in msg and "full" in msg


def _emergency_free_space() -> None:
    """Local disk is full. Delete the oldest, no-longer-live rows to make
    room immediately — they're already mirrored to Sheets (and Turso, if
    configured) by the normal flush cycles, so this doesn't lose data,
    only local query access to very old history. A crash from a full disk
    is worse than temporarily thinner local archive search."""
    global _conn
    log.error("Local disk is full — freeing space from oldest local rows")
    try:
        conn = _conn
        if conn is None:
            return
        # Only rows Turso already has, when Turso is the permanent copy.
        uploaded = "AND turso_dirty = 0" if settings.turso_configured else ""
        conn.execute(
            "DELETE FROM deals WHERE id IN ("
            f"SELECT id FROM deals WHERE status != 'live' {uploaded} ORDER BY last_seen_at ASC LIMIT 1000)"
        )
        # Never a price point Turso doesn't have yet — it's the only durable copy.
        conn.execute(
            "DELETE FROM price_history WHERE id IN ("
            "SELECT id FROM price_history WHERE turso_synced = 1 ORDER BY seen_at ASC LIMIT 5000)"
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - this IS the last resort; nothing left to fall back to
        log.error("Emergency space cleanup also failed: %s", exc)


def execute(sql: str, params: Iterable[Any] = ()) -> Any:
    with _lock:
        conn = connect()
        try:
            cur = _safe_exec(conn, sql, params)
            # libsql is not autocommit: without this every write is silently lost.
            conn.commit()
        except Exception as exc:  # noqa: BLE001
            if not _is_disk_full(exc):
                raise
            _emergency_free_space()
            try:
                cur = _safe_exec(conn, sql, params)
                conn.commit()
            except Exception as retry_exc:  # noqa: BLE001
                # Still full even after cleanup — drop this one write rather
                # than crash the request. Reads and everything else keep
                # working; the write is lost, but it also already reached
                # Sheets/Turso via the normal flush path in most call sites.
                log.error("Write dropped after disk-full recovery failed: %s", retry_exc)
                return _EmptyCursor()
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
                _safe_exec(conn, sql, row)
            conn.commit()
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


def turso_enqueue(sql: str, args: Iterable[Any] = ()) -> None:
    """Queue a statement to replay on Turso (a delete or rename the upload
    thread can't infer from flags). No-op when Turso isn't configured."""
    if settings.turso_configured:
        execute("INSERT INTO turso_outbox (sql, args) VALUES (?, ?)", (sql, json.dumps(list(args))))


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
