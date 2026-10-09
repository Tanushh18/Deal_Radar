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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from app.services.live_banners import STORES, parse_banners  # noqa: E402

UA = ("Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Mobile Safari/537.36")


def scrape() -> list:
    from playwright.sync_api import sync_playwright

    out = []
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
            except Exception as exc:  # noqa: BLE001
                print(f"{store}: failed ({exc})", file=sys.stderr)
            finally:
                page.close()
        browser.close()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    banners = scrape()
    if args.dry_run:
        print(json.dumps(banners, indent=2))
        return 0
    site, token = os.getenv("SITE_URL", "").rstrip("/"), os.getenv("ADMIN_TOKEN", "")
    if not (site and token):
        print("SITE_URL and ADMIN_TOKEN are required (or use --dry-run).", file=sys.stderr)
        return 2
    if not banners:
        print("No banners found; leaving the server's current ones untouched.", file=sys.stderr)
        return 0
    req = urllib.request.Request(
        f"{site}/api/admin/reader/live-banners", data=json.dumps({"banners": banners}).encode(),
        headers={"Content-Type": "application/json", "x-admin-token": token}, method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        print(resp.read().decode())
    return 0


if __name__ == "__main__":
    sys.exit(main())
