"""Push-token registration, the notification feed, and alert fan-out.

Run:  python -m tests.test_notifications
Uses a throwaway SQLite file and a stubbed Expo sender — no network.
"""
from __future__ import annotations

import asyncio
import json
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
from app.services import devices, fcm, hot_push, ingest, parser, push, store, telegram  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []

# Shaped like real FCM registration tokens.
TOKEN_A = "fcmA1:APA91b" + "a" * 140
TOKEN_B = "fcmB2:APA91b" + "b" * 140
EXPO_TOKEN = "ExponentPushToken[aaaaaaaaaaaaaaaaaaaaaa]"

push_calls = []  # one [message] list per FCM send, normalised for the checks below


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


async def fake_fcm_post(client, url, access, message):
    m = message["message"]
    d = m["data"]
    push_calls.append([{"to": m["token"], "title": d["title"], "channelId": d["channelId"],
                        "priority": m["android"]["priority"].lower(), "data": json.loads(d["body"]),
                        "raw": m}])
    if m["token"] == TOKEN_B:
        return 404, {"error": {"status": "NOT_FOUND", "details": [{"errorCode": "UNREGISTERED"}]}}
    return 200, {"name": "projects/p/messages/1"}


async def fake_access_token(account):
    return "test-access-token"


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
    fcm._post = fake_fcm_post
    fcm._access_token = fake_access_token
    real_account = fcm._account
    fcm._account = lambda: {"client_email": "x@p.iam.gserviceaccount.com", "private_key": "k", "project_id": "p"}
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
    raw = msg.get("raw", {})
    check("data-only, in the shape the app's notification library reads",
          "notification" not in raw
          and set(raw.get("data", {})) >= {"title", "message", "body", "channelId"}, str(raw))

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
    # FCM v1 is one request per device, so both of alice's phones = 2 sends.
    check("push sent to each of the user's phones",
          sorted(m["to"] for b in push_calls for m in b) == sorted([TOKEN_A, TOKEN_B]), str(len(push_calls)))
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
    token_smart = "fcmS3:APA91b" + "s" * 140
    token_old = "fcmO4:APA91b" + "o" * 140
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

    print("\n=== 8. FCM ONLY (no Expo push) ===")
    r = anon.post("/api/devices/register", json={"device_id": "expo-device-1", "platform": "android",
                                                  "push_token": EXPO_TOKEN})
    row = db.query_one("SELECT push_token FROM devices WHERE device_id = 'expo-device-1'")
    check("an old build's Expo token: phone still registers, token ignored",
          r.status_code == 200 and row and not row["push_token"], f"{r.status_code} {dict(row) if row else None}")

    print("\n=== 9. ACTIVE PHONES (admin panel) ===")
    since = settings.active_devices_since
    db.execute("DELETE FROM devices")
    for dev, platform, seen, token in [
        ("before-cutoff-01", "android", since - 3600, TOKEN_A),   # an old test install
        ("after-cutoff-001", "android", since + 60, TOKEN_A),     # opened after the cut-off, has push
        ("after-cutoff-002", "android", since + 120, ""),         # opened, notifications not set up
        ("after-cutoff-003", "android", since + 180, EXPO_TOKEN), # old build's token only
        ("web-browser-0001", "", since + 60, ""),                 # website visitor, not an install
    ]:
        db.execute("INSERT INTO devices (device_id, platform, push_token, created_at, last_seen_at) "
                   "VALUES (?, ?, ?, ?, ?)", (dev, platform, token, seen, seen))
    counts = devices.active_counts()
    check("only installs opened since the cut-off count as active", counts["active_devices"] == 3, str(counts))
    check("only active phones with a Firebase token count as reachable", counts["reachable"] == 1, str(counts))
    push_calls.clear()
    report = asyncio.run(push.send_push_detailed([EXPO_TOKEN, TOKEN_A], "t", "b", "/"))
    check("Expo tokens are skipped; only FCM is sent to",
          report["tokens"] == 1 and [m["to"] for b in push_calls for m in b] == [TOKEN_A], f"{report} {push_calls}")
    fcm._account = real_account
    settings.fcm_service_account_json = ""
    report = asyncio.run(push.send_push_detailed([TOKEN_A], "t", "b", "/"))
    check("no key on the server -> reported as FcmNotConfigured, nothing raised",
          report["errors"] == {"FcmNotConfigured": 1}, str(report))

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
