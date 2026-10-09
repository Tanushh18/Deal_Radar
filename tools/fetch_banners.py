"""Capture the stores' live sale banners from their own sale pages and push them to the server.

How it finds a banner, per store:
  1. Open the store's home page in headless Chromium (mobile first, desktop if
     the mobile page is a bot wall). Note every link that points at a sale
     ("big-billion-days-store", "/events/greatindianfestival", "...-sale"...).
  2. Open the store's official sale/event pages: the known ones in STORES plus
     the sale links found on the home page.
  3. On each page, find the hero banner: the largest wide image (or
     background-image block) near the top of the page.
  4. Screenshot exactly that element (clip) as a JPEG. That screenshot is the
     banner: the store's own artwork with this year's dates, hosted by our
     server, so it never breaks when the store rotates its image URLs.
  5. Read exact sale dates from the page text ("9th - 17th Oct").

Everything is sent to /api/admin/reader/live-banners. Stores where nothing
usable is found are simply left out; the app shows its own branded calendar
card for them instead. Nothing comes from third-party sites or image search.

Run by .github/workflows/live-banners.yml during sale windows, or by hand:

    SITE_URL=https://your-app.example ADMIN_TOKEN=... python tools/fetch_banners.py
    python tools/fetch_banners.py --dry-run     # capture into banner-debug/, print, don't push
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

MOBILE_UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
             "Chrome/124.0 Mobile Safari/537.36")
DESKTOP_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0 Safari/537.36")
UA = DESKTOP_UA  # for our own server requests

# Each store's home page, its own domains (links/images must stay on them), and
# official sale pages worth trying even when the home page links nowhere useful.
STORES = {
    "amazon": {
        "home": "https://www.amazon.in/",
        "domains": ("amazon.in", "media-amazon.com"),
        "events": ["https://www.amazon.in/events/greatindianfestival"],
        "event_label": "Amazon Great Indian Festival",     # names banners from the pages in "events"
    },
    "flipkart": {
        "home": "https://www.flipkart.com/",
        "domains": ("flipkart.com", "flixcart.com"),
        "events": ["https://www.flipkart.com/big-billion-days-store"],
        "event_label": "Flipkart Big Billion Days",
    },
    "myntra": {
        "home": "https://www.myntra.com/",
        "domains": ("myntra.com", "myntassets.com"),
        "events": [],
    },
}

SALE_LINK = re.compile(
    r"big[-_ ]?billion|great[-_ ]?indian|festival|/events?/|[-_/]sale\b|sale[-_]|diwali|"
    r"end[-_ ]of[-_ ]reason|prime[-_ ]day|bbd|gif\b", re.I)
BOT_WALL = re.compile(r"robot|captcha|access denied|are you a human|site maintenance|something went wrong", re.I)
MAX_EVENT_PAGES = 3
MAX_PER_STORE = 2
MIN_SHOT_BYTES = 12_000       # smaller than this is almost always a blank/placeholder block
MAX_SHOT_BYTES = 450_000      # server limit is 800 KB; keeps the push small


# Runs in the page. Every wide, visible image or background-image block, with
# its position, size, the text naming it and the link around it.
_JS_CANDIDATES = r"""() => {
  const out = [];
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return ''; } };
  let idx = 0;
  const push = (el, src, alt) => {
    const r = el.getBoundingClientRect();
    if (r.width < 280 || r.height < 70) return;
    const ratio = r.width / r.height;
    if (ratio < 1.5 || ratio > 7) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none' || Number(cs.opacity) < 0.2) return;
    const a = el.closest('a');
    el.setAttribute('data-bn', String(idx));
    out.push({idx: idx++, src: abs(src), alt: (alt || '').trim(),
              label: a ? (a.getAttribute('aria-label') || a.title || '').trim() : '',
              href: a ? abs(a.getAttribute('href') || '') : '',
              x: r.left + scrollX, y: r.top + scrollY, w: r.width, h: r.height});
  };
  for (const img of document.images) {
    if (!img.complete || !img.naturalWidth) continue;
    push(img, img.currentSrc || img.src, img.alt);
  }
  for (const el of document.querySelectorAll('div, section, a, span, li, figure')) {
    const r = el.getBoundingClientRect();
    if (r.width < 280 || r.height < 70) continue;          // cheap size check before computed style
    const bg = getComputedStyle(el).backgroundImage || '';
    const m = /url\(["']?([^"')]+)/.exec(bg);
    if (m && !m[1].startsWith('data:')) push(el, m[1], el.getAttribute('aria-label') || el.title);
  }
  return out;
}"""

# Every link on the page, for finding the store's own sale pages.
_JS_LINKS = r"""() => Array.from(document.querySelectorAll('a[href]')).map(a => ({
  href: a.href, text: (a.innerText || a.getAttribute('aria-label') || '').trim().slice(0, 80)}))"""


def _on_domain(url: str, domains: tuple) -> bool:
    host = urlparse(url).netloc.lower()
    return any(host == d or host.endswith("." + d) for d in domains)


def _is_wall(page) -> bool:
    try:
        text = page.inner_text("body")
    except Exception:  # noqa: BLE001
        return True
    return len(text) < 400 or bool(BOT_WALL.search(page.title() or "")) or bool(BOT_WALL.search(text[:300]))


def _open(ctxs: list, url: str):
    """Open url in the first context (mobile/desktop) that isn't served a bot wall."""
    for ctx in ctxs:
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:  # noqa: BLE001 — some pages never go idle (live tickers); that's fine
                pass
            page.mouse.wheel(0, 900)          # wake lazy-loaded hero images
            page.wait_for_timeout(1500)
            page.mouse.wheel(0, -900)
            page.wait_for_timeout(1500)
            if not _is_wall(page):
                return page
            print(f"   {url}: bot wall / empty page ({page.title()!r})", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            print(f"   {url}: failed to load ({exc.__class__.__name__}: {str(exc)[:120]})", file=sys.stderr)
        page.close()
    return None


def _sale_links(page, store: str) -> list:
    """Links on the home page that point at the store's own sale pages."""
    cfg = STORES[store]
    seen, out = set(), []
    for link in page.evaluate(_JS_LINKS):
        href = link["href"].split("#")[0]
        if not href.startswith("https://") or not _on_domain(href, cfg["domains"]):
            continue
        if not SALE_LINK.search(f"{urlparse(href).path} {link['text']}"):
            continue
        key = urlparse(href)._replace(query="").geturl()
        if key in seen or key.rstrip("/") == cfg["home"].rstrip("/"):
            continue
        seen.add(key)
        out.append(href)
    return out


def _hero(cands: list, store: str) -> dict | None:
    """The hero banner: largest wide candidate near the top, on the store's own servers."""
    domains = STORES[store]["domains"]
    good = [c for c in cands
            if c["src"].startswith("https://") and _on_domain(c["src"], domains) and c["y"] < 1600
            and c["w"] >= 300 and 2.2 <= c["w"] / c["h"] <= 7]   # a strip, like the app's banner card
    if not good:
        return None
    # Among the full-width strips (at least 60% as wide as the widest), the topmost
    # one is the hero; mid-page promo strips sit lower.
    widest = max(c["w"] for c in good)
    return min((c for c in good if c["w"] >= 0.6 * widest), key=lambda c: c["y"])


def _landing_url(page_url: str, cfg: dict) -> str:
    """Where tapping the banner goes: the store's main sale page when the banner came from
    it or one of its sub-pages (not a long tracking URL), else the page itself, else home."""
    base = page_url.split("?")[0].rstrip("/")
    for event in cfg["events"]:
        if base == event.rstrip("/") or base.startswith(event.rstrip("/") + "/"):
            return event
    return page_url.split("#")[0] if _on_domain(page_url, cfg["domains"]) else cfg["home"]


def _name_for(page, store: str, hero: dict, sale_names: dict) -> str:
    """Prefer the sale's calendar name; else the hero's alt text; else the page title."""
    if sale_names.get(store):
        return sale_names[store]
    cfg = STORES[store]
    if cfg.get("event_label") and any(page.url.split("?")[0].rstrip("/") == e.rstrip("/") for e in cfg["events"]):
        return cfg["event_label"]
    alt = (hero.get("alt") or hero.get("label") or "").strip()
    if len(alt) > 5 and alt.lower() not in ("image", "banner"):
        return alt[:80]
    title = re.split(r"\s[|:\-–]\s", page.title() or "")[0].strip()
    return (title or f"{store.title()} sale")[:80]


def _capture(page, hero: dict) -> bytes | None:
    """JPEG screenshot of the hero element itself (Playwright scrolls it into view
    and crops to its box, so a banner wider than the window is still captured whole)."""
    loc = page.locator(f'[data-bn="{hero["idx"]}"]').first
    try:
        loc.scroll_into_view_if_needed(timeout=5000)
        page.wait_for_timeout(600)
        for quality in (82, 70, 55):
            shot = loc.screenshot(type="jpeg", quality=quality, timeout=15000)
            if len(shot) <= MAX_SHOT_BYTES:
                return shot if len(shot) >= MIN_SHOT_BYTES else None
    except Exception as exc:  # noqa: BLE001 — one bad element must not lose the other stores
        print(f"   element screenshot failed: {exc.__class__.__name__}: {str(exc)[:140]}", file=sys.stderr)
    return None


def scrape(sale_names: dict) -> tuple:
    from playwright.sync_api import sync_playwright
    from app.services.live_banners import extract_sales

    banners, sales = [], []
    year = datetime.now(timezone(timedelta(hours=5, minutes=30))).year
    os.makedirs("banner-debug", exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        common = {"locale": "en-IN", "timezone_id": "Asia/Kolkata", "extra_http_headers": {"Accept-Language": "en-IN,en;q=0.9"}}
        mobile = browser.new_context(user_agent=MOBILE_UA, viewport={"width": 412, "height": 915},
                                     device_scale_factor=2, is_mobile=True, has_touch=True, **common)
        desktop = browser.new_context(user_agent=DESKTOP_UA, viewport={"width": 1366, "height": 900}, **common)

        for store, cfg in STORES.items():
            print(f"== {store}", file=sys.stderr)
            found = []
            # 1. Home page: sale links to follow (and its text, for dates).
            home = _open([mobile, desktop], cfg["home"])
            links = []
            if home:
                links = _sale_links(home, store)
                sales += extract_sales(home.inner_text("body"), year)
                print(f"   home ok ({home.title()[:60]!r}); sale links: {links[:MAX_EVENT_PAGES]}", file=sys.stderr)
                home.close()
            # 2-4. Official sale pages: hero banner, screenshot it.
            pages = list(dict.fromkeys(cfg["events"] + links))[:MAX_EVENT_PAGES + len(cfg["events"])]
            for url in pages:
                if len(found) >= MAX_PER_STORE:
                    break
                # Desktop view first; if it shows no banner (stores serve different markup per
                # visit/device), look again as a phone before giving up on this page.
                for ctx in (desktop, mobile):
                    if len(found) >= MAX_PER_STORE:
                        break
                    page = _open([ctx], url)
                    if not page:
                        continue
                    got_hero = False
                    try:
                        sales += extract_sales(page.inner_text("body"), year)
                        cands = page.evaluate(_JS_CANDIDATES)
                        if not _hero(cands, store):   # lazy images often arrive late: scroll, wait, look again
                            for _ in range(2):
                                page.mouse.wheel(0, 600)
                                page.wait_for_timeout(2500)
                                page.mouse.wheel(0, -2000)
                                page.wait_for_timeout(1500)
                                cands = page.evaluate(_JS_CANDIDATES)
                                if _hero(cands, store):
                                    break
                        view = "desktop" if ctx is desktop else "mobile"
                        slug = re.sub(r"[^a-z0-9]+", "-", urlparse(page.url).path.lower()).strip("-")[:40] or "home"
                        page.screenshot(path=f"banner-debug/{store}-page-{slug}-{view}.jpg", type="jpeg", quality=55)
                        hero = _hero(cands, store)
                        print(f"   [{view}] {page.url[:80]}: {len(cands)} wide images, hero="
                              f"{(hero or {}).get('src', '')[:80] or None}", file=sys.stderr)
                        if not hero:
                            continue
                        got_hero = True
                        if any(b["source_image"] == hero["src"] for b in found):
                            break
                        shot = _capture(page, hero)
                        if not shot:
                            print("   hero screenshot unusable (blank or too large)", file=sys.stderr)
                            continue
                        n = len(found)
                        with open(f"banner-debug/{store}-{n}.jpg", "wb") as fh:
                            fh.write(shot)
                        found.append({
                            "store": store, "name": _name_for(page, store, hero, sale_names),
                            "image_b64": base64.b64encode(shot).decode(),
                            "url": _landing_url(page.url, cfg),
                            "source_image": hero["src"],
                        })
                        print(f"   captured {len(shot) // 1024} KB banner -> banner-debug/{store}-{n}.jpg", file=sys.stderr)
                        break
                    except Exception as exc:  # noqa: BLE001
                        print(f"   {url}: capture error {exc.__class__.__name__}: {str(exc)[:140]}", file=sys.stderr)
                    finally:
                        page.close()
                    if got_hero:
                        break
            if not found:
                print(f"   no banner for {store}; the app will show its calendar card", file=sys.stderr)
            banners += found
        browser.close()
    # One date per sale (first found wins).
    seen, uniq = set(), []
    for s in sales:
        if s["key"] not in seen:
            seen.add(s["key"])
            uniq.append(s)
    return banners, uniq


def _request(url: str, token: str, body: dict | None = None):
    headers = {"x-admin-token": token, "User-Agent": UA, "Accept": "application/json"}
    data = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode() or "{}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="capture and print; don't contact the server")
    ap.add_argument("--check", action="store_true", help="only print whether a capture is needed")
    ap.add_argument("--force", action="store_true", help="capture even if no sale is live or starting soon")
    args = ap.parse_args()

    site, token = os.getenv("SITE_URL", "").rstrip("/"), os.getenv("ADMIN_TOKEN", "")
    sale_names: dict = {}
    if not args.dry_run:
        if not (site and token):
            print("SITE_URL and ADMIN_TOKEN are required (or use --dry-run).", file=sys.stderr)
            return 2
        try:
            info = _request(f"{site}/api/admin/reader/live-banners/needed", token)
        except urllib.error.HTTPError as exc:
            print(f"Server check failed: HTTP {exc.code}", file=sys.stderr)
            info = {}
        print(f"server says: {json.dumps(info)[:300]}", file=sys.stderr)
        needed = args.force or bool(info.get("needed"))
        for s in info.get("sales") or []:
            sale_names.setdefault(s.get("store"), s.get("name"))
        if args.check:   # stdlib-only gate for CI: prints true/false, nothing installed yet
            print("true" if needed else "false")
            return 0
        if not needed:
            print("No sale live or starting soon (or banners are fresh); skipping.", file=sys.stderr)
            return 0

    banners, sales = scrape(sale_names)
    summary = [{k: v for k, v in b.items() if k != "image_b64"} for b in banners]
    print(json.dumps({"banners": summary, "sales": sales}, indent=2))
    if args.dry_run:
        return 0
    if not banners and not sales:
        print("Nothing captured; leaving the server's current banners untouched.", file=sys.stderr)
        return 0
    try:
        print(_request(f"{site}/api/admin/reader/live-banners", token, {"banners": banners, "sales": sales}))
    except urllib.error.HTTPError as exc:
        hint = {403: "ADMIN_TOKEN mismatch, or a firewall in front of the server",
                404: "the server hasn't deployed the live-banners endpoint yet",
                413: "request too large for the server/proxy"}.get(exc.code, "")
        print(f"Push failed: HTTP {exc.code} {exc.read().decode()[:200]} {hint}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
