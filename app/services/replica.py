"""Read replica mode (ROLE=replica).

A replica answers the hot, read-only deal endpoints from its own local cache
and forwards everything else (sign-in, watchlists, devices, admin, writes,
click-tracking redirects) to the primary, which is the only instance that runs
ingest, Telegram and push. All instances must share SECRET_KEY, MONGODB_URI and
the Turso settings so a session cookie issued by one is valid on the others.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
from fastapi import Request, Response

from ..config import settings
from . import leader, nodes, turso_backup

log = logging.getLogger("dealradar.replica")

_LOCAL_EXACT = {"/api/ping", "/api/health", "/api/node-check"}  # node-check: temporary
_HOP = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade",
        "proxy-authenticate", "proxy-authorization", "host", "content-length", "content-encoding"}
_RESP_HOP = _HOP - {"content-encoding"}  # we pass the upstream's raw bytes through, so its encoding header must stay
_client: httpx.AsyncClient | None = None


def _same_origin(a: str, b: str) -> bool:
    return a.rstrip("/").lower() == b.rstrip("/").lower()


def forwards() -> bool:
    if settings.is_replica:
        return True
    if settings.sharded:
        return not settings.is_user_node and bool(nodes.user_urls())
    url = leader.leader_url()
    # Servers sharing one public domain all advertise that same URL, so "forward to the
    # leader" would just hit the load balancer and come straight back (508 Loop Detected).
    # Only forward when the leader has an address of its own.
    return (leader.enabled() and not leader.is_leader() and bool(url)
            and not _same_origin(url, settings.self_url))


def upstream_url() -> str:
    if settings.sharded:
        return nodes.pick_user_url()
    return settings.primary_url if settings.is_replica else leader.leader_url()


def serves_locally(request: Request) -> bool:
    path = request.url.path
    if not path.startswith("/api/"):
        return True  # static shell
    if request.method not in ("GET", "HEAD"):
        return False
    if path in _LOCAL_EXACT:
        return True
    return path.startswith("/api/deals") and not path.endswith("/go")


async def forward(request: Request) -> Optional[Response]:
    """Send the request to a healthy upstream; try a second one before giving up."""
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=30.0, follow_redirects=False)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in _HOP}
    headers["x-dr-forwarded"] = "1"
    headers["x-forwarded-for"] = (request.headers.get("x-forwarded-for") or
                                  (request.client.host if request.client else ""))
    body = await request.body()
    tried: set = set()
    for _ in range(2):
        base = upstream_url()
        if not base or base in tried:
            break
        tried.add(base)
        url = base + request.url.path + (f"?{request.url.query}" if request.url.query else "")
        try:
            req = _client.build_request(request.method, url, headers=headers, content=body)
            upstream = await _client.send(req, stream=True)
            try:
                decoded = False
                try:
                    raw = b"".join([chunk async for chunk in upstream.aiter_raw()])  # undecoded, as sent
                except httpx.StreamConsumed:
                    raw, decoded = upstream.content, True  # already read (and decoded) by the transport
            finally:
                await upstream.aclose()
        except httpx.HTTPError as exc:
            log.warning("Upstream %s unreachable for %s %s: %s", base, request.method, request.url.path, exc)
            nodes.mark_failed(base)
            continue
        if upstream.status_code in (404, 502, 503, 504, 508):
            nodes.mark_failed(base)  # that address isn't a live DealRadar server right now
            continue
        out = Response(content=raw, status_code=upstream.status_code)
        for k, v in upstream.headers.multi_items():
            if k.lower() not in (_HOP if decoded else _RESP_HOP):
                out.headers.append(k, v)
        return out
    return None  # nothing reachable: serve it here rather than fail


async def refresh_loop() -> None:
    """Pull new deals from Turso so the local cache tracks what the primary ingests."""
    loop = asyncio.get_event_loop()
    while True:
        await asyncio.sleep(settings.replica_refresh_seconds)
        if not forwards() and not settings.sharded:
            continue  # the leader is the one writing deals — nothing to pull
        try:
            await loop.run_in_executor(None, turso_backup.restore, settings.cache_days)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            log.warning("Replica refresh failed: %s", exc)
