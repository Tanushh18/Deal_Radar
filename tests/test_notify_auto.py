"""Notification auto mode (notify_auto.py) and the manual-mode hot-push fix.

Every time-dependent call gets an explicit `now` (IST clock times on a fixed
day), so nothing depends on when the test runs. Pushes are faked.

Run:  python -m tests.test_notify_auto
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from datetime import datetime

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="", PUSH_QUIET_HOURS="23-8", BROADCAST_MIN_SCORE="30", PUSHES_PER_CYCLE="2")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import devices, hot_push, notify_auto, push  # noqa: E402
from app.services.hot_push import IST  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []
ADMIN = {"X-Admin-Token": "t"}
TOKEN = "fcmTK:APA91b" + "t" * 140


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def T(h: int, m: int = 0, day: int = 1) -> float:
    """An IST clock time on 1 Oct 2026 (+day-1 days)."""
    return datetime(2026, 10, day, h, m, tzinfo=IST).timestamp()


# --- fakes -----------------------------------------------------------------

sent_pushes = []


async def fake_send_push_detailed(tokens, title, body, url, deal_id=None, image="", kind="", expires_at=0):
    sent_pushes.append({"tokens": list(tokens), "title": title, "kind": kind})
    return {"tokens": len(tokens), "accepted": len(tokens), "failed": 0, "errors": {}}


async def fake_send_push(tokens, title, body, url, deal_id=None, image="", kind="", expires_at=0):
    sent_pushes.append({"tokens": list(tokens), "title": title, "kind": kind})


async def no_loop():
    return None


push.send_push_detailed = fake_send_push_detailed
push.send_push = fake_send_push
notify_auto.loop = no_loop  # the lifespan task — tests drive tick() with their own clock


# --- helpers ---------------------------------------------------------------

def reset() -> None:
    for table in ("deals", "push_log", "devices", "device_notifications", "broadcasts", "price_history"):
        db.execute(f"DELETE FROM {table}")
    db.set_meta(notify_auto.PLAN_META_KEY, "")
    db.set_meta("last_hot_push_at", "0")
    for name in notify_auto.SETTINGS:
        db.set_meta(f"notify_auto_{name}", "")
    db.set_meta(notify_auto.MODE_META_KEY, "")
    notify_auto.reset_cache()
    sent_pushes.clear()


def add_deal(deal_id: str, now: float, score: float = 50, age_min: float = 120, is_lowest: int = 0,
             disc: int = 30, price: float = 1000, mrp: float = 1500, title: str = "", store: str = "amazon") -> None:
    db.execute(
        "INSERT INTO deals (id, title, product_key, price, mrp, discount_pct, store, image_url, status, score, "
        "is_lowest, first_seen_at, last_seen_at, expires_at, posted_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'live', ?, ?, ?, ?, ?, ?)",
        (deal_id, title or f"Gadget {deal_id}", f"pk-{deal_id}", price, mrp, disc, store,
         "https://img.example/x.jpg", score, is_lowest, now - age_min * 60, now, now + 86400, now - age_min * 60),
    )


def add_device(device_id: str, last_seen: float, token: str = TOKEN) -> None:
    db.execute("INSERT INTO devices (device_id, platform, push_token, created_at, last_seen_at) "
               "VALUES (?, 'android', ?, ?, ?)", (device_id, token, last_seen, last_seen))


def log_row(kind: str, at: float, key: str = "") -> None:
    db.execute("INSERT INTO push_log (product_key, deal_id, title, sent_at, kind, reach) VALUES (?, ?, ?, ?, ?, 1)",
               (key, key, f"{kind} push", at, kind))


def kinds_logged():
    return [r["kind"] for r in db.query("SELECT kind FROM push_log ORDER BY sent_at")]


def main() -> int:
    with TestClient(app) as c:
        reset()
        print("\n=== DEFAULT: MANUAL ===")
        check("mode defaults to manual", notify_auto.mode() == "manual")
        g = c.get("/api/admin/reader/notify-auto", headers=ADMIN)
        check("GET /notify-auto → 200", g.status_code == 200, g.text)
        check("GET says manual", g.json().get("mode") == "manual", g.text)
        add_deal("m1", T(12), score=95, age_min=5)
        check("manual mode: auto tick does nothing", asyncio.run(notify_auto.tick(T(12))) == {"status": "off"})
        check("…and nothing was pushed", not sent_pushes and not kinds_logged())

        print("\n=== MANUAL: SHORT CYCLES STILL PUSH (BUG FIX) ===")

        async def short_cycles():
            before = time.time()
            plan = hot_push.schedule_cycle(["m1"], 60)
            tasks = set(hot_push._pending)
            again = hot_push.schedule_cycle(["m2"], 60)
            third = hot_push.schedule_cycle([], 120)
            await asyncio.sleep(0)
            alive = all(not t.cancelled() and not t.done() for t in tasks)
            pending = len(hot_push._pending)
            hot_push._cancel_pending()
            return before, plan, again, third, alive, pending

        before, plan, again, third, alive, pending = asyncio.run(short_cycles())
        offsets = [p["fires_at"] - before for p in plan]
        check("a 60 s cycle still plans PUSHES_PER_CYCLE pushes", len(plan) == 2, str(plan))
        check("…spread over an hour-sized window, not 60 s",
              all(60 <= o <= 3601 for o in offsets) and max(offsets) > 1800, str(offsets))
        check("the next cycle doesn't cancel them", alive and pending == 2, f"alive={alive} pending={pending}")
        check("…and doesn't double-schedule (same plan returned)",
              [p["fires_at"] for p in again] == [p["fires_at"] for p in plan]
              and [p["fires_at"] for p in third] == [p["fires_at"] for p in plan], str(again))
        check("new deals from later cycles are remembered for the pending push", "m2" in hot_push._fresh)

        print("\n=== AUTO MODE TAKES OVER PACING ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(8, 0))
        check("mode is auto", notify_auto.mode() == "auto")

        async def cycle_in_auto():
            return hot_push.schedule_cycle(["x"], 60), len(hot_push._pending)
        planned, pend = asyncio.run(cycle_in_auto())
        check("schedule_cycle does nothing in auto mode", planned == [] and pend == 0, str(planned))

        print("\n=== QUIET HOURS ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(2, 0))
        add_deal("q1", T(2, 0), score=96, age_min=5)
        out = asyncio.run(notify_auto.tick(T(2, 0)))
        check("a crazy deal at 2 a.m. waits", out["crazy"]["status"] == "skipped"
              and out["crazy"]["why"] == "quiet hours", str(out))
        check("nothing went out in quiet hours", not sent_pushes and not kinds_logged())
        check("nudges also wait in quiet hours", notify_auto.run_nudges(T(23, 30)) == 0)

        print("\n=== CRAZY DEALS ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(8, 0))
        base = T(12, 0)
        add_deal("ord", base, score=60, age_min=10, disc=40)
        check("an ordinary good deal isn't 'crazy'", notify_auto.find_crazy(base) is None)
        add_deal("old", base, score=97, age_min=120)
        check("an exceptional deal first seen 2 h ago isn't 'crazy' (not fresh)", notify_auto.find_crazy(base) is None)
        add_deal("low", base, score=70, age_min=10, is_lowest=1, disc=74, price=5299, mrp=20000,
                 title="Loot: Philips Air Fryer HD9252 @ ₹5,299")
        found = notify_auto.find_crazy(base)
        check("lowest-ever at 74% off is crazy", found and found[0]["id"] == "low", str(found and found[2]))
        title, body = notify_auto.compose_crazy(found[0], found[2])
        both = f"{title} {body}"
        check("crazy copy comes from the pitara: filled in, specific, no fake urgency",
              "{" not in both and "₹5,299" in both and 0 < len(title) <= 90
              and not any(w in both.lower() for w in ("hurry", "fast", "gone", "last chance", "limited")), both)
        from app.config import settings as _settings
        _settings.notification_pitara_enabled = False
        try:
            title, body = notify_auto.compose_crazy(found[0], found[2])
        finally:
            _settings.notification_pitara_enabled = True
        check("pitara off -> the built-in crazy title is honest and specific",
              title.startswith("Lowest price ever: Philips Air Fryer") and "₹5,299" in title, title)
        check("…and its body states the discount and store, no fake urgency",
              "74% off right now" in body and "Amazon" in body
              and not any(w in body.lower() for w in ("hurry", "fast", "gone", "last chance")), body)

        out = asyncio.run(notify_auto.tick(base))
        check("sent as soon as the guards allow", out["crazy"]["status"] == "sent", str(out["crazy"]))
        check("…with kind crazy_deal (push + push_log + feed row)",
              sent_pushes and sent_pushes[-1]["kind"] == "crazy_deal" and kinds_logged() == ["crazy_deal"]
              and db.query_one("SELECT kind FROM broadcasts ORDER BY id DESC LIMIT 1")["kind"] == "crazy_deal",
              f"{sent_pushes} {kinds_logged()}")
        check("never the same deal twice", notify_auto.find_crazy(base + 60) is None)

        add_deal("big", base + 20 * 60, score=93, age_min=5)
        out = asyncio.run(notify_auto.tick(base + 20 * 60))
        check("a 2nd crazy deal waits for the 45-min gap", out["crazy"]["status"] == "skipped"
              and "gap 45 min" in out["crazy"]["why"], str(out["crazy"]))
        out = asyncio.run(notify_auto.tick(base + 46 * 60))
        check("…then goes out", out["crazy"]["status"] == "sent", str(out["crazy"]))
        db.execute("INSERT INTO price_history (product_key, price, store, seen_at) VALUES "
                   "('pk-half', 2000, 'amazon', ?), ('pk-half', 2100, 'amazon', ?), ('pk-half', 1900, 'amazon', ?)",
                   (base - 86400 * 3, base - 86400 * 2, base - 86400))
        add_deal("half", base + 95 * 60, score=55, age_min=5, price=900, mrp=2500, disc=64)
        found = notify_auto.find_crazy(base + 95 * 60)
        check("half its usual price is crazy (median rule)", found and found[0]["id"] == "half"
              and "below its usual" in found[2], str(found and found[2]))
        out = asyncio.run(notify_auto.tick(base + 95 * 60))
        check("crazy_per_day (2) is respected", out["crazy"]["status"] == "skipped"
              and "crazy-deal cap" in out["crazy"]["why"], str(out["crazy"]))
        check("exactly two crazy pushes today", kinds_logged().count("crazy_deal") == 2, str(kinds_logged()))

        print("\n=== DAILY CAP AND MIN GAP ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(8, 0))
        log_row("hot_deal", T(18, 0))
        check("min gap (120 min) blocks a push an hour later",
              (notify_auto.broadcast_guard(T(19, 0)) or "").startswith("last push only 60 min"),
              str(notify_auto.broadcast_guard(T(19, 0))))
        check("…and allows it after two hours", notify_auto.broadcast_guard(T(20, 1)) is None)
        check("nudges don't count as broadcasts", (log_row("nudge", T(20, 0)) or True)
              and notify_auto.broadcast_guard(T(20, 1)) is None)
        for h in (9, 11, 14):
            log_row("hot_deal", T(h, 0))
        check("daily cap (4) blocks the 5th", (notify_auto.broadcast_guard(T(21, 0)) or "").startswith("daily cap"),
              str(notify_auto.broadcast_guard(T(21, 0))))
        check("yesterday's pushes don't count today", notify_auto.today_counts(T(10, 0, day=2))["sent"] == 0)
        check("cap follows the setting", (notify_auto.update({"daily_cap": 6}, now=T(8, 0)) or True)
              and notify_auto.broadcast_guard(T(21, 0)) is None)

        print("\n=== BEST-DEAL SLOTS ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(8, 0))
        slot_day = {"day": "2026-10-01", "made_at": T(0, 5), "slots": [
            {"at": T(19, 0), "until": T(22, 0), "kind": "hot_deal", "status": "pending", "why": "evening"},
            {"at": T(19, 30), "until": T(22, 0), "kind": "hot_deal", "status": "pending", "why": "evening"},
        ]}
        notify_auto._save_plan(slot_day)
        asyncio.run(notify_auto.run_slots(T(19, 5)))
        plan = notify_auto._load_plan()["slots"]
        check("a due slot with nothing above the score bar is skipped", plan[0]["status"] == "skipped"
              and "no eligible deal" in plan[0]["why"], str(plan[0]))
        check("…and nothing was sent", not kinds_logged())
        add_deal("best", T(19, 30), score=80, age_min=60, title="Boat Airdopes 141")
        asyncio.run(notify_auto.run_slots(T(19, 31)))
        plan = notify_auto._load_plan()["slots"]
        check("a due slot sends the best deal via send_best", plan[1]["status"] == "sent", str(plan[1]))
        check("…logged as kind hot_deal", kinds_logged() == ["hot_deal"], str(kinds_logged()))
        notify_auto._save_plan({"day": "2026-10-01", "made_at": T(0, 5), "slots": plan + [
            {"at": T(20, 0), "until": T(22, 0), "kind": "hot_deal", "status": "pending", "why": "evening"}]})
        add_deal("best2", T(20, 0), score=75, age_min=60)
        asyncio.run(notify_auto.run_slots(T(20, 5)))
        s3 = notify_auto._load_plan()["slots"][2]
        check("a slot inside the min gap waits", s3["status"] == "pending" and s3["why"].startswith("waiting"), str(s3))
        asyncio.run(notify_auto.run_slots(T(22, 1)))
        s3 = notify_auto._load_plan()["slots"][2]
        check("…and is skipped once its window closes", s3["status"] == "skipped"
              and s3["why"].startswith("window closed"), str(s3))

        print("\n=== PLAN: ONCE A DAY, PERSISTED ===")
        reset()
        for i in range(6):
            add_deal(f"p{i}", T(0, 30), score=60 + i, age_min=60)
        notify_auto.update({"mode": "auto"}, now=T(0, 30))
        plan = notify_auto.ensure_plan(T(0, 30))
        pending = [s for s in plan["slots"] if s["status"] == "pending"]
        check("plan sized to daily cap minus the crazy reserve (4-1=3)", len(pending) == 3, str(len(pending)))
        in_windows = all(any(s <= p["at"] <= e for s, e, _ in notify_auto._windows(T(0, 30))) for p in pending)
        check("every slot sits inside a prime window", in_windows, str([p["at"] for p in pending]))
        notify_auto.reset_cache()
        again = notify_auto.ensure_plan(T(3, 0))
        check("a restart (cache reset) reuses the saved plan — no re-plan",
              [s["at"] for s in again["slots"]] == [s["at"] for s in plan["slots"]])
        tomorrow = notify_auto.ensure_plan(T(0, 30, day=2))
        check("a new day gets a new plan", tomorrow["day"] == "2026-10-02")
        reset()
        add_deal("only", T(0, 30), score=60, age_min=60)
        notify_auto.update({"mode": "auto"}, now=T(0, 30))
        few = [s for s in notify_auto.ensure_plan(T(0, 30))["slots"] if s["status"] == "pending"]
        check("few good candidates → fewer slots", len(few) == 1, str(len(few)))
        reset()
        notify_auto.update({"mode": "auto"}, now=T(20, 0))
        late = notify_auto.ensure_plan(T(20, 0))["slots"]
        check("switched on at 8 pm: only evening slots after now",
              all(T(20, 0) < s["at"] <= T(22, 0) for s in late), str([s["at"] for s in late]))

        print("\n=== RE-ENGAGEMENT NUDGES ===")
        reset()
        notify_auto.update({"mode": "auto"}, now=T(8, 0))
        evening = T(19, 0)
        add_device("lapsed", evening - 5 * 86400)
        add_device("recent", evening - 1 * 86400)
        add_device("notoken", evening - 5 * 86400, token="")
        for i in range(12):
            add_deal(f"n{i}", evening, score=50 + i, age_min=60, price=499 + i, title=f"Nike Shoe {i}")
        add_deal("nlow", evening, score=45, age_min=60, price=1299, is_lowest=1, disc=48, title="Puma Backpack")
        db.execute("UPDATE deals SET expires_at = ?", (T(0, 0, day=30),))  # still live across the whole fortnight
        check("outside the evening window: no nudges", notify_auto.run_nudges(T(17, 0)) == 0)
        check("preview counts eligible devices", notify_auto.preview(evening)["nudge_eligible"] == 1)
        sent = notify_auto.run_nudges(evening)
        check("only the device inactive >= 3 days with a token is nudged", sent == 1, str(sent))
        db.execute("DELETE FROM devices WHERE device_id IN ('recent', 'notoken')")  # keep the rest about one device
        row = db.query_one("SELECT * FROM device_notifications WHERE kind = 'nudge'")
        check("it's a targeted notify(), not a broadcast", row and row["device_id"] == "lapsed"
              and not db.query_one("SELECT 1 FROM broadcasts"), str(row))
        nudge_text = f"{row['title']} {row['body']}" if row else ""
        check("nudge text is filled in and only quotes real data (13 new deals, the top pick n11 at ₹510)",
              row and "{" not in nudge_text and any(s in nudge_text for s in ("13", "₹510", "Nike Shoe 11")),
              nudge_text)
        check("the top deal is attached (so the phone picks the moment)", row and row["deal_id"] == "n11")
        check("logged once for the admin as 'nudge'", kinds_logged() == ["nudge"])
        check("not twice within nudge_every_days", notify_auto.run_nudges(evening + 3600) == 0)
        check("…nor the next evening", notify_auto.run_nudges(T(19, 0, day=2)) == 0)
        check("…nor exactly 3 days later to the minute (strictly older)",
              notify_auto.run_nudges(T(18, 59, day=4)) == 0)
        second = notify_auto.run_nudges(T(19, 0, day=5))
        check("again after 3 days", second == 1, str(second))
        rows = db.query("SELECT title, deal_id FROM device_notifications WHERE kind = 'nudge' ORDER BY id")
        check("the content rotates (2nd nudge: a sale within 3 days, from the calendar)",
              len(rows) == 2 and rows[1]["title"] != rows[0]["title"]
              and any(n in rows[1]["title"] for n in ("Festival", "Big Billion", "Sale")), str(rows))
        check("third nudge", notify_auto.run_nudges(T(19, 0, day=9)) == 1)
        rows = db.query("SELECT title, deal_id FROM device_notifications WHERE kind = 'nudge' ORDER BY id")
        third = db.query_one("SELECT title, body FROM device_notifications WHERE kind = 'nudge' ORDER BY id DESC LIMIT 1")
        check("3rd variant: the best lowest-ever deal", len(rows) == 3 and rows[2]["deal_id"] == "nlow"
              and "₹1,299" in f"{third['title']} {third['body']}", str(rows[-1:]))
        check("stops after nudge_max (3)", notify_auto.run_nudges(T(19, 0, day=13)) == 0)
        count = db.query_one("SELECT nudges_since_seen FROM devices WHERE device_id = 'lapsed'")["nudges_since_seen"]
        check("bookkeeping counts nudges since last seen", count == 3, str(count))
        devices.register("lapsed", "android")
        row = db.query_one("SELECT nudges_since_seen, turso_dirty FROM devices WHERE device_id = 'lapsed'")
        check("seeing the device again resets the count", row["nudges_since_seen"] == 0, str(dict(row)))
        db.execute("UPDATE devices SET last_seen_at = ? WHERE device_id = 'lapsed'", (T(19, 0, day=13),))
        check("…so a later lapse can be nudged again", notify_auto.run_nudges(T(19, 0, day=17)) == 1)
        notify_auto.update({"nudge_max": 0}, now=T(8, 0))
        db.execute("UPDATE devices SET last_nudge_at = 0, nudges_since_seen = 0")
        check("nudge_max 0 turns nudges off", notify_auto.run_nudges(T(19, 0, day=20)) == 0)

        print("\n=== ADMIN API ===")
        reset()
        add_device("lapsed", time.time() - 5 * 86400)  # the admin API runs on the real clock
        for i in range(8):
            add_deal(f"a{i}", time.time(), score=60 + i, age_min=10)
        log_row("nudge", time.time() - 60)
        g = c.get("/api/admin/reader/notify-auto", headers=ADMIN).json()
        keys = {"mode", "settings", "quiet_hours", "quiet_now", "today", "plan", "next", "recent",
                "reachable_devices", "inactive_devices"}
        check("GET has exactly the contract keys", set(g) == keys, str(set(g) ^ keys))
        check("settings defaults", g["settings"] == {"daily_cap": 4, "min_gap_minutes": 120, "crazy_per_day": 2,
                                                     "nudge_after_days": 3, "nudge_every_days": 3, "nudge_max": 3},
              str(g["settings"]))
        check("today block", set(g["today"]) == {"sent", "cap", "crazy_sent", "nudges_sent"} and g["today"]["cap"] == 4,
              str(g["today"]))
        check("recent shows audience + count", g["recent"] and g["recent"][0]["audience"] == "inactive users"
              and g["recent"][0]["kind"] == "nudge" and g["recent"][0]["count"] == 1, str(g["recent"]))
        check("inactive devices counted", g["inactive_devices"] == 1, str(g["inactive_devices"]))
        check("quiet hours echoed", g["quiet_hours"] == "23-8" and isinstance(g["quiet_now"], bool))
        for bad in ({"daily_cap": 0}, {"daily_cap": 13}, {"min_gap_minutes": 29}, {"crazy_per_day": 6},
                    {"nudge_after_days": 31}, {"nudge_max": 11}, {"mode": "sometimes"}, {"daily_cap": "4"},
                    {"daily_cap": 2.5}, {"bogus": 1}):
            r = c.post("/api/admin/reader/notify-auto", json=bad, headers=ADMIN)
            check(f"rejects {bad} with 400", r.status_code == 400, f"{r.status_code} {r.text}")
        check("rejected values weren't saved", notify_auto.current_settings()["daily_cap"] == 4)
        r = c.post("/api/admin/reader/notify-auto", json={"mode": "auto", "daily_cap": 6, "nudge_max": 2},
                   headers=ADMIN)
        body = r.json()
        check("POST → 200 with the GET body", r.status_code == 200 and set(body) == keys, r.text)
        check("POST applied", body["mode"] == "auto" and body["settings"]["daily_cap"] == 6
              and body["settings"]["nudge_max"] == 2, str(body.get("settings")))
        check("switching on shows today's plan", isinstance(body["plan"], list)
              and all(set(p) == {"at", "kind", "status", "why"} for p in body["plan"]), str(body["plan"]))
        check("next is a pending slot or null", body["next"] is None or set(body["next"]) == {"at", "kind", "why"},
              str(body["next"]))
        notify_auto.reset_cache()
        check("settings persisted in meta", notify_auto.mode() == "auto"
              and notify_auto.current_settings()["daily_cap"] == 6)
        r = c.post("/api/admin/reader/notify-auto", json={"mode": "manual"}, headers=ADMIN)
        check("back to manual", r.status_code == 200 and r.json()["mode"] == "manual")
        p = c.get("/api/admin/reader/notify-auto/preview", headers=ADMIN)
        pv = p.json()
        check("preview → 200 with the contract keys", p.status_code == 200
              and set(pv) == {"crazy", "best", "nudge_eligible", "sample_nudge"}, p.text)
        check("preview finds the best deal", pv["best"] and pv["best"]["id"] == "a7", str(pv["best"]))
        check("preview flags nothing crazy among ordinary deals", pv["crazy"] is None, str(pv["crazy"]))
        check("preview samples a nudge", pv["nudge_eligible"] == 1 and pv["sample_nudge"]
              and set(pv["sample_nudge"]) == {"title", "body"}, str(pv))
        check("preview sent nothing", not sent_pushes and kinds_logged() == ["nudge"], str(kinds_logged()))
        check("old push-status endpoint still works",
              c.get("/api/admin/reader/push-status", headers=ADMIN).status_code == 200)

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
