"""Promotion counters: /get, /d/<id> counting, the share/invite endpoint, privacy, the admin view.

Run:  python -m tests.test_growth
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from urllib.parse import parse_qs, unquote, urlparse

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import growth, ratelimit  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []
ADMIN = {"X-Admin-Token": "t"}
ANDROID = "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36"
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"
WHATSAPP_PREVIEW = "WhatsApp/2.24.5.76 A"
TELEGRAM_PREVIEW = "TelegramBot (like TwitterBot)"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def today_total(event: str, src: str = None) -> int:
    day = growth._load().get(growth._today(), {})
    return sum(n for k, n in day.items() if k.split("|")[0] == event and (src is None or k.split("|")[1] == src))


with TestClient(app, follow_redirects=False) as c:
    db.execute("DELETE FROM meta WHERE key = 'growth_counts'")
    growth.reset_cache()

    print("\n=== LABELS, BOTS, THE PLAY STORE LINK ===")
    check("a label is lowercase letters, digits, _ and - only", growth.clean_src("Tele gram!!_1") == "telegram_1")
    check("an empty or junk label falls back", growth.clean_src("") == "direct" and growth.clean_src("!!!", "x") == "x")
    check("a long label is cut", len(growth.clean_src("a" * 100)) == 24)
    check("link-preview fetchers are recognised as bots",
          growth.is_bot(WHATSAPP_PREVIEW) and growth.is_bot(TELEGRAM_PREVIEW) and growth.is_bot("facebookexternalhit/1.1")
          and growth.is_bot("") and not growth.is_bot(ANDROID) and not growth.is_bot(DESKTOP))
    check("Android is detected", growth.is_android(ANDROID) and not growth.is_android(DESKTOP))
    url = growth.play_url("Telegram")
    q = parse_qs(urlparse(url).query)
    check("the Play Store link is the app's page with the source as the install referrer",
          url.startswith("https://play.google.com/store/apps/details?id=com.tanush.dealradar&referrer=")
          and unquote(q["referrer"][0]) == "utm_source=telegram&utm_medium=link&utm_campaign=promo", url)
    settings.play_store_url = "https://play.google.com/store/apps/details?id=other.app"
    check("PLAY_STORE_URL overrides the page", growth.play_url("x").startswith("https://play.google.com/store/apps/details?id=other.app&referrer="))
    settings.play_store_url = ""

    print("\n=== /get ===")
    r = c.get("/get?src=Telegram", headers={"User-Agent": ANDROID})
    check("an Android phone goes to the Play Store, with the label as the referrer",
          r.status_code == 302 and r.headers["location"].startswith("https://play.google.com/store/apps/details?id=com.tanush.dealradar")
          and "utm_source%3Dtelegram" in r.headers["location"], str(r.headers.get("location")))
    check("…and the redirect is never cached", "no-store" in r.headers.get("cache-control", ""))
    r = c.get("/get?src=telegram", headers={"User-Agent": DESKTOP})
    check("anyone else gets the website", r.status_code == 302 and r.headers["location"] == "/")
    c.get("/get?src=telegram", headers={"User-Agent": WHATSAPP_PREVIEW})
    c.get("/get", headers={"User-Agent": ANDROID})
    check("two real clicks on 'telegram', one 'direct'; the preview bot isn't counted",
          today_total("get_click", "telegram") == 2 and today_total("get_click", "direct") == 1,
          str(growth._load()))

    print("\n=== SHARED DEAL LINKS ===")
    c.get("/d/missing-deal?src=whatsapp", headers={"User-Agent": ANDROID})
    c.get("/d/missing-deal?src=whatsapp", headers={"User-Agent": ANDROID})
    c.get("/d/missing-deal", headers={"User-Agent": DESKTOP})
    c.get("/d/missing-deal?src=whatsapp", headers={"User-Agent": WHATSAPP_PREVIEW})
    c.get("/d/missing-deal?src=whatsapp", headers={"User-Agent": TELEGRAM_PREVIEW})
    check("a shared link opened by people is counted by label; preview fetchers aren't",
          today_total("deal_open", "whatsapp") == 2 and today_total("deal_open", "link") == 1)

    print("\n=== SHARE AND INVITE TAPS ===")
    ratelimit._buckets.clear()
    r = c.post("/api/growth/event", json={"event": "share", "src": "app"})
    check("a share tap is counted", r.status_code == 200 and r.json() == {"ok": True} and today_total("share", "app") == 1)
    c.post("/api/growth/event", json={"event": "invite", "src": "App"})
    check("an invite tap is counted, label cleaned", today_total("invite", "app") == 1)
    c.post("/api/growth/event", json={"event": "share"})
    check("no label -> 'unknown'", today_total("share", "unknown") == 1)
    for body in ({"event": "deal_open", "src": "x"}, {"event": "get_click"}, {"event": "nonsense"}, {}, None):
        r = c.post("/api/growth/event", json=body)
        check(f"the client can't report {body!r}", r.status_code == 400)
    check("nothing leaked into server-only counts", today_total("get_click") == 3 and today_total("deal_open") == 3)

    print("\n=== LIMITS ===")
    ratelimit._buckets.clear()
    codes = [c.post("/api/growth/event", json={"event": "share", "src": "app"}).status_code for _ in range(32)]
    check("30 a minute per client, then 429", codes[:30] == [200] * 30 and 429 in codes[30:], str(codes[28:]))
    ratelimit._buckets.clear()
    growth.reset_cache()
    db.execute("DELETE FROM meta WHERE key = 'growth_counts'")
    for i in range(growth.MAX_KEYS_PER_DAY + 40):
        growth.record("share", f"src{i}")
    day = growth._load()[growth._today()]
    check("distinct labels are capped per day; the overflow lands in 'other'",
          len(day) <= growth.MAX_KEYS_PER_DAY + len(growth.EVENTS) and day.get("share|other", 0) >= 40, str(len(day)))
    check("an unknown event is ignored", growth.record("nope", "x") is False)

    print("\n=== PRIVACY ===")
    growth.reset_cache()
    db.execute("DELETE FROM meta WHERE key = 'growth_counts'")
    c.get("/get?src=reels", headers={"User-Agent": ANDROID, "X-Forwarded-For": "203.0.113.77"})
    c.get("/d/x?src=reels", headers={"User-Agent": ANDROID, "X-Forwarded-For": "203.0.113.77"})
    growth.flush()
    blob = db.get_meta("growth_counts") or ""
    stored = json.loads(blob)
    keys = [k for day in stored.values() for k in day]
    check("what is stored is only day -> 'event|label' -> count",
          keys and all(re.fullmatch(r"(deal_open|get_click|share|invite)\|[a-z0-9_-]{1,24}", k) for k in keys), str(keys))
    check("no IP address, user agent or other request detail ends up in it",
          "203.0.113.77" not in blob and "Android" not in blob and "Mozilla" not in blob and "Pixel" not in blob)

    print("\n=== SURVIVES A RESTART ===")
    before = json.loads(db.get_meta("growth_counts"))
    growth.reset_cache()
    check("the counts come back from the database", growth._load() == before and before)
    growth.record("share", "app")
    growth.flush()
    old_day = "2020-01-01"
    stored = json.loads(db.get_meta("growth_counts"))
    stored[old_day] = {"share|app": 5}
    db.set_meta("growth_counts", json.dumps(stored))
    growth.reset_cache()
    growth.record("share", "app")
    growth.flush()
    check("days older than 90 are dropped on save", old_day not in json.loads(db.get_meta("growth_counts")))
    growth._dirty = False
    before_blob = db.get_meta("growth_counts")
    growth.flush()
    check("nothing changed -> nothing written", db.get_meta("growth_counts") == before_blob)

    print("\n=== THE ADMIN VIEW ===")
    growth.reset_cache()
    db.execute("DELETE FROM meta WHERE key = 'growth_counts'")
    for _ in range(3):
        growth.record("get_click", "telegram")
    growth.record("get_click", "reels")
    growth.record("deal_open", "telegram")
    growth.record("share", "app")
    growth.record("invite", "app")
    check("the admin API needs the token", c.get("/api/admin/reader/growth").status_code == 403)
    g = c.get("/api/admin/reader/growth", headers=ADMIN).json()
    check("totals add up", g["totals"] == {"deal_open": 1, "get_click": 4, "share": 1, "invite": 1}, str(g["totals"]))
    check("sources are ranked by visitors and carry every event",
          g["sources"][0]["src"] == "telegram" and g["sources"][0]["get_click"] == 3 and g["sources"][0]["deal_open"] == 1
          and g["sources"][1]["src"] == "reels", str(g["sources"][:2]))
    check("a daily series and the privacy note are there", g["daily"] and g["daily"][-1]["get_click"] == 4 and "no IP" in g["note"])
    check("days is clamped", c.get("/api/admin/reader/growth?days=9999", headers=ADMIN).json()["days"] == 90)

    print("\n=== THE SITE ===")
    page = c.get("/", headers={"User-Agent": ANDROID}).text
    check("the site ships the banner and share-source code",
          "initAppBanner" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "assets", "app.js")).read()
          and 'id="toasts"' in page)
    priv = c.get("/privacy").text
    check("the privacy page tells people about these counts", "shared deal link" in priv and "totals only" in priv)

print()
if failures:
    print(f"\033[91m✗ {len(failures)} failed\033[0m: " + "; ".join(failures))
    sys.exit(1)
print("\033[92m✓ All checks passed.\033[0m")
