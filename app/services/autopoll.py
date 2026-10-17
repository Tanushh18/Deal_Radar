"""Auto poll mode: let live data pick how often ingest runs.

A fixed interval is always wrong for part of the day — too slow during a
sale-day rush (deals expire before we show them), too fast at 4 a.m. (burns
Telegram requests for nothing). In auto mode the interval is re-decided at
the end of every ingest cycle from three cheap signals, each a 0..1
"pressure" to poll faster:

  supply — few live deals on the site → go fetch more
  demand — lots of people on the app/site right now → keep it fresh
  yield  — recent cycles kept finding new deals → channels are busy

The strongest pressure wins and maps linearly onto [auto_min, auto_max].
The move is smoothed (half-way per cycle) so one odd cycle doesn't swing it,
except when pressure is high — then it jumps straight down so a rush is
served now, not three cycles from now.

Telegram flood-wait safety applies in BOTH modes: after Telegram tells the
reader account to back off, we stay slow (>= 10 min, >= 2x the wait) for an
hour. The owner may pick 1-minute polling by hand, but the reader account
must never get locked for it.

Everything is cached in memory after the first read so /api/ping stays
DB-free; the cache is only written by the setters here.
"""
from __future__ import annotations

import logging
import time
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple

from .. import db

log = logging.getLogger(__name__)

MODE_META_KEY = "poll_mode"
AUTO_MIN_META_KEY = "poll_auto_min_seconds"
AUTO_MAX_META_KEY = "poll_auto_max_seconds"
FLOOD_META_KEY = "poll_flood_wait"        # "<unix ts>:<seconds>" — survives a restart mid-cooldown

MODES = ("manual", "auto")
BOUND_MIN_SECONDS = 60                    # same range as manual (ingest.MIN/MAX_POLL_INTERVAL_SECONDS)
BOUND_MAX_SECONDS = 3600
DEFAULT_AUTO_MIN_SECONDS = 120
DEFAULT_AUTO_MAX_SECONDS = 1800

TARGET_LIVE_DEALS = 300                   # a healthy-looking feed; fewer → fetch harder
BUSY_USERS = 25                           # this many people at once = full demand pressure
ACTIVE_WINDOW_SECONDS = 900               # "active user" = seen in the last 15 min
YIELD_FULL = 10                           # new deals per cycle that counts as "busy channels"
YIELD_WEIGHT = 0.8                        # yield alone never pins us to the floor
JUMP_PRESSURE = 0.8                       # at/above this, skip smoothing when speeding up
ROUND_TO = 30

FLOOD_GUARD_WINDOW = 3600                 # stay careful for an hour after a flood wait
FLOOD_GUARD_FLOOR = 600                   # ...never faster than 10 min meanwhile

_mode_cache: Optional[str] = None
_bounds_cache: Optional[Tuple[int, int]] = None
_flood_cache: Optional[Tuple[float, int]] = None
_flood_loaded = False
_recent_new: Deque[int] = deque(maxlen=3)
_decision: Optional[Dict[str, Any]] = None


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _int_meta(key: str, default: int) -> int:
    try:
        value = int(db.get_meta(key) or "")
    except (TypeError, ValueError):
        return default
    return value if BOUND_MIN_SECONDS <= value <= BOUND_MAX_SECONDS else default


# --- settings --------------------------------------------------------------

def mode() -> str:
    global _mode_cache
    if _mode_cache is None:
        raw = (db.get_meta(MODE_META_KEY) or "").strip().lower()
        _mode_cache = raw if raw in MODES else "manual"
    return _mode_cache


def bounds() -> Tuple[int, int]:
    global _bounds_cache
    if _bounds_cache is None:
        lo = _int_meta(AUTO_MIN_META_KEY, DEFAULT_AUTO_MIN_SECONDS)
        hi = _int_meta(AUTO_MAX_META_KEY, DEFAULT_AUTO_MAX_SECONDS)
        if lo > hi:  # a half-written pair — fall back rather than guess
            lo, hi = DEFAULT_AUTO_MIN_SECONDS, DEFAULT_AUTO_MAX_SECONDS
        _bounds_cache = (lo, hi)
    return _bounds_cache


