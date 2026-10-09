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
import sys
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


def scrape() -> tuple:
    from playwright.sync_api import sync_playwright
    from app.services.live_banners import STORES, extract_sales, parse_banners

    out, sales = [], []
    year = datetime.now(timezone(timedelta(hours=5, minutes=30))).year
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(user_agent=UA, locale="en-IN", viewport={"width": 412, "height": 900})
        for store, url in STORES.items():
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="networkidle", timeout=45000)
                page.mouse.wheel(0, 1500)       # trigger lazy-loaded banners
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
    site, token = os.getenv("SITE_URL", "").rstrip("/"), os.getenv("ADMIN_TOKEN", "")
    if args.check or not args.dry_run:
        if not (site and token):
            print("SITE_URL and ADMIN_TOKEN are required (or use --dry-run).", file=sys.stderr)
            return 2
        gate = urllib.request.Request(f"{site}/api/admin/reader/live-banners/needed", headers={"x-admin-token": token})
        needed = args.force or json.load(urllib.request.urlopen(gate, timeout=30)).get("needed")
        if args.check:   # stdlib-only gate for CI: prints true/false, nothing installed yet
            print("true" if needed else "false")
            return 0
        if not needed:
            print("No sale live or starting soon (or banners are fresh); skipping.", file=sys.stderr)
            return 0
    banners, sales = scrape()
    if args.dry_run:
        print(json.dumps({"banners": banners, "sales": sales}, indent=2))
        return 0
    if not banners and not sales:
        print("No banners or dates found; leaving the server's current ones untouched.", file=sys.stderr)
        return 0
    req = urllib.request.Request(
        f"{site}/api/admin/reader/live-banners", data=json.dumps({"banners": banners, "sales": sales}).encode(),
        headers={"Content-Type": "application/json", "x-admin-token": token}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
