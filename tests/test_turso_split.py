"""TURSO_DB_02: moving price_points into its own Turso database.

Run:  python -m tests.test_turso_split
Two fake Turso backends (separate in-memory SQLite connections), routed by
request hostname, so the test proves statements actually land on the
database they're supposed to — not just that no exception was raised.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(
    SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
    PUBLIC_URL="https://dealradar.example", ADMIN_TOKEN="test-admin-token",
    TURSO_DATABASE_URL="libsql://main-db.turso.io", TURSO_AUTH_TOKEN="main-token",
    TURSO_DB_02="libsql://prices-db.turso.io", TURSO_DB_02_AUTH_TOKEN="prices-token",
)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import parser, price_store, store, turso_backup  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# --- two fake Turso databases, routed by hostname -----------------------
main_db = sqlite3.connect(":memory:", check_same_thread=False)
main_db.row_factory = sqlite3.Row
prices_db = sqlite3.connect(":memory:", check_same_thread=False)
prices_db.row_factory = sqlite3.Row
hits = {"main-db.turso.io": 0, "prices-db.turso.io": 0}


def _value(a):
    t, v = a.get("type"), a.get("value")
    if t == "null":
        return None
    if t == "integer":
        return int(v)
    if t == "float":
        return float(v)
    return v


def _cell(v):
    if v is None:
        return {"type": "null"}
    if isinstance(v, int):
        return {"type": "integer", "value": str(v)}
    if isinstance(v, float):
        return {"type": "float", "value": v}
    return {"type": "text", "value": str(v)}


def handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    assert host in hits, f"unexpected Turso host: {host}"
    hits[host] += 1
    conn = main_db if host == "main-db.turso.io" else prices_db
    expected_token = "main-token" if host == "main-db.turso.io" else "prices-token"
    assert request.headers["authorization"] == f"Bearer {expected_token}", request.headers["authorization"]
    results = []
    for req in httpx.Response(200, content=request.content).json()["requests"]:
        if req["type"] == "close":
            results.append({"type": "ok", "response": {"type": "close"}})
            continue
        stmt = req["stmt"]
        try:
            cur = conn.execute(stmt["sql"], [_value(a) for a in stmt.get("args", [])])
            rows = cur.fetchall()
            conn.commit()
        except sqlite3.Error as exc:
            results.append({"type": "error", "error": {"message": str(exc)}})
            continue
        cols = [{"name": d[0]} for d in (cur.description or [])]
        results.append({"type": "ok", "response": {"type": "execute", "result": {
            "cols": cols, "rows": [[_cell(v) for v in r] for r in rows]}}})
    return httpx.Response(200, json={"results": results})


class _FakeHttpx:
    def __getattr__(self, name):
        return getattr(httpx, name)

    def Client(self, **kw):  # noqa: N802
        return httpx.Client(transport=httpx.MockTransport(handler), **kw)

    def AsyncClient(self, **kw):  # noqa: N802
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw)


turso_backup.httpx = price_store.httpx = _FakeHttpx()


def post(text: str, channel: int):
    deal = parser.parse_message(text, channel_id=channel, channel_title="c", message_id=channel, posted_at=time.time())
    store.save_deal(deal)
    return deal


def q(conn, sql, args=()):
    return [dict(r) for r in conn.execute(sql, args).fetchall()]


def main() -> int:
    # Simulate an existing deployment: main db already has deals/products/
    # price_alerts/dr_schema(v3) AND price_points with data in it — the state
    # right before someone sets TURSO_DB_02 on a running app.
    url = "https://www.amazon.in/dp/B07PR1CL3S"
    key = "amazon:B07PR1CL3S"
    main_db.execute("CREATE TABLE dr_schema (version INTEGER NOT NULL)")
    main_db.execute("INSERT INTO dr_schema VALUES (3)")
    main_db.execute("CREATE TABLE price_points (product_key TEXT, seen_at INTEGER, price REAL, "
                    "PRIMARY KEY (product_key, seen_at))")
    for i in range(3):
        main_db.execute("INSERT INTO price_points VALUES (?, ?, ?)", (key, 1000 + i, 1000.0 + i))
    main_db.commit()

    with TestClient(app) as c:
        print("\n=== SPLIT KICKS IN: price_points MIGRATES OUT OF MAIN ===")
        turso_backup._backup_round()
        check("schema bumped to v4 on main", q(main_db, "SELECT version FROM dr_schema") == [{"version": 4}])
        check("price_points table created on the prices db", bool(
            q(prices_db, "SELECT name FROM sqlite_master WHERE name = 'price_points'")))
        moved = q(prices_db, "SELECT product_key, seen_at, price FROM price_points ORDER BY seen_at")
        check("existing rows migrated to the prices db", len(moved) == 3, str(moved))
        check("price_points dropped from main once migrated",
              not q(main_db, "SELECT name FROM sqlite_master WHERE name = 'price_points'"))
        check("migration recorded locally (won't re-run)", db.get_meta("turso_price_migrated") == "1")
        check("both hosts were actually hit", hits["main-db.turso.io"] > 0 and hits["prices-db.turso.io"] > 0, str(hits))

        print("\n=== NEW PRICE DATA GOES STRAIGHT TO THE PRICES DB ===")
        deal = post(f"boAt Rockerz 450 Headphones ₹1,299 (MRP ₹2,990) {url}", 1)
        time.sleep(1.1)
        post(f"boAt Rockerz 450 Headphones now ₹1,149 {url}", 2)
        turso_backup._backup_round()
        pts = q(prices_db, "SELECT price FROM price_points WHERE product_key = ? ORDER BY seen_at", (key,))
        check("new prices land on the prices db", [p["price"] for p in pts][-2:] == [1299.0, 1149.0], str(pts))
        check("no new price_points table recreated on main",
              not q(main_db, "SELECT name FROM sqlite_master WHERE name = 'price_points'"))
        prod = q(main_db, "SELECT product_key, last_price FROM products WHERE product_key = ?", (key,))
        check("products summary stays on the main db", prod and prod[0]["last_price"] == 1149.0, str(prod))

        print("\n=== READS COME FROM THE PRICES DB ===")
        db.execute("DELETE FROM price_history")
        price_store._cache.clear()
        r = c.get(f"/api/deals/{deal['id']}/history").json()
        # Includes the 3 migrated points (1000/1001/1002) plus the 2 new ones —
        # proof the migrated history and the freshly-uploaded history are the
        # same rows in the same prices database, read together.
        check("history read from the prices db", [p["price"] for p in r["points"]][-2:] == [1299.0, 1149.0]
              and len(r["points"]) == 5, str(r))
        d = c.get(f"/api/deals/{deal['id']}").json()
        check("deal detail stats from the prices db", d["price_history"]["points"] == 5, str(d["price_history"]))
        lk = c.get("/api/lookup", params={"url": url}).json()
        check("lookup tracked product from the main db", (lk.get("tracked") or {}).get("last_price") == 1149.0)
        check("lookup history from the prices db", [p["price"] for p in lk["history"]][-2:] == [1299.0, 1149.0])

        print("\n=== RENAME/DELETE OUTBOX ROUTES TO THE RIGHT DATABASE ===")
        db.turso_enqueue("DELETE FROM price_points WHERE product_key = ?", ("amazon:GHOST",), target="prices")
        db.turso_enqueue("UPDATE products SET title = ? WHERE product_key = ?", ("Renamed", key))
        turso_backup._backup_round()
        check("prices-targeted outbox row hit the prices db", hits["prices-db.turso.io"] > 0)
        check("main-targeted outbox row updated the main db",
              q(main_db, "SELECT title FROM products WHERE product_key = ?", (key,)) == [{"title": "Renamed"}])

        print("\n=== RESTORE: prices_empty CHECKS THE PRICES DB ===")
        res = turso_backup.restore(15)
        check("restore reports the prices db as non-empty", res and res["prices_empty"] is False, str(res))

        print("\n=== NO TURSO_DB_02: FALLS BACK TO ONE DATABASE (no crash) ===")
        import app.config as config_mod
        old2, old2t = config_mod.settings.turso2_url, config_mod.settings.turso2_auth_token
        config_mod.settings.turso2_url = config_mod.settings.turso2_auth_token = ""
        try:
            check("target_prices falls back to main when TURSO_DB_02 unset",
                  turso_backup.target_prices() == turso_backup.target_main())
        finally:
            config_mod.settings.turso2_url, config_mod.settings.turso2_auth_token = old2, old2t

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
