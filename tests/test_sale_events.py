"""Upcoming-sales calendar: seed, admin CRUD, AI hype, the Telegram heads-up.

Run:  python -m tests.test_sale_events
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  TG_BOT_TOKEN="123:abc", TG_POST_CHANNEL="dealradar18", GROQ_API_KEY="fake-groq-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import sale_events as se  # noqa: E402
from app.services import ai_enrich, tg_post  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []
ADMIN = {"X-Admin-Token": "t"}


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# --- fakes: Telegram Bot API + Groq --------------------------------------
tg_calls, groq_calls = [], []


def bot_api(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    tg_calls.append(body)
    return httpx.Response(200, json={"ok": True, "result": {"message_id": len(tg_calls)}})


def groq_api(request: httpx.Request) -> httpx.Response:
    groq_calls.append(json.loads(request.content))
    return httpx.Response(200, json={
        "choices": [{"message": {"content": "Amazon's biggest sale of the year — huge markdowns, real ones."}}],
        "usage": {"total_tokens": 42},
    })


def router(request: httpx.Request) -> httpx.Response:
    if "api.telegram.org" in str(request.url):
        return bot_api(request)
    return groq_api(request)


class _FakeHttpx:
    def __getattr__(self, name):
        return getattr(httpx, name)

    def AsyncClient(self, **kw):  # noqa: N802
        return httpx.AsyncClient(transport=httpx.MockTransport(router), **kw)


tg_post.httpx = ai_enrich.httpx = se.httpx = _FakeHttpx()


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def main() -> int:
    db.connect()
    with TestClient(app) as c:
        print("\n=== SEED CALENDAR ===")
        events = se.list_all()
        check("seeds the known recurring sales", len(events) == len(se._SEED_TEMPLATE), str(len(events)))
        check("every seeded event is genuinely upcoming",
              all((e["ends_at"] or e["starts_at"]) >= time.time() for e in events))
        check("seeded events are marked approximate", all(e["approximate"] for e in events))
        r = c.get("/api/sale-events").json()
        check("public endpoint lists upcoming events, soonest first",
              r["events"] == sorted(r["events"], key=lambda e: e["starts_at"]) and len(r["events"]) == len(events))
        check("public endpoint never leaks admin-only fields",
              "heads_up_posted" not in r["events"][0] and "updated_at" not in r["events"][0])

        print("\n=== ADMIN CRUD ===")
        check("admin list needs the token", c.get("/api/admin/reader/sale-events").status_code in (401, 403))
        confirmed_start = (datetime.now(timezone.utc) + timedelta(days=10)).timestamp()
        payload = {"name": "Flipkart Big Billion Days", "store": "flipkart",
                   "starts_at": confirmed_start, "ends_at": confirmed_start + 5 * 86400, "approximate": False}
        r = c.post("/api/admin/reader/sale-events", json=payload, headers=ADMIN)
        check("admin can confirm real dates for an event", r.status_code == 200, r.text)
        event = r.json()["event"]
        check("confirming dates clears 'approximate'", event["approximate"] is False)
        check("AI hype line generated on save", event["hype"].startswith("Amazon") or len(event["hype"]) > 10,
              event["hype"])
        check("one Groq call made for the hype line", len(groq_calls) == 1, str(len(groq_calls)))

        r = c.post(f"/api/admin/reader/sale-events/{event['id']}/hype", headers=ADMIN)
        check("regenerate-hype endpoint works", r.status_code == 200 and len(groq_calls) == 2, r.text)

        r = c.delete(f"/api/admin/reader/sale-events/{event['id']}", headers=ADMIN)
        check("admin can delete an event", r.status_code == 200)
        check("deleting an unknown id 404s", c.delete("/api/admin/reader/sale-events/nope", headers=ADMIN).status_code == 404)
        remaining = c.get("/api/admin/reader/sale-events", headers=ADMIN).json()["events"]
        check("deleted event is gone", not any(e["name"] == "Flipkart Big Billion Days" and not e["approximate"]
                                                for e in remaining))

        print("\n=== TELEGRAM HEADS-UP ===")
        tg_calls.clear()
        near = se.upsert({"name": "Test Mega Sale", "store": "amazon",
                          "starts_at": time.time() + 86400, "ends_at": time.time() + 4 * 86400,
                          "approximate": True})
        far = se.upsert({"name": "Far Off Sale", "store": "amazon",
                         "starts_at": time.time() + 30 * 86400, "ends_at": time.time() + 33 * 86400})
        due = se.due_for_heads_up()
        ids = {e["id"] for e in due}
        # Depending on today's date a seeded recurring sale can also legitimately
        # fall inside the 2-day window — only assert what this test actually controls.
        check("near event is due, far-off one is not", near["id"] in ids and far["id"] not in ids, str(ids))

        posted = run(se.run_heads_up_check())
        check("heads-up posted for every due event, including ours", posted == len(due) and posted >= 1, str(posted))
        near_msg = next((b["text"] for b in tg_calls if "Test Mega Sale" in b["text"]), None)
        check("post names the event and the store", near_msg and "Amazon" in near_msg, near_msg)
        check("goes to the configured channel", all(b["chat_id"] == "@dealradar18" for b in tg_calls))

        near_reloaded = next(e for e in se.list_all() if e["id"] == near["id"])
        check("event marked as announced", near_reloaded["heads_up_posted"] is True)

        before = len(tg_calls)
        run(se.run_heads_up_check())
        check("never announced twice", len(tg_calls) == before)

        print("\n=== ADMIN 'POST NOW' OVERRIDE ===")
        far_now = next(e for e in se.list_all() if e["id"] == far["id"])
        before = len(tg_calls)
        r = c.post(f"/api/admin/reader/sale-events/{far_now['id']}/post-now", headers=ADMIN)
        check("admin can force-post a far-off event", r.status_code == 200 and len(tg_calls) == before + 1, r.text)

        print("\n=== SURVIVES A RESTART (meta persistence) ===")
        se.reset_cache()
        reloaded = se.list_all()
        check("events survive a cache reset (meta-backed)",
              any(e["id"] == far["id"] and e["heads_up_posted"] for e in reloaded))

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
