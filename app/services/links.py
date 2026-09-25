"""Resolve deal shortlinks to the real store page.

About half of all posts link through an affiliate shortener (bitli.in,
bilty.co, fkrt.site, myntr.in, pturl.in, …) instead of the store. Left as-is,
the "store" is recorded as `bitli` and — worse — the same product posted by
eight channels through eight different shortlinks becomes eight cards, because
there's no ASIN / Flipkart pid to match them on. Corroboration across channels
is the strongest quality signal we have, so this is what makes it work.

Two ways to reach the store URL, cheapest first:
  * unwrap — many trackers carry the destination in the URL itself
    (`linkredirect.in/…?dl=https://flipkart…`, `urlgeni.us/https://amazon…`),
    so no request is needed at all;
  * follow redirects — one GET per hop, never reading a body, each hop
    re-checked by the same SSRF guard the link prober uses.
"""
from __future__ import annotations

import asyncio
import re
from collections import OrderedDict
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx

from . import parser

MAX_HOPS = 6
HOP_TIMEOUT = 6.0
CONCURRENCY = 8
_CACHE_MAX = 4000
_cache: "OrderedDict[str, Optional[str]]" = OrderedDict()

# Real store sites — a URL on one of these is a destination, not a hop.
# (taxonomy.STORE_DOMAINS also lists each store's shorteners, which are hops.)
STORE_SITES = (
    "amazon.in", "amazon.com", "flipkart.com", "myntra.com", "ajio.com", "meesho.com",
    "jiomart.com", "tatacliq.com", "nykaa.com", "croma.com", "reliancedigital.in",
    "snapdeal.com", "shopsy.in", "firstcry.com", "bigbasket.com", "zeptonow.com",
    "blinkit.com", "swiggy.com", "pharmeasy.in", "boat-lifestyle.com", "puma.com",
    "adidas.co.in",
)

# Hosts that are never a place to buy anything.
SOCIAL_HOSTS = (
    "t.me", "telegram.me", "telegram.dog", "youtube.com", "youtu.be", "instagram.com",
    "whatsapp.com", "wa.me", "facebook.com", "fb.me", "twitter.com", "x.com",
    "play.google.com", "apps.apple.com", "forms.gle", "docs.google.com",
)

# Query keys trackers use to carry the destination URL.
_WRAPPER_KEYS = (
    "dl", "url", "u", "o", "r", "redirect", "redirect_url", "redirecturl", "target", "dest",
    "destination", "ued", "link", "af_web_dp", "af_ios_url", "af_android_url", "deep_link_value",
)

# A single product's page, per store. Anything else on a store site is a
# listing (search, brand, category, sale page) — a round-up, not one deal.
_PRODUCT_PAGE = {
    "amazon": re.compile(r"/(?:dp|gp/product|gp/aw/d|d)/[A-Z0-9]{10}", re.IGNORECASE),
    "flipkart": re.compile(r"/p/itm", re.IGNORECASE),
    "shopsy": re.compile(r"/p/itm", re.IGNORECASE),
    "myntra": re.compile(r"/\d{6,}(?:/buy)?/?$"),
    "ajio": re.compile(r"/p/[0-9a-z_]+", re.IGNORECASE),
    "nykaa": re.compile(r"/p/\d+"),
    "meesho": re.compile(r"/p/[0-9a-z]+", re.IGNORECASE),
    "tatacliq": re.compile(r"/p-mp\d+", re.IGNORECASE),
}


def _host(url: str) -> str:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""
    return host[4:] if host.startswith("www.") else host


