"""Push-token registration, the notification feed, and alert fan-out.

Run:  python -m tests.test_notifications
Uses a throwaway SQLite file and a stubbed Expo sender — no network.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")  # neutral ranking unless a test sets a rule
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test_notifications.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import devices, hot_push, ingest, parser, push, store, telegram  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []

TOKEN_A = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"
TOKEN_B = "ExpoPushToken[bbbbbbbbbbbbbbbbbbbbbb]"

push_calls = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


async def fake_post_batch(messages):
    push_calls.append(messages)
    tickets = []
    for m in messages:
        if m["to"] == TOKEN_B:
            tickets.append({"status": "error", "message": "gone", "details": {"error": "DeviceNotRegistered"}})
        else:
            tickets.append({"status": "ok", "id": "x"})
    return tickets


async def no_client(user_id):
    return None


def make_user(telegram_id: int, name: str) -> tuple[int, TestClient]:
    cur = db.execute(
        "INSERT INTO users (telegram_id, username, first_name, created_at, last_login_at) VALUES (?, ?, ?, ?, ?)",
        (telegram_id, name, name, time.time(), time.time()),
    )
    client = TestClient(app)
    client.cookies.set(settings.session_cookie, auth.create_session(telegram_id))
    return cur.lastrowid, client


def main() -> int:
    db.connect()
    push._post_batch = fake_post_batch
    telegram.get_client = no_client

    alice_id, alice = make_user(9001, "alice")
    bob_id, bob = make_user(9002, "bob")
    anon = TestClient(app)

    print("\n=== 1. AUTH ===")
    check("feed requires auth", anon.get("/api/notifications").status_code == 401)
    check("register requires auth",
          anon.post("/api/push/register", json={"token": TOKEN_A}).status_code == 401)

    print("\n=== 2. REGISTER / UNREGISTER ===")
    r = alice.post("/api/push/register", json={"token": "not-a-token", "platform": "android"})
    check("bad token -> 400", r.status_code == 400, str(r.status_code))
    r = alice.post("/api/push/register", json={"token": TOKEN_A, "platform": "android"})
    check("register ok", r.status_code == 200 and r.json() == {"status": "ok"}, r.text)
    alice.post("/api/push/register", json={"token": TOKEN_A, "platform": "android"})
    check("re-register is idempotent",
          db.query_one("SELECT COUNT(*) c FROM push_tokens WHERE token=?", (TOKEN_A,))["c"] == 1)
    bob.post("/api/push/register", json={"token": TOKEN_A, "platform": "android"})
    owner = db.query_one("SELECT user_id FROM push_tokens WHERE token=?", (TOKEN_A,))["user_id"]
    check("token re-assigned to new user", owner == bob_id, str(owner))
    alice.post("/api/push/register", json={"token": TOKEN_A, "platform": "android"})
    r = bob.post("/api/push/unregister", json={"token": TOKEN_A})
    check("unregister for non-owner leaves token",
          r.status_code == 200 and push.user_tokens(alice_id) == [TOKEN_A])
    alice.post("/api/push/register", json={"token": TOKEN_B, "platform": "android"})
    alice.post("/api/push/unregister", json={"token": TOKEN_B})
    check("unregister removes token", push.user_tokens(alice_id) == [TOKEN_A])

    print("\n=== 3. TEST ENDPOINT ===")
    deal = parser.parse_message(
        "boAt Rockerz 450 Bluetooth On Ear Headphones\n₹1,299 (MRP ₹2,990) — 56% OFF\n"
        "https://www.amazon.in/dp/B07PR1CL3S",
        channel_id=1001, channel_title="Loot", message_id=1,
        posted_at=time.time() - 60, ttl_hours=96,
    )
    deal["image_url"] = "https://img.example/boat.jpg"  # feeds show photo cards only
    store.save_deal(deal)
    push_calls.clear()
    r = alice.post("/api/notifications/test")
    body = r.json()
    check("test endpoint ok", r.status_code == 200 and body["status"] == "ok", r.text)
    check("test push sent to 1 token", body.get("push_sent") == 1, str(body.get("push_sent")))
    check("test notification links newest deal",
          body["notification"]["url"] == f"/?deal={deal['id']}", body["notification"]["url"])
    msg = push_calls[0][0] if push_calls else {}
    check("push payload shape",
          msg.get("channelId") == "deal-alerts" and msg.get("priority") == "high"
          and msg.get("data", {}).get("deal_id") == deal["id"], str(msg))

    print("\n=== 4. FEED ===")
    r = alice.get("/api/notifications")
    feed = r.json()
    check("feed returns notification + now",
          len(feed["notifications"]) == 1 and feed["now"] >= feed["notifications"][0]["created_at"])
    check("feed items have contract keys",
          set(feed["notifications"][0]) == {"id", "deal_id", "title", "body", "url", "created_at"})
    later = alice.get("/api/notifications", params={"since": feed["now"]}).json()
    check("since filters older rows", later["notifications"] == [], str(later))
    check("other users see nothing", bob.get("/api/notifications").json()["notifications"] == [])
    check("limit validated", alice.get("/api/notifications", params={"limit": 51}).status_code == 422)
    for i in range(3):
        push.create_notification(alice_id, f"n{i}", "b", "/", None)
        time.sleep(0.002)
    items = alice.get("/api/notifications", params={"limit": 2}).json()["notifications"]
    check("newest first + limit", [n["title"] for n in items] == ["n2", "n1"], str(items))

    print("\n=== 5. WATCHLIST ALERTS WITHOUT TELEGRAM ===")
    db.execute(
        "INSERT INTO watchlists (user_id, query, filters, notify, created_at) VALUES (?, ?, '{}', 1, ?)",
        (alice_id, "headphone", time.time()),
    )
    alice.post("/api/push/register", json={"token": TOKEN_B, "platform": "android"})
    before = db.query_one("SELECT COUNT(*) c FROM notifications WHERE user_id=?", (alice_id,))["c"]
    push_calls.clear()
    result = asyncio.run(ingest.run_watchlist_alerts())
    after = db.query_one("SELECT COUNT(*) c FROM notifications WHERE user_id=?", (alice_id,))["c"]
    check("alert created a notification", after == before + 1, f"{before} -> {after}")
    check("push sender called", len(push_calls) == 1, str(len(push_calls)))
    check("telegram unavailable -> 0 telegram alerts", result["alerts_sent"] == 0, str(result))
    newest = alice.get("/api/notifications", params={"limit": 1}).json()["notifications"][0]
    check("alert title/url",
          newest["title"].startswith('🔔 "headphone": ₹1,299') and newest["url"] == f"/?deal={deal['id']}",
          str(newest))
    check("DeviceNotRegistered token pruned", push.user_tokens(alice_id) == [TOKEN_A])
    push_calls.clear()
    asyncio.run(ingest.run_watchlist_alerts())
    again = db.query_one("SELECT COUNT(*) c FROM notifications WHERE user_id=?", (alice_id,))["c"]
    check("dedup: second run adds nothing", again == after and not push_calls)

    print("\n=== 6. PRUNE ===")
    db.execute("UPDATE notifications SET created_at = ? WHERE title = 'n0'", (time.time() - 31 * 86400,))
    check("old notifications pruned", push.prune_notifications() == 1)

    print("\n=== 7. SMART-SCHEDULE DEVICES ===")
    token_smart = "ExponentPushToken[smartsmartsmartsmart00]"
    token_old = "ExponentPushToken[oldoldoldoldoldold0000]"
    r = anon.post("/api/devices/register", json={"device_id": "smart-device-1", "platform": "android",
                                                  "push_token": token_smart, "smart_schedule": True})
    check("register accepts smart_schedule", r.status_code == 200, r.text)
    devices.register("old-device-1", "android", token_old)
    check("smart flag stored",
          db.query_one("SELECT smart_schedule s FROM devices WHERE device_id='smart-device-1'")["s"] == 1)

    async def notify_and_settle(device_id, kind):
        devices.notify(device_id, kind, "t", "b", dict(db.query_one("SELECT * FROM deals WHERE id=?", (deal["id"],))))
        await asyncio.sleep(0.05)

    push_calls.clear()
    asyncio.run(notify_and_settle("smart-device-1", "follow"))
    check("routine alert: no direct push to a smart device", not push_calls, str(push_calls))
    asyncio.run(notify_and_settle("smart-device-1", "price_drop"))
    check("price_drop still pushes instantly", len(push_calls) == 1, str(len(push_calls)))
    push_calls.clear()
    asyncio.run(notify_and_settle("old-device-1", "follow"))
    check("old app builds still get the direct push", len(push_calls) == 1, str(len(push_calls)))

    push_calls.clear()
    asyncio.run(devices.broadcast("hot", "b", dict(db.query_one("SELECT * FROM deals WHERE id=?", (deal["id"],))),
                                  kind="hot_deal"))
    sent_to = {m["to"] for batch in push_calls for m in batch}
    check("hot_deal broadcast skips smart devices", token_smart not in sent_to and token_old in sent_to, str(sent_to))

    push_calls.clear()
    result = asyncio.run(hot_push.send_best(force=True, reason="admin"))
    sent_to = {m["to"] for batch in push_calls for m in batch}
    check("admin 'send now' reaches smart devices instantly too",
          result.get("status") == "sent" and token_smart in sent_to and token_old in sent_to, f"{result} {sent_to}")

    items = anon.get("/api/devices/feed", params={"device_id": "smart-device-1"}).json()["items"]
    own = [i for i in items if i["kind"] == "follow"]
    check("feed carries deal fields for on-phone copy",
          own and own[0]["price"] == 1299 and own[0]["discount_pct"] == 56 and "category" in own[0], str(own[:1]))

    print("\n" + "=" * 52)
    if failures:
        print(f"{FAIL} {len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"    - {f}")
        return 1
    print(f"{PASS} All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
