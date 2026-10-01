"""The model drafts new notification lines; a person (or, if switched on, the lint) lets them in.

The model never writes a notification that goes out. It writes *templates* with
placeholders ({name}, {price}, {discount}…) for the pitara — the numbers, the
claims and the sending stay in code. Every draft has to pass the same rules as
the shipped lines plus stricter ones for lines nobody has read yet
(pitara_lint.check_drafted: no digits, no price/percent in the text, no claim
the code can't back), and can't repeat what's already there.

Shares Groq with deal enrichment and sale blurbs (services/ai_enrich.py) — one
key, one daily budget — so it is built to take a small slice and step back first:
  * its own tokens-per-day ceiling,
  * no run at all once the shared budget is mostly spent (what's left is theirs),
  * the shared RPM spacing, plus a longer gap of its own (Groq's tokens-per-minute
    ceiling is the tight one for this model),
  * a 429 ends the run, and a long review queue pauses it.
Everything here is best-effort: a failure is logged and swallowed.

Lines live in the `meta` table (key pitara_lines) and are mirrored to MongoDB like
the other admin settings, so they survive a restart. Approved lines are merged into
the pitara by pitara.load(); the app picks them up through the same endpoint.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .. import db
from ..config import settings
from . import ai_enrich, pitara, pitara_lint

log = logging.getLogger("dealradar.pitara_writer")

STATE_KEY = "pitara_lines"
ENABLED_KEY = "pitara_writer_enabled"
AUTO_KEY = "pitara_writer_auto_approve"
LAST_RUN_KEY = "pitara_writer_last"
LAST_DAY_KEY = "pitara_writer_day"
WRITER_LABEL = "writer"             # its own row in ai_usage, next to enrichment's "primary"
IST = timezone(timedelta(hours=5, minutes=30))
RUN_HOURS = (1, 5)                  # IST: when the nightly run may start
TICK_SECONDS = 30 * 60
MIN_GAP_SECONDS = 25.0              # between this writer's calls (tokens-per-minute, not requests, is the tight limit)
MAX_RESPONSE_TOKENS = 2000
MAX_STORED = 3000                   # state size cap; the oldest rejected lines go first
ACTIONS = ("approve", "reject", "disable", "enable")

CATEGORIES = ["Women Fashion", "Men Fashion", "Beauty", "Electronics", "Footwear", "Home & Kitchen", "Baby & Kids",
              "Sports & Fitness", "Bags & Luggage", "Books & Stationery", "Grocery", "Any"]
MOODS = ["chai", "salary", "monthend", "sunday", "monday", "wfh", "hostel", "ghar", "festival", "heart", "dost", "night",
         "morning", "weekend", "season", "general"]

_SYSTEM = (
    "You write short push-notification templates for an Indian deals app, in Hinglish "
    "(Hindi written in roman letters, mixed with English). Voice: playful, cheeky, relatable and warm, like a friend "
    "teasing you, never mean. Joke at the deal, the situation or yourself — never at the reader's body, money "
    "troubles, religion, caste, region, gender, health or relationships. No fake urgency or scarcity, and no claims "
    "about stock, deadlines, delivery or 'lowest price'. NEVER write numbers, prices or percentages: use placeholders. "
    "Allowed placeholders: {name} (the product — required in the title or body), {price}, {mrp}, {save}, {discount}, "
    "{brand}, {store}. Do not name real brands, celebrities or apps. Title at most 55 characters, body at most 110 "
    "(a placeholder counts as about 8). At most 2 emoji. tone is one of: tease, dost, self, heart. Reply with ONLY a "
    'JSON array of objects {"mood": "...", "tone": "...", "title": "...", "body": "..."} — no prose, no code fences.'
)


# --- state -----------------------------------------------------------------------

_state: Optional[Dict[str, Any]] = None


def _empty() -> Dict[str, Any]:
    return {"v": 1, "lines": [], "disabled_ids": []}


def _load() -> Dict[str, Any]:
    global _state
    if _state is not None:
        return _state
    try:
        raw = db.get_meta(STATE_KEY)
        parsed = json.loads(raw) if raw else _empty()
        if not isinstance(parsed, dict) or not isinstance(parsed.get("lines"), list):
            parsed = _empty()
    except Exception as exc:  # noqa: BLE001 — no database yet, or junk: behave as empty, don't cache it
        log.debug("Pitara state unreadable: %s", exc)
        return _empty()
    parsed.setdefault("disabled_ids", [])
    _state = parsed
    return _state


def _save(state: Dict[str, Any]) -> None:
    global _state
    lines = state["lines"]
    if len(lines) > MAX_STORED:
        rejected = sorted((x for x in lines if x.get("status") == "rejected"), key=lambda x: x.get("created_at", 0))
        drop = {x["id"] for x in rejected[: len(lines) - MAX_STORED]}
        state["lines"] = [x for x in lines if x["id"] not in drop]
    _state = state
    blob = json.dumps(state, ensure_ascii=False)
    db.set_meta(STATE_KEY, blob)
    try:
        from . import mongo_store
        if mongo_store.is_enabled():
            mongo_store.save_setting(STATE_KEY, blob)
    except Exception as exc:  # noqa: BLE001 — the local copy is saved
        log.warning("Couldn't mirror pitara lines to MongoDB: %s", exc)


def reset_cache() -> None:
    """Forget the in-memory copy (tests; mirrors a restart)."""
    global _state
    _state = None


def _flag(key: str) -> bool:
    try:
        return (db.get_meta(key) or "") == "1"
    except Exception:  # noqa: BLE001
        return False


def _set_flag(key: str, on: bool) -> None:
    value = "1" if on else "0"
    db.set_meta(key, value)
    try:
        from . import mongo_store
        if mongo_store.is_enabled():
            mongo_store.save_setting(key, value)
    except Exception as exc:  # noqa: BLE001
        log.warning("Couldn't mirror %s to MongoDB: %s", key, exc)


def enabled() -> bool:
    return _flag(ENABLED_KEY)


def auto_approve() -> bool:
    return _flag(AUTO_KEY)


def configure(enabled_: Optional[bool] = None, auto: Optional[bool] = None) -> None:
    if enabled_ is not None:
        _set_flag(ENABLED_KEY, enabled_)
    if auto is not None:
        _set_flag(AUTO_KEY, auto)


# --- what the pitara reads ---------------------------------------------------------

def approved_lines() -> List[Dict[str, Any]]:
    """Approved drafts, in the shipped-line shape. Never raises."""
    try:
        return [{"id": x["id"], "category": x["category"], "mood": x["mood"], "tone": x["tone"], "title": x["title"],
                 "body": x["body"], "needs": list(x.get("needs") or []), "source": "ai"}
                for x in _load()["lines"] if x.get("status") == "approved"]
    except Exception:  # noqa: BLE001
        return []


def disabled_ids() -> set:
    try:
        return set(_load().get("disabled_ids") or [])
    except Exception:  # noqa: BLE001
        return set()


def signature() -> str:
    """Changes whenever what the app should see changes (feeds the pitara's version hash)."""
    try:
        ids = sorted(x["id"] for x in approved_lines())
        if not ids and not disabled_ids():
            return ""  # nothing added or removed: the version stays what the shipped files give
        return hashlib.sha1((",".join(ids) + "|" + ",".join(sorted(disabled_ids()))).encode()).hexdigest()[:12]
    except Exception:  # noqa: BLE001
        return ""


# --- budget (shared with ai_enrich) ---------------------------------------------------

def _writer_row() -> Dict[str, int]:
    row = db.query_one("SELECT requests, tokens FROM ai_usage WHERE day = ? AND key_label = ?",
                       (ai_enrich._today(), WRITER_LABEL))
    return {"requests": row["requests"], "tokens": row["tokens"]} if row else {"requests": 0, "tokens": 0}


def _record_writer(tokens: int) -> None:
    db.execute(
        "INSERT INTO ai_usage (day, key_label, requests, tokens) VALUES (?, ?, 1, ?) "
        "ON CONFLICT(day, key_label) DO UPDATE SET requests = requests + 1, tokens = tokens + excluded.tokens",
        (ai_enrich._today(), WRITER_LABEL, int(tokens)))


def budget() -> Dict[str, Any]:
    """Where the shared Groq budget stands and whether the writer may draw on it now."""
    shared = ai_enrich._usage_row()
    own = _writer_row()
    cap_t, cap_r = settings.groq_max_tokens_per_day, settings.groq_max_requests_per_day
    keep = max(0, min(90, settings.pitara_writer_leave_free_pct)) / 100.0
    ok, why = True, ""
    if not settings.ai_enrich_enabled:
        ok, why = False, "no Groq key configured"
    elif not ai_enrich._budget_ok():
        ok, why = False, "today's shared Groq budget is used up"
    elif shared["tokens"] >= cap_t * (1 - keep) or shared["requests"] >= cap_r * (1 - keep):
        ok, why = False, f"leaving the last {int(keep * 100)}% of today's shared budget for deal enrichment"
    elif own["tokens"] >= settings.pitara_writer_tokens_per_day:
        ok, why = False, "the writer's own daily allowance is used"
    return {"ok": ok, "why": why, "shared_tokens": shared["tokens"], "shared_token_cap": cap_t,
            "shared_requests": shared["requests"], "shared_request_cap": cap_r,
            "writer_tokens": own["tokens"], "writer_token_cap": settings.pitara_writer_tokens_per_day,
            "writer_requests": own["requests"], "leave_free_pct": int(keep * 100)}


# --- drafting ---------------------------------------------------------------------------

def _counts() -> Dict[str, int]:
    out = {"pending": 0, "approved": 0, "rejected": 0}
    for x in _load()["lines"]:
        out[x.get("status", "pending")] = out.get(x.get("status", "pending"), 0) + 1
    return out


def _existing() -> Tuple[set, set, Dict[str, List[set]]]:
    """Text keys and title keys already in the pitara or the drafts, and word sets of titles per category."""
    keys, titles, words = set(), set(), {}
    lines = [t for t in pitara.load()[0]] + [x for x in _load()["lines"]]
    for t in lines:
        keys.add(pitara_lint.text_key(t))
        titles.add(pitara_lint.title_key(t))
        words.setdefault(t.get("category") or "Any", []).append(set(re.findall(r"[a-z]+", str(t.get("title", "")).lower())))
    return keys, titles, words


def _too_close(title: str, category: str, words: Dict[str, List[set]]) -> bool:
    mine = set(re.findall(r"[a-z]+", title.lower()))
    if len(mine) < 3:
        return False
    for other in words.get(category, []) + words.get("Any", []):
        if other and len(mine & other) / max(1, len(mine | other)) >= 0.75:
            return True
    return False


def _pick_category() -> str:
    have: Dict[str, int] = {c: 0 for c in CATEGORIES}
    for t in pitara.load()[0]:
        have[t.get("category") if t.get("category") in have else "Any"] += 1
    for x in _load()["lines"]:
        if x.get("status") == "pending":  # approved ones are already in the pitara
            have[x["category"] if x.get("category") in have else "Any"] += 1
    least = min(have.values())
    return random.choice([c for c, n in have.items() if n <= least + 5])


def _examples(category: str, n: int = 6) -> List[str]:
    pool = [t for t in pitara.load()[0] if (t.get("category") in (category, "Any")) and not t.get("personal")
            and not t.get("kinds") and "{" in t["title"] + t["body"]]
    random.shuffle(pool)
    return [f'- {t["title"]} | {t["body"]}' for t in pool[:n]]


def _user_prompt(category: str, moods: List[str], n: int) -> str:
    shown = "Any product" if category == "Any" else category
    return (f"Category: {shown}. Write {n} different templates, each with a different idea. "
            f"Moods to cover: {', '.join(moods)}.\nExamples of the voice (do not copy them):\n"
            + "\n".join(_examples(category)) + "\n")


def _parse(content: str) -> List[Dict[str, Any]]:
    text = (content or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.IGNORECASE).strip()
    for candidate in (text, text[text.find("["): text.rfind("]") + 1] if "[" in text and "]" in text else ""):
        try:
            data = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict):
            data = data.get("templates") or data.get("lines") or []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
    return []


