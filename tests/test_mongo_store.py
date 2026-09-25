"""MongoDB backing store — users, channels, tracking links, watchlists, settings.

Run:  python -m tests.test_mongo_store
Uses mongomock (an in-memory pymongo-compatible driver), so every call in
mongo_store.py runs its real query/upsert/index code, not a hand-rolled fake.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  MONGODB_URI="mongodb://fake-host/", MONGODB_DB_NAME="dealradar_test",
                  TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mongomock  # noqa: E402

from app import db  # noqa: E402
from app.services import mongo_store as ms  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


import pymongo as _real_pymongo  # noqa: E402


class _FakePymongoModule:
    """Swaps mongomock's MongoClient in for the real driver's; UpdateOne (used
    to build bulk_write ops) is the same real pymongo class either way."""

    def MongoClient(self, uri, **kw):  # noqa: N802
        return mongomock.MongoClient()

    UpdateOne = _real_pymongo.UpdateOne


ms.pymongo = _FakePymongoModule()


def main() -> int:
    db.connect()
    print("\n=== CONNECT ===")
    check("enabled when MONGODB_URI is set", ms.is_enabled())
    check("connects (fake driver, always succeeds)", ms.connect())
    st = ms.status()
    check("status reports connected", st["configured"] and st["connected"], str(st))

    print("\n=== USERS ===")
    now = time.time()
    db.execute("INSERT INTO users (telegram_id, username, first_name, phone, session_enc, created_at, last_login_at) "
               "VALUES (111, 'alice', 'Alice', '+91999', 'x', ?, ?)", (now, now))
    db.execute("INSERT INTO users (telegram_id, username, first_name, phone, session_enc, created_at, last_login_at) "
               "VALUES (222, 'bob', 'Bob', '+91888', 'x', ?, ?)", (now, now))
    n = ms.sync_users()
    check("2 users synced", n == 2, str(n))
    docs = list(ms._db()["users"].find())
    check("upserted with the right shape", {d["telegram_id"] for d in docs} == {111, 222}, str(docs))
    n2 = ms.sync_users()
    check("re-sync is idempotent (still 2 docs, not 4)", ms._db()["users"].count_documents({}) == 2, str(n2))

    print("\n=== CHANNELS ===")
    db.execute("INSERT INTO channels (tg_id, username, title, participants, last_message_id, "
               "last_fetched_at, source_user_id, active) VALUES (501, 'loot1', 'Loot One', 1000, 42, ?, "
               "(SELECT id FROM users WHERE telegram_id = 111), 1)", (now,))
    check("channel synced", ms.sync_channels() == 1)
    ch = ms._db()["channels"].find_one({"tg_id": 501})
    check("carries the source user's telegram_id", ch["source_user_telegram_id"] == 111, str(ch))

    print("\n=== USER_CHANNELS + WATCHLISTS ===")
    db.execute("INSERT INTO user_channels (user_id, channel_id, enabled, added_at) "
               "SELECT (SELECT id FROM users WHERE telegram_id = 111), id, 1, ? FROM channels WHERE tg_id = 501",
               (now,))
    check("user_channels synced", ms.sync_user_channels() == 1)
    db.execute("INSERT INTO watchlists (user_id, query, filters, notify, created_at, last_notified_at) "
               "SELECT id, 'boat earbuds', ?, 1, ?, 0 FROM users WHERE telegram_id = 111",
               (json.dumps({"category": "Electronics", "min_discount": 40}), now))
    check("watchlist synced", ms.sync_watchlists() == 1)

    print("\n=== FULL RESTORE INTO A CLEAN LOCAL DB ===")
    db.execute("DELETE FROM watchlists")
    db.execute("DELETE FROM user_channels")
    db.execute("DELETE FROM channels")
    db.execute("DELETE FROM users")
    meta = ms.restore_all_meta()
    check("restored 2 users, 1 channel, 1 link, 1 watchlist",
          meta == {"users": 2, "channels": 1, "user_channels": 1, "watchlists": 1}, str(meta))
    restored_user = db.query_one("SELECT * FROM users WHERE telegram_id = 111")
    check("restored user has no session (must sign in again)",
          restored_user and restored_user["session_enc"] == "", str(restored_user))
    restored_channel = db.query_one("SELECT * FROM channels WHERE tg_id = 501")
    check("restored channel resolves its source user by telegram_id",
          restored_channel and db.query_one("SELECT telegram_id FROM users WHERE id = ?",
                                            (restored_channel["source_user_id"],))["telegram_id"] == 111)
    link = db.query_one("SELECT enabled FROM user_channels")
    check("restored tracking link enabled", link and link["enabled"] == 1)
    wl = db.query_one("SELECT query, filters FROM watchlists")
    check("restored watchlist keeps its filters", wl and json.loads(wl["filters"])["min_discount"] == 40, str(wl))

    print("\n=== RESTORE IS SAFE TO RE-RUN (no duplicates) ===")
    meta2 = ms.restore_all_meta()
    check("users not duplicated (ON CONFLICT DO NOTHING)",
          db.query_one("SELECT COUNT(*) AS c FROM users")["c"] == 2, str(meta2))
    check("watchlist not duplicated (dedup by user+query)",
          db.query_one("SELECT COUNT(*) AS c FROM watchlists")["c"] == 1)

    print("\n=== SETTINGS (priority rule, sale-events calendar) ===")
    check("save_setting works", ms.save_setting("priority_rule", json.dumps({"preset": "women"})))
    check("save_setting overwrites, not duplicates",
          ms.save_setting("priority_rule", json.dumps({"preset": "men"})) and
          ms._db()["settings"].count_documents({"key": "priority_rule"}) == 1)
    db.execute("DELETE FROM meta WHERE key = 'priority_rule'")
    restored_n = ms.restore_settings()
    check("settings restored into local meta", restored_n >= 1 and db.get_meta("priority_rule") is not None)
    check("restored value is the latest one saved", json.loads(db.get_meta("priority_rule"))["preset"] == "men")

    print("\n=== DISABLED WHEN NOT CONFIGURED ===")
    import app.config as config_mod
    old_uri = config_mod.settings.mongo_uri
    config_mod.settings.mongo_uri = ""
    try:
        check("is_enabled() reflects the config", ms.is_enabled() is False)
        check("sync functions no-op instead of raising", ms.sync_users() == 0)
    finally:
        config_mod.settings.mongo_uri = old_uri

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
