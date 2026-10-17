import asyncio

from app.config import settings
from app.services import cover, mongo_store, nodes, standby


def _drive(monkeypatch, rounds):
    """Run the ingest server's cover loop over a scripted sequence of registry reads."""
    monkeypatch.setattr(settings, "self_url", "https://ingest.example")
    monkeypatch.setattr(mongo_store, "is_enabled", lambda: True)
    monkeypatch.setattr(nodes, "beat_now", lambda: None)
    monkeypatch.setattr(nodes, "covering", set())
    monkeypatch.setattr(cover, "CHECK_SECONDS", 0)
    script = iter(rounds)
    events = []

    def live_nodes():
        try:
            return next(script)
        except StopIteration:
            raise asyncio.CancelledError

    def start(role):
        events.append(("start", role))

        async def work():
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                events.append(("stop", role))
                raise
        return [asyncio.get_event_loop().create_task(work())]

    monkeypatch.setattr(nodes, "live_nodes", live_nodes)
    try:
        asyncio.run(cover.run(start))
    except asyncio.CancelledError:
        pass
    return events


def node(role, standby=False, covers=()):
    return {"role": role, "url": f"https://{role}.example", "standby": standby, "covers": list(covers)}


BOTH = [node("user"), node("validator")]
N = cover.COVER_CHECKS


def test_idle_while_both_servers_are_up(monkeypatch):
    assert _drive(monkeypatch, [BOTH] * (N + 2)) == []


def test_alone_runs_everything(monkeypatch):
    events = _drive(monkeypatch, [[]] * N)
    assert sorted(e for e in events if e[0] == "start") == [("start", "user"), ("start", "validator")]


def test_hands_back_when_the_server_returns(monkeypatch):
    events = _drive(monkeypatch, [[node("user")]] * N + [BOTH])
    assert events[:2] == [("start", "validator"), ("stop", "validator")]


def test_brief_blip_is_not_a_takeover(monkeypatch):
    assert _drive(monkeypatch, [[node("user")]] * (N - 1) + [BOTH] * 3) == []


def test_leaves_a_role_the_standby_covers(monkeypatch):
    events = _drive(monkeypatch, [[node("validator"), node("user", standby=True)]] * (N + 2))
    assert events == []


def test_up_roles_counts_covers():
    assert cover.up_roles([node("ingest", covers=["user"]), node("standby", standby=True)]) == {"ingest", "user"}


def test_standby_skips_roles_the_ingest_server_covers(monkeypatch):
    monkeypatch.setattr(settings, "node_role", "standby")
    monkeypatch.setattr(settings, "is_standby", True)
    monkeypatch.setattr(settings, "self_url", "https://spare.example")
    monkeypatch.setattr(mongo_store, "is_enabled", lambda: True)
    monkeypatch.setattr(nodes, "beat_now", lambda: None)
    monkeypatch.setattr(standby, "CHECK_SECONDS", 0)
    script = iter([[node("ingest", covers=["user", "validator"])]] * 6)

    def live_nodes():
        try:
            return next(script)
        except StopIteration:
            raise asyncio.CancelledError

    started = []

    async def start(role):
        started.append(role)

    async def stop():
        pass

    monkeypatch.setattr(nodes, "live_nodes", live_nodes)
    try:
        asyncio.run(standby.run(start, stop))
    except asyncio.CancelledError:
        pass
    assert started == []
