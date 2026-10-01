"""The notification-copy writer: shared Groq budget, safety rules, review, persistence.

Groq is faked (no network). Run:  python -m tests.test_pitara_writer
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="", GROQ_API_KEY="fake-groq-key", GROQ_MAX_TOKENS_PER_DAY="10000",
                  GROQ_MAX_REQUESTS_PER_DAY="100", PITARA_WRITER_TOKENS_PER_DAY="6000",
                  PITARA_WRITER_MAX_CALLS_PER_RUN="1", PITARA_WRITER_LEAVE_FREE_PCT="30")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import db  # noqa: E402
from app.main import app  # noqa: E402
from app.services import ai_enrich, pitara, pitara_lint, pitara_writer  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []
ADMIN = {"X-Admin-Token": "t"}


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


# --- a fake Groq -----------------------------------------------------------------------

calls = []
reply = {"status": 200, "lines": [], "tokens": 1500, "content": None}


def groq(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    calls.append(body)
    if reply["status"] != 200:
        return httpx.Response(reply["status"], json={"error": "x"})
    content = reply["content"] if reply["content"] is not None else json.dumps(reply["lines"])
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}],
                                     "usage": {"total_tokens": reply["tokens"]}})


class _FakeHttpx:
    def __getattr__(self, name):
        return getattr(httpx, name)

    def AsyncClient(self, **kw):  # noqa: N802
        return httpx.AsyncClient(transport=httpx.MockTransport(groq), **kw)


from app.services import pitara_writer as _pw  # noqa: E402

_pw.httpx = ai_enrich.httpx = _FakeHttpx()
_pw.MIN_GAP_SECONDS = 0.0  # the real 25 s spacing would just slow the test

GOOD = [
    {"mood": "chai", "tone": "tease", "title": "Chai garam, deal us se bhi garam ☕", "body": "{name} at {price}. Pakoda baad mein."},
    {"mood": "sunday", "tone": "dost", "title": "Sunday ka plan: ek deal aur ek neend", "body": "{name} — {discount}% off. Uthna nahi padega."},
    {"mood": "ghar", "tone": "self", "title": "Mummy ne poocha 'ye kab liya?' 😅", "body": "{name}, {price}. Jawab ready hai."},
]


def fresh_run(lines, **kw):
    reply.update(status=200, lines=lines, content=None, tokens=kw.get("tokens", 1500))
    calls.clear()
    return asyncio.run(pitara_writer.run_once(force=True))


def reset_state():
    db.execute("DELETE FROM meta WHERE key LIKE 'pitara_%'")
    db.execute("DELETE FROM ai_usage")
    pitara_writer.reset_cache()
    pitara.reset()


with TestClient(app) as client:
    reset_state()

    print("\n=== THE RULES FOR DRAFTED LINES ===")
    ok_line = dict(GOOD[0], category="Any", needs=["price"])
    check("a clean draft passes", not pitara_lint.check_drafted(ok_line), str(pitara_lint.check_drafted(ok_line)))
    for label, mutate in [
        ("a price written into the text", lambda t: t.update(body="{name} sirf ₹499 mein.")),
        ("a number written into the text", lambda t: t.update(body="{name}, 50 percent off bilkul.")),
        ("a percentage", lambda t: t.update(title="Dekho 70% ki chhoot")),
        ("no product named", lambda t: t.update(body="Aaj kuch khaas hai {price}.")),
        ("fake urgency", lambda t: t.update(body="{name} — selling fast, hurry.")),
        ("a lowest-price claim", lambda t: t.update(body="{name}: ab tak ki sabse kam price.")),
        ("a real brand", lambda t: t.update(body="{name} amazon se bhi sasti.")),
        ("a link", lambda t: t.update(body="{name} dekho https://x.in")),
        ("devanagari", lambda t: t.update(title="आज की डील")),
        ("an unknown placeholder", lambda t: t.update(body="{name} {coupon_x}")),
        ("too long", lambda t: t.update(body="{name} " + "bahut lamba " * 20)),
    ]:
        t = dict(ok_line)
        mutate(t)
        t["needs"] = pitara_lint.needs_for(t["title"], t["body"])
        check(f"refused: {label}", bool(pitara_lint.check_drafted(t)))

    print("\n=== A RUN ===")
    check("off by default", pitara_writer.enabled() is False and pitara_writer.auto_approve() is False)
    check("a scheduled run does nothing while it's switched off",
          asyncio.run(pitara_writer.run_once())["why"] == "switched off" and not calls)
    r = fresh_run(GOOD + [{"mood": "x", "tone": "tease", "title": "Sirf ₹99 mein!", "body": "{name} ₹99"},
                          {"mood": "x", "tone": "tease", "title": "Selling fast 🔥", "body": "{name} hurry {price}"}])
    check("good drafts are kept, bad ones refused with a reason", r["added"] == 3 and sum(r["rejected"].values()) == 2, str(r))
    st = pitara_writer.status()
    check("drafts wait for review — nothing is live", st["counts"]["pending"] == 3 and st["counts"]["approved"] == 0)
    check("the prompt tells the model the rules",
          "NEVER write numbers" in calls[0]["messages"][0]["content"] and "{name}" in calls[0]["messages"][0]["content"])
    ids = [x["id"] for x in st["pending"]]
    check("pending drafts aren't in the pitara or the app's list",
          not any(i in {t["id"] for t in pitara.load(force=True)[0]} for i in ids))

    print("\n=== REPEATS ===")
    r = fresh_run(GOOD)
    check("the same lines again are refused as repeats", r["added"] == 0 and r["rejected"].get("repeats an existing line") == 3, str(r))
    shipped = pitara.load()[0][0]
    r = fresh_run([{"mood": "x", "tone": "dost", "title": shipped["title"], "body": "{name}, bilkul alag body."}])
    check("a shipped title reused is refused", r["added"] == 0, str(r))

    print("\n=== REVIEW ===")
    v0 = pitara.version()
    res = client.post("/api/admin/reader/pitara/decide", json={"ids": ids[:2], "action": "approve"}, headers=ADMIN).json()
    check("approving moves lines out of the queue", res["changed"] == 2 and res["counts"]["pending"] == 1
          and res["counts"]["approved"] == 2, str(res["counts"]))
    served = {t["id"]: t for t in client.get("/api/notification-templates").json()["templates"]}
    check("approved lines are served to the app, flagged as AI-drafted", all(i in served and served[i].get("source") == "ai"
                                                                         for i in ids[:2]))
    check("…and the version changes so phones re-fetch", pitara.version() != v0)
    check("the third (still pending) isn't served", ids[2] not in served)
    approved_now = [t for t in pitara.load(force=True)[0] if t["id"] in ids[:2]]
    deal = {"id": "d", "title": "Some Kurta", "price": 499, "mrp": 999, "discount_pct": 50, "store": "myntra"}
    check("an approved line is eligible, for its own category, like any shipped one",
          len(approved_now) == 2 and all(pitara._eligible(t, "hot_deal", dict(deal, category=t["category"]), {}, None,
                                                           time.time(), t["category"]) for t in approved_now),
          str(approved_now))
    check("…and is not offered for another category's deal",
          not any(pitara._eligible(t, "hot_deal", dict(deal, category="Zzz"), {}, None, time.time(), "Zzz")
                  for t in approved_now if t["category"] != "Any"))
    client.post("/api/admin/reader/pitara/decide", json={"ids": [ids[2]], "action": "reject"}, headers=ADMIN)
    check("rejecting removes it for good", pitara_writer.status()["counts"]["rejected"] >= 1)
    client.post("/api/admin/reader/pitara/decide", json={"ids": [ids[0]], "action": "disable"}, headers=ADMIN)
    check("disable pulls an approved line back", ids[0] not in {t["id"] for t in pitara.load(force=True)[0]})
    shipped_id = pitara.load()[0][0]["id"]
    client.post("/api/admin/reader/pitara/decide", json={"ids": [shipped_id], "action": "disable"}, headers=ADMIN)
    check("a shipped line can be switched off by id", shipped_id not in {t["id"] for t in pitara.load(force=True)[0]}
          and pitara_writer.status()["counts"]["switched_off"] == 1)
    client.post("/api/admin/reader/pitara/decide", json={"ids": [shipped_id], "action": "enable"}, headers=ADMIN)
    check("…and back on", shipped_id in {t["id"] for t in pitara.load(force=True)[0]})
    bad = client.post("/api/admin/reader/pitara/decide", json={"ids": ["x"], "action": "launch"}, headers=ADMIN)
    check("a bad action is a readable 400", bad.status_code == 400 and "action must be" in bad.text)
    check("the admin endpoints need the token", client.get("/api/admin/reader/pitara").status_code == 403)

    print("\n=== SURVIVES A RESTART ===")
    before = {x["id"] for x in pitara_writer.approved_lines()}
    pitara_writer.reset_cache()
    pitara.reset()
    check("approved lines come back from the database", {x["id"] for x in pitara_writer.approved_lines()} == before and before)

    print("\n=== THE SHARED BUDGET ===")
    reset_state()
    r = fresh_run([dict(GOOD[0], title="Pehli deal ka mood ☕")], tokens=1500)
    b = pitara_writer.budget()
    check("a call is counted in the shared counter AND the writer's own",
          b["shared_tokens"] == 1500 and b["writer_tokens"] == 1500 and b["shared_requests"] == 1 and b["writer_requests"] == 1, str(b))
    reset_state()
    db.execute("INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, 'primary', 5, 7500)", (ai_enrich._today(),))
    r = fresh_run(GOOD)
    check("steps back once the shared budget is mostly spent (cap 10000, keeps 30%)",
          r["status"] == "skipped" and "enrichment" in r["why"] and not calls, str(r))
    reset_state()
    db.execute("INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, 'writer', 3, 6000)", (ai_enrich._today(),))
    r = fresh_run(GOOD)
    check("stops at its own daily allowance", r["status"] == "skipped" and "allowance" in r["why"] and not calls, str(r))
    reset_state()
    db.execute("INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, 'primary', 100, 100)", (ai_enrich._today(),))
    r = fresh_run(GOOD)
    check("never runs when the shared budget is exhausted", not calls and r["status"] == "skipped")
    reset_state()
    pitara_writer.settings.pitara_writer_max_calls_per_run = 3
    r = fresh_run([dict(GOOD[0], title=f"Idea {chr(97 + i)} ke saath chai ☕", body="{name} " + w) for i, w in
                   enumerate(["alag hai.", "mast hai.", "zabardast."])], tokens=3000)
    check("a run is capped by the allowance (6000 tokens, 3000 a call: two calls, not three)",
          r["calls"] == 2 and pitara_writer.budget()["writer_tokens"] == 6000, str(r))
    pitara_writer.settings.pitara_writer_max_calls_per_run = 1
    reset_state()
    reply.update(status=429)
    calls.clear()
    r = asyncio.run(pitara_writer.run_once(force=True))
    check("a 429 ends the run, nothing raised, nothing stored", r["status"] == "skipped" and r["why"] == "rate-limited"
          and pitara_writer.status()["counts"]["pending"] == 0, str(r))
    reply.update(status=500)
    r = asyncio.run(pitara_writer.run_once(force=True))
    check("a server error ends the run quietly", r["status"] == "skipped" and "500" in r["why"])
    reply.update(status=200, content="sorry, I can't do that")
    r = asyncio.run(pitara_writer.run_once(force=True))
    check("unreadable output adds nothing and breaks nothing", r["added"] == 0 and r["status"] in ("ok", "skipped"), str(r))
    reply.update(content="```json\n" + json.dumps([dict(GOOD[1], title="Fenced hai par theek hai 😄")]) + "\n```")
    r = asyncio.run(pitara_writer.run_once(force=True))
    check("code-fenced JSON is still read", r["added"] == 1, str(r))

    print("\n=== QUEUE AND AUTO-PUBLISH ===")
    reset_state()
    pitara_writer.settings.pitara_writer_max_pending = 1
    fresh_run([dict(GOOD[0], title="Queue test ek ☕")])
    r = fresh_run([dict(GOOD[1], title="Queue test do 😄")])
    check("a full review queue pauses drafting", "waiting for review" in r["why"] and not calls, str(r))
    pitara_writer.settings.pitara_writer_max_pending = 120
    reset_state()
    res = client.post("/api/admin/reader/pitara/settings", json={"enabled": True, "auto_approve": True}, headers=ADMIN).json()
    check("the switches are admin settings", res["enabled"] is True and res["auto_approve"] is True)
    bad = client.post("/api/admin/reader/pitara/settings", json={"enabled": "yes"}, headers=ADMIN)
    check("a non-boolean switch is a readable 400", bad.status_code == 400)
    r = fresh_run([dict(GOOD[2], title="Auto publish wali line 🙈")])
    check("auto-publish still only lets lint-clean lines in, and they go live at once",
          r["added"] == 1 and pitara_writer.status()["counts"]["approved"] == 1)
    check("…but not lines that break the rules", fresh_run([{"mood": "x", "tone": "tease", "title": "Hurry 50% off", "body": "{name}"}])["added"] == 0)

    print("\n=== THE ENDPOINT, THE LOOP, THE PANEL API ===")
    reply.update(status=200, lines=[dict(GOOD[1], title="Admin ne dabaya Write now 🙂")], content=None)
    res = client.post("/api/admin/reader/pitara/run", headers=ADMIN).json()
    check("'Write now' runs one batch and reports it", res["report"]["added"] == 1 and "pending" in res["counts"], str(res["report"]))
    st = client.get("/api/admin/reader/pitara", headers=ADMIN).json()
    check("status carries budget, counts and the queue",
          {"enabled", "auto_approve", "budget", "counts", "pending", "last_run"} <= set(st) and st["budget"]["writer_token_cap"] == 6000)
    pitara_writer.configure(enabled_=False)
    check("the nightly loop is part of the app (started from main)",
          "pitara_writer.loop()" in open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app", "main.py")).read())

print()
if failures:
    print(f"\033[91m✗ {len(failures)} failed\033[0m: " + "; ".join(failures))
    sys.exit(1)
print("\033[92m✓ All checks passed.\033[0m")
