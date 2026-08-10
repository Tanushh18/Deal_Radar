"""End-to-end check of parse -> dedup -> search, with no Telegram involved.

Run:  python -m tests.test_pipeline
Uses a throwaway SQLite file so it never touches real data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import parser, search, store, taxonomy  # noqa: E402

# Real-world-shaped posts from Indian deal channels.
SAMPLES = [
    ("""🔥🔥 LOOT DEAL 🔥🔥
boAt Rockerz 450 Bluetooth On Ear Headphones with Mic
₹1,299 (MRP ₹2,990) — 56% OFF
https://amzn.to/3xYzAbc?tag=dealchannel-21""", 1001, "Loot Deals India"),

    ("""boAt Rockerz 450 Bluetooth On Ear Headphone
Price ₹1299 | MRP ₹2990
Buy: https://www.amazon.in/dp/B07PR1CL3S?tag=another-21&ref=xyz""", 1002, "Amazon Loot"),

    ("""Anouk Women Printed Anarkali Kurta with Dupatta
₹649 ₹2,199 (70% off)
Size: S, M, L, XL, XXL
https://www.myntra.com/kurtas/anouk/x/12345678/buy""", 1001, "Loot Deals India"),

    ("""⚡ Ambrane 20000mAh Power Bank 20W Fast Charging
Deal Price: Rs. 999
MRP: Rs 2499
Use code SAVE100 for extra off
https://dl.flipkart.com/dl/p/itm123abc?pid=ACCFXYZ123&affid=test""", 1003, "Flipkart Deals"),

    ("""Campus Men's Running Sports Shoes
@899 only (was 1999)
https://www.flipkart.com/p/itmshoe999?pid=SHOABCD123""", 1002, "Amazon Loot"),

    ("""Join our backup channel for more loot deals
https://t.me/joinchat/xyzabc
Share and support us 🙏""", 1001, "Loot Deals India"),

    ("""Good morning everyone! Deals coming soon today.""", 1001, "Loot Deals India"),

    ("""Prestige Omega Deluxe Granite Non Stick Kadai 240mm
₹749 (MRP ₹1,895) 60% off
https://www.amazon.in/dp/B08XYZ1234""", 1003, "Flipkart Deals"),

    ("""Mamaearth Vitamin C Face Wash 100ml
₹199 only
https://www.nykaa.com/mamaearth-vitamin-c-face-wash/p/998877""", 1004, "Beauty Deals"),

    ("""boAt Rockerz 450 Headphone — PRICE DROP
Now ₹1,149 (lowest ever!)
https://amzn.to/3xYzAbc""", 1004, "Beauty Deals"),
]

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def main() -> int:
    db.connect()
    print("\n=== 1. PARSING ===")
    parsed = []
    now = time.time()
    for text, channel_id, channel_title in SAMPLES:
        deal = parser.parse_message(
            text,
            channel_id=channel_id,
            channel_title=channel_title,
            message_id=hash(text) % 100000,
            posted_at=now - 3600,
            ttl_hours=96,
        )
        parsed.append(deal)

    check("spam post rejected (join/share)", parsed[5] is None)
    check("chatter post rejected (no price/link)", parsed[6] is None)
    real = [d for d in parsed if d]
    check(f"{len(real)}/8 real deals parsed", len(real) == 8, f"got {len(real)}")

    headphone = parsed[0]
    check("price extracted", headphone["price"] == 1299.0, str(headphone["price"]))
    check("MRP extracted", headphone["mrp"] == 2990.0, str(headphone["mrp"]))
    check("discount computed", headphone["discount_pct"] == 56, str(headphone["discount_pct"]))
    check("store detected", headphone["store"] == "amazon", headphone["store"])
    check("affiliate tag stripped", "tag=" not in headphone["clean_url"], headphone["clean_url"])
    check("category classified", headphone["category"] == "Electronics", headphone["category"])
    check("subcategory classified", headphone["subcategory"] == "Headphones", headphone["subcategory"])
    check("brand detected", headphone["brand"].lower() == "boat", headphone["brand"])
    check("title cleaned of emoji/noise", "🔥" not in headphone["title"], headphone["title"])

    kurta = parsed[2]
    check("kurta -> Women Fashion", kurta["category"] == "Women Fashion", kurta["category"])
    check("kurta subcategory", kurta["subcategory"] == "Kurta", kurta["subcategory"])
    check("sizes extracted", "XXL" in (kurta["sizes"] or ""), str(kurta["sizes"]))
    check("myntra store", kurta["store"] == "myntra", kurta["store"])

    powerbank = parsed[3]
    check("Rs. format price", powerbank["price"] == 999.0, str(powerbank["price"]))
    check("coupon extracted", powerbank["coupon"] == "SAVE100", str(powerbank["coupon"]))
    check("power bank category", powerbank["subcategory"] == "Power Bank", powerbank["subcategory"])
    check("flipkart pid product key", powerbank["product_key"].startswith("flipkart:"), powerbank["product_key"])

    shoes = parsed[4]
    check("@899 format price", shoes["price"] == 899.0, str(shoes["price"]))
    check("shoes -> Footwear", shoes["category"] == "Footwear", shoes["category"])

    check("ASIN product key", parsed[1]["product_key"] == "amazon:B07PR1CL3S", parsed[1]["product_key"])
    check("kadai -> Home & Kitchen", parsed[7]["category"] == "Home & Kitchen", parsed[7]["category"])
    check("face wash -> Beauty", parsed[8]["category"] == "Beauty", parsed[8]["category"])

    print("\n=== 2. DEDUP & STORAGE ===")
    outcomes = [store.save_deal(d) for d in real]
    print(f"  outcomes: {outcomes}")
    live = db.query_one("SELECT COUNT(*) AS c FROM deals")["c"]
    check("same headphone from 2 channels merged", "merged" in outcomes)
    check(f"stored {live} rows for 8 posts (dedup applied)", live < 8, f"{live} rows")

    merged_row = db.query_one(
        "SELECT * FROM deals WHERE title LIKE '%Rockerz%' ORDER BY repost_count DESC LIMIT 1"
    )
    check("repost_count > 1 on the duplicated deal",
          merged_row is not None and merged_row["repost_count"] > 1,
          str(merged_row["repost_count"]) if merged_row else "none")

    print("\n=== 3. PRICE HISTORY ===")
    stats = store.price_stats("amazon:B07PR1CL3S")
    check("price history recorded", stats["points"] >= 1, str(stats))

    print("\n=== 4. SYNONYM SEARCH ===")
    cases = [
        ("kurta", "Kurta"),
        ("kurti", "Kurta"),
        ("women dress", "Women Fashion"),
        ("power bank", "Power Bank"),
        ("powerbank", "Power Bank"),
        ("headphone", "Headphones"),
        ("running shoes", "Sports Shoes"),
        ("face wash", "Skincare"),
        ("kadai", "Cookware"),
    ]
    for query, expected in cases:
        res = search.search(q=query, sort="relevance", limit=10)
        found = any(
            expected.lower() in (r.get("subcategory", "") + r.get("category", "")).lower()
            for r in res["results"]
        )
        check(f"search '{query}' finds {expected}", found and res["total"] > 0, f"{res['total']} results")

    print("\n=== 5. TYPO TOLERANCE ===")
    res = search.search(q="headphon", sort="relevance", limit=10)
    check("fuzzy match on 'headphon'", res["total"] > 0, f"{res['total']} results")

    print("\n=== 6. FILTERS ===")
    cheap = search.search(max_price=800, sort="best", limit=50)
    check("max_price filter", all(r["price"] <= 800 for r in cheap["results"] if r["price"]),
          str([r["price"] for r in cheap["results"]]))
    big = search.search(min_discount=60, sort="best", limit=50)
    check("min_discount filter", all(r["discount_pct"] >= 60 for r in big["results"]),
          str([r["discount_pct"] for r in big["results"]]))
    amazon = search.search(store="amazon", limit=50)
    check("store filter", all(r["store"] == "amazon" for r in amazon["results"]) and amazon["total"] > 0)

    print("\n=== 7. FACETS & SCORING ===")
    facets = search.facets()
    check("category facets built", len(facets["categories"]) >= 4, str(len(facets["categories"])))
    check("store facets built", len(facets["stores"]) >= 3, str(len(facets["stores"])))
    top = search.search(sort="best", limit=50)["results"]
    check("scores are ordered descending",
          all(top[i]["score"] >= top[i + 1]["score"] for i in range(len(top) - 1)))
    check("scores in 0-100 range", all(0 <= r["score"] <= 100 for r in top))

    print("\n=== 8. EXPIRY ===")
    db.execute("UPDATE deals SET expires_at = ? WHERE 1", (time.time() - 10,))
    expired = store.expire_stale()
    check(f"expired {expired} stale deals", expired > 0)
    check("expired deals leave the default search", search.search(limit=50)["total"] == 0)

    print("\n=== 9. PRODUCT IDENTITY (model-number gating) ===")
    same_cases = [
        ("boat rockerz 450 headphone", "boat rockerz 450 bluetooth on ear headphones mic"),
        ("ambrane 20000mah power bank 20w", "ambrane 20000mah power bank fast charging"),
        ("anouk women printed anarkali kurta dupatta", "anouk women anarkali kurta dupatta"),
    ]
    diff_cases = [
        ("boat rockerz 450 headphone", "boat rockerz 550 headphone"),
        ("boat airdopes 141 earbuds", "boat airdopes 131 earbuds"),
        ("campus mens running sports shoes", "campus womens running sports shoes"),
        ("ambrane 20000mah power bank", "ambrane 10000mah power bank"),
        ("prestige omega kadai 240mm", "prestige omega kadai 200mm"),
    ]
    for a, b in same_cases:
        check(f"same product: '{a[:28]}…'", store.same_product(a, b))
    for a, b in diff_cases:
        check(f"different product: '{a[:28]}…'", not store.same_product(a, b))

    print("\n=== 10. TAXONOMY COVERAGE ===")
    cats = taxonomy.category_list()
    subs = sum(len(c["subcategories"]) for c in cats)
    check(f"{len(cats)} categories / {subs} subcategories", len(cats) >= 10 and subs >= 50)
    check("expand_query('kurta') pulls siblings", "kurti" in taxonomy.expand_query("kurta"))
    check("expand_query('charger') pulls siblings",
          len(taxonomy.expand_query("charger")) > 5, str(taxonomy.expand_query("charger")))

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
