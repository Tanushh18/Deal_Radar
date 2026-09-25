"""Real-time listener + posting hot deals to your own Telegram channel.

Run:  python -m tests.test_live
Fake channel messages go through live.handle_message exactly as Telethon
would deliver them; the Telegram Bot API is a fake that records every call.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  TG_BOT_TOKEN="123:abc", TG_POST_CHANNEL="@mydeals", TG_MAX_POSTS_PER_HOUR="5",
                  LIVENESS_CHECK="false")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.services import ingest, live, telegram, tg_post  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# --- fake Bot API --------------------------------------------------------
calls = []


def bot_api(request: httpx.Request) -> httpx.Response:
    method = request.url.path.rsplit("/", 1)[-1]
    assert request.url.path.startswith("/bot123:abc/"), request.url.path
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("application/json"):
        body = json.loads(request.content)
    else:  # multipart (photo upload)
        body = {"_multipart": True, "_has_photo": b"JPEGDATA" in request.content,
                "_caption": b"LOWEST" in request.content or b"OFF" in request.content}
    calls.append((method, body))
    return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})


class _FakeHttpx:
    def __getattr__(self, name):
        return getattr(httpx, name)

    def AsyncClient(self, **kw):  # noqa: N802
        return httpx.AsyncClient(transport=httpx.MockTransport(bot_api), **kw)


tg_post.httpx = _FakeHttpx()


class FakeClient:
    async def download_media(self, message, file=None, **kw):
        return b"JPEGDATA"


client = FakeClient()
_mid = [100]


def msg(text: str, channel: int, photo: bool = False, age_minutes: float = 0):
    _mid[0] += 1
    return SimpleNamespace(
        peer_id=SimpleNamespace(channel_id=channel), message=text, raw_text=text, id=_mid[0],
        date=datetime.now(timezone.utc) - timedelta(minutes=age_minutes),
        photo=object() if photo else None, entities=None, reply_markup=None,
    )


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def main() -> int:
    db.connect()
    for tg_id, name in ((1, "loot1"), (2, "loot2"), (3, "loot3"), (9, "mydeals")):
        db.execute("INSERT INTO channels (tg_id, username, title, active) VALUES (?, ?, ?, 1)", (tg_id, name, name))
    db.execute("INSERT INTO channels (tg_id, username, title, active) VALUES (8, 'blocked', 'blocked', 0)")

    print("\n=== HOT DEAL POSTED WITHIN THE SAME CALL ===")
    t0 = time.time()
    run(live.handle_message(client, msg("boAt Airdopes 141 ₹899 (MRP ₹4,490) https://www.amazon.in/dp/B09N3ZNHTY", 1)))
    took = time.time() - t0
    check("hot deal (80% off) posted", len(calls) == 1, str(calls))
    method, body = calls[-1] if calls else ("", {})
    check("posted to the configured channel", body.get("chat_id") == "@mydeals", str(body))
    check("caption shows the discount + price", "80% OFF" in body.get("text", "") and "₹899" in body.get("text", ""),
          body.get("text", ""))
    btns = (body.get("reply_markup") or {}).get("inline_keyboard", [[]])[0]
    check("buy + price-history buttons", [b["text"] for b in btns] == ["🛒 Buy now", "📈 Price history"]
          and btns[1]["url"].startswith("https://deals.example/?deal="), str(btns))
    check("saved for the website at the same time", db.query_one("SELECT COUNT(*) AS c FROM deals")["c"] == 1)
    check("seconds, not minutes", took < 2, f"{took:.2f}s")

    print("\n=== NO DUPLICATES / NOT-HOT / FAKE MRP ===")
    run(live.handle_message(client, msg("boAt Airdopes 141 ₹899 (MRP ₹4,490) https://www.amazon.in/dp/B09N3ZNHTY", 2)))
    check("same product from another channel not re-posted", len(calls) == 1)
    run(live.handle_message(client, msg("Philips Trimmer BT1232 ₹1,099 (MRP ₹1,395) https://www.amazon.in/dp/B07KWM3B8J", 1)))
    check("21% off is not hot — saved, not posted", len(calls) == 1
          and db.query_one("SELECT COUNT(*) AS c FROM deals WHERE product_key LIKE '%B07KWM3B8J'")["c"] == 1)
    db.execute("INSERT INTO price_history (product_key, price, store, seen_at) VALUES "
               "('amazon:B0FAKEMRP1', 500, 'amazon', 1), ('amazon:B0FAKEMRP1', 510, 'amazon', 2), "
               "('amazon:B0FAKEMRP1', 505, 'amazon', 3)")
    run(live.handle_message(client, msg("Generic Watch ₹499 (MRP ₹9,999) https://www.amazon.in/dp/B0FAKEMRP1", 1)))
    check("fake-MRP deal never posted", len(calls) == 1, str(calls[-1:]))

    print("\n=== IGNORED SOURCES ===")
    run(live.handle_message(client, msg("Mega TV ₹9,999 (MRP ₹49,999) https://www.amazon.in/dp/B0UNTRACK1", 77)))
    check("untracked channel ignored", len(calls) == 1 and not db.query_one("SELECT 1 FROM deals WHERE product_key LIKE '%UNTRACK1'"))
    run(live.handle_message(client, msg("Mega TV ₹9,999 (MRP ₹49,999) https://www.amazon.in/dp/B0BLOCKED1", 8)))
    check("blocked (inactive) channel ignored", len(calls) == 1)
    run(live.handle_message(client, msg("Mega TV ₹9,999 (MRP ₹49,999) https://www.amazon.in/dp/B0OWNPOST1", 9)))
    check("our own output channel never re-ingested", len(calls) == 1)

    print("\n=== PHOTO, PRICE DROP, TRENDING ===")
    run(live.handle_message(client, msg("Noise Smartwatch ₹1,299 (MRP ₹5,999) https://www.amazon.in/dp/B0PHOTO001", 1, photo=True)))
    check("photo uploaded with the post", calls[-1][0] == "sendPhoto" and calls[-1][1].get("_has_photo"), str(calls[-1]))
    n = len(calls)
    run(live.handle_message(client, msg("boAt Airdopes 141 now ₹799 (MRP ₹4,490) https://www.amazon.in/dp/B09N3ZNHTY", 3)))
    check("same product re-posted when it got cheaper", len(calls) == n + 1 and "₹799" in calls[-1][1].get("text", ""),
          str(calls[-1:]))
    n = len(calls)
    for ch in (1, 2, 3):
        run(live.handle_message(client, msg("JBL Go 3 Speaker ₹2,499 (MRP ₹3,999) https://www.amazon.in/dp/B0TREND001", ch)))
    check("trending: posted once the 3rd channel carries it", len(calls) == n + 1
          and "TRENDING" in calls[-1][1].get("text", ""), str(calls[-1:]))

    print("\n=== HOURLY CAP ===")
    n = len(calls)
    names = ["Prestige Induction Cooktop", "Lenovo Wireless Mouse", "Havells Steam Iron",
             "Milton Thermosteel Flask", "Skullcandy Earbuds"]
    for i, name in enumerate(names):
        run(live.handle_message(client, msg(f"{name} ₹{300 + i * 97} (MRP ₹4,999) https://www.amazon.in/dp/B0CAP0000{i}", 1)))
    check("never more than TG_MAX_POSTS_PER_HOUR (5)", db.query_one(
        "SELECT COUNT(*) AS c FROM tg_posts WHERE posted_at > ?", (time.time() - 3600,))["c"] == 5, str(len(calls) - n))

    print("\n=== POLL CYCLE: SAFETY NET ONLY FOR FRESH DEALS ===")
    db.execute("DELETE FROM tg_posts")
    calls.clear()
    reader = db.execute("INSERT INTO users (telegram_id, username) VALUES (555, 'reader')").lastrowid
    db.execute("UPDATE channels SET source_user_id = ?, last_message_id = 0", (reader,))
    batch = [msg("Fresh Loot Mixer ₹999 (MRP ₹4,999) https://www.amazon.in/dp/B0MISSED01", 2),
             msg("Old Loot Kettle ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0OLDPOST1", 2, age_minutes=60)]

    async def fake_fetch(*a, **k):
        return batch

    async def fake_get_client(uid):
        return client

    telegram.fetch_messages, telegram.get_client = fake_fetch, fake_get_client
    channel = dict(db.query_one("SELECT * FROM channels WHERE tg_id = 2"))
    run(ingest.ingest_channel(channel))
    texts = [c[1].get("text", "") for c in calls]
    check("fresh deal the listener missed is posted by the cycle", any("Mixer" in t for t in texts), str(texts))
    check("hour-old deal is not posted (website/app only)", not any("Kettle" in t for t in texts), str(texts))
    run(ingest.ingest_channel(channel))
    check("cycle re-reading the same post doesn't re-post", len(calls) == 1, str(len(calls)))

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
