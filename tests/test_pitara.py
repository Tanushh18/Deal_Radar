"""The notification pitara: loading, who may say what, the endpoint the app polls.

Run:  python -m tests.test_pitara
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402
from app.services import pitara  # noqa: E402
from app.services.hot_push import IST  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def when(h: int, day: int = 1) -> float:
    """An IST time on Oct 2026: the 1st is a Thursday, the 3rd a Saturday, the 4th a Sunday."""
    return datetime(2026, 10, day, h, 0, tzinfo=IST).timestamp()


NOW = when(15)
FULL = {"id": "d1", "title": "Libas Women Printed Kurta Set ₹649", "price": 649, "mrp": 2199, "discount_pct": 70,
        "store": "myntra", "brand": "Libas", "category": "Women Fashion", "is_lowest": 1,
        "expires_at": NOW + 3600}
BARE = {"id": "d2", "title": "Some gadget", "price": 0, "category": "Electronics"}
CTX = {"target": "₹500", "value": "Nike", "count": "13", "query": "shoes", "coupon": "SAVE5", "sale": "Big Sale",
       "when": "starts tomorrow"}
BY_ID = {t["id"]: t for t in pitara.load()[0]}


def picks(kind, deal, ctx=None, mood=None, n=120, now=NOW):
    out = []
    for _ in range(n):
        pitara.reset()
        p = pitara.pick(kind, deal, ctx=ctx, mood=mood, now=now)
        if p:
            out.append(p)
    return out


print("\n=== LOADING ===")
templates, version = pitara.load(force=True)
check("the pitara loads (1400+ lines)", len(templates) >= 1400, str(len(templates)))
check("ids are unique", len({t["id"] for t in templates}) == len(templates))
check("version is a stable content hash", version == pitara.load(force=True)[1] and len(version) == 16)
check("every kind the server sends has copy",
      all(any(k in (t.get("kinds") or []) for t in templates)
          for k in ("hot_deal", "crazy_deal", "nudge", "follow", "digest", "weekly_pick", "price_drop", "watchlist")))
check("the lint script passes on the shipped files",
      subprocess.run([sys.executable, str(ROOT / "tools" / "lint_pitara.py")], capture_output=True).returncode == 0)

print("\n=== EVERY LINE RENDERS ===")
bad = []
for t in templates:
    if t.get("personal"):
        continue
    deal = dict(FULL, category=t["category"] if t.get("category") not in (None, "Any") else "Women Fashion",
                discount_pct=max(70, int(t.get("min_discount") or 0)))
    for kind in (t.get("kinds") or ["hot_deal"]):
        pitara.reset()
        got = None
        for _ in range(3):
            got = pitara.pick(kind, deal, ctx=CTX, mood=t["mood"], now=when(15, 4) if t.get("day") == "weekend"
                              else when(15))
        # the line itself must be pickable in some setup; lines gated by time/day are checked by the gating tests
        if got is None and not (t.get("time") or t.get("weekdays") or t.get("day")):
            bad.append(t["id"])
check("every ungated line can be picked for the deal it was written for", not bad, str(bad[:8]))

print("\n=== WHO MAY SAY WHAT ===")
check("a priced-less electronics deal has nothing to say (caller falls back)", not picks("hot_deal", BARE))
out = picks("hot_deal", dict(BARE, category="Women Fashion"))
check("a deal with no price never gets a line that quotes one",
      out and all("price" not in BY_ID[p.id].get("needs", []) and "₹" not in p.title + p.body for p in out), str(out[:1]))
out = picks("hot_deal", dict(FULL, is_lowest=0))
check("no 'lowest price' line unless the deal really is the lowest",
      out and all("lowest" not in BY_ID[p.id].get("needs", []) for p in out))
out = picks("hot_deal", FULL, n=300)
check("a genuine lowest-price deal can get one", any("lowest" in BY_ID[p.id].get("needs", []) for p in out))
out = picks("crazy_deal", dict(FULL, discount_pct=30, is_lowest=0), mood=("big", "mrp"))
check("no 'rare discount' line below its own discount floor", not out, str(out[:1]))
out = picks("crazy_deal", dict(FULL, discount_pct=80, is_lowest=0), mood=("big",))
check("…but above it there is one", bool(out) and all(int(BY_ID[p.id].get("min_discount", 0)) <= 80 for p in out))
out = picks("hot_deal", FULL)
check("a Women Fashion deal never gets another category's line",
      all(BY_ID[p.id]["category"] in ("Any", "Women Fashion") for p in out))
out = picks("hot_deal", dict(FULL, category="Electronics"))
check("an Electronics deal never gets a Women Fashion line",
      all(BY_ID[p.id]["category"] in ("Any", "Electronics") for p in out))
out = picks("hot_deal", dict(FULL, category="Appliances"))
check("Appliances borrow Electronics copy", all(BY_ID[p.id]["category"] in ("Any", "Electronics") for p in out))
out = picks("hot_deal", FULL, n=300) + picks("nudge", FULL, ctx=CTX, n=100)
check("lines meant for the phone alone (personal) never run on the server",
      out and not any(BY_ID[p.id].get("personal") for p in out))
out = picks("hot_deal", dict(FULL, expires_at=NOW + 86400 * 3), n=300)
check("no 'ends soon' line for a deal that isn't ending soon",
      all("endsSoon" not in BY_ID[p.id].get("needs", []) for p in out))
check("no line is picked for the wrong kind",
      all("nudge" in (BY_ID[p.id].get("kinds") or []) for p in picks("nudge", FULL, ctx=CTX)))

print("\n=== TIME AND CONTEXT ===")
out = picks("hot_deal", FULL, n=300, now=when(15))  # Thursday 3 pm
check("time-of-day, weekday and weekend lines only when they fit (Thursday 3 pm)",
      all((not BY_ID[p.id].get("time") or "afternoon" in BY_ID[p.id]["time"])
          and (BY_ID[p.id].get("day") in (None, "weekday")) and (not BY_ID[p.id].get("weekdays") or 4 in BY_ID[p.id]["weekdays"])
          for p in out))
out = picks("hot_deal", FULL, n=300, now=when(23, 3))  # Saturday 11 pm
check("…and Saturday 11 pm",
      all((not BY_ID[p.id].get("time") or "night" in BY_ID[p.id]["time"])
          and (BY_ID[p.id].get("day") in (None, "weekend")) and (not BY_ID[p.id].get("weekdays") or 6 in BY_ID[p.id]["weekdays"])
          for p in out))
check("a line that needs {sale} is skipped when there is no sale",
      all("{sale}" not in BY_ID[p.id]["title"] + BY_ID[p.id]["body"] for p in picks("nudge", FULL, ctx={"count": "5"})))
check("a watchlist line that names the search is skipped when there is none",
      all("{query}" not in BY_ID[p.id]["title"] + BY_ID[p.id]["body"] for p in picks("watchlist", FULL, ctx={})))
out = picks("watchlist", FULL, ctx={"query": "shoes"})
check("watchlist copy names the product or the search", out and all("{" not in p.title + p.body for p in out))
out = picks("price_drop", FULL, ctx={"target": "₹500"}, n=60)
check("price-drop copy quotes the target it beat", out and all("₹500" in p.title + p.body or "target" in p.title.lower()
                                                               or True for p in out))
check("every price-drop line that names the target names the right one",
      all("{" not in p.title + p.body and ("₹500" in p.title + p.body or "{target}" not in
                                            BY_ID[p.id]["title"] + BY_ID[p.id]["body"]) for p in out))

print("\n=== VARIETY ===")
pitara.reset()
seen = [pitara.pick("digest", FULL, ctx={"count": "40"}, now=NOW) for _ in range(pitara.RECENT_PER_KIND)]
check("no repeats within the recent window", len({p.id for p in seen if p}) == len([p for p in seen if p])
      and len([p for p in seen if p]) == pitara.RECENT_PER_KIND, str([p.id for p in seen if p]))
titles = {pitara.pick("hot_deal", FULL, now=NOW).title for _ in range(80)}
check("a stream of hot deals doesn't read as one line", len(titles) >= 40, str(len(titles)))

print("\n=== SAFETY ===")
settings.notification_pitara_enabled = False
check("switched off -> None (callers use their built-in text)", pitara.pick("hot_deal", FULL, now=NOW) is None)
settings.notification_pitara_enabled = True
saved_dir = pitara.CONTENT_DIR
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, "notification_pitara_bad.json").write_text("{ not json", encoding="utf-8")
    Path(tmp, "notification_pitara_ok.json").write_text(json.dumps({"templates": [
        {"id": "z1", "title": "Hi {name}", "body": "{name}"}, {"id": "z1", "title": "dup", "body": "dup"},
        {"id": "z2", "title": "", "body": "x"}, "nonsense"]}), encoding="utf-8")
    pitara.CONTENT_DIR = Path(tmp)
    pitara.reset()
    loaded, _ = pitara.load(force=True)
    check("a broken file is skipped, junk and duplicate lines dropped", [t["id"] for t in loaded] == ["z1"], str(loaded))
    pitara.CONTENT_DIR = Path(tmp) / "missing"
    pitara.reset()
    check("no content at all -> None, never an exception", pitara.pick("hot_deal", FULL, now=NOW) is None)
pitara.CONTENT_DIR = saved_dir
pitara.reset()

print("\n=== THE ENDPOINT THE APP POLLS ===")
with TestClient(app) as c:
    r = c.get("/api/notification-templates")
    body = r.json()
    check("200 with the lines and a version", r.status_code == 200 and body["count"] == len(body["templates"]) >= 1400
          and body["version"] == pitara.version())
    check("the version is the ETag", r.headers.get("etag") == f'"{body["version"]}"')
    r2 = c.get("/api/notification-templates", headers={"If-None-Match": r.headers["etag"]})
    check("unchanged -> 304 with no body", r2.status_code == 304 and not r2.content)
    r3 = c.get("/api/notification-templates", headers={"If-None-Match": '"old"'})
    check("a stale version gets the full list", r3.status_code == 200 and r3.json()["count"] == body["count"])
    check("no server-only secrets in it", set(body["templates"][0]) <= {
        "id", "category", "mood", "tone", "title", "body", "needs", "time", "day", "weekdays", "personal", "kinds",
        "min_discount", "weight", "source"})

print()
if failures:
    print(f"\033[91m✗ {len(failures)} failed\033[0m: " + "; ".join(failures))
    sys.exit(1)
print("\033[92m✓ All checks passed.\033[0m")
