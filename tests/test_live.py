"""Real-time listener + posting verified, women-focused deals to your Telegram channel.

Run:  python -m tests.test_live
Fake channel messages go through live.handle_message exactly as Telethon
would deliver them; the Telegram Bot API and the product-page check are
fakes, so every rule in tg_post.assess is exercised deterministically.
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
                  TG_BOT_TOKEN="123:abc", TG_POST_CHANNEL="@mydeals", TG_MAX_POSTS_PER_HOUR="12",
                  TG_OTHER_MAX_PER_HOUR="3", LIVENESS_CHECK="false")
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
    if request.headers.get("content-type", "").startswith("application/json"):
        body = json.loads(request.content)
        body["_text"] = body.get("text") or body.get("caption") or ""
    else:  # multipart (photo upload)
        raw = request.content.decode("utf-8", errors="ignore")
        body = {"_multipart": True, "_has_photo": "JPEGDATA" in raw, "_text": raw}
    calls.append((method, body))
    return httpx.Response(200, json={"ok": True, "result": {"message_id": len(calls)}})


class _FakeHttpx:
    def __getattr__(self, name):
        return getattr(httpx, name)

    def AsyncClient(self, **kw):  # noqa: N802
        return httpx.AsyncClient(transport=httpx.MockTransport(bot_api), **kw)


tg_post.httpx = _FakeHttpx()

# --- fake product-page check: keyed by a marker in the URL -----------------
PAGES = {}   # marker -> probe result
probed = []


async def fake_probe(url):
    probed.append(url)
    for marker, page in PAGES.items():
        if marker in (url or ""):
            return {"status": "unknown", "price": None, "rating": None, "reviews": None, "url": url, **page}
    return {"status": "live", "price": None, "rating": None, "reviews": None, "url": url}


tg_post.probe_product = fake_probe


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


def feed(text: str, channel: int = 1, **kw) -> bool:
    """Deliver one post; True if it produced a Telegram post."""
    before = len(calls)
    run(live.handle_message(client, msg(text, channel, **kw)))
    return len(calls) > before


def last_text() -> str:
    return calls[-1][1].get("_text", "") if calls else ""


def main() -> int:
    db.connect()
    for tg_id, name in ((1, "loot1"), (2, "loot2"), (3, "loot3"), (9, "mydeals")):
        db.execute("INSERT INTO channels (tg_id, username, title, active) VALUES (?, ?, ?, 1)", (tg_id, name, name))
    db.execute("INSERT INTO channels (tg_id, username, title, active) VALUES (8, 'blocked', 'blocked', 0)")

    print("\n=== WOMEN'S ACCESSORIES: THE CHANNEL'S FOCUS ===")
    t0 = time.time()
    ok = feed("Oxidised Jhumka Earrings for Women ₹199 (MRP ₹999) https://www.amazon.in/dp/B0JHUMKA01", photo=True)
    took = time.time() - t0
    check("₹199 jhumkas, 80% off → posted", ok, str(calls[-1:]))
    check("uploaded with the channel's photo", calls and calls[-1][0] == "sendPhoto" and calls[-1][1].get("_has_photo"))
    check("labelled a CRAZY DEAL", "CRAZY DEAL" in last_text(), last_text()[:300])
    check("tagged as women's jewellery", "Women's jewellery" in last_text(), last_text()[:300])
    check("says it was verified in stock", "Verified in stock just now" in last_text())
    check("buy + price-history buttons", '"🛒 Buy now"' in last_text() or "Buy now" in last_text())
    check("seconds, not minutes (page check included)", took < 2, f"{took:.2f}s")
    check("saved for the website at the same time", db.query_one("SELECT COUNT(*) AS c FROM deals")["c"] == 1)

    check("women's kurta at 55% off → posted (women need 50%)",
          feed("Libas Women Kurta ₹449 (MRP ₹999) https://www.myntra.com/kurtas/libas/1234567/buy"))
    check("…and labelled for her", "For her" in last_text(), last_text()[:200])
    check("gadget at 60% off → NOT posted (others need 70%)",
          not feed("Portronics Power Bank 20000mAh ₹999 (MRP ₹2,499) https://www.amazon.in/dp/B0POWERB01"))
    check("gadget at 80% off → posted",
          feed("boAt Airdopes 141 ₹899 (MRP ₹4,490) https://www.amazon.in/dp/B09N3ZNHTY"))
    check("…as a plain 80% OFF, no women tag", "80% OFF" in last_text() and "For her" not in last_text())

    print("\n=== NOTHING FAKE OR VAGUE GETS OUT ===")
    check("'starting from ₹199' sale post rejected",
          not feed("Women Kurtis starting from ₹199 https://www.myntra.com/kurtis"))
    check("'up to 80% off' sale post rejected",
          not feed("Up to 80% off on women handbags ₹299 https://www.amazon.in/b?node=123"))
    check("'buy 3' per-unit price rejected",
          not feed("Hair Clips for Women ₹99 each, min order 3 (MRP ₹999) https://www.amazon.in/dp/B0MINBUY01"))
    check("97% off (invented MRP) rejected",
          not feed("Women Watch ₹99 (MRP ₹3,999) https://www.amazon.in/dp/B0FAKE0097"))
    db.execute("INSERT INTO price_history (product_key, price, store, seen_at) VALUES "
               "('amazon:B0FAKEMRP1', 500, 'amazon', 1), ('amazon:B0FAKEMRP1', 510, 'amazon', 2), "
               "('amazon:B0FAKEMRP1', 505, 'amazon', 3)")
    check("inflated MRP vs our own history rejected",
          not feed("Women Handbag ₹499 (MRP ₹4,999) https://www.amazon.in/dp/B0FAKEMRP1"))
    check("unknown store rejected",
          not feed("Women Sunglasses ₹149 (MRP ₹999) https://www.randomshop.xyz/p/123"))

    print("\n=== VERIFIED ON THE PRODUCT PAGE RIGHT NOW ===")
    PAGES["B0DEAD0001"] = {"status": "dead"}
    check("out of stock right now → rejected",
          not feed("Women Tote Bag ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0DEAD0001"))
    dead = db.query_one("SELECT status FROM deals WHERE product_key = 'amazon:B0DEAD0001'")
    check("…and retired from the website too", dead and dead["status"] != "live", str(dead))
    PAGES["B0OVER0001"] = {"status": "live", "price": 699.0}
    check("page price now ₹699 vs posted ₹299 → rejected (deal is over)",
          not feed("Women Clutch ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0OVER0001"))
    PAGES["B0BADRATE1"] = {"status": "live", "rating": 3.1, "reviews": 240}
    check("3.1★ product rejected",
          not feed("Women Bracelet ₹199 (MRP ₹1,299) https://www.amazon.in/dp/B0BADRATE1"))
    PAGES["B0GOODRATE"] = {"status": "live", "price": 249.0, "rating": 4.3, "reviews": 1520}
    check("4.3★ product posted", feed("Rose Gold Necklace for Women ₹249 (MRP ₹1,499) "
                                      "https://www.amazon.in/dp/B0GOODRATE"))
    check("…with its rating shown", "4.3 (1,520 ratings)" in last_text(), last_text()[:400])

    print("\n=== BLOCKED CHECK: ONLY WITH CORROBORATION ===")
    PAGES["B0BLOCKED1"] = {"status": "unknown"}
    text = "Fastrack Ladies Watch 6152SM ₹399 (MRP ₹1,999) https://www.amazon.in/dp/B0BLOCKED1"
    check("store blocked the check, one channel → not posted", not feed(text, 1))
    check("second channel posts it → posted", feed(text, 2))
    check("…saying it's confirmed by 2 channels", "Confirmed by 2 channels" in last_text(), last_text()[:400])

    print("\n=== SHORTLINKS MUST LAND ON A KNOWN STORE ===")
    PAGES["cutt.ly/good"] = {"status": "live", "url": "https://www.amazon.in/dp/B0SHORTOK1"}
    check("shortlink → amazon posted",
          feed("Women Hair Claw Clips Set ₹149 (MRP ₹799) https://cutt.ly/good"))
    PAGES["cutt.ly/bad"] = {"status": "live", "url": "https://scam.example/win"}
    check("shortlink → unknown site rejected",
          not feed("Women Scarf ₹99 (MRP ₹799) https://cutt.ly/bad"))

    print("\n=== NO DUPLICATES / IGNORED SOURCES ===")
    check("same jhumkas from another channel not re-posted",
          not feed("Oxidised Jhumka Earrings for Women ₹199 (MRP ₹999) https://www.amazon.in/dp/B0JHUMKA01", 2))
    check("same jhumkas cheaper (₹179) → re-posted",
          feed("Oxidised Jhumka Earrings for Women now ₹179 (MRP ₹999) https://www.amazon.in/dp/B0JHUMKA01", 3))
    check("untracked channel ignored",
          not feed("Women Watch ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0UNTRACK1", 77))
    check("blocked channel ignored", not feed("Women Watch ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0BLOCK001", 8))
    check("our own output channel never re-ingested",
          not feed("Women Watch ₹299 (MRP ₹1,999) https://www.amazon.in/dp/B0OWNPOST1", 9))

    print("\n=== SLOTS KEPT FOR WOMEN'S DEALS ===")
    for i, name in enumerate(["Prestige Induction Cooktop", "Lenovo Wireless Mouse", "Havells Steam Iron"]):
        feed(f"{name} ₹{300 + i * 97} (MRP ₹4,999) https://www.amazon.in/dp/B0CAP0000{i}")
    others = db.query_one("SELECT COUNT(*) AS c FROM tg_posts WHERE audience = 'other'")["c"]
    check("non-women deals capped at TG_OTHER_MAX_PER_HOUR (3)", others == 3, str(others))
    check("women's deal still gets through after that",
          feed("Caprese Mini Crossbody Sling for Women ₹299 (MRP ₹1,799) https://www.amazon.in/dp/B0SLING001"))
    n = db.query_one("SELECT COUNT(*) AS c FROM tg_posts")["c"]
    for i, name in enumerate(["Sukkhi Kundan Choker K12", "Estele Pearl Studs E77", "Voylla Silver Anklet A31",
                              "Anekaant Velvet Potli P09", "Yellow Chimes Beaded Bracelet B45", "Priyaasi Maang Tikka M20",
                              "Shining Diva Hair Clips H88"]):
        feed(f"{name} for Women ₹{150 + i * 11} (MRP ₹999) https://www.amazon.in/dp/B0TOTAL0{i:02d}")
    total = db.query_one("SELECT COUNT(*) AS c FROM tg_posts WHERE posted_at > ?", (time.time() - 3600,))["c"]
    check("never more than TG_MAX_POSTS_PER_HOUR (12) in total", total == 12, f"{total} (was {n})")

    print("\n=== POLL CYCLE: SAFETY NET ONLY FOR FRESH DEALS ===")
    db.execute("DELETE FROM tg_posts")
    calls.clear()
    reader = db.execute("INSERT INTO users (telegram_id, username) VALUES (555, 'reader')").lastrowid
    db.execute("UPDATE channels SET source_user_id = ?, last_message_id = 0", (reader,))
    batch = [msg("Zaveri Pearls Temple Jhumki ZPFK9921 ₹199 (MRP ₹999) https://www.amazon.in/dp/B0MISSED01", 2),
             msg("Giva Sterling Silver Toe Ring RG311 ₹149 (MRP ₹999) https://www.amazon.in/dp/B0OLDPOST1", 2, age_minutes=60)]

    async def fake_fetch(*a, **k):
        return batch

    async def fake_get_client(uid):
        return client

    telegram.fetch_messages, telegram.get_client = fake_fetch, fake_get_client
    channel = dict(db.query_one("SELECT * FROM channels WHERE tg_id = 2"))
    run(ingest.ingest_channel(channel))
    texts = [c[1].get("_text", "") for c in calls]
    check("fresh deal the listener missed is posted by the cycle", any("Temple Jhumki" in t for t in texts), str(texts))
    check("hour-old deal is not posted (website/app only)", not any("Toe Ring" in t for t in texts))
    run(ingest.ingest_channel(channel))
    check("cycle re-reading the same post doesn't re-post", len(calls) == 1, str(len(calls)))

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
