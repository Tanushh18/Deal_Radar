"""Cuelinks affiliate links, made on demand when someone taps "Buy".

Deal cards carry the plain store URL (see links.plain_url). The affiliate
version is only built at click time, server-side, so the API key never leaves
the server and the website, the mobile app and the Telegram button all earn
through one code path. Any failure — no key configured, a store without an
approved campaign, a timeout — falls back to the plain store URL, so Buy
never breaks.
"""
from __future__ import annotations

import logging
import re
import time
from collections import OrderedDict
from typing import Any, Dict, Optional, Tuple

import httpx

from ..config import settings
from . import links

log = logging.getLogger("dealradar.affiliate")

API_URL = "https://www.cuelinks.com/api/v2/links.json"
TIMEOUT = 5.0
HIT_TTL = 6 * 3600      # a converted link stays valid; reuse it
MISS_TTL = 10 * 60      # a store with no approved campaign: don't ask again for a while
_CACHE_MAX = 4000
_cache: "OrderedDict[Tuple[str, str], Tuple[float, Optional[str]]]" = OrderedDict()
_SUBID_RE = re.compile(r"[^A-Za-z0-9_-]")
_URL_KEYS = ("affiliate_url", "affiliateUrl", "url", "link", "shortened_url", "short_url")


def enabled() -> bool:
    return bool(settings.cuelinks_api_key)


def _find_link(data: Any) -> Optional[str]:
    """The affiliate URL in Cuelinks' JSON reply, wherever it is nested."""
    if isinstance(data, str):
        return data if data.startswith(("http://", "https://")) else None
    if isinstance(data, dict):
        for key in _URL_KEYS:
            found = _find_link(data.get(key))
            if found:
                return found
        for value in data.values():
            found = _find_link(value)
            if found:
                return found
    if isinstance(data, list):
        for value in data:
            found = _find_link(value)
            if found:
                return found
    return None


def _remember(key: Tuple[str, str], value: Optional[str]) -> Optional[str]:
    _cache[key] = (time.time() + (HIT_TTL if value else MISS_TTL), value)
    _cache.move_to_end(key)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return value


async def convert(url: str, subid: str = "") -> Optional[str]:
    """Cuelinks affiliate URL for a store URL, or None if it can't be made."""
    if not (enabled() and url and links.is_store_site(url)):
        return None
    subid = _SUBID_RE.sub("", subid)[:40]
    key = (url, subid)
    cached = _cache.get(key)
    if cached and cached[0] > time.time():
        return cached[1]
    params = {"url": url}
    if subid:
        params["subid"] = subid
    key_value = settings.cuelinks_api_key
    # Cuelinks documents a "token" header; its v2 examples use Authorization: Token token=…
    headers = {"token": key_value, "Authorization": f"Token token={key_value}", "Accept": "application/json"}
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.get(API_URL, params=params, headers=headers)
        if resp.status_code != 200:
            log.warning("Cuelinks link API returned %s for %s", resp.status_code, url)
            return _remember(key, None)
        return _remember(key, _find_link(resp.json()))
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("Cuelinks link API failed: %s", exc)
        return None   # transient: not cached, so the next tap retries


async def buy_url(deal: Dict[str, Any], subid: str = "") -> str:
    """Where Buy should send the shopper: affiliate link if possible, else the plain store link."""
    plain = links.plain_url(deal)
    return await convert(plain, subid) or plain
