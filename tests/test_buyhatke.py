"""BuyHatke history: page parsing, which URL gets looked up, downsampling,
caching and the block pause — all offline (the HTTP call is stubbed).

Run:  python -m tests.test_buyhatke
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("DB_PATH", os.path.join(tempfile.mkdtemp(), "test_buyhatke.db"))
os.environ.setdefault("SECRET_KEY", "test-secret-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app import db  # noqa: E402
from app.services import buyhatke  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  {PASS if condition else FAIL} {label}" + (f"  — {detail}" if detail and not condition else ""))
    if not condition:
        failures.append(label)


PAGE = ('...predictedData:{history:[{from:"2024-11-09 01:10:26",to:"2024-11-12 00:20:58",price:2099},'
        '{from:"2024-11-13 00:01:31",to:"2025-01-11 00:05:16",price:1499},'
        '{from:"2025-01-12 01:40:21",to:"2025-01-12 01:40:21",price:1404}]}...')


class FakeResponse:
    def __init__(self, status: int, text: str, url: str = "https://buyhatke.com/some-product-price-in-india-1"):
        self.status_code, self.text, self.url = status, text, url


def stub(responses):
    calls = []

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls.append(url)
            return responses.pop(0)

    buyhatke.httpx.AsyncClient = Client
    return calls


def main() -> int:
    db.connect()
    buyhatke.MIN_GAP = 0

    print("\n=== 1. PARSING ===")
    pts = buyhatke.parse(PAGE)
    check("from/to pairs become points (duplicate stamp collapses)", len(pts) == 5, str(pts))
    check("oldest first", pts == sorted(pts))
    check("prices read", [p for _, p in pts] == [2099, 2099, 1499, 1499, 1404])
    check("no history -> no points", buyhatke.parse("<html>nothing here</html>") == [])

    print("\n=== 2. WHICH URL IS LOOKED UP ===")
    check("amazon ASIN -> canonical amazon.in/dp",
          buyhatke.store_url({"product_key": "amazon:B07PR1CL3S", "url": "https://amzn.to/x"})
          == "https://www.amazon.in/dp/B07PR1CL3S")
    check("amazon without an ASIN -> skipped", buyhatke.store_url({"product_key": "amazon:t:abc", "url": "https://amzn.to/y"}) is None)
    check("flipkart -> resolved store page, query stripped",
          buyhatke.store_url({"product_key": "flipkart:itm1", "url": "https://fkrt.to/a",
                              "resolved_url": "https://www.flipkart.com/x/p/itm1?pid=1&affid=me"})
          == "https://www.flipkart.com/x/p/itm1")
    check("short link only -> skipped", buyhatke.store_url({"product_key": "x:u:1", "url": "https://bitli.in/a"}) is None)

    print("\n=== 3. DOWNSAMPLING KEEPS THE EXTREMES ===")
    many = [(float(i), 1000.0 + (i % 50)) for i in range(2000)]
    many[1234] = (1234.0, 1.0)
    many[777] = (777.0, 99999.0)
    small = buyhatke._downsample(many)
    check("capped", len(small) <= buyhatke.MAX_POINTS + 3, str(len(small)))
    check("lowest kept", (1234.0, 1.0) in small)
    check("highest kept", (777.0, 99999.0) in small)
    check("latest kept", small[-1] == many[-1])

    print("\n=== 4. FETCH, CACHE, NO-DATA, BLOCK ===")
    deal = {"product_key": "amazon:B07PR1CL3S"}
    calls = stub([FakeResponse(200, PAGE)])
    got = asyncio.run(buyhatke.history(deal))
    check("fetched and parsed", len(got) == 5, str(got))
    check("asked BuyHatke's public page for the canonical URL",
          calls == ["https://buyhatke.com/https://www.amazon.in/dp/B07PR1CL3S"], str(calls))
    calls = stub([])
    check("second view served from memory", len(asyncio.run(buyhatke.history(deal))) == 5 and not calls)
    check("cached() sees it without fetching", len(buyhatke.cached(deal)) == 5)

    other = {"product_key": "flipkart:itm9", "resolved_url": "https://www.flipkart.com/y/p/itm9"}
    stub([FakeResponse(200, "<html>no data</html>")])
    check("no data -> empty", asyncio.run(buyhatke.history(other)) == [])
    calls = stub([])
    asyncio.run(buyhatke.history(other))
    check("no-data result cached too (not re-asked)", not calls)

    blocked = {"product_key": "flipkart:itm10", "resolved_url": "https://www.flipkart.com/z/p/itm10"}
    stub([FakeResponse(403, "")])
    check("blocked -> empty", asyncio.run(buyhatke.history(blocked)) == [])
    check("blocked -> paused", buyhatke.status()["paused_for_seconds"] > 0)
    calls = stub([])
    fresh = {"product_key": "flipkart:itm11", "resolved_url": "https://www.flipkart.com/w/p/itm11"}
    asyncio.run(buyhatke.history(fresh))
    check("while paused, nothing is requested", not calls)
    check("but cached products still show", len(asyncio.run(buyhatke.history(deal))) == 5)

    print("\n=== 5. NEVER STORED IN THE DATABASE ===")
    tables = [r["name"] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")]
    check("no BuyHatke table exists", not any("hatke" in t.lower() for t in tables), str(tables))
    check("no price_history rows written", db.query_one("SELECT COUNT(*) c FROM price_history")["c"] == 0)

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
