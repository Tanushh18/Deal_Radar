"""Scrape the stores' live sale banners with a real browser and push them to the server.

The stores build their home pages with JavaScript, so the server's plain HTTP
fetch often sees no banners. This renders the pages in headless Chromium,
extracts sale banners, and POSTs them to /api/admin/reader/live-banners.
Run by .github/workflows/live-banners.yml every few hours, or by hand:

    SITE_URL=https://your-app.example ADMIN_TOKEN=... python tools/fetch_banners.py
    python tools/fetch_banners.py --dry-run     # print, don't push
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")


# Runs in the page: wide, reasonably large images near the top (hero banners),
# with whatever text names them — alt, aria-label/title of the wrapping link, or its href.
_JS_CANDIDATES = """() => {
  const out = [];
  const abs = (u) => { try { return new URL(u, location.href).href; } catch (e) { return ''; } };
  for (const img of document.images) {
    const r = img.getBoundingClientRect();
    const w = Math.max(r.width, img.naturalWidth || 0), h = Math.max(r.height, img.naturalHeight || 0);
    if (w < 280 || h < 60 || w / h < 1.6) continue;
    const a = img.closest('a');
    const src = img.currentSrc || img.src || img.getAttribute('data-src') || '';
    out.push({src: abs(src), alt: img.alt || '', label: a ? (a.getAttribute('aria-label') || a.title || '') : '',
              href: a ? abs(a.getAttribute('href') || '') : '', top: Math.round(r.top + scrollY), w: Math.round(w), h: Math.round(h)});
  }
  for (const el of document.querySelectorAll('[style*="background-image"]')) {
    const m = /url\\(["']?([^"')]+)/.exec(el.getAttribute('style') || '');
    const r = el.getBoundingClientRect();
    if (!m || r.width < 280 || r.height < 60 || r.width / r.height < 1.6) continue;
    const a = el.closest('a');
    out.push({src: abs(m[1]), alt: el.getAttribute('aria-label') || el.title || '', label: a ? (a.getAttribute('aria-label') || a.title || '') : '',
              href: a ? abs(a.getAttribute('href') || '') : '', top: Math.round(r.top + scrollY), w: Math.round(r.width), h: Math.round(r.height)});
  }
  return out;
}"""


def pick_banners(store: str, url: str, cands: list) -> list:
    """Sale banners among the wide-image candidates: sale wording in the alt/label/link."""
    from app.services.live_banners import MAX_PER_STORE, _SALE_WORDS

    out, seen = [], set()
    for c in sorted(cands, key=lambda c: c["top"]):
        name = (c["alt"] or c["label"]).strip()
        if name.lower() in ("", "image", "banner", "img", "photo"):   # generic alt: name it from the link instead
            slug = re.sub(r"\?.*", "", c["href"]).rstrip("/").rsplit("/", 1)[-1]
            name = re.sub(r"[-_]+", " ", slug).strip().title()
        hay = f"{name} {c['href']}"
        src = c["src"]
        if not src.startswith("https://") or src in seen or c["top"] > 3000:
            continue
        if not _SALE_WORDS.search(hay.replace("-", " ").replace("/", " ")):
            continue
        seen.add(src)
        out.append({"id": f"live-{store}-{len(out)}", "name": (name or f"{store.title()} sale")[:80], "store": store,
                    "starts_at": None, "ends_at": None, "approximate": False, "hype": "",
                    "image_url": src, "url": c["href"] if c["href"].startswith("https://") else url, "live": True})
        if len(out) >= MAX_PER_STORE:
            break
    return out


_OFFICIAL = {   # a store's own image servers: always preferred over blog pictures
    "amazon": ("amazon.in", "media-amazon.com", "aboutamazon"),
    "flipkart": ("flipkart.com", "flixcart.com"),
    "myntra": ("myntra.com", "myntassets.com"),
    "ajio": ("ajio.com",), "meesho": ("meesho.com",), "nykaa": ("nykaa.com",),
}
_OLD_IMAGE = re.compile(r"(?:/|[-_])(?:20(?:1\d|2[0-5])|img(?:1\d|2[0-5]))(?:/|[-_.]|$)", re.I)


def ddg_banner(sale: dict) -> dict | None:
    """The single image fallback: DuckDuckGo image search via the open-source `ddgs`
    package (no key). Wide, large https images that are really about this sale,
    skipping old-year pictures; the store's own image servers rank first.
    Credits the site the image came from."""
    from urllib.parse import urlparse
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS
        results = DDGS().images(f"{sale['name']} {datetime.now().year} banner", region="in-en",
                                safesearch="moderate", size="Large", layout="Wide", max_results=20) or []
    except Exception as exc:  # noqa: BLE001
        print(f"{sale['store']}: image search failed: {exc}", file=sys.stderr)
        return None
    words = [w for w in re.findall(r"[a-z]+", sale["name"].lower()) if len(w) > 3]
    good = []
    for r in results:
        img = r.get("image") or ""
        try:
            w, h = int(r.get("width") or 0), int(r.get("height") or 0)
        except ValueError:
            continue
        text = f"{r.get('title', '')} {r.get('url', '')}".lower()
        if not (img.startswith("https://") and w >= 600 and h and w / h >= 1.6 and not _OLD_IMAGE.search(img)
                and sum(wd in text for wd in words) >= 2):
            continue
        host = urlparse(img).netloc.lower() + " " + urlparse(r.get("url") or "").netloc.lower()
        official = any(d in host for d in _OFFICIAL.get(sale["store"], ()))
        good.append((not official, {"id": "", "name": sale["name"], "store": sale["store"], "image_url": img,
                                    "url": STORES_HOME.get(sale["store"], ""),
                                    "credit": urlparse(r.get("url") or img).netloc.removeprefix("www.")}))
    print(f"{sale['store']}: image search -> {len(results)} results, {len(good)} usable", file=sys.stderr)
    return sorted(good, key=lambda g: g[0])[0][1] if good else None


STORES_HOME = {"amazon": "https://www.amazon.in/", "flipkart": "https://www.flipkart.com/",
               "myntra": "https://www.myntra.com/", "ajio": "https://www.ajio.com/",
               "meesho": "https://www.meesho.com/", "nykaa": "https://www.nykaa.com/"}


def scrape() -> tuple:
    from playwright.sync_api import sync_playwright
    from app.services.live_banners import STORES, extract_sales, parse_banners

    out, sales = [], []
    year = datetime.now(timezone(timedelta(hours=5, minutes=30))).year
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA, locale="en-IN", viewport={"width": 412, "height": 900})
        desktop = browser.new_context(locale="en-IN", viewport={"width": 1366, "height": 900},
                                      user_agent=UA.replace("Linux; Android 13; Pixel 7", "X11; Linux x86_64").replace(" Mobile", ""))
        for store, url in STORES.items():
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.mouse.wheel(0, 1500)       # trigger lazy-loaded banners
                page.wait_for_timeout(2500)
                if len(page.inner_text("body")) < 500:   # bot-check / stub page: retry once as desktop Chrome
                    page.close()
                    page = desktop.new_page()
                    page.goto(url, wait_until="networkidle", timeout=45000)
                    page.wait_for_timeout(2500)
                cands = page.evaluate(_JS_CANDIDATES)
                found = pick_banners(store, url, cands) or parse_banners(store, url, page.content())
                print(f"{store}: title={page.title()!r} url={page.url} text={len(page.inner_text('body'))} "
                      f"wide_images={len(cands)} -> {len(found)} banner(s)", file=sys.stderr)
                for c in cands[:8]:   # what the page offered, so a miss can be diagnosed from the log
                    print(f"   cand top={c['top']} {c['w']}x{c['h']} alt={c['alt'][:50]!r} label={c['label'][:40]!r} "
                          f"href={c['href'][:70]} src={c['src'][:70]}", file=sys.stderr)
                os.makedirs("banner-debug", exist_ok=True)
                page.screenshot(path=f"banner-debug/{store}.png")
                out += found
                # Exact dates: the visible page text plus each banner's alt text.
                text = page.inner_text("body") + "\n" + "\n".join(b["name"] for b in found)
                sales += extract_sales(text, year)
            except Exception as exc:  # noqa: BLE001
                print(f"{store}: failed ({exc})", file=sys.stderr)
            finally:
                page.close()
        browser.close()
    return out, sales


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--check", action="store_true", help="only print whether a scrape is needed")
    ap.add_argument("--force", action="store_true", help="skip the is-a-sale-active check")
    args = ap.parse_args()
    active_sales = []
    site, token = os.getenv("SITE_URL", "").rstrip("/"), os.getenv("ADMIN_TOKEN", "")
    if args.check or not args.dry_run:
        if not (site and token):
            print("SITE_URL and ADMIN_TOKEN are required (or use --dry-run).", file=sys.stderr)
            return 2
        gate = urllib.request.Request(f"{site}/api/admin/reader/live-banners/needed", headers={"x-admin-token": token, "User-Agent": UA, "Accept": "application/json"})
        info = json.load(urllib.request.urlopen(gate, timeout=30))
        needed = args.force or info.get("needed")
        active_sales = info.get("sales") or [
            {"name": n, "store": next((k for k in STORES_HOME if k in n.lower()), "")} for n in info.get("active") or []]
        if args.check:   # stdlib-only gate for CI: prints true/false, nothing installed yet
            print("true" if needed else "false")
            return 0
        if not needed:
            print("No sale live or starting soon (or banners are fresh); skipping.", file=sys.stderr)
            return 0
    banners, sales = scrape()
    # Stores whose own page gave no banner (blocked, JS-built hero…): fall back to
    # image search for each sale that's live or starting soon.
    if not args.dry_run:
        have = {b["store"] for b in banners}
        for sale in active_sales:
            if not sale["store"] or sale["store"] in have:
                continue
            b = ddg_banner(sale)
            if b:
                b["id"] = f"live-{b['store']}-g{len(banners)}"
                banners.append(b)
                have.add(b["store"])
                print(f"{sale['store']}: google image fallback -> {b['image_url'][:80]}", file=sys.stderr)
    if args.dry_run:
        print(json.dumps({"banners": banners, "sales": sales}, indent=2))
        return 0
    if not banners and not sales:
        print("No banners or dates found; leaving the server's current ones untouched.", file=sys.stderr)
        return 0
    req = urllib.request.Request(
        f"{site}/api/admin/reader/live-banners", data=json.dumps({"banners": banners, "sales": sales}).encode(),
        headers={"Content-Type": "application/json", "x-admin-token": token, "User-Agent": UA, "Accept": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            print(resp.read().decode())
    except urllib.error.HTTPError as exc:
        hint = {403: "ADMIN_TOKEN does not match the server's ADMIN_TOKEN env var (or it is unset there)",
                404: "the server has not deployed the live-banners endpoint yet"}.get(exc.code, "")
        print(f"Push failed: HTTP {exc.code} {exc.read().decode()[:200]} {hint}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
