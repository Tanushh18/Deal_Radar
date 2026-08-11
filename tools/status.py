"""Answer 'is it actually getting deals?' in one command.

    make status            (or: python tools/status.py)

Reads the local cache directly, so it works whether or not the server is up.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.config import settings  # noqa: E402

OK, BAD, WARN = "\033[92m✓\033[0m", "\033[91m✗\033[0m", "\033[93m!\033[0m"


def ago(ts) -> str:
    if not ts:
        return "never"
    secs = time.time() - float(ts)
    if secs < 90:
        return f"{int(secs)}s ago"
    if secs < 5400:
        return f"{int(secs / 60)}m ago"
    if secs < 172800:
        return f"{int(secs / 3600)}h ago"
    return f"{int(secs / 86400)}d ago"


def main() -> int:
    print("\n\033[1mDealRadar status\033[0m")
    print("=" * 62)

    # --- configuration ---
    print("\n\033[1mConfiguration\033[0m")
    print(f"  {OK if settings.telegram_configured else BAD} Telegram API credentials")
    if settings.sheets_configured:
        print(f"  {OK} Google Sheets configured")
    else:
        sheet_id = settings.sheet_id
        if sheet_id and sheet_id.startswith("1AbC"):
            print(f"  {WARN} GOOGLE_SHEET_ID is still the placeholder from .env.example")
        elif sheet_id:
            print(f"  {WARN} Sheet id set, but no service-account JSON")
        else:
            print(f"  {WARN} Google Sheets not configured (SQLite-only: data is lost on restart)")
    secure = settings.secret_key != "dev-insecure-change-me"
    print(f"  {OK if secure else BAD} SECRET_KEY set to a real value")

    # --- storage ---
    if not os.path.exists(settings.db_path):
        print(f"\n  {WARN} No cache database yet ({settings.db_path}).")
        print("      Nothing has been fetched. Sign in at http://localhost:8000 first.\n")
        return 0

    conn = sqlite3.connect(settings.db_path)
    conn.row_factory = sqlite3.Row
    q = lambda sql, p=(): conn.execute(sql, p).fetchall()  # noqa: E731
    one = lambda sql, p=(): conn.execute(sql, p).fetchone()  # noqa: E731

    users = one("SELECT COUNT(*) n FROM users")["n"]
    channels = q("SELECT * FROM channels ORDER BY title")
    active = [c for c in channels if c["active"]]
    total_deals = one("SELECT COUNT(*) n FROM deals")["n"]
    live = one("SELECT COUNT(*) n FROM deals WHERE status='live' AND expires_at > ?", (time.time(),))["n"]

    print("\n\033[1mPipeline\033[0m")
    print(f"  {OK if users else BAD} signed-in users:      {users}")
    print(f"  {OK if active else BAD} channels tracked:     {len(active)}")
    print(f"  {OK if total_deals else BAD} deals stored:         {total_deals}  ({live} live)")

    # --- where is it stuck? ---
    if not users:
        print(f"\n  {WARN} \033[1mNothing will fetch until you sign in.\033[0m")
        print("      Open http://localhost:8000 and log in with your phone number.\n")
        return 0
    if not active:
        print(f"\n  {WARN} \033[1mSigned in, but no channels selected.\033[0m")
        print("      Go to the Channels tab, tick some deal channels, Save selection.\n")
        return 0

    # --- per-channel detail ---
    print("\n\033[1mChannels\033[0m")
    for c in active:
        got = one("SELECT COUNT(*) n FROM deals WHERE channel_id = ?", (c["tg_id"],))["n"]
        mark = OK if got else WARN
        fetched = ago(c["last_fetched_at"])
        print(f"  {mark} {(c['title'] or '?')[:38]:40} {got:>4} deals   last fetch: {fetched}")
        if not got and c["last_fetched_at"]:
            print("       (fetched, but no parseable deals — likely image-only posts or no prices)")

    never = [c for c in active if not c["last_fetched_at"]]
    if never:
        print(f"\n  {WARN} {len(never)} channel(s) never fetched — run a sync (see below).")

    # --- deals ---
    if total_deals:
        print("\n\033[1mMost recent deals\033[0m")
        for d in q(
            "SELECT title, price, discount_pct, store, repost_count, is_lowest, first_seen_at "
            "FROM deals ORDER BY first_seen_at DESC LIMIT 8"
        ):
            low = " \033[93m[ALL-TIME LOW]\033[0m" if d["is_lowest"] else ""
            price = f"Rs{d['price']:.0f}" if d["price"] else "—"
            print(f"  {price:>8}  {(d['discount_pct'] or 0):>2}%  x{d['repost_count']}  "
                  f"{(d['title'] or '')[:44]:46} {ago(d['first_seen_at'])}{low}")

        print("\n\033[1mBy category\033[0m")
        for r in q(
            "SELECT category, COUNT(*) n FROM deals WHERE status='live' "
            "GROUP BY category ORDER BY n DESC LIMIT 8"
        ):
            print(f"  {r['category'][:28]:30} {r['n']}")

    print("\n\033[1mNext\033[0m")
    if not total_deals:
        print("  Trigger a fetch:  click Sync in the UI, or")
        print("  curl -X POST http://localhost:8000/api/admin/ingest -H \"X-Admin-Token: $ADMIN_TOKEN\"")
    else:
        print("  Search:  curl 'http://localhost:8000/api/deals?q=charger&all_channels=true'")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
