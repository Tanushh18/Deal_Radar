"""Public mode seeding: PUBLIC_CHANNELS must not be re-joined after the reader leaves them,
and an admin block (enabled=0) must survive a restart.

Run:  python -m tests.test_public_reader
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from types import SimpleNamespace

os.environ.setdefault("PRIORITY_AUDIENCE", "off")
os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")
os.environ.update(SECRET_KEY="test-secret-key", GOOGLE_SHEET_ID="", TELEGRAM_API_ID="", TELEGRAM_API_HASH="",
                  PUBLIC_URL="https://deals.example", ADMIN_TOKEN="t", TURSO_DATABASE_URL="", TURSO_AUTH_TOKEN="",
                  MONGODB_URI="")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import db  # noqa: E402
from app.config import settings  # noqa: E402
from app.services import public_reader, telegram  # noqa: E402

PASS, FAIL = "\033[92m✓\033[0m", "\033[91m✗\033[0m"
failures = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {PASS if ok else FAIL} {label}" + (f"  — {detail}" if detail and not ok else ""))
    if not ok:
        failures.append(label)


class FakeClient:
    async def connect(self):
        return None

    async def is_user_authorized(self):
        return True

    async def get_me(self):
        return SimpleNamespace(id=999001, username="reader", first_name="Reader")

    async def disconnect(self):
        return None


joined: list[str] = []


async def fake_resolve(user_id: int, username: str):
    joined.append(username)
    return {"tg_id": abs(hash(username)) % 10**9 + 1, "username": username, "title": username, "participants": 0}


async def noop_async(*_a, **_k):
    return None


def run_bootstrap() -> int:
    return asyncio.run(public_reader.bootstrap())


def patch() -> None:
    telegram._new_client = lambda _session: FakeClient()
    telegram.resolve_public_channel = fake_resolve
    telegram.drop_client = noop_async
    telegram.encrypt_session = lambda s: s
    public_reader.sync_followed = noop_async


def user_row(username: str):
    return db.query_one(
        "SELECT uc.enabled FROM channels c JOIN user_channels uc ON uc.channel_id = c.id "
        "JOIN users u ON u.id = uc.user_id WHERE LOWER(c.username) = LOWER(?) AND u.telegram_id = 999001",
        (username,),
    )


def main() -> int:
    patch()
    settings.public_channels = ["alpha", "beta"]

    print("First boot seeds the list once")
    run_bootstrap()
    check("joins both seeded channels", joined == ["alpha", "beta"], f"joined={joined}")
    check("alpha is tracked", user_row("alpha") is not None and user_row("alpha")["enabled"] == 1)

    print("Reader leaves alpha on Telegram; sync removes its row; restart")
    uid = int(db.query_one("SELECT id FROM users WHERE telegram_id = 999001")["id"])
    db.execute("DELETE FROM user_channels WHERE user_id = ? AND channel_id IN "
               "(SELECT id FROM channels WHERE LOWER(username) = 'alpha')", (uid,))
    joined.clear()
    run_bootstrap()
    check("alpha is NOT re-joined after restart", "alpha" not in joined, f"joined={joined}")
    check("alpha stays untracked", user_row("alpha") is None)

    print("Admin blocks beta; restart must not unblock or re-join it")
    db.execute("UPDATE user_channels SET enabled = 0 WHERE user_id = ? AND channel_id IN "
               "(SELECT id FROM channels WHERE LOWER(username) = 'beta')", (uid,))
    joined.clear()
    run_bootstrap()
    check("beta is NOT re-joined", "beta" not in joined, f"joined={joined}")
    check("beta stays blocked (enabled=0)", user_row("beta") is not None and user_row("beta")["enabled"] == 0)

    print("A brand-new channel added to PUBLIC_CHANNELS is still seeded")
    settings.public_channels = ["alpha", "beta", "gamma"]
    joined.clear()
    run_bootstrap()
    check("only the new channel is joined", joined == ["gamma"], f"joined={joined}")

    print()
    if failures:
        print(f"\033[91m{len(failures)} check(s) failed\033[0m")
        return 1
    print("\033[92mAll public-reader checks passed\033[0m")
    return 0


if __name__ == "__main__":
    sys.exit(main())
