"""Visitor devices: follows, digest, price-drop feed, lookup, similar, verdict.

Run:  python -m tests.test_devices
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime

os.environ.setdefault("PRIORITY_AUDIENCE", "off")  # neutral ranking unless a test sets a rule
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://dealradar.example", ADMIN_TOKEN="test-admin-token")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import devices, fcm, hot_push, ingest, parser, push, store, turso_backup  # noqa: E402

ADMIN = {"X-Admin-Token": "test-admin-token"}

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def post(text: str, channel: int):
    deal = parser.parse_message(text, channel_id=channel, channel_title="c", message_id=channel, posted_at=time.time())
    deal["image_url"] = store.telegram_image_url(deal["id"], channel, channel)
    store.save_deal(deal)
    return deal


def main() -> int:
    dev = "app_1234567890abcdef"
    with TestClient(app) as c:
        print("\n=== DEVICES ===")
        r = c.post("/api/devices/register", json={"device_id": dev, "platform": "android", "digest": True, "digest_hour": 19})
        check("register", r.status_code == 200 and r.json()["device"]["digest"] is True)
        check("bad device id rejected", c.get("/api/devices/settings", params={"device_id": "x"}).status_code == 400)
        r = c.post("/api/devices/register", json={"device_id": dev, "push_token": "nope"})
        check("bad push token ignored (device still registers, token not stored)",
              r.status_code == 200 and not db.query_one("SELECT push_token FROM devices WHERE device_id = ?", (dev,))["push_token"])
        r = c.post("/api/devices/follows", json={"device_id": dev, "kind": "brand", "value": "boAt", "min_discount": 30})
        check("follow brand", r.status_code == 200)
        check("follow kind validated", c.post("/api/devices/follows", json={"device_id": dev, "kind": "x", "value": "y"}).status_code == 400)
        r = c.post("/api/devices/follows", json={"device_id": dev, "kind": "store", "value": "amazon", "min_discount": None})
        check("null min_discount accepted as 0 (not 422)", r.status_code == 200 and r.json()["follow"]["min_discount"] == 0, str(r.json()))
        r = c.post("/api/devices/follows", json={"device_id": dev, "kind": "store", "value": "flipkart"})
        check("omitted min_discount defaults to 0", r.status_code == 200 and r.json()["follow"]["min_discount"] == 0)

        deal = post("boAt Rockerz 450 Headphones ₹1,299 (MRP ₹2,990) https://www.amazon.in/dp/B07PR1CL3S", 1)
        items = c.get("/api/devices/feed", params={"device_id": dev}).json()["items"]
        check("new followed-brand deal notifies", items and items[0]["kind"] == "follow", str(items))
        check("notification carries absolute image", items and items[0]["image_url"].startswith("https://dealradar.example/"))
        post("boAt Airdopes 141 TWS ₹899 (MRP ₹4,490) https://www.amazon.in/dp/B09N3ZNHTY", 2)
        check("follow pushes are rate-limited", len(c.get("/api/devices/feed", params={"device_id": dev}).json()["items"]) == 1)

        db.execute("UPDATE devices SET digest_hour = ?", (datetime.now(devices.IST).hour,))
        check("digest sends at the chosen hour", devices.digest_tick() == 1)
        check("digest only once a day", devices.digest_tick() == 0)

        c.post("/api/price-alerts", json={"device_id": dev, "deal_id": deal["id"], "target_price": 1200})
        post("boAt Rockerz 450 Headphones now ₹1,149 https://www.amazon.in/dp/B07PR1CL3S", 3)
        kinds = [i["kind"] for i in c.get("/api/devices/feed", params={"device_id": dev}).json()["items"]]
        check("price drop lands in the device feed", "price_drop" in kinds, str(kinds))

        print("\n=== LOOKUP / SIMILAR / VERDICT ===")
        r = c.get("/api/lookup", params={"url": "see https://www.amazon.in/dp/B07PR1CL3S?tag=aff-21"}).json()
        check("lookup finds product from a messy link", r["product_key"] == "amazon:B07PR1CL3S" and len(r["deals"]) == 1)
        check("lookup gives BuyHatke link", r["price_history_url"].startswith("https://buyhatke.com/"))
        check("lookup rejects non-links", c.get("/api/lookup", params={"url": "not a link at all"}).status_code == 400)
        check("verdict: new low is great", (r["verdict"] or {}).get("level") == "great", str(r["verdict"]))
        check("similar endpoint", c.get(f"/api/deals/{deal['id']}/similar").status_code == 200)
        check("ending-soon sort", c.get("/api/deals", params={"sort": "ending"}).status_code == 200)

        print("\n=== ADMIN: PAUSE SYNCING ===")
        check("pause requires admin token", c.post("/api/admin/reader/pause", json={"paused": True}).status_code == 403)
        check("not paused by default", ingest.sync_paused() is False)
        r = c.post("/api/admin/reader/pause", json={"paused": True}, headers=ADMIN)
        check("pause takes effect", r.status_code == 200 and r.json()["sync_paused"] is True)
        check("ingest.sync_paused() reflects it", ingest.sync_paused() is True)
        r = c.get("/api/admin/reader", headers=ADMIN)
        check("status reports sync_paused", r.json()["sync_paused"] is True)
        r = c.post("/api/admin/reader/pause", json={"paused": False}, headers=ADMIN)
        check("resume works", r.status_code == 200 and r.json()["sync_paused"] is False and ingest.sync_paused() is False)

        print("\n=== ADMIN: POLL INTERVAL ===")
        r = c.get("/api/admin/reader/poll-interval", headers=ADMIN).json()
        check("default reported before any override", r["seconds"] == r["default_seconds"] and r["is_override"] is False, str(r))
        check("get requires admin token", c.get("/api/admin/reader/poll-interval").status_code == 403)
        check("set requires admin token", c.post("/api/admin/reader/poll-interval", json={"seconds": 900}).status_code == 403)
        check("below the floor is rejected",
              c.post("/api/admin/reader/poll-interval", json={"seconds": 60}, headers=ADMIN).status_code == 400)
        check("above the ceiling is rejected",
              c.post("/api/admin/reader/poll-interval", json={"seconds": 999999}, headers=ADMIN).status_code == 400)
        r = c.post("/api/admin/reader/poll-interval", json={"seconds": 900}, headers=ADMIN)
        check("set to 15 min", r.status_code == 200 and r.json()["seconds"] == 900, str(r.json()))
        check("takes effect immediately", ingest.poll_interval_seconds() == 900)
        r = c.get("/api/admin/reader/poll-interval", headers=ADMIN).json()
        check("now reports itself as an override", r["seconds"] == 900 and r["is_override"] is True, str(r))
        check("/api/ping reflects the override", c.get("/api/ping").json()["poll_interval_seconds"] == 900)
        r = c.post("/api/admin/reader/poll-interval/reset", headers=ADMIN)
        check("reset back to default", r.status_code == 200 and r.json()["seconds"] == r.json()["seconds"] and
              ingest.poll_interval_seconds() == ingest.settings.poll_interval_seconds)

        print("\n=== ADMIN: BROADCAST NOTIFICATION ===")
        check("broadcast requires admin token", c.post("/api/admin/reader/broadcast", json={"title": "hi", "body": "hi"}).status_code == 403)
        check("broadcast needs title+body", c.post("/api/admin/reader/broadcast", json={"title": "", "body": ""}, headers=ADMIN).status_code == 400)
        before = len(c.get("/api/devices/feed", params={"device_id": dev}).json()["items"])
        r = c.post("/api/admin/reader/broadcast", json={"title": "Sale is live", "body": "Everything 50% off today"}, headers=ADMIN)
        check("broadcast with no deal", r.status_code == 200 and r.json()["devices"] >= 1, str(r.json()))
        after = c.get("/api/devices/feed", params={"device_id": dev}).json()["items"]
        check("broadcast reaches every registered device's feed", len(after) == before + 1)
        check("broadcast without a deal has no image/deal_id", after[0]["deal_id"] in (None, "") and after[0]["title"] == "Sale is live")
        r = c.post("/api/admin/reader/broadcast", json={"title": "Check this out", "body": "Great price", "deal_id": deal["id"]}, headers=ADMIN)
        check("broadcast attaching a real deal", r.status_code == 200)
        after2 = c.get("/api/devices/feed", params={"device_id": dev}).json()["items"]
        check("attached-deal broadcast carries the deal id and image", after2[0]["deal_id"] == deal["id"] and after2[0]["image_url"])
        check("broadcast rejects an unknown deal id",
              c.post("/api/admin/reader/broadcast", json={"title": "x", "body": "y", "deal_id": "does-not-exist"}, headers=ADMIN).status_code == 404)

        print("\n=== BROADCAST DELIVERY REPORT ===")
        token_ok, token_bad = "fcmOK:APA91b" + "a" * 140, "fcmBD:APA91b" + "b" * 140
        c.post("/api/devices/register", json={"device_id": "app_tokenholder000001", "platform": "android", "push_token": token_ok})
        c.post("/api/devices/register", json={"device_id": "app_tokenholder000002", "platform": "android", "push_token": token_bad})

        async def fake_fcm_post(client, url, access, message):
            if message["message"]["token"] == token_ok:
                return 200, {"name": "projects/p/messages/1"}
            return 403, {"error": {"status": "PERMISSION_DENIED", "details": [{"errorCode": "SENDER_ID_MISMATCH"}]}}

        async def fake_access_token(account):
            return "test-access-token"

        real_post, real_token, real_account = fcm._post, fcm._access_token, fcm._account
        fcm._post, fcm._access_token = fake_fcm_post, fake_access_token
        fcm._account = lambda: {"client_email": "x@p.iam.gserviceaccount.com", "private_key": "k", "project_id": "p"}
        try:
            r = c.post("/api/admin/reader/broadcast", json={"title": "Test", "body": "Hello"}, headers=ADMIN).json()
            check("report counts tokens", r["tokens"] == 2, str(r))
            check("report counts FCM acceptances", r["accepted"] == 1, str(r))
            check("report names the failure reason", r["errors"].get("SENDER_ID_MISMATCH") == 1, str(r))

            print("\n=== BROADCASTS REACH DEVICES REGISTERED LATER ===")
            late = "app_registeredafter0001"
            c.post("/api/devices/register", json={"device_id": late})
            items = c.get("/api/devices/feed", params={"device_id": late}).json()["items"]
            check("a device registered after the send still gets it", any(i["title"] == "Test" for i in items), str(items))
            check("broadcast ids can't collide with device item ids", all(
                str(i["id"]).startswith("b") for i in items if i["kind"] == "broadcast"))

            print("\n=== HOT-DEAL PUSHES ===")
            db.execute("DELETE FROM push_log")
            now = time.time()
            women = post("Libas Women Printed Kurta Set ₹649 (MRP ₹2,199) https://www.myntra.com/kurtas/libas/x/11111111/buy", 11)
            gadget = post("Samsung 25W USB-C Charger ₹899 (MRP ₹1,999) https://www.amazon.in/dp/B0CHARGE01", 12)
            db.execute("UPDATE deals SET score = 70, last_seen_at = ? WHERE id = ?", (now, women["id"]))
            db.execute("UPDATE deals SET score = 85, last_seen_at = ? WHERE id = ?", (now, gadget["id"]))
            ranked = hot_push.candidates()
            check("women's item outranks a higher-scored gadget", ranked and ranked[0][0]["id"] == women["id"],
                  str([(d["title"][:20], round(r, 1)) for d, _, r in ranked[:3]]))
            check("it's recognised as a women's item", ranked and ranked[0][1] is True)
            title, body = hot_push.compose(ranked[0][0], True)
            check("title carries an emoji hook and the product", title[0] not in "abcdefghijklmnopqrstuvwxyz" and "Libas" in title, title)
            check("body quotes the price and what it was", "₹649" in body and "₹2,199" in body, body)
            check("title never repeats the price or channel hype", "₹" not in title and "Loot" not in title, title)
            for raw, want in [("Loot: AGEasy Relief Compact Massage Gun @799", "AGEasy Relief Compact Massage Gun"),
                              ("86% Off - Kamiliant Large Suitcase (78 cm) At Rs.1,899", "Kamiliant Large Suitcase (78 cm)"),
                              ("Flat Iron Hair Straightener at Rs 499", "Flat Iron Hair Straightener"),
                              ("Cashews - ₹289 +36 supercoins", "Cashews"),
                              ("Formal Shirts for Men", "Formal Shirts for Men")]:
                check(f"clean title: {want}", hot_push.clean_title(raw) == want, hot_push.clean_title(raw))
            for _ in range(20):
                d1, d2 = hot_push.random_delays(2, 2400)
                if not (60 <= d1 < 1200 <= d2 <= 2400):
                    check("two pushes land one in each half of the cycle", False, f"{d1:.0f}, {d2:.0f}")
                    break
            else:
                check("two pushes land one in each half of the cycle", True)
            spread = {round(hot_push.random_delays(2, 2400)[0]) for _ in range(10)}
            check("push times vary cycle to cycle (never a fixed clock)", len(spread) > 5, str(spread))
            check("quiet hours respected (23-8 IST)", hot_push.is_quiet(datetime(2026, 1, 1, 2, 0)) is True
                  and hot_push.is_quiet(datetime(2026, 1, 1, 14, 0)) is False)

            first = asyncio.run(hot_push.send_best(force=True))
            check("admin 'send now' pushes the women's item", first.get("deal_id") == women["id"], str(first))
            check("…and reports delivery", first.get("tokens") == 2 and first.get("accepted") == 1, str(first))
            second = asyncio.run(hot_push.send_best(force=True))
            check("the same product is never pushed twice in a row", second.get("deal_id") == gadget["id"], str(second))
            check("respects the minimum gap between automatic pushes",
                  asyncio.run(hot_push.send_best()).get("why", "").startswith("too soon")
                  or hot_push.is_quiet())
            r = c.get("/api/admin/reader/push-status", headers=ADMIN).json()
            check("admin status lists what went out and who can get push",
                  len(r["recent"]) == 2 and r["reachable"] == 2 and r["active_devices"] >= 2, str(r))
            r = c.post("/api/admin/reader/push-now", headers=ADMIN).json()
            check("admin push-now endpoint works", r.get("status") in ("sent", "skipped"), str(r))

            async def plan_and_cancel():
                plan = hot_push.schedule_cycle([women["id"]], 2400)
                hot_push._cancel_pending()
                return plan
            plan = asyncio.run(plan_and_cancel())
            check("each cycle plans PUSHES_PER_CYCLE pushes", len(plan) == 2, str(plan))
        finally:
            fcm._post, fcm._access_token, fcm._account = real_post, real_token, real_account

        print("\n=== DEVICES SURVIVE A RESTART (TURSO RESTORE) ===")
        saved = [{"device_id": "app_restoredfromturso1", "platform": "android",
                  "push_token": "fcmRS:APA91b" + "c" * 140, "digest": 1, "digest_hour": 9,
                  "follows": '[{"kind": "brand", "value": "Libas", "min_discount": 20, "created_at": 1}]',
                  "created_at": 1.0, "last_seen_at": time.time()}]
        check("restores a device", turso_backup._restore_devices(saved) == 1)
        row = db.query_one("SELECT push_token, digest_hour FROM devices WHERE device_id = 'app_restoredfromturso1'")
        check("with its push token", row and row["push_token"].startswith("fcmRS:"))
        check("with its follows", devices.follows("app_restoredfromturso1")[0]["value"] == "Libas")
        check("a device already re-registered locally keeps its own row", turso_backup._restore_devices(saved) == 0)
        check("registering marks a device for backup", db.query_one(
            "SELECT turso_dirty FROM devices WHERE device_id = ?", (dev,))["turso_dirty"] > 0)
    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
