"""Diagnostic: can we get sale banner images from open image-search sources from this machine?
Prints raw results for DuckDuckGo (ddgs) and a Bing Images page scrape."""
import html
import json
import re
import sys
import urllib.parse
import urllib.request

QUERIES = ["Amazon Great Indian Festival 2026 banner", "Flipkart Big Billion Days 2026 banner"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def ddg(q):
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS
    return [{"img": r.get("image"), "w": r.get("width"), "h": r.get("height"), "title": r.get("title"), "page": r.get("url")}
            for r in DDGS().images(q, region="in-en", safesearch="moderate", size="Large", layout="Wide", max_results=15)]


def bing(q):
    url = "https://www.bing.com/images/search?" + urllib.parse.urlencode({"q": q, "qft": "+filterui:imagesize-large+filterui:aspect-wide", "form": "IRFLTR"})
    page = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": UA}), timeout=30).read().decode("utf8", "replace")
    out = []
    for m in re.finditer(r'class="iusc"[^>]*\sm="([^"]+)"', page):
        try:
            d = json.loads(html.unescape(m.group(1)))
        except ValueError:
            continue
        out.append({"img": d.get("murl"), "title": d.get("t"), "page": d.get("purl")})
    return out


for name, fn in (("ddgs", ddg), ("bing", bing)):
    for q in QUERIES:
        try:
            res = fn(q)
            print(f"== {name} | {q} -> {len(res)} results")
            for r in res[:6]:
                print("  ", r)
        except Exception as exc:  # noqa: BLE001
            print(f"== {name} | {q} FAILED: {type(exc).__name__}: {exc}")
sys.exit(0)
