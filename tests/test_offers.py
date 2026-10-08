""""More offers": posts the noise gate turned away, still findable by search.

Run:  python -m tests.test_offers
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from types import SimpleNamespace

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="", TG_BOT_TOKEN="", TG_POST_CHANNEL="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ingest, links, offers, telegram  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


POSTS = [
    # (text, has photo)
    ("Levi's Men Slim Fit Jeans @999\nhttps://www.myntra.com/jeans/levis/x/16090648/buy", True),            # a card
    ("Myntra : 70-75% Off On Levi's Men's Clothing.\nhttps://www.myntra.com/levis", False),                 # no price
    ("Loot: Mens Branded Kurta - starting 229\nhttps://www.flipkart.com/kurtas/pr?sid=clo", True),          # round-up
    ("Nippon Gold Interior Emulsion Paint, 4 Ltr @902.\nhttps://www.amazon.in/dp/B0DXX8P18C", False),       # no photo
    ("Refer 6 Friends Get ₹25,000 Voucher\nhttps://www.amazon.in/dp/B0REFERRAL", True),                     # promo: never kept
    ("Myntra | Upto 84% Off - Dollar Men's Tshirts\nhttps://www.myntra.com/dollar", False),                 # no price
]


def _messages(first_id: int):
    now = datetime.now(timezone.utc)
    return [SimpleNamespace(id=first_id + i, message=text, raw_text=text, date=now, entities=None,
                            photo=object() if photo else None, reply_markup=None)
            for i, (text, photo) in enumerate(POSTS)]


def found(c, q):
    """Channel-post results only (the Amazon / Flipkart search links are always appended)."""
    return [o for o in c.get("/api/deals/offers", params={"q": q}).json()["results"] if not o.get("marketplace")]


def main() -> int:
    db.connect()
    db.execute("INSERT INTO channels (tg_id, title, username, active, last_message_id, source_user_id) "
               "VALUES (777, 'Secret Loot Channel', 'secretloot', 1, 100, 1)")
    db.execute("INSERT INTO channels (tg_id, title, username, active, last_message_id, source_user_id) "
               "VALUES (778, 'Other Channel', 'other', 1, 100, 1)")

    async def fake_reader(channel):
        return 1

    async def fake_fetch(user_id, tg_id, min_id=0, limit=60):
        return _messages(1000 if tg_id == 777 else 2000)

    async def no_network(deals):
        return 0

    real = (ingest._resolve_reader, telegram.fetch_messages, links.resolve_deals, links.fill_store_images)
    ingest._resolve_reader, telegram.fetch_messages = fake_reader, fake_fetch
    links.resolve_deals = links.fill_store_images = no_network

    async def run_both():
        for tg_id in (777, 778):
            channel = db.row_to_dict(db.query_one("SELECT * FROM channels WHERE tg_id = ?", (tg_id,)))
            await ingest.ingest_channel(channel)
        await asyncio.gather(*list(offers._pending))   # the background saves

    try:
        print("\n=== TURNED-AWAY POSTS ARE KEPT ===")
        asyncio.run(run_both())
        rows = db.rows_to_dicts(db.query("SELECT * FROM offers"))
        titles = " | ".join(r["title"] for r in rows)
        check("round-ups, no-price and no-photo posts are kept", len(rows) == 8, f"{len(rows)}: {titles}")
        check("promo spam is never kept", "Refer" not in titles, titles)
        check("the post that became a card isn't duplicated as an offer", "Slim Fit Jeans" not in titles, titles)
        cols = {c for r in rows for c in r}
        check("no post text or channel is stored", not ({"raw_text", "channel_title", "channel_id"} & cols)
              and "Secret" not in str(rows), str(cols))

        with TestClient(app) as c:
            print("\n=== SEARCH ===")
            r = found(c, "levis")
            check("search finds the Levi's round-up", any("Levi" in o["title"] for o in r), str(r))
            check("same offer from two channels shows once", len({o["title"] for o in r}) == len(r) and len(r) == 1, str(r))
            check("result has just title, price, store, link, time",
                  set(r[0]) == {"id", "title", "price", "price_from", "store", "url", "posted_at"}, str(r[0]))
            r = found(c, "kurta")
            check("'kurta' finds 'Kurta starting 229' as from ₹229", r and r[0]["price"] == 229.0 and r[0]["price_from"], str(r))
            check("starting prices with commas and @ are read",
                  offers._from_price("Luggage Deals | Starting @ ₹1,349") == 1349.0
                  and offers._from_price("Sweatshirts Starting From Rs.192") == 192.0
                  and offers._from_price("Kids Wear | All Under ₹399") == 399.0
                  and offers._from_price("70-75% Off On Levi's") == 0.0)
            r = found(c, "paint")
            check("photo-less deal is findable", r and r[0]["store"] == "amazon", str(r))
            r = found(c, "tshirt")
            check("synonyms work ('tshirt' → T-shirts)", len(r) == 1, str(r))
            check("unrelated words find nothing", found(c, "refrigerator") == [])
            check("one-letter or empty query returns nothing", found(c, "l") == [])
            mp = c.get("/api/deals/offers", params={"q": "running shoes"}).json()["results"][-2:]
            check("Amazon and Flipkart search links always follow",
                  [(o["store"], o["url"]) for o in mp] == [
                      ("amazon", "https://www.amazon.in/s?k=running+shoes"),
                      ("flipkart", "https://www.flipkart.com/search?q=running+shoes")]
                  and all(o["marketplace"] for o in mp), str(mp))
            check("promos/vouchers (non-spam) are kept for search",
                  offers.keep({"title": "Swiggy Dineout 50% Offer", "url": "https://x.in/a"}, "promo")
                  and offers.keep({"title": "Some Product Here", "url": "https://x.in/a"}, "no_price")
                  and not offers.keep({"title": "Refer 6 Friends Get Voucher", "url": "https://x.in/a",
                                       "raw_text": "Refer 6 Friends Get Voucher"}, "promo"))
            check("route doesn't clash with /api/deals/{id}", c.get("/api/deals/offers").status_code == 200)

        print("\n=== CLEAN-UP ===")
        db.execute("UPDATE offers SET posted_at = ? WHERE title LIKE '%Kurta%'", (time.time() - 8 * 86400,))
        removed = offers.prune()
        check("offers older than a week are pruned", removed == 2, str(removed))
        check("a channel handle is never an offer title",
              not offers.keep({"title": "@Lootunboxing", "url": "https://bitli.in/x"}, "vague")
              and offers.clean_title("Kurta Set @lootdeals - starting 229 t.me/lootdeals") == "Kurta Set - starting 229"
              and offers.clean_title("Story@Home Door Curtains") == "Story@Home Door Curtains")
        db.upsert("offers", {"id": "9:9", "title": "@Lootunboxing", "title_key": "lootunboxing", "posted_at": time.time()})
        db.upsert("offers", {"id": "9:10", "title": "Nike Shoes @nikeloot", "title_key": "x", "posted_at": time.time()})
        offers.prune()
        left = {r["id"]: r["title"] for r in db.rows_to_dicts(db.query("SELECT id, title FROM offers WHERE id LIKE '9:%'"))}
        check("handles already stored are cleaned or dropped", left == {"9:10": "Nike Shoes"}, str(left))
        check("unknown shortener isn't shown as a store",
              offers._row({"title": "Some Product Here", "url": "https://bitli.in/x", "store": "bitli"})["store"] == "")
    finally:
        ingest._resolve_reader, telegram.fetch_messages, links.resolve_deals, links.fill_store_images = real

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
