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


# --- Store photos for text-only posts ---------------------------------------
# About a quarter of posts are just "Product @price + link" with no photo, and
# the photo gate drops them. Once the shortlink is resolved we know the product:
#   * Amazon — its image CDN serves the photo by ASIN: one image request, never
#     a product page (Amazon pages stay unscraped). An unknown ASIN comes back
#     as a tiny placeholder GIF, hence the size check.
#   * Flipkart / Shopsy / Myntra — the product page names its photo in an
#     og:image tag near the top; we read only until we find it.
# Ajio and Meesho turn servers away (403), so they're not tried.
AMAZON_IMAGE_URL = "https://m.media-amazon.com/images/P/{asin}.01._SCLZZZZZZZ_.jpg"
_MIN_IMAGE_BYTES = 1000
_PAGE_READ_LIMIT = 450_000   # Myntra's tag sits ~220KB in; Flipkart's ~80KB
_PAGE_IMAGE_HOSTS = ("flipkart.com", "shopsy.in", "myntra.com")
_PAGE_IMAGE_TIMEOUT = 8.0
_MOBILE_UA = ("Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36")
_OG_IMAGE_RE = re.compile(
    r"""<meta[^>]+(?:property|name)=["'](?:og:image|twitter:image)["'][^>]*content=["']([^"']+)["']"""
    r"""|<meta[^>]+content=["']([^"']+)["'][^>]*(?:property|name)=["'](?:og:image|twitter:image)["']""",
    re.IGNORECASE,
)
_images: "OrderedDict[str, str]" = OrderedDict()   # product key -> photo url ("" = none)


def amazon_asin(deal: Dict[str, Any]) -> Optional[str]:
    key = deal.get("product_key") or ""
    return key.split(":", 1)[1] if key.startswith("amazon:") else None


def _page_host(url: str) -> Optional[str]:
    """The store host we may read a photo from (never one in SCRAPE_SKIP_STORES)."""
    from ..config import settings  # local: keep this module importable without app config
    host = (urlparse(url).hostname or "").lower()
    match = next((h for h in _PAGE_IMAGE_HOSTS if host == h or host.endswith("." + h)), None)
    return match if match and parser.detect_store(url) not in settings.scrape_skip_stores else None


def _tidy_image(url: str) -> str:
    """Card-sized, https version of a store's photo URL."""
    url = url.replace("&amp;", "&").strip()
    if url.startswith("//"):
        url = "https:" + url
    url = re.sub(r"^http://", "https://", url)
    # Flipkart/Shopsy: /image/{w}/{h}/… (sometimes a literal {@width}/{@height} template).
    url = re.sub(r"/image/(?:\{@width\}|\d+)/(?:\{@height\}|\d+)/", "/image/416/416/", url)
    # Myntra: h_1440,q_75,w_1080 → half size.
    url = re.sub(r"h_\d+,q_(\d+),w_\d+", r"h_720,q_\1,w_540", url)
    return url


def image_from_page(html: str) -> str:
    match = _OG_IMAGE_RE.search(html or "")
    return _tidy_image(match.group(1) or match.group(2)) if match else ""


async def _page_image(client: httpx.AsyncClient, url: str) -> str:
    """The og:image of a Flipkart/Shopsy/Myntra product page, reading as little as possible."""
    async with client.stream("GET", url) as r:
        if r.status_code != 200 or not _page_host(str(r.url)):
            return ""
        seen = ""
        async for chunk in r.aiter_text():
            seen += chunk
            found = image_from_page(seen)
            if found:
                return found
            if len(seen) > _PAGE_READ_LIMIT:
                break
    return ""


async def _amazon_image(client: httpx.AsyncClient, asin: str) -> str:
    url = AMAZON_IMAGE_URL.format(asin=asin)
    r = await client.get(url)
    ok = r.status_code == 200 and r.headers.get("content-type", "").startswith("image/") and len(r.content) >= _MIN_IMAGE_BYTES
    return url if ok else ""


async def fill_store_images(deals: List[Dict[str, Any]]) -> int:
    """Give photo-less deals their product photo from the store. Returns how many got one."""
    todo = []
    for deal in deals:
        if deal.get("image_url"):
            continue
        asin = amazon_asin(deal)
        page = deal.get("resolved_url") or deal.get("url") or ""
        if asin:
            todo.append((deal, deal["product_key"], "amazon", asin))
        elif _page_host(page) and is_product_page(page):
            todo.append((deal, deal.get("product_key") or page, "page", page))
    if not todo:
        return 0
    semaphore = asyncio.Semaphore(CONCURRENCY)
    filled = 0

    async with httpx.AsyncClient(timeout=_PAGE_IMAGE_TIMEOUT, follow_redirects=True,
                                 headers={"User-Agent": _MOBILE_UA}) as client:

        async def one(deal: Dict[str, Any], key: str, kind: str, target: str) -> None:
            nonlocal filled
            if key not in _images:
                async with semaphore:
                    try:
                        found = await (_amazon_image(client, target) if kind == "amazon" else _page_image(client, target))
                    except (httpx.HTTPError, UnicodeDecodeError):
                        return  # network blip: try again on the next repost, don't cache
                _images[key] = found
                while len(_images) > _CACHE_MAX:
                    _images.popitem(last=False)
            if _images[key]:
                deal["image_url"] = _images[key]
                filled += 1

        await asyncio.gather(*(one(*item) for item in todo), return_exceptions=True)
    return filled
