"""Google Sheets durable store.

Sheets is the source of truth that survives Render restarts (the free tier's
disk does not). SQLite is the fast index in front of it.

Write strategy: keep an in-memory {deal_id -> sheet row number} map, then flush
dirty rows as ONE batch_update plus ONE append_rows per cycle. Sheets allows
~60 write requests/minute — per-deal writes would blow that instantly.

Everything degrades gracefully: if credentials are absent the app runs fine in
SQLite-only mode.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

from .. import db
from ..config import settings

log = logging.getLogger(__name__)

DEALS_HEADER = [
    "id", "title", "price", "mrp", "discount_pct", "store", "category", "subcategory",
    "brand", "url", "clean_url", "image_url", "coupon", "sizes", "channel_title",
    "message_id", "posted_at_iso", "expires_at_iso", "repost_count", "status",
    "score", "is_lowest", "flags", "product_key", "raw_text",
]
CHANNELS_HEADER = [
    "tg_id", "username", "title", "participants", "last_message_id", "last_fetched_iso",
    "active", "source_user_telegram_id",
]
USERS_HEADER = ["telegram_id", "username", "first_name", "created_iso", "last_login_iso"]
WATCH_HEADER = ["user_telegram_id", "query", "category", "store", "max_price", "min_discount", "notify", "created_iso"]
# Which user tracks which channel. Keyed by stable Telegram ids (not local
# autoincrement ids) so it survives a full cold start, where every local id
# gets reassigned as rows are recreated.
USER_CHANNELS_HEADER = ["user_telegram_id", "channel_tg_id", "enabled", "added_iso"]

_lock = threading.RLock()
_client = None
_spreadsheet = None
_row_map: Dict[str, int] = {}   # deal id -> 1-based row number in the Deals sheet
_next_row = 2
_ready = False
_last_error: Optional[str] = None
_last_flush: float = 0.0


def _iso(ts: Optional[float]) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(ts)))


def is_enabled() -> bool:
    return settings.sheets_configured


def status() -> Dict[str, Any]:
    return {
        "configured": settings.sheets_configured,
        "connected": _ready,
        "sheet_id": settings.sheet_id[:12] + "…" if settings.sheet_id else "",
        "rows_tracked": len(_row_map),
        "last_flush": _iso(_last_flush) if _last_flush else None,
        "last_error": _last_error,
    }


def connect() -> bool:
    """Open the spreadsheet and make sure every tab exists. Idempotent."""
    global _client, _spreadsheet, _ready, _last_error
    if not settings.sheets_configured:
        _last_error = "Google Sheets not configured"
        return False
    with _lock:
        if _ready and _spreadsheet is not None:
            return True
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            info = settings.service_account_info()
            if not info:
                _last_error = "Service account JSON could not be parsed"
                return False
            creds = Credentials.from_service_account_info(
                info,
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive.file",
                ],
            )
            _client = gspread.authorize(creds)
            _spreadsheet = _client.open_by_key(settings.sheet_id)
            _ensure_tabs()
            _load_row_map()
            _ready = True
            _last_error = None
            log.info("Google Sheets connected: %s", _spreadsheet.title)
            return True
        except Exception as exc:  # noqa: BLE001 - never let Sheets break the app
            _ready = False
            _last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Sheets connect failed: %s", _last_error)
            return False


def _ensure_tabs() -> None:
    wanted = {
        "Deals": DEALS_HEADER,
        "Channels": CHANNELS_HEADER,
        "Users": USERS_HEADER,
        "Watchlists": WATCH_HEADER,
        "UserChannels": USER_CHANNELS_HEADER,
    }
    existing = {ws.title: ws for ws in _spreadsheet.worksheets()}
    for title, header in wanted.items():
        if title not in existing:
            ws = _spreadsheet.add_worksheet(title=title, rows=1000, cols=max(len(header), 10))
            ws.update("A1", [header])
            ws.freeze(rows=1)
        else:
            ws = existing[title]
            current = ws.row_values(1)
            if current != header:
                ws.update("A1", [header])
    # A fresh spreadsheet ships with a stray "Sheet1"; drop it.
    if "Sheet1" in existing and len(existing) > 1:
        try:
            _spreadsheet.del_worksheet(existing["Sheet1"])
        except Exception:  # noqa: BLE001
            pass


def _load_row_map() -> None:
    global _row_map, _next_row
    ws = _spreadsheet.worksheet("Deals")
    ids = ws.col_values(1)[1:]  # skip header
    _row_map = {deal_id: idx + 2 for idx, deal_id in enumerate(ids) if deal_id}
    _next_row = len(ids) + 2
    log.info("Sheets row map loaded: %d deals", len(_row_map))


def _deal_to_row(deal: Dict[str, Any]) -> List[Any]:
    flags = deal.get("flags")
    if isinstance(flags, str):
        try:
            flags = json.loads(flags)
        except json.JSONDecodeError:
            flags = []
    return [
        deal.get("id", ""),
        (deal.get("title") or "")[:400],
        deal.get("price") or "",
        deal.get("mrp") or "",
        deal.get("discount_pct") or 0,
        deal.get("store") or "",
        deal.get("category") or "",
        deal.get("subcategory") or "",
        deal.get("brand") or "",
        (deal.get("url") or "")[:900],
        (deal.get("clean_url") or "")[:900],
        (deal.get("image_url") or "")[:900],
        deal.get("coupon") or "",
        deal.get("sizes") or "",
        deal.get("channel_title") or "",
        deal.get("message_id") or "",
        _iso(deal.get("posted_at")),
        _iso(deal.get("expires_at")),
        deal.get("repost_count") or 1,
        deal.get("status") or "live",
        deal.get("score") or 0,
        "yes" if deal.get("is_lowest") else "",
        ", ".join(flags or []),
        deal.get("product_key") or "",
        (deal.get("raw_text") or "")[:800],
    ]


def _a1(row: int, width: int) -> str:
    last_col = chr(ord("A") + width - 1) if width <= 26 else "A" + chr(ord("A") + width - 27)
    return f"A{row}:{last_col}{row}"


def flush_deals(limit: int = 400) -> Dict[str, int]:
    """Push dirty deals to the Deals tab. Safe to call on a timer."""
    global _next_row, _last_flush, _last_error
    if not connect():
        return {"updated": 0, "appended": 0, "skipped": 0}

    rows = db.query(
        "SELECT * FROM deals WHERE dirty = 1 ORDER BY last_seen_at DESC LIMIT ?", (limit,)
    )
    if not rows:
        return {"updated": 0, "appended": 0, "skipped": 0}

    deals = db.rows_to_dicts(rows)
    updates: List[Dict[str, Any]] = []
    appends: List[List[Any]] = []
    appended_ids: List[str] = []
    width = len(DEALS_HEADER)

    with _lock:
        for deal in deals:
            values = _deal_to_row(deal)
            deal_id = deal["id"]
            if deal_id in _row_map:
                updates.append({"range": _a1(_row_map[deal_id], width), "values": [values]})
            else:
                appends.append(values)
                appended_ids.append(deal_id)

        try:
            ws = _spreadsheet.worksheet("Deals")
            if updates:
                ws.batch_update(updates, value_input_option="RAW")
            if appends:
                ws.append_rows(appends, value_input_option="RAW", table_range="A1")
                for deal_id in appended_ids:
                    _row_map[deal_id] = _next_row
                    _next_row += 1
            db.execute_many(
                "UPDATE deals SET dirty = 0 WHERE id = ?", [(d["id"],) for d in deals]
            )
            _last_flush = time.time()
            _last_error = None
        except Exception as exc:  # noqa: BLE001
            _last_error = f"{type(exc).__name__}: {exc}"
            log.warning("Sheets flush failed (will retry next cycle): %s", _last_error)
            return {"updated": 0, "appended": 0, "skipped": len(deals)}

    return {"updated": len(updates), "appended": len(appends), "skipped": 0}


def delete_deals(ids: List[str]) -> int:
    """Remove specific rows from the Deals tab — mirrors a local purge.

    Every other write to this tab is additive (new deal) or in-place
    (status flips to expired/dead), so without this Sheets keeps every deal
    forever while the local cache quietly forgets old ones via
    store.purge_ancient, and the two drift apart. This keeps them in sync.

    Deleting spreadsheet rows shifts every row below them, which would
    silently corrupt _row_map for every other deal if done carelessly. The
    fix: issue all deletions as one batch, ordered highest row-number first,
    so each deleteDimension request lands before any row above it moves —
    then reload the row map from scratch rather than trying to patch offsets.
    """
    if not ids or not connect():
        return 0
    with _lock:
        target_rows = sorted({_row_map[i] for i in ids if i in _row_map}, reverse=True)
        if not target_rows:
            return 0
        try:
            ws = _spreadsheet.worksheet("Deals")
            requests = [
                {
                    "deleteDimension": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "ROWS",
                            "startIndex": row - 1,  # 0-indexed, inclusive
                            "endIndex": row,          # 0-indexed, exclusive
                        }
                    }
                }
                for row in target_rows
            ]
            _spreadsheet.batch_update({"requests": requests})
            _load_row_map()
        except Exception as exc:  # noqa: BLE001
            log.warning("Sheets delete failed (will retry next cycle): %s", exc)
            return 0
    return len(target_rows)


def restore_deals() -> int:
    """Rebuild the SQLite cache from Sheets after a cold start."""
    if not connect():
        return 0
    from .parser import normalize_title

    try:
        ws = _spreadsheet.worksheet("Deals")
        records = ws.get_all_records()
    except Exception as exc:  # noqa: BLE001
        log.warning("Sheets restore failed: %s", exc)
        return 0

    def _ts(value: str) -> float:
        if not value:
            return 0.0
        try:
            return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
        except (ValueError, TypeError):
            return 0.0

    restored = 0
    for rec in records:
        deal_id = str(rec.get("id") or "").strip()
        if not deal_id:
            continue
        title = str(rec.get("title") or "")
        flags = [f.strip() for f in str(rec.get("flags") or "").split(",") if f.strip()]
        row = {
            "id": deal_id,
            "title": title,
            "norm_title": normalize_title(title),
            "product_key": str(rec.get("product_key") or ""),
            "price": float(rec["price"]) if str(rec.get("price") or "").strip() else None,
            "mrp": float(rec["mrp"]) if str(rec.get("mrp") or "").strip() else None,
            "discount_pct": int(rec.get("discount_pct") or 0),
            "currency": "INR",
            "store": str(rec.get("store") or ""),
            "url": str(rec.get("url") or ""),
            "clean_url": str(rec.get("clean_url") or ""),
            "image_url": str(rec.get("image_url") or ""),
            "coupon": str(rec.get("coupon") or ""),
            "category": str(rec.get("category") or "Other"),
            "subcategory": str(rec.get("subcategory") or "General"),
            "brand": str(rec.get("brand") or ""),
            "sizes": str(rec.get("sizes") or ""),
            "channel_id": 0,
            "channel_title": str(rec.get("channel_title") or ""),
            "message_id": int(rec.get("message_id") or 0),
            "posted_at": _ts(rec.get("posted_at_iso")),
            "first_seen_at": _ts(rec.get("posted_at_iso")),
            "last_seen_at": _ts(rec.get("posted_at_iso")),
            "expires_at": _ts(rec.get("expires_at_iso")),
            "repost_count": int(rec.get("repost_count") or 1),
            "channels_seen": "[]",
            "status": str(rec.get("status") or "live"),
            "score": float(rec.get("score") or 0),
            "is_lowest": 1 if str(rec.get("is_lowest")).lower() in {"yes", "true", "1"} else 0,
            "flags": json.dumps(flags),
            "raw_text": str(rec.get("raw_text") or ""),
            "search_blob": " ".join(
                filter(None, [title.lower(), str(rec.get("brand") or "").lower(),
                              str(rec.get("category") or "").lower(),
                              str(rec.get("subcategory") or "").lower()])
            ),
            "dirty": 0,
        }
        db.upsert("deals", row, conflict="id")
        restored += 1

    with _lock:
        _load_row_map()
    log.info("Restored %d deals from Google Sheets", restored)
    return restored


def sync_channels() -> int:
    """Mirror the channel registry into its tab (small, so full rewrite is fine)."""
    if not connect():
        return 0
    rows = db.query(
        "SELECT c.*, u.telegram_id AS source_telegram_id FROM channels c "
        "LEFT JOIN users u ON u.id = c.source_user_id ORDER BY c.title"
    )
    values = [[
        r["tg_id"], r["username"] or "", r["title"] or "", r["participants"] or 0,
        r["last_message_id"] or 0, _iso(r["last_fetched_at"]), "yes" if r["active"] else "no",
        r["source_telegram_id"] or "",
    ] for r in rows]
    try:
        ws = _spreadsheet.worksheet("Channels")
        ws.batch_clear(["A2:H10000"])
        if values:
            ws.update("A2", values, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log.warning("Channel sync failed: %s", exc)
        return 0
    return len(values)


def sync_users() -> int:
    if not connect():
        return 0
    rows = db.query("SELECT * FROM users ORDER BY created_at")
    values = [[
        r["telegram_id"], r["username"] or "", r["first_name"] or "",
        _iso(r["created_at"]), _iso(r["last_login_at"]),
    ] for r in rows]
    try:
        ws = _spreadsheet.worksheet("Users")
        ws.batch_clear(["A2:E5000"])
        if values:
            ws.update("A2", values, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log.warning("User sync failed: %s", exc)
        return 0
    return len(values)


def sync_user_channels() -> int:
    """Mirror who-tracks-what, keyed by stable Telegram ids."""
    if not connect():
        return 0
    rows = db.query(
        "SELECT u.telegram_id AS user_tid, c.tg_id AS channel_tid, uc.enabled, uc.added_at "
        "FROM user_channels uc "
        "JOIN users u ON u.id = uc.user_id JOIN channels c ON c.id = uc.channel_id"
    )
    values = [[
        r["user_tid"], r["channel_tid"], "yes" if r["enabled"] else "no", _iso(r["added_at"]),
    ] for r in rows]
    try:
        ws = _spreadsheet.worksheet("UserChannels")
        ws.batch_clear(["A2:D20000"])
        if values:
            ws.update("A2", values, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log.warning("UserChannels sync failed: %s", exc)
        return 0
    return len(values)


def sync_watchlists() -> int:
    if not connect():
        return 0
    rows = db.query(
        "SELECT w.*, u.telegram_id AS user_tid FROM watchlists w JOIN users u ON u.id = w.user_id"
    )
    values = []
    for r in rows:
        try:
            filters = json.loads(r["filters"] or "{}")
        except (json.JSONDecodeError, TypeError):
            filters = {}
        values.append([
            r["user_tid"], r["query"] or "", filters.get("category") or "",
            filters.get("store") or "", filters.get("max_price") or "",
            filters.get("min_discount") or 0, "yes" if r["notify"] else "no",
            _iso(r["created_at"]),
        ])
    try:
        ws = _spreadsheet.worksheet("Watchlists")
        ws.batch_clear(["A2:H5000"])
        if values:
            ws.update("A2", values, value_input_option="RAW")
    except Exception as exc:  # noqa: BLE001
        log.warning("Watchlist sync failed: %s", exc)
        return 0
    return len(values)


def sync_all_meta() -> Dict[str, int]:
    """Flush every small table. Cheap enough to call once per ingest cycle."""
    return {
        "users": sync_users(),
        "channels": sync_channels(),
        "user_channels": sync_user_channels(),
        "watchlists": sync_watchlists(),
    }


def restore_users() -> int:
    """Recreate user shells from Sheets. Sessions are never stored in Sheets
    (by design — they're a secret), so restored users must sign in again;
    what this buys is that their *local id* is stable once they do, so
    anything restored below that references them (channels, alerts) still
    lines up. See _finish_login, which updates in place when telegram_id
    already exists instead of minting a new id.
    """
    if not connect():
        return 0
    try:
        records = _spreadsheet.worksheet("Users").get_all_records()
    except Exception as exc:  # noqa: BLE001
        log.warning("Users restore failed: %s", exc)
        return 0

    restored = 0
    for rec in records:
        tid = rec.get("telegram_id")
        if not tid:
            continue
        try:
            tid = int(tid)
        except (TypeError, ValueError):
            continue
        db.execute(
            "INSERT INTO users (telegram_id, username, first_name, phone, session_enc, "
            "created_at, last_login_at) VALUES (?, ?, ?, '', '', ?, ?) "
            "ON CONFLICT(telegram_id) DO NOTHING",
            (tid, str(rec.get("username") or ""), str(rec.get("first_name") or ""),
             _parse_iso(rec.get("created_iso")), _parse_iso(rec.get("last_login_iso"))),
        )
        restored += 1
    log.info("Restored %d user shells from Sheets (re-login required for each)", restored)
    return restored


def restore_channels() -> int:
    """Recreate the channel registry, resolving source_user by telegram_id."""
    if not connect():
        return 0
    try:
        records = _spreadsheet.worksheet("Channels").get_all_records()
    except Exception as exc:  # noqa: BLE001
        log.warning("Channels restore failed: %s", exc)
        return 0

    restored = 0
    for rec in records:
        tg_id = rec.get("tg_id")
        if not tg_id:
            continue
        source_tid = rec.get("source_user_telegram_id")
        source_user_id = None
        if source_tid:
            row = db.query_one("SELECT id FROM users WHERE telegram_id = ?", (source_tid,))
            source_user_id = row["id"] if row else None
        db.upsert("channels", {
            "tg_id": int(tg_id),
            "username": str(rec.get("username") or ""),
            "title": str(rec.get("title") or ""),
            "participants": int(rec.get("participants") or 0),
            "last_message_id": int(rec.get("last_message_id") or 0),
            "last_fetched_at": _parse_iso(rec.get("last_fetched_iso")),
            "source_user_id": source_user_id,
            "active": 1 if str(rec.get("active")).lower() in {"yes", "true", "1"} else 0,
        }, conflict="tg_id")
        restored += 1
    log.info("Restored %d channels from Sheets", restored)
    return restored


def restore_user_channels() -> int:
    if not connect():
        return 0
    try:
        records = _spreadsheet.worksheet("UserChannels").get_all_records()
    except Exception as exc:  # noqa: BLE001
        log.warning("UserChannels restore failed: %s", exc)
        return 0

    restored = 0
    for rec in records:
        user_row = db.query_one(
            "SELECT id FROM users WHERE telegram_id = ?", (rec.get("user_telegram_id"),)
        )
        channel_row = db.query_one(
            "SELECT id FROM channels WHERE tg_id = ?", (rec.get("channel_tg_id"),)
        )
        if not user_row or not channel_row:
            continue
        enabled = 1 if str(rec.get("enabled")).lower() in {"yes", "true", "1"} else 0
        db.execute(
            "INSERT INTO user_channels (user_id, channel_id, enabled, added_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, channel_id) DO UPDATE SET enabled = excluded.enabled",
            (user_row["id"], channel_row["id"], enabled, _parse_iso(rec.get("added_iso")) or time.time()),
        )
        restored += 1
    log.info("Restored %d channel-tracking links from Sheets", restored)
    return restored


def restore_watchlists() -> int:
    if not connect():
        return 0
    try:
        records = _spreadsheet.worksheet("Watchlists").get_all_records()
    except Exception as exc:  # noqa: BLE001
        log.warning("Watchlists restore failed: %s", exc)
        return 0

    restored = 0
    for rec in records:
        user_row = db.query_one(
            "SELECT id FROM users WHERE telegram_id = ?", (rec.get("user_telegram_id"),)
        )
        query = str(rec.get("query") or "").strip()
        if not user_row or not query:
            continue
        filters = {
            "category": rec.get("category") or "",
            "store": rec.get("store") or "",
            "max_price": float(rec["max_price"]) if str(rec.get("max_price") or "").strip() else None,
            "min_discount": int(rec.get("min_discount") or 0),
        }
        already = db.query_one(
            "SELECT id FROM watchlists WHERE user_id = ? AND query = ?", (user_row["id"], query)
        )
        if already:
            continue
        notify = 1 if str(rec.get("notify")).lower() in {"yes", "true", "1"} else 0
        db.execute(
            "INSERT INTO watchlists (user_id, query, filters, notify, created_at, last_notified_at) "
            "VALUES (?, ?, ?, ?, ?, 0)",
            (user_row["id"], query, json.dumps(filters), notify,
             _parse_iso(rec.get("created_iso")) or time.time()),
        )
        restored += 1
    log.info("Restored %d watchlists from Sheets", restored)
    return restored


def restore_all_meta() -> Dict[str, int]:
    """Full cold-start recovery, in dependency order: users -> channels -> links."""
    return {
        "users": restore_users(),
        "channels": restore_channels(),
        "user_channels": restore_user_channels(),
        "watchlists": restore_watchlists(),
    }


def _parse_iso(value: Any) -> float:
    if not value:
        return 0.0
    try:
        return time.mktime(time.strptime(str(value), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, TypeError):
        return 0.0