async def _call(system: str, user: str) -> Tuple[Optional[str], str]:
    """(content, stop_reason): stop_reason is "" on success, else why this run should end."""
    await asyncio.sleep(max(0.0, MIN_GAP_SECONDS - (time.monotonic() - _last_call[0])) if _last_call[0] else 0.0)
    async with ai_enrich._lock:
        if not budget()["ok"]:
            return None, "budget"
        gap = 60.0 / max(1, settings.groq_max_requests_per_minute)
        wait = ai_enrich._last_call_at + gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        ai_enrich._last_call_at = time.monotonic()
        _last_call[0] = time.monotonic()
        payload = {"model": settings.groq_model, "temperature": 0.9, "max_tokens": MAX_RESPONSE_TOKENS,
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "reasoning_effort": "low"}
        resp = None
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                headers = {"Authorization": f"Bearer {settings.groq_api_key}", "Content-Type": "application/json"}
                resp = await client.post(ai_enrich.GROQ_URL, headers=headers, json=payload)
                if resp.status_code == 400:  # a model that doesn't take reasoning_effort
                    payload.pop("reasoning_effort")
                    resp = await client.post(ai_enrich.GROQ_URL, headers=headers, json=payload)
        except (httpx.TimeoutException, httpx.HTTPError) as exc:
            log.info("Pitara writer: Groq call failed (network): %s", exc)
            return None, "network"
        if resp.status_code == 429:
            log.info("Pitara writer: Groq rate-limited; ending the run")
            return None, "rate-limited"
        if resp.status_code != 200:
            log.info("Pitara writer: Groq call failed: HTTP %s", resp.status_code)
            return None, f"HTTP {resp.status_code}"
        try:
            body = resp.json()
            tokens = int((body.get("usage") or {}).get("total_tokens") or 0)
            content = body["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, ValueError, TypeError):
            return None, "bad response"
        ai_enrich._record_usage(tokens)   # the shared counter every other Groq user reads
        _record_writer(tokens)            # and the writer's own slice of it
        return content, ""


_last_call: List[float] = [0.0]


def _admit(raw: Dict[str, Any], category: str, keys: set, titles: set, words: Dict[str, List[set]],
           reasons: Dict[str, int]) -> Optional[Dict[str, Any]]:
    """A drafted line -> a stored one, or None (reason counted)."""
    def refuse(why: str) -> None:
        reasons[why] = reasons.get(why, 0) + 1

    title = " ".join(str(raw.get("title") or "").split())
    body = " ".join(str(raw.get("body") or "").split())
    tone = str(raw.get("tone") or "").strip().lower()
    mood = re.sub(r"[^a-z_]", "", str(raw.get("mood") or "general").strip().lower())[:20] or "general"
    if tone not in pitara_lint.TONES:
        tone = "dost"
    line = {"category": category, "mood": mood, "tone": tone, "title": title, "body": body,
            "needs": pitara_lint.needs_for(title, body)}
    problems = pitara_lint.check_drafted(line)
    if problems:
        refuse(problems[0].split(" (")[0][:60])
        return None
    key, tkey = pitara_lint.text_key(line), pitara_lint.title_key(line)
    if key in keys or tkey in titles or _too_close(title, category, words):
        refuse("repeats an existing line")
        return None
    keys.add(key)
    titles.add(tkey)
    words.setdefault(category, []).append(set(re.findall(r"[a-z]+", title.lower())))
    line["id"] = "ai-" + hashlib.sha1(key.encode()).hexdigest()[:10]
    return line


async def run_once(now: Optional[float] = None, force: bool = False) -> Dict[str, Any]:
    """One drafting run: up to a few calls, each stopping early if the budget or queue says so.
    `force` is the admin pressing "Write now" — it skips the on/off switch, never the budget."""
    now = now or time.time()
    report: Dict[str, Any] = {"status": "skipped", "why": "", "calls": 0, "added": 0, "rejected": {}, "at": now}
    try:
        if not force and not enabled():
            report["why"] = "switched off"
            return report
        auto = auto_approve()
        keys, titles, words = _existing()
        for _ in range(max(1, settings.pitara_writer_max_calls_per_run)):
            state = _load()
            if _counts()["pending"] >= settings.pitara_writer_max_pending:
                report["why"] = "waiting for review (queue is full)"
                break
            b = budget()
            if not b["ok"]:
                report["why"] = b["why"]
                break
            category = _pick_category()
            moods = random.sample(MOODS, 4)
            n = max(1, settings.pitara_writer_lines_per_call)
            content, stop = await _call(_SYSTEM, _user_prompt(category, moods, n))
            if content is None:
                report["why"] = stop
                break
            report["calls"] += 1
            for raw in _parse(content)[: n + 3]:
                line = _admit(raw, category, keys, titles, words, report["rejected"])
                if line:
                    line.update(status="approved" if auto else "pending", created_at=now, model=settings.groq_model,
                                decided_at=now if auto else None)
                    state["lines"].append(line)
                    report["added"] += 1
            _save(state)
            if auto:
                pitara.reset()
        report["status"] = "ok" if report["calls"] else "skipped"
    except Exception as exc:  # noqa: BLE001 — drafting is never worth an error anywhere else
        log.warning("Pitara writer run failed: %s", exc)
        report["status"], report["why"] = "error", str(exc)[:120]
    try:
        db.set_meta(LAST_RUN_KEY, json.dumps(report))
    except Exception:  # noqa: BLE001
        pass
    if report["added"]:
        log.info("Pitara writer: %d new line(s) %s", report["added"], "published" if auto_approve() else "waiting for review")
    return report


# --- review --------------------------------------------------------------------------------

def decide(ids: List[str], action: str) -> Dict[str, int]:
    """approve / reject drafts; disable / enable any line (a shipped line is switched off by id)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {', '.join(ACTIONS)}.")
    state = _load()
    by_id = {x["id"]: x for x in state["lines"]}
    disabled = set(state.get("disabled_ids") or [])
    shipped = {t["id"] for t in pitara.load()[0] if t.get("source") != "ai"}
    changed = 0
    now = time.time()
    for i in ids:
        line = by_id.get(i)
        if action in ("approve", "reject") and line:
            line["status"], line["decided_at"] = ("approved" if action == "approve" else "rejected"), now
            changed += 1
        elif action == "disable":
            if line:
                line["status"], line["decided_at"] = "rejected", now
                changed += 1
            elif i in shipped:
                disabled.add(i)
                changed += 1
        elif action == "enable" and i in disabled:
            disabled.discard(i)
            changed += 1
    state["disabled_ids"] = sorted(disabled)
    if changed:
        _save(state)
        pitara.reset()
    return {"changed": changed}


def status() -> Dict[str, Any]:
    state = _load()
    lines = state["lines"]
    pending = sorted((x for x in lines if x.get("status") == "pending"), key=lambda x: -x.get("created_at", 0))
    approved = sorted((x for x in lines if x.get("status") == "approved"), key=lambda x: -(x.get("decided_at") or 0))
    try:
        last = json.loads(db.get_meta(LAST_RUN_KEY) or "null")
    except (ValueError, TypeError):
        last = None
    keep = ("id", "category", "mood", "tone", "title", "body")
    return {
        "enabled": enabled(),
        "auto_approve": auto_approve(),
        "groq_configured": settings.ai_enrich_enabled,
        "budget": budget(),
        "counts": {**_counts(), "shipped": len([t for t in pitara.load()[0] if t.get("source") != "ai"]),
                   "switched_off": len(state.get("disabled_ids") or [])},
        "last_run": last,
        "pending": [{k: x.get(k) for k in keep} for x in pending[:60]],
        "recent_approved": [{k: x.get(k) for k in keep} for x in approved[:10]],
    }


# --- the nightly loop -------------------------------------------------------------------------

async def loop() -> None:
    """Started from app/main.py. Once a night (IST 1–5 am), if switched on, one drafting run."""
    await asyncio.sleep(120)
    while True:
        try:
            now = datetime.now(IST)
            today = now.strftime("%Y-%m-%d")
            if RUN_HOURS[0] <= now.hour < RUN_HOURS[1] and enabled() and (db.get_meta(LAST_DAY_KEY) or "") != today:
                db.set_meta(LAST_DAY_KEY, today)  # before the run: a restart mid-run mustn't start another
                await run_once()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log.warning("Pitara writer tick failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)
