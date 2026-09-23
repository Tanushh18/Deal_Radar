"""Stored deals parsed by an older parser get corrected from their raw text.

Run:  python -m tests.test_reparse
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.services import parser, store  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def main() -> int:
    db.connect()
    now = time.time()
    # The real post that the old parser stored at ₹91 (live site, 2026-09-23).
    raw = "Analog Watch - For Men @ ₹300\n\n👉https://cuttli.in/ujOKVp3T\n👉https://cuttli.in/8Yxn7i5r"
    deal = parser.parse_message(raw, channel_id=77, channel_title="Loot", message_id=5, posted_at=now)
    deal_id = deal["id"]
    store.save_deal(deal)
    key = db.query_one("SELECT product_key FROM deals WHERE id = ?", (deal_id,))["product_key"]

    # Rewind the row to what the old parser wrote.
    db.execute("UPDATE deals SET price = 91, mrp = 300, discount_pct = 70, is_lowest = 1, dirty = 0 WHERE id = ?", (deal_id,))
    db.execute("DELETE FROM price_history WHERE product_key = ?", (key,))
    db.execute("INSERT INTO price_history (product_key, price, store, seen_at) VALUES (?, 91, 'cuttli', ?)", (key, now - 60))
    db.execute("DELETE FROM meta WHERE key = 'parser_version'")

    print("\n=== RE-PARSE STORED DEALS ===")
    fixed = store.reparse_stored_deals()
    row = db.query_one("SELECT price, mrp, discount_pct, is_lowest, dirty FROM deals WHERE id = ?", (deal_id,))
    check("one deal corrected", fixed == 1, str(fixed))
    check("price is ₹300 again", row["price"] == 300.0, str(row["price"]))
    check("fabricated MRP removed", row["mrp"] is None, str(row["mrp"]))
    check("fabricated discount removed", row["discount_pct"] == 0, str(row["discount_pct"]))
    check("not flagged all-time low", row["is_lowest"] == 0, str(row["is_lowest"]))
    check("marked dirty for Sheets", row["dirty"] == 1)
    hist = [r["price"] for r in db.query("SELECT price FROM price_history WHERE product_key = ?", (key,))]
    check("wrong ₹91 dropped from history", 91.0 not in hist and 300.0 in hist, str(hist))
    check("version recorded", db.get_meta("parser_version") == str(parser.PARSER_VERSION))
    check("second run is a no-op", store.reparse_stored_deals() == 0)

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
