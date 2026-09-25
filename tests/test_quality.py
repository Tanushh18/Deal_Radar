"""Noise gate, shortlink unwrapping, photo links, and the women-first rule.

Run:  python -m tests.test_quality
Titles below are real posts from the live feed (Sept 2026). No network used.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time

os.environ["PRIORITY_AUDIENCE"] = "women"
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import links, parser, priority, quality, search, store, taxonomy  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def post(text: str, channel: int, message: int, photo: bool = True):
    deal = parser.parse_message(text, channel_id=channel, channel_title=f"c{channel}",
                                message_id=message, posted_at=time.time() - 600)
    if deal and photo:
        deal["image_url"] = store.telegram_image_url(deal["id"], channel, message)
    return deal


KEEP = [
    "Havells 1200mm Elio BLDC Ceiling Fan | Remote Controlled @ ₹2414\nhttps://www.amazon.in/dp/B0HAVELLS1",
    "OnePlus N6 Lite | 4GB+64GB | Midnight Hype @ ₹16,999\nhttps://www.amazon.in/dp/B0ONEPLUS6",
    "Maheshvi Casual Animal Print Women's Black Top ₹217\nhttps://bilty.co/15KkPb",
    "63% Off : TRESemme Shampoo, 580ml at ₹322.\nhttps://amzn.to/abc",
    "Water gun deal — Nerf Super Soaker from Hasbro @ ₹349\nhttps://amzn.to/xyz",
    "76% off - Ak fashion mall women kurta and pant set - Rs. 499\nhttps://fkrt.cc/q",
]
DROP = {
    "Myntra : Upto 70% Off On Top Branded Shoes @299\nhttps://myntr.in/x": "roundup",
    "AJIO : T-shirts Starts @80.\nhttps://ajiio.co/x": "roundup",
    "Handbags from at 132\nhttps://fkrt.cc/x": "roundup",
    "Loot: Spykar & Pepe Jeans Under @799\nhttps://fkrt.cc/y": "roundup",
    "New Big Loot : Refer 6 Friends Get ₹25,000 Amazon Voucher\nhttps://bilty.co/z": "promo",
    "Swiggy Dineout Offer : CCD Coffee @ ₹49.\nhttps://swiggy.com/x": "promo",
    "Biggest Pre Sale Is Going To Live Today At Midnight @ ₹99\nhttps://amzn.to/p": "promo",
    "Buy Max @ ₹82\nhttps://dl.flipkart.com/x": "vague",
    "Apply 12000 Coupon @ ₹1998\nhttps://amzn.to/q": "vague",
    "Wooden handcrafted temple guys\nhttps://bilty.co/w": "no_price",
    "Boat Airdopes 141 @ ₹899\nhttps://t.me/somechannel": "no_link",
    ("Kitchen deals\nBoat Airdopes 141 @ ₹899\nhttps://amzn.to/a\nhttps://amzn.to/b\nhttps://fkrt.cc/c"): "roundup",
}


def main() -> int:
    db.connect()

    print("\n=== 1. TEXT GATE (real posts) ===")
    for text in KEEP:
        deal = parser.parse_message(text, channel_id=1, channel_title="c", message_id=1, posted_at=time.time())
        reason = quality.text_reason(deal) if deal else "unparsed"
        check(f"keeps: {text.splitlines()[0][:55]}", reason is None, str(reason))
    for text, expected in DROP.items():
        deal = parser.parse_message(text, channel_id=1, channel_title="c", message_id=1, posted_at=time.time())
        reason = quality.text_reason(deal) if deal else "unparsed"
        check(f"drops ({expected}): {text.splitlines()[0][:50]}", reason in (expected, "unparsed"), str(reason))

    print("\n=== 2. WHERE THE LINK LEADS ===")
    check("product page recognised (amazon /dp/)", links.is_product_page("https://www.amazon.in/dp/B0DPL9C4FS") is True)
    check("product page recognised (flipkart /p/itm)", links.is_product_page("https://www.flipkart.com/x/p/itm3ec779eedc6fb?pid=A") is True)
    check("product page recognised (myntra id)", links.is_product_page("https://www.myntra.com/kurtas/anouk/x/12345678/buy") is True)
    check("brand listing detected (myntra)", links.is_product_page("https://www.myntra.com/vishudh?rawQuery=Vishudh") is False)
    check("category listing detected (ajio /c/)", links.is_product_page("https://www.ajio.com/women-dupattas/c/830303005") is False)
    check("search listing detected (amazon /s)", links.is_product_page("https://www.amazon.in/s?k=shoes") is False)
    check("unknown shortener isn't judged", links.is_product_page("https://bitli.in/eSpRk0l") is None)
    check("unwraps linkredirect dl=",
          links.unwrap("https://trackingv3.linkredirect.in/visitretailer/1?dl=https%3A%2F%2Fwww.flipkart.com%2Fx%2Fp%2Fitm1")
          == "https://www.flipkart.com/x/p/itm1")
    check("unwraps urlgeni path", links.unwrap("https://amzn.urlgeni.us/https://www.amazon.in/dp/B0H4215LJ8?tag=x")
          == "https://www.amazon.in/dp/B0H4215LJ8?tag=x")
    check("unwraps indiadesire pid", links.unwrap("https://indiadesire.com/Redirect?redirectpid1=B0DPL9C4FS&store=amazon")
          == "https://www.amazon.in/dp/B0DPL9C4FS")
    deal = post("Maheshvi Women's Black Top ₹217\nhttps://bilty.co/15KkPb", 1, 1)
    parser.rebase_on_url(deal, "https://www.shopsy.in/maheshvi-top/p/itme4e605ba6955d?pid=XTOGX8GGPZF5KABC")
    check("resolved link fixes the store", deal["store"] == "shopsy", deal["store"])
    check("resolved link gives a real product id", deal["product_key"] == "flipkart:XTOGX8GGPZF5KABC", deal["product_key"])
    listing = post("Vishudh Women Kurta Set ₹399\nhttps://myntr.in/80HD5z", 1, 2)
    listing["resolved_url"] = "https://www.myntra.com/vishudh?rawQuery=Vishudh"
    check("link to a listing page is a round-up", quality.reject_reason(listing) == "roundup",
          str(quality.reject_reason(listing)))
    check("no photo -> rejected", quality.reject_reason(post("Lakme Kajal @ ₹99\nhttps://amzn.to/k", 1, 3, photo=False))
          == "no_image")

    print("\n=== 3. GATE IN STORAGE ===")
    gate = quality.reject_reason
    first = post("boAt Rockerz 450 Bluetooth Headphones @ ₹1299\nhttps://www.amazon.in/dp/B07PR1CL3S", 10, 100)
    check("good post becomes a card", store.save_deal(first, gate=gate) == "new")
    messy = post("boAt Rockerz 450 Bluetooth Headphones @ ₹1299\nhttps://www.amazon.in/dp/B07PR1CL3S", 11, 200,
                 photo=False)
    check("photo-less repost still counts as corroboration", store.save_deal(messy, gate=gate) == "merged")
    row = db.query_one("SELECT repost_count, image_url FROM deals WHERE id = ?", (first["id"],))
    check("repost count went up", row["repost_count"] == 2, str(row["repost_count"]))
    noise = post("Buy Max @ ₹82\nhttps://www.amazon.in/dp/B0NOISE001", 12, 300)
    check("noise never becomes a card", store.save_deal(noise, gate=gate) == "filtered")
    check("…and isn't stored at all", db.query_one("SELECT 1 FROM deals WHERE id = ?", (noise["id"],)) is None)

    print("\n=== 4. PHOTOS FROM REPOSTS ===")
    bare = post("Prestige Omega Granite Kadai 240mm @ ₹749\nhttps://www.amazon.in/dp/B08XYZ1234", 20, 1, photo=False)
    store.save_deal(bare)  # no gate: legacy path, stored without a photo
    with_photo = post("Prestige Omega Granite Kadai 240mm @ ₹749\nhttps://www.amazon.in/dp/B08XYZ1234", 21, 77)
    store.save_deal(with_photo, gate=gate)
    row = db.query_one("SELECT image_url FROM deals WHERE id = ?", (bare["id"],))
    check("card adopts the repost's photo", bool(row["image_url"]), row["image_url"])
    check("photo link addresses this card", row["image_url"].startswith(f"/api/deals/{bare['id']}/image"), row["image_url"])
    check("photo link names the source post", store.image_source(row["image_url"]) == (21, 77), row["image_url"])
    check("legacy link on its own card survives", store.rebase_image_url("/api/deals/abc/image", "abc") == "/api/deals/abc/image")
    check("legacy link on another card is dropped", store.rebase_image_url("/api/deals/xyz/image", "abc") == "")
    check("external image passes through", store.rebase_image_url("https://img/x.jpg", "abc") == "https://img/x.jpg")

    print("\n=== 5. PHOTO-LESS DEALS NEVER LISTED ===")
    textonly = post("Milton Thermosteel Flask 1L @ ₹699\nhttps://www.amazon.in/dp/B0MILTON01", 30, 1, photo=False)
    store.save_deal(textonly)  # legacy path stores it live
    ids = [d["id"] for d in search.search(sort="newest", limit=100)["results"]]
    check("photo-less deal hidden from the feed", textonly["id"] not in ids)
    check("photo deals shown", first["id"] in ids)
    retired = store.retire_imageless()
    check("photo-less live deals retired each cycle", retired >= 1, str(retired))

    print("\n=== 6. SWEEP OF STORED DEALS ===")
    old_noise = post("Myntra : Upto 70% Off On Top Branded Shoes @299\nhttps://myntr.in/x", 40, 1)
    store.save_deal(old_noise)  # stored before the gate existed
    swapped = post("Ajio LOOT : Women T-shirts @81\nhttps://www.ajio.com/p/702744631004", 41, 1)
    store.save_deal(swapped)
    db.execute("UPDATE deals SET category = 'Men Fashion', subcategory = 'T-Shirt' WHERE id = ?", (swapped["id"],))
    stats = quality.sweep_stored()
    row = db.query_one("SELECT status, flags FROM deals WHERE id = ?", (old_noise["id"],))
    check("old round-up retired", row["status"] == "dead" and "low_quality" in json.loads(row["flags"]), dict(row).__repr__())
    row = db.query_one("SELECT status, category FROM deals WHERE id = ?", (swapped["id"],))
    check("women's item re-filed under Women Fashion", row["category"] == "Women Fashion", row["category"])
    check("good deals untouched", db.query_one("SELECT status FROM deals WHERE id = ?", (first["id"],))["status"] == "live")
    check("sweep reports its work", stats["removed"] >= 1 and stats["recategorised"] >= 1, str(stats))
    check("second sweep is a no-op", quality.sweep_stored()["checked"] == 0)
    archive = [d["id"] for d in search.search(archive=True, limit=100)["results"]]
    check("retired noise stays out of the archive too", old_noise["id"] not in archive)
    revived = post("Myntra : Upto 70% Off On Top Branded Shoes @299\nhttps://myntr.in/x", 42, 2)
    check("a noisy repost can't revive it", store.save_deal(revived, gate=gate) == "filtered")

    print("\n=== 7. WOMEN FIRST ===")
    check("women's t-shirt -> Women Fashion", taxonomy.classify("Ajio : Women T-shirts")[0] == "Women Fashion")
    check("kurtas for men -> Men Fashion", taxonomy.classify("Fabindia Kurtas & Shirts For Men")[0] == "Men Fashion")
    for text, cat_photo in [
        ("Lakme 9to5 Primer Matte Lipstick @ ₹299\nhttps://www.amazon.in/dp/B0LAKME001", 50),
        ("AXE Dark Temptation Perfume 150ml @ ₹248\nhttps://www.amazon.in/dp/B0AXEDARK1", 51),
        ("Beardo Godfather Perfume for Men @ ₹399\nhttps://www.amazon.in/dp/B0BEARDO01", 52),
        ("Anouk Women Printed Anarkali Kurta @ ₹649\nhttps://www.amazon.in/dp/B0ANOUK001", 53),
    ]:
        store.save_deal(post(text, cat_photo, 1), gate=gate)
    feed = [d["title"] for d in search.search(sort="newest", limit=100)["results"]]
    lead = feed[:2]
    check("women's deals lead the feed", any("Lipstick" in t for t in lead) and any("Kurta" in t for t in lead), str(lead))
    axe = next(i for i, t in enumerate(feed) if t.startswith("AXE"))
    beardo = next(i for i, t in enumerate(feed) if t.startswith("Beardo"))
    check("men's grooming doesn't lead a women's feed", axe >= 2 and beardo >= 2, str(feed))
    legacy = {"preset": "women", "label": "Women", "categories": ["Women Fashion", "Beauty"], "keywords": ["women"],
              "stores": []}
    db.set_meta(priority.META_KEY, json.dumps(legacy))
    priority.reset_cache()
    check("a rule saved before exclusions picks them up", "beardo" in priority.get()["exclude_keywords"])

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
