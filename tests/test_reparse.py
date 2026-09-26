"""Stored deals parsed by an older parser get corrected from their raw text.

Run:  python -m tests.test_reparse
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

os.environ.setdefault("PRIORITY_AUDIENCE", "off")  # neutral ranking unless a test sets a rule
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

    print("\n=== COUPON CODES ===")
    cases = {
        "Apply Coupon : https://amazon.in/dp/B0DRCYP93L": "",   # live post stored as "HTTPS"
        "Apply Coupon Payment Page ✅": "",                    # stored as "PAYMENT"
        "Apply coupon with ICICI card": "",
        "code amzn.to/abc": "",
        "USE CODE: EK20": "EK20",
        "🏷 Use Code : GBOULT-100": "GBOULT-100",             # was cut to "GBOULT"
        "✅ Use Coupon: BBD699": "BBD699",
        "Coupon: FURNITURE": "FURNITURE",
    }
    for text, want in cases.items():
        got = parser.extract_coupon(text)
        check(f"{text[:34]!r} -> {want or '(none)'}", got == want, got)

    # Stored with the old parser's junk code, and one whose coupon users reported dead.
    junk = parser.parse_message("Boat Airdopes 141 @ ₹999\nApply Coupon : https://amzn.to/4Awrt1P",
                                channel_id=77, channel_title="Loot", message_id=6, posted_at=now)
    dead = parser.parse_message("Mi Power Bank @ ₹1199\n✅ Use Coupon: BBD699\nhttps://amzn.to/4xDB0Bn",
                                channel_id=77, channel_title="Loot", message_id=7, posted_at=now)
    store.save_deal(junk)
    store.save_deal(dead)
    db.execute("UPDATE deals SET coupon = 'HTTPS' WHERE id = ?", (junk["id"],))
    db.execute("UPDATE deals SET coupon = '' WHERE id = ?", (dead["id"],))
    db.execute("DELETE FROM meta WHERE key = 'parser_version'")
    store.reparse_stored_deals()
    coupon = lambda i: db.query_one("SELECT coupon FROM deals WHERE id = ?", (i,))["coupon"]  # noqa: E731
    check("re-parse clears the stored 'HTTPS' code", coupon(junk["id"]) == "", coupon(junk["id"]))
    check("re-parse never revives a coupon reported dead", coupon(dead["id"]) == "", coupon(dead["id"]))

    print("\n" + ("\033[92m✓ All checks passed.\033[0m" if not failures else f"\033[91m✗ {len(failures)} failed\033[0m"))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
