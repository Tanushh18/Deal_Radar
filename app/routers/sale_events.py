"""Public read of live sale banners and the upcoming-sales calendar (admin CRUD lives in admin.py)."""
from __future__ import annotations

from fastapi import APIRouter

from ..services import live_banners, sale_events as service

router = APIRouter(prefix="/api/sale-events", tags=["sale-events"])

_FIELDS = ("id", "name", "store", "starts_at", "ends_at", "approximate", "hype")


@router.get("")
async def upcoming():
    """Banners for the website/app: the stores' own live sale banners when we
    could read them, otherwise the curated calendar of upcoming sales."""
    live = await live_banners.get_live()
    if live:
        return {"events": live, "source": "live"}
    events = service.list_all(upcoming_only=True)
    return {"events": [{k: e[k] for k in _FIELDS} for e in events], "source": "calendar"}