def _on(host: str, domains: Iterable[str]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_store_site(url: str) -> bool:
    return _on(_host(url), STORE_SITES)


def is_social(url: str) -> bool:
    return _on(_host(url), SOCIAL_HOSTS)


def is_product_page(url: str) -> Optional[bool]:
    """True/False on a store site we know the shape of; None when we can't tell."""
    if not url or not is_store_site(url):
        return None
    pattern = _PRODUCT_PAGE.get(parser.detect_store(url))
    if pattern is None:
        return None
    try:
        path = urlparse(url).path
    except ValueError:
        return None
    return bool(pattern.search(path))


def unwrap(url: str) -> Optional[str]:
    """A store URL embedded in a tracking URL, found without any request."""
    try:
        parts = urlparse(url)
    except ValueError:
        return None
    candidates: List[str] = []

    # indiadesire: /Redirect?redirectpid1=<ASIN>&store=amazon
    params = {k.lower(): v for k, v in parse_qs(parts.query).items()}
    if _host(url).endswith("indiadesire.com") and params.get("store", [""])[0].lower() == "amazon":
        pid = params.get("redirectpid1", [""])[0]
        if re.fullmatch(r"[A-Z0-9]{10}", pid or "", re.IGNORECASE):
            candidates.append(f"https://www.amazon.in/dp/{pid.upper()}")

    for key in _WRAPPER_KEYS:
        for value in params.get(key, []):
            for _ in range(2):  # sometimes double-encoded
                if value.lower().startswith(("http://", "https://")):
                    break
                value = unquote(value)
            if value.lower().startswith(("http://", "https://")):
                candidates.append(value)

    # urlgeni.us/https://www.amazon.in/dp/… — the destination is the path.
    inner = re.search(r"/(https?://.+)$", url[len(parts.scheme) + 3:] if parts.scheme else url)
    if inner:
        candidates.append(inner.group(1))

    for candidate in candidates:
        if is_store_site(candidate):
            return candidate
        deeper = unwrap(candidate)
        if deeper:
            return deeper
    return None


def _remember(url: str, final: Optional[str]) -> Optional[str]:
    _cache[url] = final
    _cache.move_to_end(url)
    while len(_cache) > _CACHE_MAX:
        _cache.popitem(last=False)
    return final


async def resolve(url: str, client: httpx.AsyncClient) -> Optional[str]:
    """The store URL behind `url`, or None if it doesn't lead to a store."""
    if not url:
        return None
    if url in _cache:
        _cache.move_to_end(url)
        return _cache[url]
    from .ingest import _resolve_is_safe  # local: ingest imports this module

    current = url
    for _ in range(MAX_HOPS):
        current = unwrap(current) or current
        if is_store_site(current):
            return _remember(url, current)
        if not await _resolve_is_safe(current):
            break
        try:
            async with client.stream("GET", current) as resp:
                location = resp.headers.get("location")
                if resp.status_code in (301, 302, 303, 307, 308) and location:
                    current = urljoin(current, location)
                    continue
        except (httpx.HTTPError, asyncio.TimeoutError, ValueError):
            pass  # a dead shortener is not evidence of anything; keep the post as-is
        break
    return _remember(url, None)


def needs_resolving(deal: Dict[str, Any]) -> bool:
    """Only links that didn't already give us a real product id or store page."""
    url = deal.get("url") or ""
    if not url or is_social(url):
        return False
    key = str(deal.get("product_key") or "")
    return ":t:" in key or ":u:" in key or not is_store_site(url)


async def resolve_deals(deals: List[Dict[str, Any]]) -> int:
    """Resolve every deal's shortlink in place (concurrently). Returns how many resolved."""
    todo = [d for d in deals if needs_resolving(d)]
    if not todo:
        return 0
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0 Safari/537.36"
        )
    }
    semaphore = asyncio.Semaphore(CONCURRENCY)
    resolved = 0

    async with httpx.AsyncClient(follow_redirects=False, timeout=HOP_TIMEOUT, headers=headers) as client:

        async def one(deal: Dict[str, Any]) -> None:
            nonlocal resolved
            async with semaphore:
                try:
                    final = await asyncio.wait_for(resolve(deal["url"], client), timeout=HOP_TIMEOUT * 2)
                except asyncio.TimeoutError:
                    return
            if final:
                parser.rebase_on_url(deal, final)
                resolved += 1

        await asyncio.gather(*(one(d) for d in todo), return_exceptions=True)
    return resolved
