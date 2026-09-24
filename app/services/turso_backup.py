"""Turso as a durable backup, off the request path entirely.

SQLite (app/db.py) stays the live database — every read and write in the
app goes there, instant, nothing can block on it. This module runs in its
own background thread (not an asyncio task, not run_in_executor — a real
OS thread started once at boot) that, every BACKUP_INTERVAL_SECONDS,
pushes changed rows to Turso over its plain HTTP API using plain httpx
calls. It never touches the asyncio event loop, so it can never repeat
the freeze that made the app crash-loop when db.py itself connected to
Turso directly.

Uses Turso's HTTP "pipeline" API (https://<db>.turso.io/v2/pipeline) —
no libsql client library needed. If a round fails (network hiccup, Turso
down, bad token) it's logged and skipped; the app never notices.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional

import httpx

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.turso_backup")

BACKUP_INTERVAL_SECONDS = 180
BATCH_SIZE = 300
REQUEST_TIMEOUT = 20.0

# Mirrors db.SCHEMA's CREATE statements for just the tables this module
# backs up — kept separate and minimal rather than reusing db.SCHEMA
# wholesale, since this only needs to be safe to re-run, not identical.
_SCHEMA = [
    """CREATE TABLE IF NOT EXISTS deals (
        id TEXT PRIMARY KEY, title TEXT, norm_title TEXT, product_key TEXT,
        price REAL, mrp REAL, discount_pct INTEGER, currency TEXT, store TEXT,
        url TEXT, clean_url TEXT, image_url TEXT, coupon TEXT, category TEXT,
        subcategory TEXT, brand TEXT, sizes TEXT, channel_id INTEGER,
        channel_title TEXT, message_id INTEGER, posted_at REAL,
        first_seen_at REAL, last_seen_at REAL, expires_at REAL,
        repost_count INTEGER, channels_seen TEXT, status TEXT, score REAL,
        is_lowest INTEGER, flags TEXT, raw_text TEXT, search_blob TEXT,
        resolved_url TEXT, ai_hook TEXT, ai_mrp_reason TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS price_history (
        id INTEGER PRIMARY KEY, product_key TEXT, price REAL, store TEXT, seen_at REAL
    )""",
    """CREATE TABLE IF NOT EXISTS channels (
        id INTEGER PRIMARY KEY, tg_id INTEGER, username TEXT, title TEXT,
        participants INTEGER, last_message_id INTEGER, last_fetched_at REAL,
        source_user_id INTEGER, active INTEGER
    )""",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
]

_DEAL_COLS = [
    "id", "title", "norm_title", "product_key", "price", "mrp", "discount_pct",
    "currency", "store", "url", "clean_url", "image_url", "coupon", "category",
    "subcategory", "brand", "sizes", "channel_id", "channel_title", "message_id",
    "posted_at", "first_seen_at", "last_seen_at", "expires_at", "repost_count",
    "channels_seen", "status", "score", "is_lowest", "flags", "raw_text",
    "search_blob", "resolved_url", "ai_hook", "ai_mrp_reason",
]
_CHANNEL_COLS = ["id", "tg_id", "username", "title", "participants",
                  "last_message_id", "last_fetched_at", "source_user_id", "active"]

_thread: Optional[threading.Thread] = None
_stop = threading.Event()


def _http_url() -> str:
    # libsql://<db>-<org>.turso.io  ->  https://<db>-<org>.turso.io
    url = settings.turso_url
    if url.startswith("libsql://"):
        return "https://" + url[len("libsql://"):]
    return url


def _arg(value: Any) -> Dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _row_value(cell: Dict[str, Any]) -> Any:
    t = cell.get("type")
    v = cell.get("value")
    if t == "null" or v is None:
        return None
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


def _pipeline(client: httpx.Client, statements: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """POST a batch of statements to Turso's HTTP pipeline API in one round trip."""
    requests = [{"type": "execute", "stmt": s} for s in statements] + [{"type": "close"}]
    resp = client.post(
        f"{_http_url()}/v2/pipeline",
        headers={"Authorization": f"Bearer {settings.turso_auth_token}",
                 "Content-Type": "application/json"},
        json={"requests": requests},
        timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    body = resp.json()
    results = body.get("results") or []
    for r in results:
        if r.get("type") == "error":
            raise RuntimeError(f"Turso error: {r.get('error')}")
    return results


def _ensure_schema(client: httpx.Client) -> None:
    _pipeline(client, [{"sql": s} for s in _SCHEMA])


def _rows_to_dicts(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = (result.get("response") or {}).get("result") or {}
    cols = [c.get("name") for c in (r.get("cols") or [])]
    out = []
    for row in r.get("rows") or []:
        out.append({cols[i]: _row_value(row[i]) for i in range(len(cols))})
    return out


def _upsert_sql(table: str, cols: List[str]) -> str:
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != "id")
    return (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(id) DO UPDATE SET {updates}")


def _chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _backup_deals(client: httpx.Client) -> int:
    rows = db.query("SELECT * FROM deals WHERE dirty = 1 LIMIT ?", (BATCH_SIZE,))
    if not rows:
        return 0
    sql = _upsert_sql("deals", _DEAL_COLS)
    for batch in _chunks(rows, 50):
        statements = [{"sql": sql, "args": [_arg(r.get(c)) for c in _DEAL_COLS]} for r in batch]
        _pipeline(client, statements)
        ids = [r["id"] for r in batch]
        placeholders = ",".join("?" * len(ids))
        db.execute(f"UPDATE deals SET dirty = 0 WHERE id IN ({placeholders})", ids)
    return len(rows)


def _backup_price_history(client: httpx.Client) -> int:
    rows = db.query(
        "SELECT id, product_key, price, store, seen_at FROM price_history "
        "WHERE synced = 0 LIMIT ?", (BATCH_SIZE,),
    )
    if not rows:
        return 0
    sql = ("INSERT INTO price_history (id, product_key, price, store, seen_at) "
           "VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET "
           "product_key=excluded.product_key, price=excluded.price, "
           "store=excluded.store, seen_at=excluded.seen_at")
    for batch in _chunks(rows, 50):
        statements = [
            {"sql": sql, "args": [_arg(r["id"]), _arg(r["product_key"]), _arg(r["price"]),
                                   _arg(r["store"]), _arg(r["seen_at"])]}
            for r in batch
        ]
        _pipeline(client, statements)
        ids = [r["id"] for r in batch]
        placeholders = ",".join("?" * len(ids))
        db.execute(f"UPDATE price_history SET synced = 1 WHERE id IN ({placeholders})", ids)
    return len(rows)


def _backup_small_tables(client: httpx.Client) -> None:
    channels = db.query("SELECT * FROM channels")
    if channels:
        sql = _upsert_sql("channels", _CHANNEL_COLS)
        for batch in _chunks(channels, 50):
            statements = [{"sql": sql, "args": [_arg(r.get(c)) for c in _CHANNEL_COLS]} for r in batch]
            _pipeline(client, statements)

    meta_rows = db.query("SELECT key, value FROM meta")
    if meta_rows:
        sql = ("INSERT INTO meta (key, value) VALUES (?, ?) "
               "ON CONFLICT(key) DO UPDATE SET value = excluded.value")
        statements = [{"sql": sql, "args": [_arg(r["key"]), _arg(r["value"])]} for r in meta_rows]
        for batch in _chunks(statements, 50):
            _pipeline(client, batch)


def _backup_round() -> None:
    # BATCH_SIZE caps each table at 300 rows/round regardless of backlog
    # size, and each sub-step is independent — one table failing (a bad
    # row, a transient Turso error) must not also skip the others this
    # round; it'll just be picked up again next round.
    with httpx.Client() as client:
        _ensure_schema(client)
        deals_n = 0
        history_n = 0
        try:
            deals_n = _backup_deals(client)
        except Exception as exc:  # noqa: BLE001
            log.warning("Turso deals backup failed: %s", exc)
        try:
            history_n = _backup_price_history(client)
        except Exception as exc:  # noqa: BLE001
            log.warning("Turso price-history backup failed: %s", exc)
        try:
            _backup_small_tables(client)
        except Exception as exc:  # noqa: BLE001
            log.warning("Turso channels/meta backup failed: %s", exc)
        if deals_n or history_n:
            log.info("Turso backup: %d deals, %d price points uploaded", deals_n, history_n)


def _run_loop() -> None:
    log.info("Turso backup thread started (every %ds)", BACKUP_INTERVAL_SECONDS)
    while not _stop.is_set():
        if _stop.wait(BACKUP_INTERVAL_SECONDS):
            break
        if not settings.turso_configured:
            continue
        try:
            _backup_round()
        except Exception as exc:  # noqa: BLE001 - a bad round must never kill this thread
            log.warning("Turso backup round failed: %s", exc)


def start() -> None:
    """Call once at app boot. No-op if Turso isn't configured."""
    global _thread
    if not settings.turso_configured or _thread is not None:
        return
    _stop.clear()
    _thread = threading.Thread(target=_run_loop, name="turso-backup", daemon=True)
    _thread.start()


def stop() -> None:
    _stop.set()


def restore() -> int:
    """Pull deals/price_history/channels/meta from Turso into local SQLite.

    Runs synchronously — call it via run_in_executor from async code (it
    does blocking HTTP + SQLite calls). Returns how many deals were
    restored; 0 (with no exception) means "nothing to restore", which the
    caller should treat as a signal to fall back to the Sheets restore.
    """
    if not settings.turso_configured:
        return 0
    try:
        with httpx.Client() as client:
            _ensure_schema(client)
            results = _pipeline(client, [
                {"sql": f"SELECT {', '.join(_DEAL_COLS)} FROM deals"},
                {"sql": f"SELECT {', '.join(_CHANNEL_COLS)} FROM channels"},
                {"sql": "SELECT key, value FROM meta"},
                {"sql": "SELECT id, product_key, price, store, seen_at FROM price_history"},
            ])
        deal_rows = _rows_to_dicts(results[0])
        channel_rows = _rows_to_dicts(results[1])
        meta_rows = _rows_to_dicts(results[2])
        history_rows = _rows_to_dicts(results[3])
    except Exception as exc:  # noqa: BLE001 - restore must never crash boot
        log.warning("Turso restore failed: %s", exc)
        return 0

    for row in deal_rows:
        row["dirty"] = 0
        db.upsert("deals", row, conflict="id")
    for row in channel_rows:
        db.upsert("channels", row, conflict="id")
    for row in meta_rows:
        db.set_meta(row["key"], row["value"])
    for row in history_rows:
        row["synced"] = 1
        db.upsert("price_history", row, conflict="id")

    if deal_rows:
        log.info("Restored %d deals, %d price points from Turso", len(deal_rows), len(history_rows))
    return len(deal_rows)
