"""Visitor devices: follows, digest, price-drop feed, lookup, similar, verdict.

Run:  python -m tests.test_devices
"""
from __future__ import annotations

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
from app.services import devices, ingest, parser, store  # noqa: E402

ADMIN = {"X-Admin-Token": "test-admin-token"}

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def post(text: str, channel: int):
    deal = parser.parse_message(text, channel_id=channel, channel_title="c", message_id=channel, posted_at=time.time())
    deal["image_url"] = "/api/deals/x/image"
    store.save_deal(deal)
    return deal


def main() -> int:
    dev = "app_1234567890abcdef"
    with TestClient(app) as c:
        print("\n=== DEVICES ===")
        r = c.post("/api/devices/register", json={"device_id": dev, "platform": "android", "digest": True, "digest_hour": 19})
        check("register", r.status_code == 200 and r.json()["device"]["digest"] is True)
        check("bad device id rejected", c.get("/api/devices/settings", params={"device_id": "x"}).status_code == 400)
        check("bad push token rejected", c.post("/api/devices/register", json={"device_id": dev, "push_token": "nope"}).status_code == 400)
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
    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
