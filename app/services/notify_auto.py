"""Notification auto mode: the server decides WHAT goes out and HOW MUCH.

Manual mode (the default) is the old behaviour: hot_push.schedule_cycle plans
PUSHES_PER_CYCLE pushes per ingest window. That ties notifications to the
poll interval — which the owner (or autopoll) can now set anywhere from one
minute to an hour — so "how many pushes a day" was really "how often we
poll". Auto mode runs from its own one-minute loop instead, and paces the
day like a person would:

  crazy deals  — something genuinely exceptional that appeared in the last
                 45 min (score >= 90, a lowest-ever price at >= 70% off, or
                 half its usual price) goes out as soon as the guards allow,
                 at most `crazy_per_day`.
  best deals   — a few slots a day in the prime windows (IST 09:30–11:00,
                 13:00–14:30, 18:30–22:00), planned once per day at random
                 minutes and persisted in meta so a restart never re-plans or
                 doubles them. A slot with nothing above the score bar is
                 skipped: quiet days send less.
  nudges       — "haven't seen you in a while" to phones not opened for
                 `nudge_after_days`, one device at a time (devices.notify, not
                 a broadcast), in the evening window only, at most
                 `nudge_max` times per lapse — then we leave them alone.

Every broadcast (crazy or best) shares one daily cap and a minimum gap,
counted from push_log, and nothing ever goes out in quiet hours.

The phone decides WHEN: it learns each person's active hours and queues any
deal-carrying notification of a non-instant kind to their best moment,
writing its own copy. So the server's title/body for those is a fallback,
and the windows here are "roughly when people are around", not precise.

Settings live in meta (notify_auto_*) cached in memory, mirrored to MongoDB
by the admin router — same pattern as autopoll.py.
"""
from __future__ import annotations

import asyncio
import json
import logging
import random
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .. import db
from ..config import settings as app_settings
from . import hot_push
from .hot_push import IST

log = logging.getLogger(__name__)

MODE_META_KEY = "notify_auto_mode"
PLAN_META_KEY = "notify_auto_plan"         # today's slots — local only, re-made each day
MODES = ("manual", "auto")

# name: (default, lowest allowed, highest allowed)
SETTINGS: Dict[str, Tuple[int, int, int]] = {
    "daily_cap": (4, 1, 12),               # broadcast pushes per IST day (crazy + best)
    "min_gap_minutes": (120, 30, 480),     # between two broadcasts
    "crazy_per_day": (2, 0, 5),
    "nudge_after_days": (3, 1, 30),        # "inactive" = not opened for this long
    "nudge_every_days": (3, 1, 30),        # at most one nudge per device per this
    "nudge_max": (3, 0, 10),               # per lapse; reset when the device is seen
}

TICK_SECONDS = 60
CRAZY_FRESH_SECONDS = 45 * 60              # only deals that just appeared
CRAZY_GAP_SECONDS = 45 * 60                # a crazy deal may follow any broadcast this soon
CRAZY_SCORE = 90
CRAZY_LOWEST_DISCOUNT = 70
CRAZY_BELOW_MEDIAN = 0.5                   # price at most half its usual
CRAZY_MIN_POINTS = 3                       # a "usual price" needs a few sightings
NUDGE_BATCH = 200                          # per tick, to spread load
SALE_SOON_DAYS = 3
MAX_CANDIDATES_FOR_SLOTS = 20

# (start h, m), (end h, m), label — IST
PRIME_WINDOWS = [((9, 30), (11, 0), "morning"), ((13, 0), (14, 30), "lunch"), ((18, 30), (22, 0), "evening")]
NUDGE_WINDOW = ((18, 30), (21, 30))

_mode_cache: Optional[str] = None
_settings_cache: Optional[Dict[str, int]] = None
_plan_cache: Optional[Dict[str, Any]] = None


# --- settings --------------------------------------------------------------

def _meta_key(name: str) -> str:
    return f"notify_auto_{name}"


def mode() -> str:
    global _mode_cache
    if _mode_cache is None:
        raw = (db.get_meta(MODE_META_KEY) or "").strip().lower()
        _mode_cache = raw if raw in MODES else "manual"
    return _mode_cache


