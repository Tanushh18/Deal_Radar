"""Telegram link-generator bot.

Run:  python -m tests.test_linkbot
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", TELEGRAM_API_ID="", TELEGRAM_API_HASH="", TURSO_DATABASE_URL="",
                  TURSO_AUTH_TOKEN="", TG_BOT_TOKEN="123:abc", TG_POST_CHANNEL="", CUELINK_API="test-key",
                  PUBLIC_URL="https://x.example")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import telegram_bot  # noqa: E402
from app.services import affiliate, links, tg_linkbot  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(name, ok):
    print(f"  {PASS if ok else FAIL} {name}")
    if not ok:
        failures.append(name)


sent, converted = [], []


async def fake_send(chat_id, text):
    sent.append((chat_id, text))


async def fake_convert(url, subid=""):
    converted.append((url, subid))
    return "https://clnk.in/abc" if "amazon" in url else None


async def fake_resolve(url, client):
    return "https://www.flipkart.com/x/p/itmabc?pid=1&affid=someone" if "fkrt" in url else None


tg_linkbot._send, affiliate.convert, links.resolve = fake_send, fake_convert, fake_resolve


async def main():
    await tg_linkbot.handle_update({"message": {"chat": {"id": 1, "type": "private"}, "text": "/start"}})
    check("/start sends help", sent[-1][1] == tg_linkbot.HELP)

    await tg_linkbot.handle_update({"message": {"chat": {"id": 1, "type": "private"},
                                                "text": "look https://www.amazon.in/dp/B0ABCDEFGH?tag=other-21 and https://fkrt.to/abc and https://t.me/x"}})
    reply = sent[-1][1]
    check("store link gets the affiliate link", "https://clnk.in/abc" in reply)
    check("tagged subid=telegrambot", converted[0][1] == "telegrambot")
    check("foreign affiliate tag stripped before converting", "other-21" not in converted[0][0])
    check("shortlink resolved, then plain store link returned when store has no campaign",
          "flipkart.com/x/p/itmabc" in reply and "affid" not in reply and "no affiliate link available" in reply)
    check("non-store link is refused", "Not a store link I know: https://t.me/x" in reply)

    await tg_linkbot.handle_update({"message": {"chat": {"id": 2, "type": "group"}, "text": "https://www.amazon.in/dp/B0ABCDEFGH"}})
    check("groups are ignored", sent[-1][0] != 2)

    for _ in range(20):
        await tg_linkbot.handle_update({"message": {"chat": {"id": 3, "type": "private"}, "text": "https://www.amazon.in/dp/B0ABCDEFGH"}})
    check("per-chat rate limit kicks in", "Too many links" in sent[-1][1])

    app = FastAPI()
    app.include_router(telegram_bot.router)
    with TestClient(app) as c:
        body = {"message": {"chat": {"id": 9, "type": "private"}, "text": "/start"}}
        check("webhook rejects a call without Telegram's secret",
              c.post("/api/telegram/linkbot", json=body).status_code == 403)
        r = c.post("/api/telegram/linkbot", json=body, headers={"X-Telegram-Bot-Api-Secret-Token": tg_linkbot.secret_token()})
        check("webhook accepts Telegram's secret", r.status_code == 200)


asyncio.run(main())
print("FAILED" if failures else "ALL PASSED")
sys.exit(1 if failures else 0)
