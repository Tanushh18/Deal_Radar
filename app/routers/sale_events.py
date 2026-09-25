"""Public read of the upcoming-sales calendar (admin CRUD lives in admin.py)."""
from __future__ import annotations

from fastapi import APIRouter

from ..services import sale_events as service

router = APIRouter(prefix="/api/sale-events", tags=["sale-events"])


@router.get("")
async def upcoming():
    """Upcoming (not-yet-ended) sale events, soonest first — for the website/app banner."""
    events = service.list_all(upcoming_only=True)
    return {"events": [
        {k: e[k] for k in ("id", "name", "store", "starts_at", "ends_at", "approximate", "hype")}
        for e in events
    ]}
