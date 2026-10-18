"""Short affiliate links: /a/<ASIN> and /f/<pid> redirect to the tagged Amazon / EarnKaro Flipkart link."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse

from ..services import shortlinks

router = APIRouter(tags=["shortlinks"])


def _go(kind: str, ident: str) -> RedirectResponse:
    link = shortlinks.target(kind, ident)
    if not link:
        raise HTTPException(status_code=404, detail="Not found.")
    return RedirectResponse(link, status_code=302, headers={"Cache-Control": "no-store"})


@router.get("/a/{ident}", include_in_schema=False)
async def amazon_short(ident: str):
    return _go("a", ident)


@router.get("/f/{ident}", include_in_schema=False)
async def flipkart_short(ident: str):
    return _go("f", ident)
