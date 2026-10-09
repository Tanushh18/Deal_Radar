"""Public read of live sale banners and the upcoming-sales calendar (admin CRUD lives in admin.py)."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response

from ..services import live_banners, sale_events as service

router = APIRouter(prefix="/api/sale-events", tags=["sale-events"])

_FIELDS = ("id", "name", "store", "starts_at", "ends_at", "approximate", "hype")


def _origin(request: Request) -> str:
    """Public origin of this server (behind Render's proxy the scheme comes from X-Forwarded-Proto)."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    return f"{proto}://{host}"


@router.get("")
async def upcoming(request: Request):
    """Banners for the website/app: the stores' own live sale banners (captured
    from their sale pages), plus calendar cards for every other live/upcoming
    sale whose store has no captured banner. Relative image URLs (banners we
    host) are made absolute so the native app can load them too."""
    live = await live_banners.get_live()
    origin = _origin(request)
    live = [{**b, "image_url": origin + b["image_url"]} if str(b.get("image_url", "")).startswith("/") else b
            for b in live]
    covered = {b["store"] for b in live}
    calendar = [{k: e[k] for k in _FIELDS} for e in service.list_all(upcoming_only=True)
                if e["store"] not in covered]
    return {"events": live + calendar, "source": "live" if live else "calendar"}


@router.get("/banner/{banner_id}")
async def banner(banner_id: str):
    """A store banner screenshot captured by tools/fetch_banners.py."""
    found = live_banners.banner_image(banner_id)
    if not found:
        raise HTTPException(status_code=404, detail="No such banner.")
    data, media = found
    return Response(content=data, media_type=media, headers={"Cache-Control": "public, max-age=3600"})
