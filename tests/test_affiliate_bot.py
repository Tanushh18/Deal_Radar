"""Affiliate group bot: pasted Amazon / Flipkart links come back as our affiliate links.

Run:  python -m pytest tests/test_affiliate_bot.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", TELEGRAM_API_ID="", TELEGRAM_API_HASH="", PUBLIC_URL="",
                  TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="", MONGODB_URI="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import affiliate_bot as bot, links  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def _client():
    return httpx.AsyncClient()


def test_extracts_plain_and_hidden_links():
    msg = {"text": "look https://www.amazon.in/dp/B0ABCDEFGH?tag=x, and 😀 https://fkrt.cc/abc!",
           "entities": [{"type": "url", "offset": 5, "length": 43},
                        {"type": "text_link", "offset": 0, "length": 4, "url": "https://www.flipkart.com/p/itm1?pid=P1"}]}
    urls = bot.extract_urls(msg)
    assert "https://www.flipkart.com/p/itm1?pid=P1" in urls
    assert "https://www.amazon.in/dp/B0ABCDEFGH?tag=x" in urls
    assert "https://fkrt.cc/abc" in urls


def test_amazon_link_gets_our_tag(monkeypatch):
    monkeypatch.setattr(settings, "amazon_tag", "ours-21")
    store, link = _run(bot.affiliate_link("https://www.amazon.in/Some-Title/dp/B0ABCDEFGH/ref=sr_1?tag=theirs-21&th=1", _client()))
    assert store == "amazon" and link == "https://www.amazon.in/dp/B0ABCDEFGH?tag=ours-21"


def test_flipkart_link_goes_through_earnkaro(monkeypatch):
    monkeypatch.setattr(settings, "earnkaro_id", "42")
    monkeypatch.setattr(settings, "earnkaro_flipkart_retailer", "2276")
    store, link = _run(bot.affiliate_link("https://www.flipkart.com/x/p/itm123?pid=MOBABC&lid=LST1&utm_source=a", _client()))
    assert store == "flipkart"
    assert link.startswith("https://trackingv3.linkredirect.in/visitretailer/2276?id=42&dl=")
    assert "pid%3DMOBABC" in link and "utm_source" not in link and "lid" not in link


def test_short_link_is_resolved_first(monkeypatch):
    async def fake_resolve(url, client):
        return "https://www.amazon.in/dp/B0ABCDEFGH"
    monkeypatch.setattr(links, "resolve", fake_resolve)
    monkeypatch.setattr(settings, "amazon_tag", "ours-21")
    _, link = _run(bot.affiliate_link("https://amzn.to/3abc", _client()))
    assert link.endswith("/dp/B0ABCDEFGH?tag=ours-21")


def test_other_stores_and_unopenable_short_links_are_ignored(monkeypatch):
    async def no_resolve(url, client):
        return None
    monkeypatch.setattr(links, "resolve", no_resolve)
    assert _run(bot.affiliate_link("https://www.myntra.com/kurta/12345678/buy", _client())) is None
    assert _run(bot.affiliate_link("https://www.amazon.com/dp/B0ABCDEFGH", _client())) is None
    assert _run(bot.affiliate_link("https://fkrt.cc/blocked", _client())) is None


def _no_shortener(monkeypatch):
    async def same(link, client):
        return link
    monkeypatch.setattr(bot, "shorten", same)


def test_reply_has_copy_and_open_buttons(monkeypatch):
    _no_shortener(monkeypatch)
    monkeypatch.setattr(settings, "amazon_tag", "ours-21")
    payload = _run(bot.build_reply({"text": "https://www.amazon.in/dp/B0ABCDEFGH nothing https://www.myntra.com/a/12345678/buy"}, _client()))
    link = "https://www.amazon.in/dp/B0ABCDEFGH?tag=ours-21"
    assert link in payload["text"]
    (copy_btn, open_btn), = payload["reply_markup"]["inline_keyboard"]
    assert copy_btn["copy_text"]["text"] == link and open_btn["url"] == link


def test_long_link_drops_copy_button_but_stays_in_text(monkeypatch):
    _no_shortener(monkeypatch)
    monkeypatch.setattr(settings, "earnkaro_id", "42")
    url = "https://www.flipkart.com/" + "a" * 250 + "/p/itm1?pid=P1"
    payload = _run(bot.build_reply({"text": url}, _client()))
    (only,), = payload["reply_markup"]["inline_keyboard"]
    assert "url" in only and "copy_text" not in only
    assert "<code>" in payload["text"]


def test_reply_uses_the_short_link(monkeypatch):
    async def short(link, client):
        return "https://tinyurl.com/abc"
    monkeypatch.setattr(bot, "shorten", short)
    monkeypatch.setattr(settings, "earnkaro_id", "42")
    payload = _run(bot.build_reply({"text": "https://www.flipkart.com/x/p/itm1?pid=P1"}, _client()))
    assert "https://tinyurl.com/abc" in payload["text"] and "linkredirect" not in payload["text"]
    (copy_btn, open_btn), = payload["reply_markup"]["inline_keyboard"]
    assert copy_btn["copy_text"]["text"] == open_btn["url"] == "https://tinyurl.com/abc"


def test_shorten_falls_back_to_the_long_link():
    def down(request):
        return httpx.Response(500)
    def ok(request):
        return httpx.Response(200, text="https://tinyurl.com/xyz")
    long_link = "https://trackingv3.linkredirect.in/visitretailer/2276?id=42&dl=abc"
    assert _run(bot.shorten(long_link, httpx.AsyncClient(transport=httpx.MockTransport(down)))) == long_link
    assert _run(bot.shorten(long_link, httpx.AsyncClient(transport=httpx.MockTransport(ok)))) == "https://tinyurl.com/xyz"


def test_no_reply_without_store_links():
    assert _run(bot.build_reply({"text": "hello https://example.com"}, _client())) is None


def test_handle_message_replies_to_the_message_and_skips_bots(monkeypatch):
    monkeypatch.setattr(settings, "affiliate_bot_chats", set())
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    async def go():
        tg = httpx.AsyncClient(base_url="https://api.telegram.org/botT/", transport=httpx.MockTransport(handler))
        msg = {"message_id": 7, "chat": {"id": -100123}, "from": {"is_bot": False},
               "text": "https://www.amazon.in/dp/B0ABCDEFGH"}
        await bot.handle_message(msg, tg, _client())
        await bot.handle_message({**msg, "from": {"is_bot": True}}, tg, _client())
        monkeypatch.setattr(settings, "affiliate_bot_chats", {"-999"})
        await bot.handle_message(msg, tg, _client())
    _run(go())
    assert len(sent) == 1
    assert sent[0]["chat_id"] == -100123 and sent[0]["reply_parameters"]["message_id"] == 7
