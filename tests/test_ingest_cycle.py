"""run_cycle()'s own bookkeeping: the result shape a caller (admin panel,
/api/stats, the scheduler log line) actually reads.

Run:  python -m tests.test_ingest_cycle

Regression coverage for a real bug: a `for reason, n in ...` loop inside
run_cycle() reused the name of run_cycle's own `reason` parameter (why the
cycle ran — "scheduled"/"admin") — a bare `for` doesn't get its own scope in
Python, so it silently overwrote it with whatever filter reason a channel
last hit. Every cycle's result reported "reason": "no_image" (or whatever)
instead of "scheduled", corrupting the one field that says why the cycle ran.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import ingest  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


async def _fake_ingest_channel(channel):
    """Stands in for a real Telegram fetch: returns filtered-reason keys whose
    names must never leak into run_cycle's own "reason" field."""
    return {
        "fetched": 3, "new": 0, "merged": 0, "skipped": 0, "filtered": 3, "resolved": 0,
        "reasons": {"no_image": 2, "roundup": 1}, "new_deal_ids": [],
    }


def main() -> int:
    db.connect()
    db.execute("INSERT INTO channels (tg_id, title, active, last_fetched_at) VALUES (1, 'c', 1, 0)")

    real_ingest_channel = ingest.ingest_channel
    ingest.ingest_channel = _fake_ingest_channel
    try:
        print("\n=== RUN_CYCLE: RESULT BOOKKEEPING ===")
        for trigger in ("scheduled", "admin", "no_image"):  # last one: the exact shadowing value
            result = asyncio.run(ingest.run_cycle(trigger))
            check(f'reason="{trigger}" survives the channel loop', result.get("reason") == trigger,
                  str(result.get("reason")))
        result = asyncio.run(ingest.run_cycle("scheduled"))
        check("per-channel filtered reasons still aggregate correctly",
              result.get("filtered_reasons") == {"no_image": 2, "roundup": 1}, str(result.get("filtered_reasons")))
        check("filtered count aggregates across channels", result.get("filtered") == 3, str(result.get("filtered")))
    finally:
        ingest.ingest_channel = real_ingest_channel

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
