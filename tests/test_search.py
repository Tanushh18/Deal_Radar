"""Search quality + /api/deals/suggest contract, with no Telegram involved.

Run:  python -m tests.test_search
Uses a throwaway SQLite file so it never touches real data.
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")  # neutral ranking unless a test sets a rule
os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth, db  # noqa: E402
from app.routers import deals as deals_router  # noqa: E402
from app.services import parser, search, store  # noqa: E402

SAMPLES = [
    ("""boAt Rockerz 450 Bluetooth On Ear Headphones with Mic
₹1,299 (MRP ₹2,990) — 56% OFF
https://www.amazon.in/dp/B07PR1CL3S""", 1001, "Loot Deals India"),

    ("""Samsung Galaxy M35 5G Smartphone (8GB RAM, 128GB)
₹14,999 (MRP ₹24,499)
https://www.amazon.in/dp/B0D1SAMS35""", 1001, "Loot Deals India"),

    ("""Anouk Women Printed Anarkali Kurta with Dupatta
₹649 ₹2,199 (70% off)
https://www.myntra.com/kurtas/anouk/x/12345678/buy""", 1002, "Fashion Loot"),

    ("""Libas Women Straight Kurta Cotton
₹499 (MRP ₹1,599)
https://www.myntra.com/kurtas/libas/x/87654321/buy""", 1002, "Fashion Loot"),

    ("""Ambrane 20000mAh Power Bank 20W Fast Charging
Deal Price: Rs. 999
MRP: Rs 2499
https://dl.flipkart.com/dl/p/itm123abc?pid=ACCFXYZ123""", 1003, "Flipkart Deals"),

    ("""Campus Men's Running Sports Shoes
@899 only (was 1999)
https://www.flipkart.com/p/itmshoe999?pid=SHOABCD123""", 1003, "Flipkart Deals"),

    ("""Sony WH-CH520 Wireless Headphones
₹3,490 (MRP ₹5,990)
https://www.flipkart.com/p/itmsony520?pid=ACCSONY520""", 1003, "Flipkart Deals"),

    ("""Prestige Omega Deluxe Granite Non Stick Kadai 240mm
₹749 (MRP ₹1,895) 60% off
https://www.amazon.in/dp/B08XYZ1234""", 1001, "Loot Deals India"),
]

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


def titles(results) -> list:
    return [r["title"] for r in results]


