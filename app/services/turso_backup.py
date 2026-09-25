"""Turso: the permanent store of every deal, its price history, and price alerts.

Local SQLite is only a fast cache of the last LOCAL_CACHE_DAYS
(store.purge_local_cache trims it); Turso keeps everything forever, split
across up to two physical databases:

  MAIN database (TURSO_DATABASE_URL) —
    deals         every deal ever parsed, upserted whenever it changes locally;
                  the cache refills from here after a restart.
    products      one row per tracked product: title/store/url/image plus
                  running min/max/last price, so "what did this cost?" is one
                  primary-key lookup even after the deal left the local cache.
    price_alerts  visitors' "tell me below ₹X" alerts, which otherwise lived
                  only on Render's wiped-on-restart disk.
    devices       every app install that registered: its push token, digest
                  choice and follows (as JSON). Without this a restart left
                  the server unable to push to anyone until they reopened the
                  app — "Sent to 0 devices".

  PRICES database (TURSO_DB_02, optional) —
    price_points  every observed price change, keyed (product_key, seen_at) in
                  a WITHOUT ROWID table — one product's whole history is a
                  single contiguous range scan, and a read touches only the
                  rows it returns (Turso bills per row read). This is by far
                  the fastest-growing table (a row per price change, forever),
                  which is why it can live in its own database once TURSO_DB_02
                  is set — otherwise it stays in the main database as before.

Writes go out from a background OS thread (never the asyncio loop) over
Turso's HTTP pipeline API, many rows per statement. Reads (history, aged-out
deals) are in price_store.py. If a round fails it's logged and retried next
round; the app never notices.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

import httpx

from .. import db
from ..config import settings

log = logging.getLogger("dealradar.turso_backup")

BACKUP_INTERVAL_SECONDS = 180
BATCH_SIZE = 500
MAX_BATCHES_PER_ROUND = 20
ROWS_PER_STATEMENT = 100
REQUEST_TIMEOUT = 20.0
MIGRATION_BATCHES_PER_ROUND = 10  # moving price_points to TURSO_DB_02, if just enabled

# v1 (no dr_schema table) mirrored the whole app DB with colliding ids — it is
# wiped once. v2 -> v3 added the deals table. v3 -> v4 moves price_points out
# to its own database when TURSO_DB_02 is configured (one-time migration,
# resumed across restarts via the local turso_price_migrated meta flag).
SCHEMA_VERSION = 4
_V1_TABLES = ["deals", "price_history", "channels", "meta",
              "price_points", "products", "price_alerts"]

_DEAL_COLS = [
    "id", "title", "norm_title", "product_key", "price", "mrp", "discount_pct",
    "currency", "store", "url", "clean_url", "image_url", "coupon", "category",
    "subcategory", "brand", "sizes", "channel_id", "channel_title", "message_id",
    "posted_at", "first_seen_at", "last_seen_at", "expires_at", "repost_count",
    "channels_seen", "status", "score", "is_lowest", "flags", "raw_text",
    "search_blob", "resolved_url", "ai_hook", "ai_mrp_reason",
]

_SCHEMA_MAIN = [
    f"CREATE TABLE IF NOT EXISTS deals ({', '.join(c + (' TEXT PRIMARY KEY' if c == 'id' else '') for c in _DEAL_COLS)})",
    # Product lookups, and the boot-time "last few days" restore, are index
    # range scans — Turso bills rows read, so no query here scans the table.
    "CREATE INDEX IF NOT EXISTS idx_deals_pkey ON deals(product_key)",
    "CREATE INDEX IF NOT EXISTS idx_deals_last_seen ON deals(last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_deals_expires ON deals(expires_at)",
    """CREATE TABLE IF NOT EXISTS products (
        product_key TEXT PRIMARY KEY,
        title TEXT, store TEXT, url TEXT, image_url TEXT, category TEXT,
        first_seen INTEGER, last_seen INTEGER,
        last_price REAL, min_price REAL, min_at INTEGER, max_price REAL
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS price_alerts (
        device_id TEXT NOT NULL, created_at REAL NOT NULL,
        deal_id TEXT, product_key TEXT, title TEXT,
        target_price REAL, start_price REAL, push_token TEXT,
        triggered_at REAL, triggered_price REAL,
        PRIMARY KEY (device_id, created_at)
    ) WITHOUT ROWID""",
    """CREATE TABLE IF NOT EXISTS devices (
        device_id TEXT PRIMARY KEY,
        platform TEXT, push_token TEXT, digest INTEGER, digest_hour INTEGER,
        follows TEXT, created_at REAL, last_seen_at REAL
    ) WITHOUT ROWID""",
    "CREATE TABLE IF NOT EXISTS dr_schema (version INTEGER NOT NULL)",
]

_PRICE_POINTS_DDL = """CREATE TABLE IF NOT EXISTS price_points (
    product_key TEXT NOT NULL,
    seen_at     INTEGER NOT NULL,
    price       REAL NOT NULL,
    PRIMARY KEY (product_key, seen_at)
) WITHOUT ROWID"""

_ALERT_COLS = ["device_id", "created_at", "deal_id", "product_key", "title",
               "target_price", "start_price", "push_token", "triggered_at", "triggered_price"]
_DEVICE_COLS = ["device_id", "platform", "push_token", "digest", "digest_hour",
                "follows", "created_at", "last_seen_at"]
_FOLLOW_COLS = ["kind", "value", "min_discount", "created_at"]

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_schema_lock = threading.Lock()
_schema_ready = False
_price_migration_done = False  # set once local meta confirms the move finished


# --- targets: two independent (url, headers) pairs -----------------------
# "main" (deals/products/price_alerts) always uses TURSO_DATABASE_URL.
# "prices" (price_points) uses TURSO_DB_02 when set, else falls back to the
# same database as main — in which case price_points just lives there, as it
# always has, and the one-time migration below never triggers.

def _url(raw: str) -> str:
    # libsql://<db>-<org>.turso.io  ->  https://<db>-<org>.turso.io
    return "https://" + raw[len("libsql://"):] if raw.startswith("libsql://") else raw


def _headers(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def http_url() -> str:
    return _url(settings.turso_url)


def auth_headers() -> Dict[str, str]:
    return _headers(settings.turso_auth_token)


def prices_http_url() -> str:
    return _url(settings.turso2_url) if settings.turso2_configured else http_url()


def prices_auth_headers() -> Dict[str, str]:
    return _headers(settings.turso2_auth_token) if settings.turso2_configured else auth_headers()


Target = Tuple[str, Dict[str, str]]


def target_main() -> Target:
    return http_url(), auth_headers()


def target_prices() -> Target:
    return prices_http_url(), prices_auth_headers()


def _split() -> bool:
    """True once price_points has (or is getting) its own database."""
    return settings.turso2_configured


def arg(value: Any) -> Dict[str, Any]:
    if value is None:
        return {"type": "null"}
    if isinstance(value, bool):
        return {"type": "integer", "value": str(int(value))}
    if isinstance(value, int):
        return {"type": "integer", "value": str(value)}
    if isinstance(value, float):
        return {"type": "float", "value": value}
    return {"type": "text", "value": str(value)}


def _cell(cell: Dict[str, Any]) -> Any:
    t = cell.get("type")
    v = cell.get("value")
    if t == "null" or v is None:
        return None
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


def pipeline_body(statements: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {"requests": [{"type": "execute", "stmt": s} for s in statements] + [{"type": "close"}]}


def check_results(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    results = body.get("results") or []
    for r in results:
        if r.get("type") == "error":
            raise RuntimeError(f"Turso error: {r.get('error')}")
    return results


def rows_to_dicts(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    r = (result.get("response") or {}).get("result") or {}
    cols = [c.get("name") for c in (r.get("cols") or [])]
    return [{cols[i]: _cell(row[i]) for i in range(len(cols))} for row in r.get("rows") or []]


def _pipeline(client: httpx.Client, statements: List[Dict[str, Any]],
              target: Optional[Target] = None) -> List[Dict[str, Any]]:
    """POST a batch of statements to Turso's HTTP pipeline API in one round trip."""
    url, headers = target or target_main()
    resp = client.post(f"{url}/v2/pipeline", headers=headers,
                       json=pipeline_body(statements), timeout=REQUEST_TIMEOUT)
    resp.raise_for_status()
    return check_results(resp.json())


def _chunks(items: List[Any], size: int) -> Iterable[List[Any]]:
    for i in range(0, len(items), size):
        yield items[i:i + size]


# --- schema / one-time reset / one-time price_points migration -----------

def _ensure_schema(client: httpx.Client) -> None:
    """Create/upgrade the main schema once per process, then split off
    price_points into its own database if TURSO_DB_02 is configured.

    Only a v1 database (no dr_schema table at all) is wiped; later versions
    upgrade in place. The version check has to *succeed* first — a network
    error raises before anything is dropped, so a restart never clears data.
    """
    global _schema_ready, _price_migration_done
    with _schema_lock:
        if _schema_ready:
            return
        found = _pipeline(client, [
            {"sql": "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'dr_schema'"},
        ])
        version = 0
        if rows_to_dicts(found[0]):
            rows = rows_to_dicts(_pipeline(client, [{"sql": "SELECT MAX(version) AS v FROM dr_schema"}])[0])
            version = int((rows[0].get("v") if rows else 0) or 0)
        if version == 0:
            log.warning("Turso holds the old v1 layout — clearing it once and starting fresh")
            _pipeline(client, [{"sql": f"DROP TABLE IF EXISTS {t}"} for t in _V1_TABLES + ["dr_schema"]])
            db.execute("DELETE FROM turso_outbox")
        statements = [{"sql": s} for s in _SCHEMA_MAIN]
        if not _split():
            # No TURSO_DB_02: price_points lives in the main database, exactly
            # as it always has.
            statements.append({"sql": _PRICE_POINTS_DDL})
        if version < SCHEMA_VERSION:
            log.info("Turso schema v%d -> v%d", version, SCHEMA_VERSION)
            statements += [{"sql": "DELETE FROM dr_schema"},
                           {"sql": "INSERT INTO dr_schema (version) VALUES (?)", "args": [arg(SCHEMA_VERSION)]}]
        _pipeline(client, statements)

        if _split():
            _pipeline(client, [{"sql": _PRICE_POINTS_DDL}], target=target_prices())
            _price_migration_done = db.get_meta("turso_price_migrated") == "1"
        _schema_ready = True


def _migrate_price_points(client: httpx.Client) -> int:
    """One-time, resumable move of price_points from the main database to
    TURSO_DB_02, triggered by setting that env var on an existing deployment.

    Each round moves up to MIGRATION_BATCHES_PER_ROUND batches: copy a batch
    to the prices database, then delete that same batch from main — so a
    round that's interrupted just resumes with the next SELECT next round,
    never re-copying or losing rows. Finishes by dropping the now-empty
    table from main and recording completion in local meta.
    """
    global _price_migration_done
    if _price_migration_done or not _split():
        return 0
    moved = 0
    for _ in range(MIGRATION_BATCHES_PER_ROUND):
        rows = rows_to_dicts(_pipeline(client, [{
            "sql": "SELECT product_key, seen_at, price FROM price_points ORDER BY product_key, seen_at LIMIT ?",
            "args": [arg(BATCH_SIZE)],
        }])[0])
        if not rows:
            _pipeline(client, [{"sql": "DROP TABLE IF EXISTS price_points"}])
            db.set_meta("turso_price_migrated", "1")
            _price_migration_done = True
            log.info("Turso price_points migration to TURSO_DB_02 complete (%d rows moved this run)", moved)
            break
        _pipeline(client, [{
            "sql": "INSERT OR IGNORE INTO price_points (product_key, seen_at, price) VALUES "
                   + ", ".join("(?, ?, ?)" for _ in rows),
            "args": [arg(v) for r in rows for v in (r["product_key"], r["seen_at"], r["price"])],
        }], target=target_prices())
        # Delete exactly the rows just copied, one OR'd statement — confirms
        # the copy landed before anything is removed from the source.
        _pipeline(client, [{
            "sql": "DELETE FROM price_points WHERE "
                   + " OR ".join("(product_key = ? AND seen_at = ?)" for _ in rows),
            "args": [arg(v) for r in rows for v in (r["product_key"], r["seen_at"])],
        }])
        moved += len(rows)
        if len(rows) < BATCH_SIZE:
            _pipeline(client, [{"sql": "DROP TABLE IF EXISTS price_points"}])
            db.set_meta("turso_price_migrated", "1")
            _price_migration_done = True
            log.info("Turso price_points migration to TURSO_DB_02 complete (%d rows moved this run)", moved)
            break
    if moved:
        log.info("Migrated %d price_points rows to TURSO_DB_02", moved)
    return moved


# --- uploads ----------------------------------------------------------

def _push_outbox(client: httpx.Client) -> int:
    """Replay queued deletes/renames (see db.turso_enqueue) in order, each
    against the Turso database its `target` column names."""
    rows = db.query("SELECT id, sql, args, target FROM turso_outbox ORDER BY id LIMIT ?", (BATCH_SIZE,))
    for batch in _chunks(rows, 50):
        for tgt_name, tgt in (("main", target_main()), ("prices", target_prices())):
            group = [r for r in batch if (r["target"] or "main") == tgt_name]
            if not group:
                continue
            statements = [{"sql": r["sql"], "args": [arg(a) for a in json.loads(r["args"] or "[]")]} for r in group]
            try:
                _pipeline(client, statements, target=tgt)
            except RuntimeError as exc:
                # A statement Turso rejected will be rejected forever — drop it
                # rather than block every later change behind it.
                log.warning("Dropping %d Turso outbox statements: %s", len(group), exc)
        ids = [r["id"] for r in batch]
        db.execute(f"DELETE FROM turso_outbox WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(rows)


_PRODUCT_UPSERT = (
    "INSERT INTO products (product_key, title, store, url, image_url, category, first_seen, "
    "last_seen, last_price, min_price, min_at, max_price) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT(product_key) DO UPDATE SET "
    "title = COALESCE(excluded.title, title), store = COALESCE(excluded.store, store), "
    "url = COALESCE(excluded.url, url), image_url = COALESCE(NULLIF(excluded.image_url, ''), image_url), "
    "category = COALESCE(excluded.category, category), "
    "first_seen = MIN(first_seen, excluded.first_seen), "
    "last_price = CASE WHEN excluded.last_seen >= last_seen THEN excluded.last_price ELSE last_price END, "
    "last_seen = MAX(last_seen, excluded.last_seen), "
    "min_at = CASE WHEN excluded.min_price < min_price THEN excluded.min_at ELSE min_at END, "
    "min_price = MIN(min_price, excluded.min_price), max_price = MAX(max_price, excluded.max_price)"
)


def _push_price_points(client: httpx.Client) -> int:
    """price_points rows go to the prices database (TURSO_DB_02 if set, else
    main); the products summary they feed always stays in the main database."""
    rows = db.query(
        "SELECT id, product_key, price, store, seen_at FROM price_history "
        "WHERE turso_synced = 0 ORDER BY id LIMIT ?", (BATCH_SIZE,),
    )
    if not rows:
        return 0
    keys = sorted({r["product_key"] for r in rows})
    meta: Dict[str, Dict[str, Any]] = {}
    for key_batch in _chunks(keys, 200):
        for d in db.query(
            "SELECT product_key, title, store, COALESCE(NULLIF(resolved_url, ''), clean_url, url) AS url, "
            f"image_url, category FROM deals WHERE product_key IN ({','.join('?' * len(key_batch))}) "
            "ORDER BY last_seen_at ASC", key_batch,
        ):
            meta[d["product_key"]] = d  # newest deal wins

    prices_target = target_prices()
    for batch in _chunks(rows, ROWS_PER_STATEMENT):
        points = [(r["product_key"], int(r["seen_at"]), float(r["price"])) for r in batch]
        _pipeline(client, [{
            "sql": "INSERT OR IGNORE INTO price_points (product_key, seen_at, price) VALUES "
                   + ", ".join("(?, ?, ?)" for _ in points),
            "args": [arg(v) for p in points for v in p],
        }], target=prices_target)

        # One products upsert per distinct product in the batch, carrying the
        # batch's own first/last/min/max — idempotent, so a retried round
        # can't skew the running figures. Always against the main database.
        by_key: Dict[str, List[tuple]] = {}
        stores: Dict[str, str] = {}
        for r, (key, seen, price) in zip(batch, points):
            by_key.setdefault(key, []).append((seen, price))
            stores[key] = r["store"] or stores.get(key) or ""
        product_statements = []
        for key, obs in by_key.items():
            obs.sort()
            d = meta.get(key) or {}
            low = min(obs, key=lambda o: o[1])
            product_statements.append({
                "sql": _PRODUCT_UPSERT,
                "args": [arg(v) for v in (
                    key, d.get("title"), d.get("store") or stores[key], d.get("url"),
                    d.get("image_url"), d.get("category"), obs[0][0], obs[-1][0], obs[-1][1],
                    low[1], low[0], max(o[1] for o in obs),
                )],
            })
        _pipeline(client, product_statements)
        ids = [r["id"] for r in batch]
        db.execute(f"UPDATE price_history SET turso_synced = 1 WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(rows)


def _push_deals(client: httpx.Client) -> int:
    """Upsert every deal changed locally since its last upload.

    turso_dirty is a change counter bumped by triggers (db.py) whenever a
    deal is written; it's cleared only if it still holds the value read here,
    so an edit that lands mid-upload is sent again next round, never lost.
    """
    rows = db.query(f"SELECT {', '.join(_DEAL_COLS)}, turso_dirty FROM deals WHERE turso_dirty > 0 LIMIT ?",
                    (BATCH_SIZE,))
    if not rows:
        return 0
    updates = ", ".join(f"{c} = excluded.{c}" for c in _DEAL_COLS if c != "id")
    sql = (f"INSERT INTO deals ({', '.join(_DEAL_COLS)}) VALUES ({', '.join('?' * len(_DEAL_COLS))}) "
           f"ON CONFLICT(id) DO UPDATE SET {updates}")
    for batch in _chunks(rows, 50):
        _pipeline(client, [{"sql": sql, "args": [arg(r.get(c)) for c in _DEAL_COLS]} for r in batch])
        db.execute_many("UPDATE deals SET turso_dirty = 0 WHERE id = ? AND turso_dirty = ?",
                        [(r["id"], r["turso_dirty"]) for r in batch])
    return len(rows)


def _push_alerts(client: httpx.Client) -> int:
    rows = db.query(f"SELECT id, {', '.join(_ALERT_COLS)} FROM price_alerts WHERE turso_dirty = 1 LIMIT ?",
                    (BATCH_SIZE,))
    if not rows:
        return 0
    updates = ", ".join(f"{c} = excluded.{c}" for c in _ALERT_COLS[2:])
    sql = (f"INSERT INTO price_alerts ({', '.join(_ALERT_COLS)}) VALUES ({', '.join('?' * len(_ALERT_COLS))}) "
           f"ON CONFLICT(device_id, created_at) DO UPDATE SET {updates}")
    for batch in _chunks(rows, 50):
        _pipeline(client, [{"sql": sql, "args": [arg(r[c]) for c in _ALERT_COLS]} for r in batch])
        ids = [r["id"] for r in batch]
        db.execute(f"UPDATE price_alerts SET turso_dirty = 0 WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(rows)


def _push_devices(client: httpx.Client) -> int:
    """Upsert changed devices, each with its follows folded in as JSON."""
    rows = db.query(
        "SELECT device_id, platform, push_token, digest, digest_hour, created_at, last_seen_at, turso_dirty "
        "FROM devices WHERE turso_dirty > 0 LIMIT ?", (BATCH_SIZE,))
    if not rows:
        return 0
    updates = ", ".join(f"{c} = excluded.{c}" for c in _DEVICE_COLS[1:])
    sql = (f"INSERT INTO devices ({', '.join(_DEVICE_COLS)}) VALUES ({', '.join('?' * len(_DEVICE_COLS))}) "
           f"ON CONFLICT(device_id) DO UPDATE SET {updates}")
    for batch in _chunks(rows, 50):
        statements = []
        for r in batch:
            follows = [dict(f) for f in db.query(
                f"SELECT {', '.join(_FOLLOW_COLS)} FROM device_follows WHERE device_id = ?", (r["device_id"],))]
            values = dict(r)
            values["follows"] = json.dumps(follows)
            statements.append({"sql": sql, "args": [arg(values.get(c)) for c in _DEVICE_COLS]})
        _pipeline(client, statements)
        db.execute_many("UPDATE devices SET turso_dirty = 0 WHERE device_id = ? AND turso_dirty = ?",
                        [(r["device_id"], r["turso_dirty"]) for r in batch])
    return len(rows)


def _backup_round() -> None:
    # Each sub-step is independent — one failing must not skip the others;
    # whatever didn't go up is still flagged and goes next round.
    with httpx.Client() as client:
        _ensure_schema(client)
        if not _price_migration_done and _split():
            try:
                moved = _migrate_price_points(client)
                if moved:
                    log.info("Turso price_points migration: %d rows moved this round", moved)
            except Exception as exc:  # noqa: BLE001
                log.warning("Turso price_points migration failed: %s", exc)
        counts = {}
        for name, step in (("devices", _push_devices), ("outbox", _push_outbox), ("deals", _push_deals),
                           ("prices", _push_price_points), ("alerts", _push_alerts)):
            counts[name] = 0
            try:
                # Keep going while there's a backlog (e.g. the first-boot seed
                # from Sheets), up to MAX_BATCHES_PER_ROUND batches per round.
                for _ in range(MAX_BATCHES_PER_ROUND):
                    n = step(client)
                    counts[name] += n
                    if n < BATCH_SIZE:
                        break
            except Exception as exc:  # noqa: BLE001
                log.warning("Turso %s upload failed: %s", name, exc)
        if any(counts.values()):
            log.info("Turso upload: %s", counts)


def _run_loop() -> None:
    log.info("Turso upload thread started (every %ds)", BACKUP_INTERVAL_SECONDS)
    while not _stop.is_set():
        if _stop.wait(BACKUP_INTERVAL_SECONDS):
            break
        try:
            _backup_round()
        except Exception as exc:  # noqa: BLE001 - a bad round must never kill this thread
            log.warning("Turso upload round failed: %s", exc)


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


ALERT_RESTORE_DAYS = 30
DEVICE_RESTORE_DAYS = 120   # an install not opened in 4 months is almost certainly gone
RESTORE_PAGE = 1000


def _restore_deals(client: httpx.Client, since: float) -> int:
    """Copy the deals the local cache should hold (seen in the retention
    window, or still live) back down."""
    now = time.time()
    seen_ids: set = set()
    count = 0
    # Keyset-paged on the indexed column itself, so each page is a pure
    # index range scan (paging by id would let SQLite walk the whole table).
    for col, start in (("last_seen_at", since), ("expires_at", now)):
        cursor = start
        while True:
            page = rows_to_dicts(_pipeline(client, [{
                "sql": f"SELECT {', '.join(_DEAL_COLS)} FROM deals WHERE {col} >= ? ORDER BY {col} LIMIT ?",
                "args": [arg(float(cursor)), arg(RESTORE_PAGE)],
            }])[0])
            for row in page:
                if row["id"] in seen_ids:
                    continue
                # Past the window, only still-live deals belong in the cache
                # (purge_local_cache drops expired/dead ones the same way).
                if col == "expires_at" and row.get("status") != "live":
                    continue
                seen_ids.add(row["id"])
                row["dirty"] = 0
                row["turso_dirty"] = 0
                db.upsert("deals", row, conflict="id")
                count += 1
            if len(page) < RESTORE_PAGE or page[-1][col] is None or page[-1][col] == cursor:
                break
            cursor = page[-1][col]
    return count


def restore(cache_days: float) -> Optional[Dict[str, Any]]:
    """Refill the local cache from Turso after a cold start.

    Blocking — call via run_in_executor. Brings back the last `cache_days` of
    deals (plus anything still live) and recent price alerts. Price history
    is not copied: it's read from Turso (either database) on demand, in
    price_store.py. Returns counts plus whether Turso's deals/price tables
    are still empty (the caller then seeds them from Sheets), or None if
    Turso is unreachable.
    """
    if not settings.turso_configured:
        return None
    try:
        with httpx.Client() as client:
            _ensure_schema(client)
            results = _pipeline(client, [
                {"sql": "SELECT EXISTS (SELECT 1 FROM deals) AS e"},
                {"sql": f"SELECT {', '.join(_ALERT_COLS)} FROM price_alerts "
                        "WHERE triggered_at IS NULL OR triggered_at > ?",
                 "args": [arg(time.time() - ALERT_RESTORE_DAYS * 86400)]},
                {"sql": f"SELECT {', '.join(_DEVICE_COLS)} FROM devices WHERE last_seen_at > ?",
                 "args": [arg(time.time() - DEVICE_RESTORE_DAYS * 86400)]},
            ])
            prices_exists = _pipeline(client, [
                {"sql": "SELECT EXISTS (SELECT 1 FROM price_points) AS e"},
            ], target=target_prices())
            deals_empty = not rows_to_dicts(results[0])[0]["e"]
            prices_empty = not rows_to_dicts(prices_exists[0])[0]["e"]
            alerts = rows_to_dicts(results[1])
            saved_devices = rows_to_dicts(results[2])
            deals = 0 if deals_empty else _restore_deals(client, time.time() - cache_days * 86400)
    except Exception as exc:  # noqa: BLE001 - restore must never crash boot
        log.warning("Turso restore failed: %s", exc)
        return None

    restored = 0
    for a in alerts:
        if db.query_one("SELECT 1 FROM price_alerts WHERE device_id = ? AND created_at = ?",
                        (a["device_id"], a["created_at"])):
            continue
        db.execute(
            f"INSERT INTO price_alerts ({', '.join(_ALERT_COLS)}, turso_dirty) "
            f"VALUES ({', '.join('?' * len(_ALERT_COLS))}, 0)",
            [a[c] for c in _ALERT_COLS],
        )
        restored += 1
    devices_restored = _restore_devices(saved_devices)
    log.info("Restored from Turso: %d deals, %d price alerts, %d devices", deals, restored, devices_restored)
    return {"deals": deals, "alerts": restored, "devices": devices_restored,
            "deals_empty": deals_empty, "prices_empty": prices_empty}


def _restore_devices(saved: List[Dict[str, Any]]) -> int:
    """Bring devices (push tokens + follows) back after a cold start. A device
    that already re-registered locally since boot keeps its fresher row."""
    count = 0
    for d in saved:
        if db.query_one("SELECT 1 FROM devices WHERE device_id = ?", (d["device_id"],)):
            continue
        db.execute(
            "INSERT INTO devices (device_id, platform, push_token, digest, digest_hour, created_at, last_seen_at, "
            "turso_dirty) VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
            (d["device_id"], d.get("platform") or "", d.get("push_token") or "", int(d.get("digest") or 0),
             int(d.get("digest_hour") if d.get("digest_hour") is not None else 19),
             d.get("created_at"), d.get("last_seen_at")),
        )
        try:
            follows = json.loads(d.get("follows") or "[]")
        except (TypeError, ValueError):
            follows = []
        for f in follows if isinstance(follows, list) else []:
            db.execute(
                "INSERT INTO device_follows (device_id, kind, value, min_discount, created_at) VALUES (?, ?, ?, ?, ?)",
                (d["device_id"], f.get("kind"), f.get("value"), int(f.get("min_discount") or 0), f.get("created_at")),
            )
        count += 1
    return count
