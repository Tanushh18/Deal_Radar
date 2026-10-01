"""Promotion counters the app and site report: shares and "invite a friend" taps.

Anonymous totals only (services/growth.py): the label says where it came from ("app", "web"…),
nothing says who. Fire-and-forget from the client: it never needs an answer.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException
from typing import Any

from ..services import growth, ratelimit

router = APIRouter(prefix="/api/growth", tags=["growth"])
_limit = ratelimit.limit("growth-event", max_requests=30, window_seconds=60)


@router.post("/event", dependencies=[Depends(_limit)])
async def report_event(payload: Any = Body(None)):
    if not isinstance(payload, dict) or payload.get("event") not in growth.CLIENT_EVENTS:
        raise HTTPException(status_code=400, detail=f"event must be one of {', '.join(growth.CLIENT_EVENTS)}.")
    growth.record(payload["event"], growth.clean_src(payload.get("src"), default="unknown"))
    return {"ok": True}
