"""Telegram group bot: paste an Amazon or Flipkart link, get our affiliate link back at once.

The bot (from @BotFather, token in TG_AFFILIATE_BOT_TOKEN) sits in a group you add it to. Every
message there is scanned for links; each Amazon / Flipkart one is turned into the same link our
Buy button uses (links.with_affiliate: Amazon Associates tag, Flipkart via EarnKaro) and sent back
as a reply, with a "Copy link" button (Telegram's own copy-to-clipboard button) and an "Open" button.
Short links (amzn.to, fkrt.cc, …) are followed to the store page first, through the same
SSRF-guarded resolver the ingest pipeline uses. Links to other stores are ignored.

Plain long polling, so it needs no public URL and works behind any server. One poller per bot
token, so it is started only where the singletons run (app/main.py: the leader, or the user node).

In a group the bot only sees every message when its privacy mode is off (@BotFather -> /setprivacy
-> Disable) or when it is a group admin; otherwise Telegram shows it commands only.
"""
from __future__ import annotations

import asyncio
import html
import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import httpx

from ..config import settings
from . import links, parser, shortlinks, taxonomy

log = logging.getLogger("dealradar.affiliate_bot")

API = "https://api.telegram.org"
POLL_TIMEOUT = 30            # seconds Telegram holds a getUpdates call open
MAX_LINKS_PER_MESSAGE = 5    # a pasted list gets the first few answered, not a flood
COPY_TEXT_MAX = 256          # Telegram's limit for a copy_text button
URL_RE = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_TRAILING = ".,;:!?)]}>'\""
_STORE_LABEL = {"amazon": "Amazon", "flipkart": "Flipkart"}
_RESOLVE_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"}


def extract_urls(message: Dict[str, Any]) -> List[str]:
    """Every distinct link in a Telegram message: typed/pasted URLs and hidden "text link" targets."""
    text = message.get("text") or message.get("caption") or ""
    entities = message.get("entities") or message.get("caption_entities") or []
    found: List[str] = []
    for ent in entities:
        if ent.get("type") == "text_link" and ent.get("url"):
            found.append(ent["url"])
        elif ent.get("type") == "url":
            # Entity offsets are in UTF-16 code units.
            units = text.encode("utf-16-le")
            start, length = ent.get("offset", 0) * 2, ent.get("length", 0) * 2
            found.append(units[start:start + length].decode("utf-16-le", errors="ignore"))
    found.extend(URL_RE.findall(text))
    out: List[str] = []
    for url in found:
        url = url.rstrip(_TRAILING)
        if url and url not in out:
            out.append(url)
    return out


def store_of(url: str) -> Optional[str]:
    """"amazon" or "flipkart" when the link belongs to one of those stores (or its shorteners)."""
    host = links._host(url)
    for store in ("amazon", "flipkart"):
        if links._on(host, taxonomy.STORE_DOMAINS.get(store, ())):
            return store
    return None


async def affiliate_link(url: str, client: httpx.AsyncClient) -> Optional[Tuple[str, str]]:
    """(store, our short affiliate link) for an Amazon / Flipkart link; None for anything else or
    when a short link could not be opened. The short link is on our own domain (services/shortlinks.py);
    the full affiliate link is used when there is no public URL or the product has no id in its link."""
    if not store_of(url):
        return None
    store_page = url if links.is_store_site(url) else None
    if store_page is None:
        store_page = links.unwrap(url) or await links.resolve(url, client)
    if not store_page or not links.is_store_site(store_page):
        return None
    plain = parser.clean_url(store_page)
    store = store_of(plain)
    if store == "amazon" and links._host(plain) != "amazon.in":
        return None   # the Associates tag is for amazon.in only
    if store not in _STORE_LABEL:
        return None
    return store, shortlinks.short_url(store, plain) or links.with_affiliate(plain)


def _keyboard(rows: List[Tuple[str, str]]) -> Dict[str, Any]:
    """One row of buttons per link: Copy (when it fits Telegram's limit) and Open."""
    keyboard = []
    for label, link in rows:
        buttons: List[Dict[str, Any]] = []
        if len(link) <= COPY_TEXT_MAX:
            buttons.append({"text": f"📋 Copy {label} link", "copy_text": {"text": link}})
        buttons.append({"text": f"🛍 Open {label}", "url": link})
        keyboard.append(buttons)
    return {"inline_keyboard": keyboard}