def current_settings() -> Dict[str, int]:
    global _settings_cache
    if _settings_cache is None:
        out = {}
        for name, (default, lo, hi) in SETTINGS.items():
            try:
                value = int(db.get_meta(_meta_key(name)) or "")
            except (TypeError, ValueError):
                value = default
            out[name] = value if lo <= value <= hi else default
        _settings_cache = out
    return dict(_settings_cache)


def validate(changes: Dict[str, Any]) -> Optional[str]:
    """Error message for a bad request, or None if it's fine."""
    for key, value in changes.items():
        if key == "mode":
            if str(value or "").strip().lower() not in MODES:
                return f"mode must be one of {', '.join(MODES)}."
            continue
        if key not in SETTINGS:
            return f"Unknown setting {key!r}."
        _, lo, hi = SETTINGS[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            return f"{key} must be a whole number."
        if not (lo <= int(value) <= hi):
            return f"{key} must be between {lo} and {hi}."
    return None


def update(changes: Dict[str, Any], now: Optional[float] = None) -> Dict[str, str]:
    """Persist any subset of mode + SETTINGS. Raises ValueError on bad input.

    Returns the {meta key: value} pairs written, for the caller to mirror.
    """
    global _mode_cache, _settings_cache
    error = validate(changes)
    if error:
        raise ValueError(error)
    written: Dict[str, str] = {}
    current = current_settings()
    for key, value in changes.items():
        if key == "mode":
            new_mode = str(value).strip().lower()
            db.set_meta(MODE_META_KEY, new_mode)
            _mode_cache = new_mode
            written[MODE_META_KEY] = new_mode
        else:
            current[key] = int(value)
            db.set_meta(_meta_key(key), str(int(value)))
            written[_meta_key(key)] = str(int(value))
    _settings_cache = current
    if mode() == "auto" and ({"mode", "daily_cap", "crazy_per_day"} & set(changes)):
        # The admin sees a real plan the moment they switch on (or resize the budget).
        try:
            ensure_plan(now, replan=True)
        except Exception as exc:  # noqa: BLE001 — the setting itself is saved
            log.warning("Notify plan after settings change failed: %s", exc)
    return written


def reset_cache() -> None:
    """Forget everything in memory (tests; mirrors a restart)."""
    global _mode_cache, _settings_cache, _plan_cache
    _mode_cache = _settings_cache = _plan_cache = None


# --- time -------------------------------------------------------------------

def _ist(now: float) -> datetime:
    return datetime.fromtimestamp(now, IST)


def _day(now: float) -> str:
    return _ist(now).strftime("%Y-%m-%d")


def _day_start(now: float) -> float:
    d = _ist(now)
    return d.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()


def _at(now: float, hm: Tuple[int, int]) -> float:
    return _day_start(now) + hm[0] * 3600 + hm[1] * 60


def _windows(now: float) -> List[Tuple[float, float, str]]:
    return [(_at(now, s), _at(now, e), label) for s, e, label in PRIME_WINDOWS]


def _fmt_hm(hm: Tuple[int, int]) -> str:
    return f"{hm[0]:02d}:{hm[1]:02d}"


def is_quiet(now: float) -> bool:
    return hot_push.is_quiet(_ist(now))


def in_nudge_window(now: float) -> bool:
    return _at(now, NUDGE_WINDOW[0]) <= now < _at(now, NUDGE_WINDOW[1])


# --- counting (from push_log) -------------------------------------------------

def today_counts(now: Optional[float] = None) -> Dict[str, int]:
    now = now or time.time()
    row = db.query_one(
        "SELECT "
        "SUM(CASE WHEN COALESCE(kind, 'hot_deal') != 'nudge' THEN 1 ELSE 0 END) AS sent, "
        "SUM(CASE WHEN kind = 'crazy_deal' THEN 1 ELSE 0 END) AS crazy, "
        "SUM(CASE WHEN kind = 'nudge' THEN COALESCE(reach, 0) ELSE 0 END) AS nudges "
        "FROM push_log WHERE sent_at >= ? AND sent_at <= ?", (_day_start(now), now))
    row = row or {}
    return {"sent": int(row.get("sent") or 0), "crazy_sent": int(row.get("crazy") or 0),
            "nudges_sent": int(row.get("nudges") or 0)}


def last_broadcast_at(now: Optional[float] = None) -> float:
    now = now or time.time()
    row = db.query_one("SELECT MAX(sent_at) AS t FROM push_log WHERE COALESCE(kind, 'hot_deal') != 'nudge' "
                       "AND sent_at <= ?", (now,))
    return float((row or {}).get("t") or 0)


def broadcast_guard(now: float, crazy: bool = False) -> Optional[str]:
    """Why a broadcast can't go out right now, or None if it can."""
    cfg = current_settings()
    if is_quiet(now):
        return "quiet hours"
    counts = today_counts(now)
    if counts["sent"] >= cfg["daily_cap"]:
        return f"daily cap reached ({counts['sent']}/{cfg['daily_cap']})"
    if crazy and counts["crazy_sent"] >= cfg["crazy_per_day"]:
        return f"crazy-deal cap reached ({counts['crazy_sent']}/{cfg['crazy_per_day']})"
    gap = CRAZY_GAP_SECONDS if crazy else cfg["min_gap_minutes"] * 60
    since = now - last_broadcast_at(now)
    if since < gap:
        return f"last push only {int(since // 60)} min ago (gap {gap // 60} min)"
    return None


# --- crazy deals --------------------------------------------------------------

def _crazy_reason(deal: Dict[str, Any]) -> Optional[str]:
    from . import store

    price = float(deal.get("price") or 0)
    disc = int(deal.get("discount_pct") or 0)
    if deal.get("is_lowest") and disc >= CRAZY_LOWEST_DISCOUNT:
        return f"lowest price we've recorded, at {disc}% off"
    key = deal.get("product_key") or ""
    if key and price > 0:
        try:
            stats = store.price_stats(key)  # local points + prefetched cache — no network
        except Exception:  # noqa: BLE001 — the other two rules still apply
            stats = {}
        median = stats.get("median")
        if median and stats.get("points", 0) >= CRAZY_MIN_POINTS and price <= float(median) * CRAZY_BELOW_MEDIAN:
            below = int(round((1 - price / float(median)) * 100))
            return f"{below}% below its usual {hot_push._money(median)}"
    if float(deal.get("score") or 0) >= CRAZY_SCORE:
        return f"deal score {int(float(deal['score']))}/100"
    return None


def find_crazy(now: Optional[float] = None) -> Optional[Tuple[Dict[str, Any], bool, str]]:
    """The best exceptional deal that appeared in the last 45 min and hasn't
    been pushed — (deal, is_women, why) — or None."""
    from . import priority

    now = now or time.time()
    rows = db.query(
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND COALESCE(image_url, '') != '' "
        "AND COALESCE(price, 0) > 0 AND first_seen_at > ? AND first_seen_at <= ? AND score >= ? "
        "ORDER BY score DESC LIMIT 60",
        (now, now - CRAZY_FRESH_SECONDS, now, app_settings.broadcast_min_score),
    )
    recent = {r["product_key"] for r in db.query(
        "SELECT product_key FROM push_log WHERE sent_at > ? AND COALESCE(product_key, '') != ''",
        (now - hot_push.REPEAT_WINDOW,))}
    for row in rows:
        deal = db.row_to_dict(row) or {}
        if (deal.get("product_key") or deal.get("id")) in recent:
            continue
        why = _crazy_reason(deal)
        if why:
            return deal, priority.is_women(deal), why
    return None


def compose_crazy(deal: Dict[str, Any], why: str) -> Tuple[str, str]:
    """Plain and specific — the deal is the hook, no countdowns or "hurry"."""
    price = hot_push._money(deal.get("price"))
    name = hot_push._short(deal.get("title") or "This deal", 40)
    disc = int(deal.get("discount_pct") or 0)
    if deal.get("is_lowest") and disc >= CRAZY_LOWEST_DISCOUNT:
        head = "Lowest price ever"
    elif "below its usual" in why:
        head = f"{why.split('%')[0]}% below usual"
    elif disc >= 40:
        head = f"{disc}% off"
    else:
        head = "Top deal"
    title = f"{head}: {name} {price}".strip()
    bits = []
    if disc:
        bits.append(f"{disc}% off right now")
    mrp = deal.get("mrp")
    if mrp and deal.get("price") and float(mrp) > float(deal["price"]):
        bits.append(f"was {hot_push._money(mrp)}")
    store_name = (deal.get("store") or "").strip()
    if store_name and store_name != "unknown":
        bits.append(store_name.title())
    body = " · ".join(bits) or why
    return title[:90], body[:160]


async def send_crazy(now: Optional[float] = None) -> Dict[str, Any]:
    from . import devices

    now = now or time.time()
    found = find_crazy(now)
    if not found:
        return {"status": "skipped", "why": "no crazy deal right now"}
    blocked = broadcast_guard(now, crazy=True)
    if blocked:
        return {"status": "skipped", "why": blocked}
    deal, women, why = found
    title, body = compose_crazy(deal, why)
    report = await devices.broadcast(title, body, deal, kind="crazy_deal")
    hot_push.log_push("crazy_deal", deal, title, women, report, report.get("devices", 0), now)
    db.set_meta("last_hot_push_at", str(now))
    log.info("Crazy deal push: %r (%s) -> %s", title, why, report)
    return {"status": "sent", "deal_id": deal["id"], "title": title, "body": body, "why": why, **report}


# --- the day's plan -------------------------------------------------------------

def _load_plan() -> Dict[str, Any]:
    global _plan_cache
    if _plan_cache is None:
        try:
            _plan_cache = json.loads(db.get_meta(PLAN_META_KEY) or "{}") or {}
        except (TypeError, ValueError):
            _plan_cache = {}
    return _plan_cache


def _save_plan(plan: Dict[str, Any]) -> None:
    global _plan_cache
    _plan_cache = plan
    db.set_meta(PLAN_META_KEY, json.dumps(plan))


def _pick_times(n: int, now: float) -> List[Tuple[float, float, str]]:
    """n random moments across what's left of today's prime windows: the
    remaining window time is cut into n equal slices, one moment per slice
    (kept off the slice edges) — spread out, never a fixed clock."""
    spans = [(max(s, now + 60), e, label) for s, e, label in _windows(now) if e > now + 60]
    spans = [(s, e, label) for s, e, label in spans if e - s >= 60]
    total = sum(e - s for s, e, _ in spans)
    if n <= 0 or total <= 0:
        return []
    out = []
    slice_len = total / n
    for i in range(n):
        lo = i * slice_len + slice_len * 0.15
        hi = (i + 1) * slice_len - slice_len * 0.10
        offset = random.uniform(lo, max(lo, hi))
        for s, e, label in spans:
            if offset <= e - s:
                out.append((s + offset, e, label))
                break
            offset -= e - s
    return out


def ensure_plan(now: Optional[float] = None, replan: bool = False) -> Dict[str, Any]:
    """Today's best-deal slots, made once per IST day.

    Sized to what's left of the daily budget minus one reserved for a crazy
    deal, and to how many good candidates there are right now. `replan`
    (settings changed) keeps what already happened and re-makes the rest.
    """
    now = now or time.time()
    plan = _load_plan()
    today = _day(now)
    if plan.get("day") == today and not replan:
        return plan
    kept = [s for s in plan.get("slots", []) if s.get("status") != "pending"] if plan.get("day") == today else []
    cfg = current_settings()
    reserve = 1 if cfg["crazy_per_day"] > 0 else 0
    budget = cfg["daily_cap"] - reserve - today_counts(now)["sent"]
    good = len(hot_push.candidates(limit=MAX_CANDIDATES_FOR_SLOTS, now=now))
    n = max(0, min(budget, max(1, good)))
    labels = {label: f"{label} window ({_fmt_hm(s)}–{_fmt_hm(e)} IST)" for s, e, label in PRIME_WINDOWS}
    slots = kept + [{"at": round(at, 1), "until": round(until, 1), "kind": "hot_deal", "status": "pending",
                     "why": f"best deal, {labels[label]}"} for at, until, label in _pick_times(n, now)]
    plan = {"day": today, "made_at": now, "slots": sorted(slots, key=lambda s: s["at"])}
    _save_plan(plan)
    log.info("Notify plan for %s: %d slot(s) (budget %d, %d good candidates)", today,
             sum(1 for s in plan["slots"] if s["status"] == "pending"), budget, good)
    return plan


async def run_slots(now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Fire any due slot whose guards pass; retire slots whose window closed."""
    now = now or time.time()
    plan = ensure_plan(now)
    results = []
    changed = False
    for slot in plan.get("slots", []):
        if slot.get("status") != "pending" or slot["at"] > now:
            continue
        until = float(slot.get("until") or slot["at"] + 3600)
        blocked = broadcast_guard(now)
        if blocked and blocked.startswith("daily cap"):
            slot["status"], slot["why"] = "skipped", blocked
            changed = True
            continue
        if now >= until:  # waited the whole window (gap/quiet), or we were down
            slot["status"], slot["why"] = "skipped", f"window closed ({blocked})" if blocked else "window closed"
            changed = True
            continue
        if blocked:
            if slot.get("why") != f"waiting — {blocked}":
                slot["why"] = f"waiting — {blocked}"
                changed = True
            continue
        result = await hot_push.send_best(reason="auto", kind="hot_deal", now=now, check_guards=False)
        slot["status"] = "sent" if result.get("status") == "sent" else "skipped"
        slot["why"] = result.get("title") if slot["status"] == "sent" else result.get("why", "")
        changed = True
        results.append(result)
        break  # one broadcast per tick; the gap covers the rest
    if changed:
        _save_plan(plan)
    return results


# --- re-engagement nudges -------------------------------------------------------

def _eligible_sql(now: float) -> Tuple[str, List[Any]]:
    cfg = current_settings()
    return ("FROM devices WHERE COALESCE(push_token, '') != '' AND COALESCE(last_seen_at, 0) < ? "
            "AND COALESCE(last_nudge_at, 0) < ? AND COALESCE(nudges_since_seen, 0) < ?",
            [now - cfg["nudge_after_days"] * 86400, now - cfg["nudge_every_days"] * 86400, cfg["nudge_max"]])


def nudge_eligible(now: Optional[float] = None) -> int:
    now = now or time.time()
    if current_settings()["nudge_max"] <= 0:
        return 0
    where, params = _eligible_sql(now)
    row = db.query_one(f"SELECT COUNT(*) AS c {where}", params)
    return int((row or {}).get("c") or 0)


def _top_deal(now: float, lowest_only: bool = False) -> Optional[Dict[str, Any]]:
    extra = " AND is_lowest = 1" if lowest_only else ""
    row = db.query_one(
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND COALESCE(image_url, '') != '' "
        f"AND COALESCE(price, 0) > 0 AND score >= ?{extra} ORDER BY score DESC LIMIT 1",
        (now, app_settings.broadcast_min_score))
    return db.row_to_dict(row) if row else None


def _sale_soon(now: float) -> Optional[Dict[str, Any]]:
    from . import sale_events

    try:
        events = sale_events.list_all(upcoming_only=True, now=now)
    except Exception as exc:  # noqa: BLE001 — the other variants still work
        log.debug("Sale events unavailable for nudges: %s", exc)
        return None
    for e in events:
        start = float(e.get("starts_at") or 0)
        if start and start <= now + SALE_SOON_DAYS * 86400:
            return e
    return None


def _deal_line(deal: Dict[str, Any]) -> str:
    bits = [hot_push._short(deal.get("title") or "", 50), hot_push._money(deal.get("price"))]
    disc = int(deal.get("discount_pct") or 0)
    line = " ".join(b for b in bits if b)
    return f"{line} ({disc}% off)" if disc else line


def compose_nudge(device: Dict[str, Any], now: float, ctx: Dict[str, Any]
                  ) -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
    """(title, body, deal) from real data, rotating by how many times this
    device has been nudged; falls through to the next variant if one has
    nothing true to say. None if there's nothing at all worth sending."""
    top, lowest, sale = ctx.get("top"), ctx.get("lowest"), ctx.get("sale")

    def new_since() -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
        row = db.query_one("SELECT COUNT(*) AS c FROM deals WHERE status = 'live' AND expires_at > ? "
                           "AND first_seen_at > ?", (now, float(device.get("last_seen_at") or 0)))
        count = int((row or {}).get("c") or 0)
        if count < 5 or not top:
            return None
        return (f"{count:,} new deals since you last looked", f"Top pick: {_deal_line(top)}", top)

    def sale_soon() -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
        if not sale:
            return None
        start = float(sale.get("starts_at") or 0)
        days = (_ist(start).date() - _ist(now).date()).days
        when = ("is on now" if start <= now else "starts today" if days <= 0
                else "starts tomorrow" if days == 1 else f"starts in {days} days")
        approx = " (dates approximate)" if sale.get("approximate") else ""
        # No deal attached: a sale isn't a deal, and without one the phone
        # shows our text as-is (we're already inside the evening window).
        return (f"{sale.get('name')} {when}{approx}",
                "We're tracking prices so you can tell the real deals from the hype.", None)

    def lowest_ever() -> Optional[Tuple[str, str, Optional[Dict[str, Any]]]]:
        if not lowest:
            return None
        return (f"Lowest price we've seen: {hot_push._money(lowest.get('price'))}".strip(),
                _deal_line(lowest), lowest)

    variants = [new_since, sale_soon, lowest_ever]
    start = int(device.get("nudges_since_seen") or 0) % len(variants)
    for i in range(len(variants)):
        out = variants[(start + i) % len(variants)]()
        if out:
            return out
    return None


def _nudge_context(now: float) -> Dict[str, Any]:
    return {"top": _top_deal(now), "lowest": _top_deal(now, lowest_only=True), "sale": _sale_soon(now)}


def run_nudges(now: Optional[float] = None) -> int:
    """Nudge up to NUDGE_BATCH lapsed devices — evening window only."""
    from . import devices

    now = now or time.time()
    if current_settings()["nudge_max"] <= 0 or is_quiet(now) or not in_nudge_window(now):
        return 0
    where, params = _eligible_sql(now)
    rows = db.query(f"SELECT device_id, last_seen_at, nudges_since_seen {where} "
                    "ORDER BY last_seen_at DESC LIMIT ?", params + [NUDGE_BATCH])
    if not rows:
        return 0
    ctx = _nudge_context(now)
    sent = 0
    sample = ""
    for r in rows:
        msg = compose_nudge(dict(r), now, ctx)
        if not msg:
            continue
        title, body, deal = msg
        devices.notify(r["device_id"], "nudge", title, body, deal)
        db.execute("UPDATE devices SET last_nudge_at = ?, nudges_since_seen = COALESCE(nudges_since_seen, 0) + 1, "
                   "turso_dirty = 1 WHERE device_id = ?", (now, r["device_id"]))
        sample = sample or title
        sent += 1
    if sent:
        hot_push.log_push("nudge", None, sample, False, {}, sent, now)
        log.info("Nudged %d inactive device(s), e.g. %r", sent, sample)
    return sent


# --- the loop -----------------------------------------------------------------

async def tick(now: Optional[float] = None) -> Dict[str, Any]:
    """One pass. Never raises — a failed step is logged and the next still runs."""
    now = now or time.time()
    if mode() != "auto" or not app_settings.broadcast_hot_deal_enabled:
        return {"status": "off"}
    out: Dict[str, Any] = {"status": "ok"}
    try:
        out["crazy"] = await send_crazy(now)
    except Exception as exc:  # noqa: BLE001
        log.warning("Crazy-deal check failed: %s", exc)
    try:
        out["slots"] = await run_slots(now)
    except Exception as exc:  # noqa: BLE001
        log.warning("Notify slots failed: %s", exc)
    try:
        out["nudges"] = run_nudges(now)
    except Exception as exc:  # noqa: BLE001
        log.warning("Nudges failed: %s", exc)
    return out


async def loop() -> None:
    """Started from app/main.py; independent of the ingest cycle."""
    await asyncio.sleep(30)  # let the startup restores land first
    while True:
        try:
            await tick()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — never let the loop die
            log.warning("Notify auto tick failed: %s", exc)
        await asyncio.sleep(TICK_SECONDS)


# --- admin -------------------------------------------------------------------

def _public_slot(slot: Dict[str, Any]) -> Dict[str, Any]:
    return {"at": slot["at"], "kind": slot.get("kind", "hot_deal"), "status": slot.get("status", "pending"),
            "why": slot.get("why", "")}


def status(now: Optional[float] = None) -> Dict[str, Any]:
    """GET /api/admin/reader/notify-auto."""
    from . import devices

    now = now or time.time()
    cfg = current_settings()
    counts = today_counts(now)
    plan = _load_plan()
    if mode() == "auto" and plan.get("day") != _day(now):
        plan = ensure_plan(now)
    slots = [_public_slot(s) for s in plan.get("slots", [])] if plan.get("day") == _day(now) else []
    pending = [s for s in slots if s["status"] == "pending"]
    nxt = {"at": pending[0]["at"], "kind": pending[0]["kind"], "why": pending[0]["why"]} if pending else None
    recent = []
    for r in db.query("SELECT kind, title, sent_at, reach, tokens FROM push_log ORDER BY sent_at DESC LIMIT 10"):
        kind = r["kind"] or "hot_deal"
        recent.append({"kind": kind, "title": r["title"] or "", "sent_at": r["sent_at"],
                       "audience": "inactive users" if kind == "nudge" else "everyone",
                       "count": int(r["reach"] or r["tokens"] or 0)})
    inactive = db.query_one("SELECT COUNT(*) AS c FROM devices WHERE COALESCE(push_token, '') != '' "
                            "AND COALESCE(last_seen_at, 0) < ?", (now - cfg["nudge_after_days"] * 86400,))
    return {
        "mode": mode(),
        "settings": cfg,
        "quiet_hours": app_settings.push_quiet_hours,
        "quiet_now": is_quiet(now),
        "today": {"sent": counts["sent"], "cap": cfg["daily_cap"], "crazy_sent": counts["crazy_sent"],
                  "nudges_sent": counts["nudges_sent"]},
        "plan": slots,
        "next": nxt,
        "recent": recent,
        "reachable_devices": int(devices.active_counts().get("reachable", 0)),
        "inactive_devices": int((inactive or {}).get("c") or 0),
    }


def _deal_card(deal: Dict[str, Any], why: str) -> Dict[str, Any]:
    return {"id": deal.get("id"), "title": deal.get("title") or "", "price": deal.get("price"),
            "mrp": deal.get("mrp"), "discount_pct": deal.get("discount_pct"), "store": deal.get("store") or "",
            "score": deal.get("score"), "why": why}


def preview(now: Optional[float] = None) -> Dict[str, Any]:
    """What auto mode would send right now. Sends nothing."""
    now = now or time.time()
    crazy = None
    found = find_crazy(now)
    if found:
        deal, _, why = found
        title, body = compose_crazy(deal, why)
        crazy = {**_deal_card(deal, why), "push_title": title, "push_body": body}
    best = None
    ranked = hot_push.candidates(limit=1, now=now)
    if ranked:
        deal, women, _ = ranked[0]
        why = "top-ranked live deal" + (" (women's item, boosted)" if women else "")
        best = _deal_card(deal, why)
    sample = None
    eligible = nudge_eligible(now)
    if eligible:
        where, params = _eligible_sql(now)
        row = db.query_one(f"SELECT device_id, last_seen_at, nudges_since_seen {where} "
                           "ORDER BY last_seen_at DESC LIMIT 1", params)
        msg = compose_nudge(dict(row), now, _nudge_context(now)) if row else None
        if msg:
            sample = {"title": msg[0], "body": msg[1]}
    return {"crazy": crazy, "best": best, "nudge_eligible": eligible, "sample_nudge": sample}

