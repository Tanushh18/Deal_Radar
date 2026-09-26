"""Poll interval: 1–60 min manual range, auto mode, Telegram flood-wait safety.

Run:  python -m tests.test_autopoll
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="", POLL_INTERVAL_SECONDS="2400")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import activity, autopoll, ingest  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []
ADMIN = {"X-Admin-Token": "t"}
BROWSER_UA = "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/126 Mobile Safari/537.36"


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def set_live_deals(n: int) -> None:
    db.execute("DELETE FROM deals")
    now = time.time()
    db.execute_many(
        "INSERT INTO deals (id, title, status, expires_at, first_seen_at, last_seen_at) VALUES (?, ?, 'live', ?, ?, ?)",
        [(f"d{i}", f"Deal {i}", now + 86400, now, now) for i in range(n)],
    )


def set_app_users(n: int) -> None:
    db.execute("DELETE FROM devices")
    now = time.time()
    db.execute_many(
        "INSERT INTO devices (device_id, platform, created_at, last_seen_at) VALUES (?, 'android', ?, ?)",
        [(f"dev{i}", now, now) for i in range(n)],
    )


def fresh_state() -> None:
    """Quiet world: plenty of deals, nobody around, no new deals, no flood."""
    autopoll.reset_cache()
    db.set_meta(autopoll.FLOOD_META_KEY, "")
    activity.reset()
    set_live_deals(400)
    set_app_users(0)


def main() -> int:
    with TestClient(app) as c:
        print("\n=== MANUAL RANGE: 1 MIN – 60 MIN ===")
        g = c.get("/api/admin/reader/poll-interval", headers=ADMIN).json()
        check("range is 60..3600", g["min_seconds"] == 60 and g["max_seconds"] == 3600, str(g))
        check("defaults to manual mode", g["mode"] == "manual", str(g))
        for ok_value in (60, 3600):
            r = c.post("/api/admin/reader/poll-interval", json={"seconds": ok_value}, headers=ADMIN)
            check(f"accepts {ok_value}", r.status_code == 200 and r.json() == {"status": "ok", "seconds": ok_value},
                  r.text)
            check(f"{ok_value} is in effect", ingest.poll_interval_seconds() == ok_value,
                  str(ingest.poll_interval_seconds()))
        for bad in (59, 3601):
            r = c.post("/api/admin/reader/poll-interval", json={"seconds": bad}, headers=ADMIN)
            check(f"rejects {bad}", r.status_code == 400, r.text)
        c.post("/api/admin/reader/poll-interval", json={"seconds": 60}, headers=ADMIN)

        print("\n=== STALE OVERRIDE OUTSIDE THE RANGE ===")
        db.set_meta(ingest.POLL_INTERVAL_META_KEY, "21600")
        ingest.reset_poll_interval_cache()
        check("old 6 h override falls back to the env default", ingest.poll_interval_seconds() == 2400,
              str(ingest.poll_interval_seconds()))
        c.post("/api/admin/reader/poll-interval", json={"seconds": 60}, headers=ADMIN)

        print("\n=== SWITCH TO AUTO ===")
        fresh_state()
        r = c.post("/api/admin/reader/poll-mode", json={"mode": "auto"}, headers=ADMIN)
        body = r.json()
        check("POST /poll-mode auto → 200", r.status_code == 200, r.text)
        check("returns the GET body with mode=auto", body.get("mode") == "auto", str(body))
        keys = {"mode", "seconds", "manual_seconds", "default_seconds", "is_override", "min_seconds",
                "max_seconds", "auto"}
        check("body has exactly the contract keys", set(body) == keys, str(set(body) ^ keys))
        auto = body["auto"]
        check("auto block keys", set(auto) == {"min_seconds", "max_seconds", "decided_at", "reason", "signals"},
              str(auto))
        sig_keys = {"live_deals", "target_live", "active_users", "app_users", "web_users", "new_per_cycle",
                    "flood_wait_until"}
        check("signals keys", set(auto["signals"]) == sig_keys, str(set(auto["signals"]) ^ sig_keys))
        check("default auto bounds 120..1800", auto["min_seconds"] == 120 and auto["max_seconds"] == 1800, str(auto))
        check("manual value kept alongside", body["manual_seconds"] == 60 and body["is_override"] is True, str(body))
        check("quiet world → auto sits at max", body["seconds"] == 1800, str(body["seconds"]))
        check("reason is human readable", "live deals" in auto["reason"] and "every 30 min" in auto["reason"],
              auto["reason"])
        check("decided_at is set", isinstance(auto["decided_at"], float))
        check("ingest uses the auto value", ingest.poll_interval_seconds() == 1800)
        g = c.get("/api/admin/reader/poll-interval", headers=ADMIN).json()
        check("GET agrees with POST", g["mode"] == "auto" and g["seconds"] == 1800, str(g))

        print("\n=== FEW LIVE DEALS → FASTER ===")
        fresh_state()
        autopoll.set_mode("auto")
        set_live_deals(30)
        d = autopoll.decide()
        check("30 live deals (target 300) → well under max", d["seconds"] < 1800 // 2, str(d["seconds"]))
        check("signals report live deals", d["signals"]["live_deals"] == 30)

        print("\n=== MANY USERS → FASTER ===")
        fresh_state()
        autopoll.set_mode("auto")
        set_app_users(15)
        for i in range(15):
            c.get("/api/deals", headers={"User-Agent": BROWSER_UA, "X-Forwarded-For": f"10.0.0.{i}"})
        c.get("/api/deals", headers={"User-Agent": BROWSER_UA, "X-Forwarded-For": "10.0.0.1"})  # repeat visitor
        c.get("/api/deals", headers={"User-Agent": "okhttp/4.12.0", "X-Forwarded-For": "10.9.9.9"})  # the app
        d = autopoll.decide()
        s = d["signals"]
        check("web visitors counted once each, app traffic excluded", s["web_users"] == 15, str(s))
        check("app users from devices", s["app_users"] == 15, str(s))
        check("30 people → pressure >= 0.8 → jumps to the floor", d["seconds"] == 120, str(d["seconds"]))
        check("reason mentions the people", "30 people" in d["reason"], d["reason"])
        check("no raw IPs stored", not any("10.0.0" in k for k in activity._seen), str(list(activity._seen)[:3]))

        print("\n=== BUSY CHANNELS (YIELD) → FASTER ===")
        fresh_state()
        autopoll.set_mode("auto")
        for n in (10, 12, 8):
            autopoll.record_cycle(n)
        d = autopoll.decide()
        check("~10 new/cycle → faster than max", d["seconds"] < 1800, str(d["seconds"]))
        check("yield alone never pins the floor", d["seconds"] > 120, str(d["seconds"]))

        print("\n=== QUIET → DRIFTS BACK TOWARD MAX ===")
        fresh_state()
        autopoll.set_mode("auto")
        set_live_deals(0)
        low = autopoll.decide()["seconds"]
        set_live_deals(400)
        steps = [autopoll.decide()["seconds"] for _ in range(6)]
        check("starts fast with no deals", low == 120, str(low))
        check("smoothed: first step goes half-way, not all the way", 120 < steps[0] < 1800, str(steps))
        check("keeps rising", all(a <= b for a, b in zip(steps, steps[1:])), str(steps))
        check("ends at/near max", steps[-1] >= 1700, str(steps))
        check("always on a 30 s grid", all(x % 30 == 0 for x in [low, *steps]), str(steps))

        print("\n=== TELEGRAM FLOOD WAIT ===")
        fresh_state()
        autopoll.set_mode("auto", auto_min=60, auto_max=300)
        set_live_deals(0)
        autopoll.decide()
        autopoll.note_flood_wait(420)
        check("auto: flood forces >= 840 (2x wait)", ingest.poll_interval_seconds() == 840,
              str(ingest.poll_interval_seconds()))
        g = c.get("/api/admin/reader/poll-interval", headers=ADMIN).json()
        check("GET shows it + flood_wait_until", g["seconds"] == 840 and g["auto"]["signals"]["flood_wait_until"],
              str(g))
        check("reason says why", "Telegram" in g["auto"]["reason"], g["auto"]["reason"])
        autopoll.set_mode("manual")
        check("manual 60 s: flood forces >= 840 too", ingest.poll_interval_seconds() == 840,
              str(ingest.poll_interval_seconds()))
        autopoll.note_flood_wait(30)
        check("short flood still floors at 600", ingest.poll_interval_seconds() == 600,
              str(ingest.poll_interval_seconds()))
        autopoll.note_flood_wait(5000)
        check("huge flood capped at 3600", ingest.poll_interval_seconds() == 3600)
        autopoll.reset_cache()
        check("flood guard survives a restart", ingest.poll_interval_seconds() == 3600,
              str(ingest.poll_interval_seconds()))
        autopoll.note_flood_wait(30, now=time.time() - 3700)
        check("guard lifts after an hour → manual 60 again", ingest.poll_interval_seconds() == 60,
              str(ingest.poll_interval_seconds()))

        print("\n=== INVALID /poll-mode ===")
        fresh_state()
        for bad in ({"mode": "turbo"},
                    {"mode": "auto", "auto_min_seconds": 59},
                    {"mode": "auto", "auto_max_seconds": 3601},
                    {"mode": "auto", "auto_min_seconds": 900, "auto_max_seconds": 600}):
            r = c.post("/api/admin/reader/poll-mode", json=bad, headers=ADMIN)
            check(f"rejects {bad}", r.status_code == 400, r.text)
        check("nothing changed after rejects", autopoll.mode() == "manual")
        r = c.post("/api/admin/reader/poll-mode",
                   json={"mode": "auto", "auto_min_seconds": 60, "auto_max_seconds": 3600}, headers=ADMIN)
        check("accepts full 60..3600 bounds", r.status_code == 200 and r.json()["auto"]["min_seconds"] == 60
              and r.json()["auto"]["max_seconds"] == 3600, r.text)
        check("needs the admin token", c.post("/api/admin/reader/poll-mode", json={"mode": "auto"}).status_code
              in (401, 403))

        print("\n=== SURVIVES A RESTART (meta persistence) ===")
        autopoll.reset_cache()
        ingest.reset_poll_interval_cache()
        check("mode survives", autopoll.mode() == "auto")
        check("bounds survive", autopoll.bounds() == (60, 3600), str(autopoll.bounds()))
        check("manual value survives", ingest.manual_poll_interval_seconds() == 60)
        autopoll.decide()
        before = db.get_meta
        db.get_meta = lambda *a, **k: (_ for _ in ()).throw(AssertionError("DB hit"))  # type: ignore[assignment]
        try:
            ok = c.get("/api/ping").status_code == 200
        except AssertionError:
            ok = False
        finally:
            db.get_meta = before  # type: ignore[assignment]
        check("/api/ping stays DB-free once cached", ok)
        c.post("/api/admin/reader/poll-mode", json={"mode": "manual"}, headers=ADMIN)

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
