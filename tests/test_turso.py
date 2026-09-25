"""Turso price history + price tracking, against a fake Turso backed by SQLite.

Run:  python -m tests.test_turso
The fake speaks Turso's HTTP pipeline protocol, so the real SQL (WITHOUT ROWID
tables, upserts, the one-time reset) runs exactly as it would on Turso.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://dealradar.example", ADMIN_TOKEN="test-admin-token",
                  TURSO_DATABASE_URL="libsql://fake-db.turso.io", TURSO_AUTH_TOKEN="fake-token")
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


# --- fake Turso ---------------------------------------------------------
turso = sqlite3.connect(":memory:", check_same_thread=False)
turso.row_factory = sqlite3.Row
state = {"down": False, "requests": 0}


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
    state["requests"] += 1
    if state["down"]:
        raise httpx.ConnectError("turso unreachable")
    assert request.headers["authorization"] == "Bearer fake-token"
    assert str(request.url) == "https://fake-db.turso.io/v2/pipeline"
    results = []
    for req in httpx.Response(200, content=request.content).json()["requests"]:
        if req["type"] == "close":
            results.append({"type": "ok", "response": {"type": "close"}})
            continue
        stmt = req["stmt"]
        try:
            cur = turso.execute(stmt["sql"], [_value(a) for a in stmt.get("args", [])])
            rows = cur.fetchall()
            turso.commit()
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


def tq(sql, args=()):
    return [dict(r) for r in turso.execute(sql, args).fetchall()]


def main() -> int:
    # An old v1 Turso: whole-app mirror with data that must be cleared.
    turso.execute("CREATE TABLE deals (id TEXT PRIMARY KEY, title TEXT)")
    turso.execute("INSERT INTO deals VALUES ('old', 'old deal')")
    turso.execute("CREATE TABLE price_history (id INTEGER PRIMARY KEY, product_key TEXT, price REAL)")
    turso.execute("INSERT INTO price_history VALUES (1, 'amazon:OLD', 10)")
    turso.commit()

    url = "https://www.amazon.in/dp/B07PR1CL3S"
    key = "amazon:B07PR1CL3S"
    with TestClient(app) as c:
        print("\n=== ONE-TIME RESET ===")
        tables = {r["name"] for r in tq("SELECT name FROM sqlite_master WHERE type = 'table'")}
        check("old v1 tables dropped", not {"deals", "price_history"} & tables, str(tables))
        check("new tables created", {"price_points", "products", "price_alerts", "dr_schema"} <= tables, str(tables))
        check("schema version recorded", tq("SELECT version FROM dr_schema") == [{"version": 2}])

        print("\n=== PRICE HISTORY ===")
        deal = post(f"boAt Rockerz 450 Headphones ₹1,299 (MRP ₹2,990) {url}", 1)
        time.sleep(1.1)  # seen_at is stored to the second in Turso
        post(f"boAt Rockerz 450 Headphones now ₹1,149 {url}", 2)
        pending = db.query_one("SELECT COUNT(*) AS c FROM price_history WHERE turso_synced = 0")["c"]
        check("new prices queued for Turso", pending == 2, str(pending))

        r = c.get(f"/api/deals/{deal['id']}/history").json()
        check("history shows not-yet-uploaded points", [p["price"] for p in r["points"]] == [1299, 1149], str(r))

        turso_backup._backup_round()
        pts = tq("SELECT product_key, price FROM price_points ORDER BY seen_at")
        check("points uploaded to Turso", pts == [{"product_key": key, "price": 1299.0}, {"product_key": key, "price": 1149.0}], str(pts))
        prod = tq("SELECT * FROM products")
        check("products row tracks min/max/last", len(prod) == 1 and prod[0]["min_price"] == 1149
              and prod[0]["max_price"] == 1299 and prod[0]["last_price"] == 1149, str(prod))
        check("products row carries title + url", prod and "Rockerz" in (prod[0]["title"] or "") and prod[0]["url"], str(prod))
        check("local marked uploaded",
              db.query_one("SELECT COUNT(*) AS c FROM price_history WHERE turso_synced = 0")["c"] == 0)

        # Prove reads come from Turso: remove the local copy entirely.
        db.execute("DELETE FROM price_history")
        price_store._cache.clear()
        r = c.get(f"/api/deals/{deal['id']}/history").json()
        check("history is served from Turso", [p["price"] for p in r["points"]] == [1299, 1149], str(r))
        check("stats from Turso history", r["stats"]["min"] == 1149 and r["stats"]["points"] == 2, str(r["stats"]))
        d = c.get(f"/api/deals/{deal['id']}").json()
        check("deal detail price_history from Turso", d["price_history"]["points"] == 2, str(d["price_history"]))
        s = c.get("/api/deals/sparklines", params={"ids": deal["id"]}).json()
        check("sparklines from Turso", s["sparklines"].get(deal["id"]) == [1299, 1149], str(s))
        lk = c.get("/api/lookup", params={"url": url}).json()
        check("lookup includes tracked product", (lk.get("tracked") or {}).get("min_price") == 1149, str(lk.get("tracked")))
        check("lookup history from Turso", len(lk["history"]) == 2)

        before = state["requests"]
        c.get(f"/api/deals/{deal['id']}/history")
        check("repeat reads hit the cache", state["requests"] == before)

        print("\n=== IDEMPOTENT RE-UPLOAD ===")
        db.execute("INSERT INTO price_history (product_key, price, store, seen_at, turso_synced) "
                   "SELECT product_key, price, 'amazon', seen_at, 0 FROM (SELECT ? AS product_key, 1299.0 AS price, "
                   "? AS seen_at)", (key, float(pts and tq("SELECT MIN(seen_at) AS s FROM price_points")[0]["s"])))
        turso_backup._backup_round()
        check("duplicate point ignored", len(tq("SELECT * FROM price_points")) == 2)
        check("products unchanged by retry", tq("SELECT last_price FROM products")[0]["last_price"] == 1149)

        print("\n=== PRICE ALERTS (TRACKING) ===")
        dev = "app_1234567890abcdef"
        a = c.post("/api/price-alerts", json={"device_id": dev, "deal_id": deal["id"], "target_price": 900}).json()["alert"]
        c.post("/api/price-alerts", json={"device_id": dev, "deal_id": deal["id"], "target_price": 800})
        turso_backup._backup_round()
        check("alerts uploaded", len(tq("SELECT * FROM price_alerts")) == 2)
        c.delete(f"/api/price-alerts/{a['id']}", params={"device_id": dev})
        turso_backup._backup_round()
        remaining = tq("SELECT target_price FROM price_alerts")
        check("deleted alert removed from Turso", remaining == [{"target_price": 800.0}], str(remaining))
        db.execute("DELETE FROM price_alerts")  # a Render restart wipes the disk
        check("alerts restored after restart", turso_backup.restore() == 1)
        alerts = c.get("/api/price-alerts", params={"device_id": dev}).json()["alerts"]
        check("restored alert visible", [x["target_price"] for x in alerts] == [800.0], str(alerts))

        print("\n=== RENAME / FALLBACK / NO RE-RESET ===")
        db.turso_enqueue("UPDATE OR IGNORE price_points SET product_key = ? WHERE product_key = ?", ("amazon:NEW", key))
        turso_backup._backup_round()
        check("outbox replays renames", len(tq("SELECT * FROM price_points WHERE product_key = 'amazon:NEW'")) == 2)

        state["down"] = True
        price_store._cache.clear()
        db.execute("INSERT INTO price_history (product_key, price, store, seen_at, turso_synced) VALUES (?, 999, 'amazon', ?, 1)",
                   (key, time.time()))
        r = c.get(f"/api/deals/{deal['id']}/history")
        check("Turso down: history falls back to local", r.status_code == 200 and [p["price"] for p in r.json()["points"]] == [1299, 999], r.text)
        state["down"] = False

        turso_backup._schema_ready = False  # simulate the next boot
        turso_backup._backup_round()
        check("current schema is never wiped again", len(tq("SELECT * FROM price_points")) == 2)

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