async def build_reply(message: Dict[str, Any], client: httpx.AsyncClient) -> Optional[Dict[str, Any]]:
    """The sendMessage payload (minus chat/reply ids) for a message, or None if nothing to answer."""
    urls = [u for u in extract_urls(message) if store_of(u)][:MAX_LINKS_PER_MESSAGE]
    if not urls:
        return None
    results = await asyncio.gather(*(affiliate_link(u, client) for u in urls), return_exceptions=True)
    lines: List[str] = []
    rows: List[Tuple[str, str]] = []
    failed = 0
    for result in results:
        if not isinstance(result, tuple):
            failed += 1
            continue
        store, link = result
        label = _STORE_LABEL[store]
        rows.append((label, link))
        lines.append(f"<b>{label}</b> affiliate link (tap to copy):\n<code>{html.escape(link)}</code>")
    if failed:
        lines.append("⚠️ Couldn't open "
                     + ("a short link" if failed == 1 else f"{failed} short links")
                     + " — paste the full amazon.in / flipkart.com product link.")
    if not lines:
        return None
    payload: Dict[str, Any] = {"text": "\n\n".join(lines), "parse_mode": "HTML",
                               "link_preview_options": {"is_disabled": True}}
    if rows:
        payload["reply_markup"] = _keyboard(rows)
    return payload


def _allowed_chat(chat_id: Any) -> bool:
    allowed = settings.affiliate_bot_chats
    return not allowed or str(chat_id) in allowed


async def handle_message(message: Dict[str, Any], tg: httpx.AsyncClient, resolver: httpx.AsyncClient) -> None:
    if (message.get("from") or {}).get("is_bot"):
        return   # never answer bots (including ourselves)
    chat_id = (message.get("chat") or {}).get("id")
    if chat_id is None or not _allowed_chat(chat_id):
        return
    payload = await build_reply(message, resolver)
    if not payload:
        return
    payload.update(chat_id=chat_id, reply_parameters={"message_id": message["message_id"], "allow_sending_without_reply": True})
    resp = await tg.post("sendMessage", json=payload)
    if resp.status_code != 200:
        log.warning("sendMessage to %s failed: %s %s", chat_id, resp.status_code, resp.text[:200])


async def run() -> None:
    """Started from app/main.py: long-poll getUpdates for as long as the server runs."""
    token = settings.affiliate_bot_token
    if not token:
        return
    offset = 0
    async with httpx.AsyncClient(base_url=f"{API}/bot{token}/", timeout=POLL_TIMEOUT + 15) as tg, \
            httpx.AsyncClient(follow_redirects=False, timeout=links.HOP_TIMEOUT, headers=_RESOLVE_HEADERS) as resolver:
        try:
            await tg.post("deleteWebhook")   # a webhook would block getUpdates
        except httpx.HTTPError as exc:
            log.warning("Affiliate bot: deleteWebhook failed: %s", exc)
        log.info("Affiliate bot polling")
        while True:
            try:
                resp = await tg.post("getUpdates", json={"offset": offset, "timeout": POLL_TIMEOUT,
                                                         "allowed_updates": ["message", "channel_post"]})
                if resp.status_code == 409:   # another poller has this token
                    log.warning("Affiliate bot: another instance is polling this token")
                    await asyncio.sleep(15)
                    continue
                if resp.status_code != 200:
                    log.warning("Affiliate bot: getUpdates %s %s", resp.status_code, resp.text[:200])
                    await asyncio.sleep(10)
                    continue
                for update in resp.json().get("result", []):
                    offset = update["update_id"] + 1
                    message = update.get("message") or update.get("channel_post")
                    if not message:
                        continue
                    try:
                        await handle_message(message, tg, resolver)
                    except Exception as exc:  # noqa: BLE001
                        log.warning("Affiliate bot: message %s failed: %s", message.get("message_id"), exc)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("Affiliate bot poll failed: %s", exc)
                await asyncio.sleep(10)
