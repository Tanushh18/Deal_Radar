import asyncio
from types import SimpleNamespace

import httpx

from app.config import settings
from app.services import nodes, replica


def _as(monkeypatch, role, self_url="https://me.example", fallback=""):
    monkeypatch.setattr(settings, "node_role", role)
    monkeypatch.setattr(settings, "self_url", self_url)
    monkeypatch.setattr(settings, "user_node_url", fallback)
    monkeypatch.setattr(nodes, "_cache", [])
    monkeypatch.setattr(nodes, "_penalty", {})


def test_round_robin_skips_penalised_and_never_returns_self(monkeypatch):
    _as(monkeypatch, "ingest")
    monkeypatch.setattr(nodes, "_cache", ["https://a.example", "https://b.example", "https://me.example"])
    picks = {nodes.pick_user_url() for _ in range(6)}
    assert picks == {"https://a.example", "https://b.example"}
    nodes.mark_failed("https://a.example")
    assert {nodes.pick_user_url() for _ in range(6)} == {"https://b.example"}


def test_all_penalised_still_tries_something(monkeypatch):
    _as(monkeypatch, "validator")
    monkeypatch.setattr(nodes, "_cache", ["https://a.example"])
    nodes.mark_failed("https://a.example")
    assert nodes.pick_user_url() == "https://a.example"


def test_static_url_is_the_fallback_when_registry_is_empty(monkeypatch):
    _as(monkeypatch, "ingest", fallback="https://u.example")
    assert nodes.pick_user_url() == "https://u.example"


def test_who_forwards(monkeypatch):
    _as(monkeypatch, "ingest", fallback="https://u.example")
    assert replica.forwards()
    _as(monkeypatch, "validator", fallback="https://u.example")
    assert replica.forwards()
    _as(monkeypatch, "user", fallback="https://u.example")
    assert not replica.forwards()
    _as(monkeypatch, "ingest")           # nobody to forward to yet
    assert not replica.forwards()


def test_channels_belong_to_the_ingest_node_only(monkeypatch):
    _as(monkeypatch, "ingest")
    assert settings.owns_channel(123)
    _as(monkeypatch, "validator")
    assert not settings.owns_channel(123)
    _as(monkeypatch, "user")
    assert not settings.owns_channel(123)


def _request():
    return SimpleNamespace(
        method="POST", url=SimpleNamespace(path="/api/devices/register", query=""),
        headers={"content-type": "application/json"}, client=SimpleNamespace(host="1.2.3.4"),
        body=lambda: _body())


async def _body():
    return b"{}"


def test_forward_fails_over_to_the_next_user_node(monkeypatch):
    _as(monkeypatch, "ingest")
    monkeypatch.setattr(nodes, "_cache", ["https://bad.example", "https://good.example"])

    def handler(req: httpx.Request) -> httpx.Response:
        if "bad.example" in str(req.url):
            return httpx.Response(404)
        assert req.headers["x-dr-forwarded"] == "1"
        return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(replica, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(replica.forward(_request()))
    assert out is not None and out.status_code == 200
    assert nodes._penalty.get("https://bad.example", 0) > 0


def test_forward_returns_none_when_every_node_is_down(monkeypatch):
    _as(monkeypatch, "ingest")
    monkeypatch.setattr(nodes, "_cache", ["https://a.example"])
    monkeypatch.setattr(replica, "_client",
                        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(508))))
    assert asyncio.run(replica.forward(_request())) is None


def test_forward_passes_compressed_bodies_through_untouched(monkeypatch):
    import gzip
    _as(monkeypatch, "ingest")
    monkeypatch.setattr(nodes, "_cache", ["https://u.example"])
    payload = gzip.compress(b'{"hello":"world"}')

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload, headers={"content-encoding": "gzip", "content-type": "application/json"})

    monkeypatch.setattr(replica, "_client", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = asyncio.run(replica.forward(_request()))
    # Whatever the transport did, body and encoding header must agree.
    if "content-encoding" in out.headers:
        assert gzip.decompress(out.body) == b'{"hello":"world"}'
    else:
        assert out.body == b'{"hello":"world"}'


def test_non_ingest_nodes_report_the_ingest_servers_last_sync(monkeypatch):
    _as(monkeypatch, "user")
    monkeypatch.setattr(nodes, "_ingest_last", 1234.0)
    assert nodes.ingest_last_run(0.0) == 1234.0      # user server: shows ingest's time
    assert nodes.ingest_last_run(99.0) == 99.0       # a server that synced itself keeps its own
    _as(monkeypatch, "")                             # not split: nothing borrowed
    assert nodes.ingest_last_run(0.0) == 0.0


def test_ingest_urls_registry_first_then_public_domain_never_self(monkeypatch):
    _as(monkeypatch, "user", self_url="https://me.example")
    monkeypatch.setattr(settings, "public_url", "https://deals.example")
    monkeypatch.setattr(nodes, "_ingest_urls", ["https://ing.example", "https://me.example"])
    assert nodes.ingest_urls() == ["https://ing.example", "https://deals.example"]
    monkeypatch.setattr(nodes, "_ingest_urls", [])
    assert nodes.ingest_urls() == ["https://deals.example"]


def test_image_on_a_non_ingest_node_comes_from_the_ingest_server(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    from app.routers import deals

    _as(monkeypatch, "user")
    monkeypatch.setattr(nodes, "_ingest_urls", ["https://ing.example"])
    monkeypatch.setattr(settings, "public_url", "")
    seen = {}

    def handler(req):
        seen["url"], seen["xff"] = str(req.url), req.headers.get("x-forwarded-for")
        return httpx.Response(200, content=b"JPEG", headers={"content-type": "image/jpeg"})

    monkeypatch.setattr(deals, "_image_http", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    monkeypatch.setattr(deals, "_image_cache", type(deals._image_cache)())
    with TestClient(app) as c:
        r = c.get("/api/deals/abc/image?src=1:2", headers={"x-forwarded-for": "9.9.9.9"})
    assert r.status_code == 200 and r.content == b"JPEG"
    assert seen["url"] == "https://ing.example/api/deals/abc/image?src=1%3A2"
    assert seen["xff"] == "9.9.9.9"
