import asyncio

from app.config import settings
from app.services import mongo_store, nodes, standby


def _drive(monkeypatch, rounds, telegram=True):
    """Run the standby loop over a scripted sequence of registry reads."""
    monkeypatch.setattr(settings, "node_role", "standby")
    monkeypatch.setattr(settings, "is_standby", True)
    monkeypatch.setattr(settings, "self_url", "https://spare.example")
    monkeypatch.setattr(mongo_store, "is_enabled", lambda: True)
    monkeypatch.setattr(nodes, "beat_now", lambda: None)
    monkeypatch.setattr(standby, "CHECK_SECONDS", 0)
    monkeypatch.setattr(standby, "_can_cover", lambda r: telegram or r != "ingest")
    script = iter(rounds)
    events = []

    def live_nodes():
        try:
            return [{"role": r, "url": f"https://{r}.example"} for r in next(script)]
        except StopIteration:
            raise asyncio.CancelledError

    async def start(role):
        events.append(("start", role, settings.node_role))

    async def stop():
        events.append(("stop", settings.node_role))

    monkeypatch.setattr(nodes, "live_nodes", live_nodes)
    try:
        asyncio.run(standby.run(start, stop))
    except asyncio.CancelledError:
        pass
    return events


ALL = ["ingest", "user", "validator"]


def test_idle_while_everyone_is_up(monkeypatch):
    assert _drive(monkeypatch, [ALL] * 6) == []
    assert settings.node_role == "standby"


def test_covers_a_missing_role_then_hands_back(monkeypatch):
    no_user = ["ingest", "validator"]
    events = _drive(monkeypatch, [no_user] * 3 + [no_user] + [ALL])
    assert events == [("start", "user", "user"), ("stop", "user")]
    assert settings.node_role == "standby"


def test_brief_blip_is_not_a_takeover(monkeypatch):
    assert _drive(monkeypatch, [["ingest", "validator"]] * 2 + [ALL] * 3) == []


def test_most_important_role_first(monkeypatch):
    events = _drive(monkeypatch, [["validator"]] * 4)
    assert events == [("start", "ingest", "ingest")]


def test_skips_ingest_without_a_telegram_session(monkeypatch):
    events = _drive(monkeypatch, [["validator"]] * 4, telegram=False)
    assert events == [("start", "user", "user")]


def test_all_down_means_lite_mode(monkeypatch):
    assert _drive(monkeypatch, [[]] * 6) == []
    events = _drive(monkeypatch, [["validator"]] * 3 + [[]])
    assert events == [("start", "ingest", "ingest"), ("stop", "ingest")]
    assert settings.node_role == "standby"
