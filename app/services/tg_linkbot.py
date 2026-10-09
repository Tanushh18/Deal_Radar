"""Telegram link generator: send the DealRadar bot a store link, get your Cuelinks affiliate link back.

Telegram calls our webhook for every message sent to the bot. Each store URL in the
message (shortlinks like fkrt.to / amzn.to are followed to the real product page first)
goes through the same converter the Buy buttons use, tagged subid=telegrambot, so
Cuelinks reports show these separately. A link that can't be converted (store not
approved, API down) is sent back as the plain store link with a note.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import re
import time
from collections import defaultdict, deque
from typing import Any, Deque, Dict, List

import httpx

from ..config import settings
from . import affiliate, links, parser

log = logging.getLogger("dealradar.linkbot")

API = "https://api.telegram.org"
MAX_LINKS = 5
CHAT_LIMIT = (15, 60)   # messages per chat per window (seconds)
_URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_recent: Dict[int, Deque[float]] = defaultdict(deque)

HELP = ("Send me a link from Amazon, Flipkart, Myntra, Ajio or another store and I'll reply with "
        "your affiliate link. Short links (amzn.to, fkrt.to...) work too.")


def enabled() -> bool:
    return bool(settings.tg_bot_token and affiliate.enabled())


def secret_token() -> str:
    """Telegram echoes this in a header on every webhook call, proving the call is Telegram's."""
    return hmac.new(settings.secret_key.encode("utf-8"), b"tg-linkbot", hashlib.sha256).hexdigest()[:48]


def valid_secret(header: str) -> bool:
    return hmac.compare_digest(header or "", secret_token())


def _allowed(chat_id: int) -> bool:
    now, window = time.time(), _recent[chat_id]
    while window and now - window[0] > CHAT_LIMIT[1]:
        window.popleft()
    if len(window) >= CHAT_LIMIT[0]:
        return False
    window.append(now)
    return True


async def _send(chat_id: int, text: str) -> None:
    url = f"{API}/bot{settings.tg_bot_token}/sendMessage"
    async with httpx.AsyncClient(timeout=15.0) as client:
        await client.post(url, json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True})


async def _store_url(url: str, client: httpx.AsyncClient) -> str:
    """The product's own store URL behind whatever the user sent ('' if it isn't a store)."""
    url = url.rstrip(").,]")
    inner = links.unwrap(url) or url
    if not links.is_store_site(inner):
        inner = await links.resolve(inner, client) or ""
    return parser.clean_url(inner) if inner else ""


async def make_links(text: str) -> List[str]:
    """One reply line per store link found in `text`."""
    urls = _URL_RE.findall(text or "")[:MAX_LINKS]
    lines: List[str] = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/122.0 Safari/537.36"}
    async with httpx.AsyncClient(follow_redirects=False, timeout=links.HOP_TIMEOUT, headers=headers) as client:
        for raw in urls:
            try:
                store_url = await _store_url(raw, client)
            except Exception as exc:  # noqa: BLE001 — one bad link must not sink the rest
                log.warning("Couldn't open %s: %s", raw, exc)
                store_url = ""
            if not store_url:
                lines.append(f"Not a store link I know: {raw}")
                continue
            out = await affiliate.convert(store_url, "telegrambot")
            log.info("CUELINKS TELEGRAM BOT | AFFILIATED=%s | LINK=%s", "YES" if out else "NO (PLAIN STORE LINK)", out or store_url)
            lines.append(out if out else f"{store_url}\n(no affiliate link available for this store yet)")
    return lines


async def handle_update(update: Dict[str, Any]) -> None:
    message = update.get("message") or {}
    chat = message.get("chat") or {}
    text = message.get("text") or message.get("caption") or ""
    chat_id = chat.get("id")
    if not chat_id or chat.get("type") != "private" or not text:
        return
    if text.startswith(("/start", "/help")) or not _URL_RE.search(text):
        await _send(chat_id, HELP)
        return
    if not _allowed(chat_id):
        await _send(chat_id, "Too many links at once — try again in a minute.")
        return
    lines = await make_links(text)
    await _send(chat_id, "\n\n".join(lines) if lines else HELP)


async def register_webhook() -> None:
    """Point Telegram at /api/telegram/linkbot (best-effort, safe to repeat on every start)."""
    if not (enabled() and settings.public_url):
        return
    url = f"{API}/bot{settings.tg_bot_token}/setWebhook"
    body = {"url": f"{settings.public_url}/api/telegram/linkbot", "secret_token": secret_token(),
            "allowed_updates": ["message"], "drop_pending_updates": True}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=body)
        log.info("TELEGRAM LINK BOT WEBHOOK: %s", "SET" if resp.json().get("ok") else f"FAILED {resp.text[:160]}")
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("TELEGRAM LINK BOT WEBHOOK FAILED: %s", exc)
