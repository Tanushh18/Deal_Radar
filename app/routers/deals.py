"""Deal browsing, search, price history, and lazy image proxying."""
from __future__ import annotations

import io
import time
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from .. import auth, db
from ..services import ingest, search, store, taxonomy, telegram

router = APIRouter(prefix="/api/deals", tags=["deals"])

# Telegram photos are fetched on demand and kept in a small LRU so a busy grid
# doesn't re-download the same thumbnail. Bounded to protect a 512MB dyno.
_image_cache: "OrderedDict[str, bytes]" = OrderedDict()
_IMAGE_CACHE_MAX = 120


def _scope(user: Optional[dict]) -> Optional[list]:
    """Restrict results to the signed-in user's tracked channels."""
    if not user:
        return None
    ids = ingest.user_channel_ids(user["id"])
    return ids or None


@router.get("/categories")
async def categories():
    return {"categories": taxonomy.category_list()}


@router.get("/facets")
async def deal_facets(user=Depends(auth.optional_user)):
    return search.facets(_scope(user))


@router.get("/trending")
async def trending_deals(limit: int = Query(12, ge=1, le=50), user=Depends(auth.optional_user)):
    return {"results": search.trending(_scope(user), limit)}


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
    user=Depends(auth.optional_user),
):
    if sort not in search.SORTS:
        raise HTTPException(status_code=400, detail=f"sort must be one of {list(search.SORTS)}")
    scope = None if all_channels else _scope(user)
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
async def deal_image(deal_id: str):
    """Proxy the original Telegram photo, cached in memory."""
    if deal_id in _image_cache:
        _image_cache.move_to_end(deal_id)
        return Response(content=_image_cache[deal_id], media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

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