def main() -> int:
    db.connect()
    now = time.time()
    for i, (text, channel_id, channel_title) in enumerate(SAMPLES):
        deal = parser.parse_message(
            text, channel_id=channel_id, channel_title=channel_title,
            message_id=5000 + i, posted_at=now - 3600, ttl_hours=96,
        )
        assert deal, text
        deal["image_url"] = f"https://img.example/{deal['id']}.jpg"  # feeds show photo cards only
        store.save_deal(deal)
    stored = db.query_one("SELECT COUNT(*) AS c FROM deals")["c"]
    check(f"{stored} sample deals stored", stored == len(SAMPLES), str(stored))

    print("\n=== 1. MULTI-FIELD MATCH ===")
    res = search.search(q="myntra", limit=20)
    check("store name matches", res["total"] == 2 and all(r["store"] == "myntra" for r in res["results"]),
          str(titles(res["results"])))
    res = search.search(q="electronics", limit=20)
    check("category name matches", res["total"] >= 3
          and all(r["category"] == "Electronics" for r in res["results"]), str(titles(res["results"])))
    res = search.search(q="anouk", limit=20)
    check("brand matches", res["total"] == 1 and res["results"][0]["brand"].lower() == "anouk")
    res = search.search(q="power bank", limit=20)
    check("subcategory matches", res["total"] >= 1 and res["results"][0]["subcategory"] == "Power Bank")

    print("\n=== 2. MULTI-WORD RANKING ===")
    res = search.search(q="sony headphones", limit=20)
    top = res["results"][0] if res["results"] else {}
    check("deal matching every word ranks first", "Sony" in (top.get("title") or ""), str(titles(res["results"])))
    check("partial matches still returned", res["total"] >= 2, str(res["total"]))
    res = search.search(q="women kurta myntra", limit=20)
    check("3-word query keeps both kurtas", res["total"] == 2, str(titles(res["results"])))

    print("\n=== 3. TYPO TOLERANCE ===")
    res = search.search(q="headphnes", limit=20)
    check("'headphnes' finds headphones",
          res["total"] >= 2 and all(r["subcategory"] == "Headphones" for r in res["results"]),
          str(titles(res["results"])))
    res = search.search(q="samsng", limit=20)
    check("'samsng' finds Samsung", res["total"] == 1 and "Samsung" in res["results"][0]["title"],
          str(titles(res["results"])))
    res = search.search(q="prestge kadai", limit=20)
    check("typo inside multi-word query", res["total"] >= 1 and "Prestige" in res["results"][0]["title"])
    res = search.search(q="zzqxv", limit=20)
    check("gibberish returns nothing", res["total"] == 0, str(titles(res["results"])))
    res = search.search(q="shirt", limit=20)
    check("typo pass doesn't invent matches ('shirt')", res["total"] == 0, str(titles(res["results"])))

    print("\n=== 4. SYNONYMS & SORTS ===")
    res = search.search(q="kurti", limit=20)
    check("synonym 'kurti' finds kurtas", res["total"] == 2)
    for sort in search.SORTS:
        res = search.search(q="headphones", sort=sort, limit=20)
        check(f"sort={sort} works with a query", res["total"] >= 2, str(res["total"]))
    res = search.search(q="headphones", sort="price_low", limit=20)
    prices = [r["price"] for r in res["results"]]
    check("price_low honoured with a query", prices == sorted(prices), str(prices))

    print("\n=== 5. CATEGORY COUNTS ===")
    res = search.search(limit=5)
    counts = {c["name"]: c["count"] for c in res["categories"]}
    check("counts cover the whole result set", sum(counts.values()) == res["total"] == len(SAMPLES), str(counts))
    check("counts sorted by count desc",
          [c["count"] for c in res["categories"]] == sorted((c["count"] for c in res["categories"]), reverse=True))
    res = search.search(category="Women Fashion", limit=20)
    counts = {c["name"]: c["count"] for c in res["categories"]}
    check("category filter narrows results", res["total"] == 2, str(res["total"]))
    check("counts ignore the category filter itself", counts.get("Electronics", 0) >= 3, str(counts))
    res = search.search(q="flipkart", category="Footwear", limit=20)
    counts = {c["name"]: c["count"] for c in res["categories"]}
    check("counts follow the query", sum(counts.values()) == 3 and res["total"] == 1, str(counts))

    print("\n=== 6. SUGGEST ===")
    sug = search.suggest("kurta")
    check("suggest shape", set(sug) == {"query", "deals", "categories", "brands", "stores"}, str(list(sug)))
    check("suggest deals shaped like /api/deals",
          sug["deals"] and set(sug["deals"][0]) == set(search.search(limit=1)["results"][0]))
    check("suggest categories", sug["categories"] == [{"name": "Women Fashion", "count": 2}], str(sug["categories"]))
    check("suggest brands", {b["key"] for b in sug["brands"]} == {"anouk", "libas"}, str(sug["brands"]))
    check("suggest stores", sug["stores"] == [{"key": "myntra", "count": 2}], str(sug["stores"]))
    check("suggest respects limit", len(search.suggest("electronics", limit=2)["deals"]) == 2)
    check("empty query -> empty lists",
          search.suggest("") == {"query": "", "deals": [], "categories": [], "brands": [], "stores": []})
    check("1-char query -> empty lists", search.suggest("k")["deals"] == [])
    check("mid-keystroke typo ('headphn')", len(search.suggest("headphn")["deals"]) >= 2)
    scoped = search.suggest("headphones", channel_ids=[1001])
    check("suggest channel scoping", len(scoped["deals"]) == 1 and "boAt" in scoped["deals"][0]["title"],
          str(titles(scoped["deals"])))

    print("\n=== 7. ROUTES ===")
    app = FastAPI()
    app.include_router(deals_router.router)
    client = TestClient(app)
    r = client.get("/api/deals/suggest", params={"q": "kurta"})
    check("GET /api/deals/suggest not shadowed by /{deal_id}", r.status_code == 200 and "deals" in r.json(),
          str(r.status_code))
    r = client.get("/api/deals/suggest")
    check("suggest with no q is 200 + empty", r.status_code == 200 and r.json()["deals"] == [])
    r = client.get("/api/deals", params={"q": "headphones"})
    check("/api/deals has categories", r.status_code == 200 and r.json()["categories"][0]["name"] == "Electronics")

    # No visitor accounts: a signed-in cookie must not narrow what anyone sees.
    app.dependency_overrides[auth.optional_user] = lambda: {"id": 42}
    body = client.get("/api/deals/suggest", params={"q": "headphones"}).json()
    check("everyone sees every channel's deals", len(body["deals"]) == 2, str(titles(body["deals"])))

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
