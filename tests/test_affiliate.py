"""Cuelinks affiliate links on Buy.

Run:  python -m tests.test_affiliate
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="", TG_BOT_TOKEN="", TG_POST_CHANNEL="",
                  CUELINK_API="test-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.services import affiliate  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(name, ok):
    print(f"  {PASS if ok else FAIL} {name}")
    if not ok:
        failures.append(name)


class FakeClient:
    reply = (200, {"affiliate_url": "https://linksredirect.com/?cid=1&source=linkkit&url=x"})
    calls = []

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False

    async def get(self, url, params=None, headers=None):
        FakeClient.calls.append((params, headers))
        return httpx.Response(FakeClient.reply[0], json=FakeClient.reply[1])


async def main():
    affiliate.httpx.AsyncClient = FakeClient
    deal = {"id": "d1", "url": "https://www.flipkart.com/x/p/itmabc?pid=ABC", "title": "X"}

    out = await affiliate.buy_url(deal, "web")
    check("returns the Cuelinks link", out.startswith("https://linksredirect.com/"))
    check("key sent as header, URL and subid as params",
          FakeClient.calls[0][1]["token"] == "test-key" and FakeClient.calls[0][0]["subid"] == "web")
    await affiliate.buy_url(deal, "web")
    check("repeat tap is served from cache", len(FakeClient.calls) == 1)

    FakeClient.reply = (403, {"error": "forbidden"})
    out = await affiliate.buy_url({"id": "d2", "url": "https://www.myntra.com/shoes/12345678"}, "app")
    check("no approved campaign -> plain store link", out == "https://www.myntra.com/shoes/12345678")

    n = len(FakeClient.calls)
    out = await affiliate.buy_url({"id": "d3", "url": "https://t.me/somechannel/1"}, "app")
    check("non-store link is never sent to Cuelinks", len(FakeClient.calls) == n and "t.me" in out)

    affiliate.settings.cuelinks_api_key = ""
    out = await affiliate.buy_url({"id": "d4", "url": "https://www.ajio.com/p/abc123"}, "app")
    check("no key configured -> plain link, no request", len(FakeClient.calls) == n and out.endswith("/p/abc123"))


asyncio.run(main())
print("FAILED" if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
