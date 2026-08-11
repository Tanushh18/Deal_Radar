"""Ping, health, stats, and admin maintenance.

/api/ping is deliberately the cheapest endpoint in the app: no DB, no Telegram,
no Sheets. It exists so an uptime pinger can keep a Render free instance awake
without doing real work on every hit.
"""
from __future__ import annotations

import platform
import time

from fastapi import APIRouter, Depends, Response

from .. import auth, db
from ..config import settings
from ..services import ingest, sheets, store

router = APIRouter(prefix="/api", tags=["system"])

BOOT_TIME = time.time()


@router.get("/ping")
async def ping():
    """Liveness probe / keepalive target."""
    return {
        "status": "ok",
        "service": "dealradar",
        "timestamp": int(time.time()),
        "uptime_seconds": int(time.time() - BOOT_TIME),
    }


@router.head("/ping")
async def ping_head():
    """HEAD variant so uptime monitors can poll with zero response body."""
    return Response(status_code=200)


@router.get("/health")
async def health():
    """Deeper check: is storage reachable, is ingestion actually running."""
    checks = {}
    try:
        db.query_one("SELECT 1 AS ok")
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {exc}"

    checks["telegram_configured"] = settings.telegram_configured
    checks["sheets"] = sheets.status()

    ingest_state = ingest.state()
    last_run = ingest_state.get("last_run") or 0
    stale = bool(last_run) and (time.time() - last_run) > settings.poll_interval_seconds * 3
    checks["ingest"] = {
        "running": ingest_state.get("running"),
        "cycles": ingest_state.get("cycles"),
        "last_run_ago_seconds": int(time.time() - last_run) if last_run else None,
        "last_duration": ingest_state.get("last_duration"),
        "last_error": ingest_state.get("last_error"),
        "stale": stale,
    }

    healthy = checks["database"] == "ok"
    return {
        "status": "healthy" if healthy else "degraded",
        "uptime_seconds": int(time.time() - BOOT_TIME),
        "python": platform.python_version(),
        "checks": checks,
    }


@router.get("/stats")
async def stats():
    data = db.stats()
    data["ingest"] = ingest.state()
    data["poll_interval_seconds"] = settings.poll_interval_seconds
    data["deal_ttl_hours"] = settings.deal_ttl_hours
    return data


# --- admin (requires ADMIN_TOKEN) --------------------------------------
@router.post("/admin/ingest", dependencies=[Depends(auth.require_admin)])
async def admin_ingest():
    return await ingest.run_cycle("admin")


@router.post("/admin/sheets/flush", dependencies=[Depends(auth.require_admin)])
async def admin_flush():
    return sheets.flush_deals()


@router.post("/admin/sheets/restore", dependencies=[Depends(auth.require_admin)])
async def admin_restore():
    return {"restored": sheets.restore_deals()}


@router.post("/admin/backfill-channel-ids", dependencies=[Depends(auth.require_admin)])
async def admin_backfill_channel_ids():
    """Repairs deals restored before the Deals sheet tracked channel_tg_id —
    those came back as channel_id=0, invisible to any signed-in user's own
    channel-scoped view. Safe to call repeatedly."""
    fixed = store.backfill_channel_ids()
    flushed = sheets.flush_deals() if fixed else {"updated": 0, "appended": 0}
    return {"fixed": fixed, "flushed": flushed}


@router.post("/admin/sheets/sync-meta", dependencies=[Depends(auth.require_admin)])
async def admin_sync_meta():
    return {"channels": sheets.sync_channels(), "users": sheets.sync_users()}
