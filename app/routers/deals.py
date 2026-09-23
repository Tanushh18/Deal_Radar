"""Deal browsing, search, price history, and lazy image proxying."""
from __future__ import annotations

import io
import time
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .. import auth, db

from ..services import ratelimit, search, store, taxonomy, telegram

router = APIRouter(prefix="/api/deals", tags=["deals"])

# Telegram photos are fetched on demand and kept in a small LRU so a busy grid
# doesn't re-download the same thumbnail. Bounded to protect a 512MB dyno.
_image_cache: "OrderedDict[str, bytes]" = OrderedDict()
_IMAGE_CACHE_MAX = 120

# The deal catalog is intentionally browsable without signing in, so this
# can't require auth without breaking anonymous browsing — but each miss
# triggers a real Telegram API call under a tracking user's session, so it
# still needs a cap to stop that being an open door to abuse that session.
_limit_image = ratelimit.limit("deal-image", max_requests=90, window_seconds=60)


def _scope(user: Optional[dict]) -> Optional[list]:
    """Everyone sees the same catalogue: deals from the reader's channels that
    the admin hasn't blocked (so blocking hides a channel's deals at once)."""
    reader = db.get_meta("reader_user_id")
    if not reader:
        return None
    rows = db.query(
        "SELECT c.tg_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
        "WHERE uc.user_id = ? AND uc.enabled = 1", (int(reader),),
    )
    return [int(r["tg_id"]) for r in rows] or None


@router.get("/categories")
async def categories():
    return {"categories": taxonomy.category_list()}


@router.get("/facets")
async def deal_facets(user=Depends(auth.optional_user)):
    return search.facets(_scope(user))


@router.get("/trending")
async def trending_deals(limit: int = Query(12, ge=1, le=50), user=Depends(auth.optional_user)):
    return {"results": search.trending(_scope(user), limit)}


@router.get("/suggest")
async def suggest_deals(
    q: str = Query("", max_length=120),
    limit: int = Query(6, ge=1, le=20),
    all_channels: bool = False,
    user=Depends(auth.optional_user),
):
    scope = None if all_channels else _scope(user)
    return search.suggest(q, channel_ids=scope, limit=limit)


@router.get("")
async def list_deals(
    q: str = Query("", max_length=120),
    category: str = "",
    subcategory: str = "",
    store_name: str = Query("", alias="store"),
    brand: str = "",
    min_price: Optional[float] = Query(None, ge=0),
    max_price: Optional[float] = Query(None, ge=0),
    min_discount: int = Query(0, ge=0, le=99),
    only_lowest: bool = False,
    include_expired: bool = False,
    sort: str = Query("relevance"),
    limit: int = Query(48, ge=1, le=100),
    offset: int = Query(0, ge=0),
    all_channels: bool = False,
    archive: bool = Query(False, description="Past deals from the Google Sheet archive instead of live ones"),
    user=Depends(auth.optional_user),
):
    if sort not in search.SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {list(search.SORTS)}")
    scope = None if (all_channels or archive) else _scope(user)
    return search.search(
        q=q,
        category=category,
        subcategory=subcategory,
        store=store_name,
        brand=brand,
        min_price=min_price,
        max_price=max_price,
        min_discount=min_discount,
        channel_ids=scope,
        include_expired=include_expired,
        only_lowest=only_lowest,
        sort=sort,
        limit=limit,
        offset=offset,
        archive=archive,
    )


@router.get("/{deal_id}")
async def get_deal(deal_id: str):
    row = db.query_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found.")
    deal = db.row_to_dict(row) or {}
    shaped = search.shape(deal)
    shaped["raw_text"] = deal.get("raw_text")
    shaped["price_history"] = store.price_stats(deal.get("product_key") or "")
    return shaped


@router.get("/{deal_id}/history")
async def deal_history(deal_id: str):
    row = db.query_one("SELECT product_key FROM deals WHERE id = ?", (deal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found.")
    points = db.query(
        "SELECT price, seen_at FROM price_history WHERE product_key = ? ORDER BY seen_at ASC LIMIT 120",
        (row["product_key"],),
    )
    return {
        "stats": store.price_stats(row["product_key"]),
        "points": [{"price": p["price"], "at": p["seen_at"]} for p in points],
    }


@router.get("/{deal_id}/image")
async def deal_image(deal_id: str, request: Request):
    """Proxy the original Telegram photo, cached in memory."""
    if deal_id in _image_cache:
        _image_cache.move_to_end(deal_id)
        return Response(content=_image_cache[deal_id], media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

    # Only the cache-miss path spends a real Telegram API call, so only it
    # needs throttling — a popular cached image shouldn't count against it.
    await _limit_image(request)

    row = db.query_one("SELECT channel_id, message_id FROM deals WHERE id = ?", (deal_id,))
    if not row or not row["channel_id"]:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    channel = db.query_one(
        "SELECT source_user_id FROM channels WHERE tg_id = ?", (row["channel_id"],)
    )
    if not channel or not channel["source_user_id"]:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    client = await telegram.get_client(int(channel["source_user_id"]))
    if client is None:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    try:
        entity = await client.get_entity(int(row["channel_id"]))
        message = await client.get_messages(entity, ids=int(row["message_id"]))
        if not message or not getattr(message, "photo", None):
            raise HTTPException(status_code=404, detail="No image for this deal.")
        buffer = io.BytesIO()
        # thumb=-2 is a mid-size thumbnail: sharp enough for a card, small to fetch.
        await client.download_media(message, file=buffer, thumb=-2)
        data = buffer.getvalue()
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=404, detail="Image unavailable.")

    if not data:
        raise HTTPException(status_code=404, detail="Image unavailable.")

    _image_cache[deal_id] = data
    while len(_image_cache) > _IMAGE_CACHE_MAX:
        _image_cache.popitem(last=False)

    return Response(content=data, media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=86400"})
