"""The notification copy the app writes its own notifications from.

The phone picks a moment and a line for most notifications itself
(mobile/src/native/smartNotify.ts). It ships with a built-in set and refreshes
it from here, so a copy change reaches every install without a store release.
Cheap to poll: the version is a content hash, sent as the ETag, so an unchanged
list is a 304 with no body.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Response

from ..services import pitara

router = APIRouter(prefix="/api", tags=["notifications"])


@router.get("/notification-templates")
async def notification_templates(request: Request, response: Response):
    templates, version = pitara.load()
    etag = f'"{version}"'
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    return {"version": version, "count": len(templates), "templates": templates}