def validate(new_mode: str, auto_min: Optional[int] = None, auto_max: Optional[int] = None) -> Optional[str]:
    """Error message for a bad request, or None if it's fine."""
    if new_mode not in MODES:
        return f"mode must be one of {', '.join(MODES)}."
    cur_lo, cur_hi = bounds()
    lo = cur_lo if auto_min is None else auto_min
    hi = cur_hi if auto_max is None else auto_max
    for label, value in (("auto_min_seconds", lo), ("auto_max_seconds", hi)):
        if not (BOUND_MIN_SECONDS <= int(value) <= BOUND_MAX_SECONDS):
            return f"{label} must be between {BOUND_MIN_SECONDS} and {BOUND_MAX_SECONDS} seconds."
    if lo > hi:
        return "auto_min_seconds can't be more than auto_max_seconds."
    return None


def set_mode(new_mode: str, auto_min: Optional[int] = None, auto_max: Optional[int] = None) -> Dict[str, Any]:
    """Persist mode (+ optional auto bounds). Raises ValueError on bad input."""
    global _mode_cache, _bounds_cache, _decision
    error = validate(new_mode, auto_min, auto_max)
    if error:
        raise ValueError(error)
    lo, hi = bounds()
    lo = lo if auto_min is None else int(auto_min)
    hi = hi if auto_max is None else int(auto_max)
    db.set_meta(MODE_META_KEY, new_mode)
    db.set_meta(AUTO_MIN_META_KEY, str(lo))
    db.set_meta(AUTO_MAX_META_KEY, str(hi))
    _mode_cache, _bounds_cache = new_mode, (lo, hi)
    if new_mode == "auto":
        # Decide right away (not at the end of the next cycle) so the admin
        # sees a real interval + reason the moment they flip the switch.
        prev = _decision["seconds"] if _decision else None
        _decision = None
        decide(previous=prev)
    return {"mode": new_mode, "auto_min_seconds": lo, "auto_max_seconds": hi}


# --- inputs fed by ingest / telegram ----------------------------------------

def record_cycle(new_count: int) -> None:
    """Feed the yield signal — called at the end of every successful cycle."""
    _recent_new.append(max(0, int(new_count or 0)))


def _flood() -> Optional[Tuple[float, int]]:
    global _flood_cache, _flood_loaded
    if not _flood_loaded:
        _flood_loaded = True
        raw = db.get_meta(FLOOD_META_KEY) or ""
        try:
            at, secs = raw.split(":", 1)
            _flood_cache = (float(at), int(secs))
        except (TypeError, ValueError):
            _flood_cache = None
    return _flood_cache


def note_flood_wait(seconds: int, now: Optional[float] = None) -> None:
    """Telegram told the reader account to back off — slow down for a while."""
    global _flood_cache, _flood_loaded
    now = now or time.time()
    _flood_cache, _flood_loaded = (now, max(0, int(seconds or 0))), True
    try:
        db.set_meta(FLOOD_META_KEY, f"{now}:{_flood_cache[1]}")
    except Exception as exc:  # noqa: BLE001 — the in-memory guard still holds
        log.warning("Couldn't persist flood wait: %s", exc)


def flood_wait_until(now: Optional[float] = None) -> Optional[float]:
    """When the flood-wait safety lifts, or None if it isn't active."""
    flood = _flood()
    if not flood:
        return None
    at, secs = flood
    until = at + max(FLOOD_GUARD_WINDOW, secs)
    return until if (now or time.time()) < until else None


def flood_guard(seconds: int, now: Optional[float] = None) -> int:
    """Apply the flood-wait safety to any interval (manual or auto)."""
    if flood_wait_until(now) is None:
        return seconds
    _, secs = _flood()  # type: ignore[misc]
    return min(BOUND_MAX_SECONDS, max(int(seconds), FLOOD_GUARD_FLOOR, secs * 2))


def flood_note(now: Optional[float] = None) -> str:
    until = flood_wait_until(now)
    if until is None:
        return ""
    _, secs = _flood()  # type: ignore[misc]
    mins = max(1, round((until - (now or time.time())) / 60))
    return (f"Telegram asked us to wait {secs}s — polling at least every "
            f"{FLOOD_GUARD_FLOOR // 60} min for the next {mins} min to protect the reader account")


# --- the decision -----------------------------------------------------------

