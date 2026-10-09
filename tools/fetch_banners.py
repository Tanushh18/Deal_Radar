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
                found = parse_banners(store, url, page.content())
                print(f"{store}: {len(found)} banner(s)", file=sys.stderr)
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