def _live_deals(now: float) -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM deals WHERE status='live' AND expires_at > ?", (now,))
    return int(row["c"]) if row else 0


def _app_users(now: float) -> int:
    row = db.query_one(
        "SELECT COUNT(*) AS c FROM devices WHERE last_seen_at >= ? AND platform IN ('android', 'ios')",
        (now - ACTIVE_WINDOW_SECONDS,),
    )
    return int(row["c"]) if row else 0


def _fmt(seconds: int) -> str:
    if seconds % 60 == 0:
        return f"{seconds // 60} min"
    if seconds > 60:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds} s"


def decide(now: Optional[float] = None, previous: Optional[int] = None) -> Dict[str, Any]:
    """Re-decide the auto interval from live data. Cached until the next call."""
    global _decision
    from . import activity  # local: keeps this module import-light for ingest

    now = now or time.time()
    lo, hi = bounds()
    live = _live_deals(now)
    app_users = _app_users(now)
    web_users = activity.active_web_visitors(now)
    users = app_users + web_users
    new_avg = (sum(_recent_new) / len(_recent_new)) if _recent_new else 0.0

    supply = _clamp01((TARGET_LIVE_DEALS - live) / TARGET_LIVE_DEALS)
    demand = _clamp01(users / BUSY_USERS)
    yld = _clamp01(new_avg / YIELD_FULL) * YIELD_WEIGHT
    pressure = max(supply, demand, yld)
    target = hi - pressure * (hi - lo)

    if previous is None and _decision is not None:
        previous = _decision["base_seconds"]
    if previous is None or (target < previous and pressure >= JUMP_PRESSURE):
        nxt = target
    else:
        nxt = previous + (target - previous) * 0.5
    base = int(max(lo, min(hi, round(nxt / ROUND_TO) * ROUND_TO)))

    user_bit = f"{users} {'person' if users == 1 else 'people'} using the app/site"
    reason = (f"{user_bit}, {live} live deals (target {TARGET_LIVE_DEALS}), "
              f"{round(new_avg, 1):g} new deals per cycle → every {_fmt(base)}")
    _decision = {
        "base_seconds": base,
        "seconds": base,
        "decided_at": now,
        "reason": reason,
        "pressure": round(pressure, 3),
        "signals": {
            "live_deals": live,
            "target_live": TARGET_LIVE_DEALS,
            "active_users": users,
            "app_users": app_users,
            "web_users": web_users,
            "new_per_cycle": round(new_avg, 2),
        },
    }
    return dict(_decision, seconds=flood_guard(base, now))


def current_seconds(now: Optional[float] = None) -> int:
    """The auto interval in effect (flood safety applied). DB-free once decided."""
    if _decision is None:
        try:
            decide(now)
        except Exception as exc:  # noqa: BLE001 — never leave ingest without an interval
            log.warning("Auto poll decision failed: %s", exc)
            return flood_guard(bounds()[1], now)
    return flood_guard(_decision["base_seconds"], now)  # type: ignore[index]


def status(now: Optional[float] = None) -> Dict[str, Any]:
    """The "auto" block of GET /api/admin/reader/poll-interval."""
    now = now or time.time()
    lo, hi = bounds()
    if _decision is None and mode() == "auto":
        current_seconds(now)
    d = _decision or {}
    signals = dict(d.get("signals") or {
        "live_deals": 0, "target_live": TARGET_LIVE_DEALS, "active_users": 0,
        "app_users": 0, "web_users": 0, "new_per_cycle": 0.0,
    })
    signals["flood_wait_until"] = flood_wait_until(now)
    reason = d.get("reason") or "Not decided yet — auto mode decides after each ingest cycle."
    note = flood_note(now)
    if note:
        effective = flood_guard(d.get("base_seconds") or hi, now)
        reason = f"{reason}. {note} → every {_fmt(effective)}"
    return {
        "min_seconds": lo,
        "max_seconds": hi,
        "decided_at": d.get("decided_at"),
        "reason": reason,
        "signals": signals,
    }


def reset_cache() -> None:
    """Forget everything in memory (tests; mirrors a restart)."""
    global _mode_cache, _bounds_cache, _flood_cache, _flood_loaded, _decision
    _mode_cache = _bounds_cache = _flood_cache = _decision = None
    _flood_loaded = False
    _recent_new.clear()
