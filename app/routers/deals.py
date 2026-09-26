"""Deal browsing, search, price history, and lazy image proxying."""
from __future__ import annotations

import io
import time
from collections import OrderedDict
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from .. import auth, db

from ..services import buyhatke, price_store, ratelimit, search, store, taxonomy, telegram

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

# Every one of these hits the DB (or, in sheet-serving mode, the Sheets API)
# directly. A caller hammering these repeatedly is exactly what turned a
# traffic spike into DB/CPU pressure before — cap each per IP so one bad
# actor or a runaway retry loop can't do that again.
_limit_search = ratelimit.limit("deal-search", max_requests=90, window_seconds=60)
_limit_sparklines = ratelimit.limit("deal-sparklines", max_requests=60, window_seconds=60)


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
    _rl=Depends(_limit_search),
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
    archive: bool = Query(False, description="Past (non-live) deals instead of live ones"),
    has_coupon: bool = False,
    size: str = Query("", max_length=20),
    device_id: str = Query("", max_length=120, description="Required for sort=for_you"),
    user=Depends(auth.optional_user),
    _rl=Depends(_limit_search),
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
        has_coupon=has_coupon,
        size=size,
        device_id=device_id,
    )


SPARKLINE_POINTS = 30  # recent points fetched per card, downsampled to 8 below


@router.get("/sparklines")
async def sparklines(ids: str = Query(..., max_length=2000), _rl=Depends(_limit_sparklines)):
    """Batch price trend for a grid of cards: last 8 points per deal, one query.

    Avoids N+1 calls to /history when rendering a page of cards — the
    frontend collects the visible ids and calls this once per page/scroll.
    Declared before /{deal_id} so FastAPI doesn't swallow it as a deal id.
    """
    deal_ids = [d.strip() for d in ids.split(",") if d.strip()][:100]
    if not deal_ids:
        return {"sparklines": {}}
    rows = db.query(
        f"SELECT id, product_key FROM deals WHERE id IN ({','.join('?' for _ in deal_ids)})",
        deal_ids,
    )
    keys = [r["product_key"] for r in rows if r["product_key"]]
    if not keys:
        return {"sparklines": {}}
    history = await price_store.history_many(keys, limit=SPARKLINE_POINTS)
    by_key = {k: [p for _, p in pts] for k, pts in history.items()}
    out = {}
    for r in rows:
        pk = r["product_key"]
        series = by_key.get(pk) or []
        if len(series) > 8:
            step = len(series) / 8
            series = [series[int(i * step)] for i in range(8)]
        out[r["id"]] = series
    return {"sparklines": out}


def price_verdict(price, stats):
    """Great / Good / Fair / High versus everything we've seen for this product."""
    if price is None or not stats or stats.get("points", 0) < 2:
        return None
    price, low, median = float(price), float(stats["min"]), float(stats["median"])
    if price <= low:
        return {"level": "great", "label": "Great price — lowest we've seen"}
    if price <= median * 0.95:
        return {"level": "good", "label": "Good price — below its usual"}
    if price <= median * 1.05:
        return {"level": "fair", "label": "Fair price — about usual"}
    return {"level": "high", "label": "High — it's often cheaper"}


@router.get("/{deal_id}/similar")
async def similar_deals(deal_id: str, limit: int = Query(8, ge=1, le=24)):
    row = db.query_one("SELECT id, subcategory, category, brand, product_key FROM deals WHERE id = ?", (deal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found.")
    rows = db.query(
        "SELECT * FROM deals WHERE status = 'live' AND expires_at > ? AND id != ? AND product_key != ? "
        "AND COALESCE(image_url, '') != '' "
        "AND (subcategory = ? OR (brand != '' AND brand = ?)) "
        "ORDER BY (subcategory = ?) DESC, score DESC LIMIT ?",
        (time.time(), deal_id, row["product_key"] or "", row["subcategory"] or "", row["brand"] or "",
         row["subcategory"] or "", limit),
    )
    return {"results": [search.shape(d) for d in db.rows_to_dicts(rows)]}


@router.get("/{deal_id}")
async def get_deal(deal_id: str):
    row = db.query_one("SELECT * FROM deals WHERE id = ?", (deal_id,))
    # Older than the local cache (e.g. opened from an old notification): Turso has it.
    deal = db.row_to_dict(row) if row else await price_store.remote_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found.")
    shaped = search.shape(deal)
    shaped["raw_text"] = deal.get("raw_text")
    # Cached BuyHatke points only — opening a deal never waits on BuyHatke here.
    points = _merge(await price_store.history(deal.get("product_key") or ""), buyhatke.cached(deal))
    shaped["price_history"] = price_store.stats(points)
    shaped["price_verdict"] = price_verdict(deal.get("price"), shaped["price_history"])
    return shaped


COUPON_DEAD_THRESHOLD = 3  # distinct devices reporting before we suppress it


@router.post("/{deal_id}/coupon-dead")
async def report_coupon_dead(deal_id: str, device_id: str = Query(..., max_length=120)):
    """Lightweight crowd feedback: enough reports and we drop the coupon.

    Cheaper than checking codes ourselves, and self-correcting — a code that
    still works simply never accumulates reports.
    """
    row = db.query_one("SELECT coupon FROM deals WHERE id = ?", (deal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found.")
    if not row["coupon"]:
        return {"reports": 0, "suppressed": False}

    db.execute(
        "INSERT INTO coupon_reports (deal_id, device_id, reported_at) VALUES (?, ?, ?) "
        "ON CONFLICT(deal_id, device_id) DO UPDATE SET reported_at = excluded.reported_at",
        (deal_id, device_id, time.time()),
    )
    count = db.query_one(
        "SELECT COUNT(*) AS c FROM coupon_reports WHERE deal_id = ?", (deal_id,)
    )["c"]
    suppressed = count >= COUPON_DEAD_THRESHOLD
    if suppressed:
        db.execute("UPDATE deals SET coupon = '', dirty = 1 WHERE id = ?", (deal_id,))
    return {"reports": count, "suppressed": suppressed}


def _merge(own: list, theirs: list) -> list:
    """Our points plus BuyHatke's, oldest first; ours win on an exact tie."""
    merged = {int(t): (t, p) for t, p in theirs}
    merged.update({int(t): (t, p) for t, p in own})
    return [merged[k] for k in sorted(merged)]


@router.get("/{deal_id}/history")
async def deal_history(deal_id: str):
    row = db.query_one("SELECT product_key, url, clean_url, resolved_url FROM deals WHERE id = ?", (deal_id,))
    deal = db.row_to_dict(row) if row else await price_store.remote_deal(deal_id)
    if not deal:
        raise HTTPException(status_code=404, detail="Deal not found.")
    # Served from Turso, the full price record (newest HISTORY_LIMIT points),
    # plus BuyHatke's longer history when it has this product.
    own = await price_store.history(deal.get("product_key") or "")
    theirs = await buyhatke.history(deal)
    points = _merge(own, theirs)
    return {
        "stats": price_store.stats(points),
        "points": price_store.as_json(points),
        "source": "buyhatke" if theirs else "own",
        "buyhatke": {"points": len(theirs), "since": theirs[0][0]} if theirs else None,
    }


@router.get("/{deal_id}/image")
async def deal_image(deal_id: str, request: Request):
    """Proxy the original Telegram photo, cached in memory.

    The source message comes from the card's stored image link (never from the
    request), so a photo adopted from another channel's repost resolves too.
    """
    if deal_id in _image_cache:
        _image_cache.move_to_end(deal_id)
        return Response(content=_image_cache[deal_id], media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400"})

    # Only the cache-miss path spends a real Telegram API call, so only it
    # needs throttling — a popular cached image shouldn't count against it.
    await _limit_image(request)

    row = db.query_one("SELECT channel_id, message_id, image_url FROM deals WHERE id = ?", (deal_id,))
    if not row or not row["image_url"]:
        raise HTTPException(status_code=404, detail="No image for this deal.")
    source = store.image_source(row["image_url"]) or (row["channel_id"], row["message_id"])
    channel_id, message_id = int(source[0] or 0), int(source[1] or 0)
    if not channel_id:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    channel = db.query_one(
        "SELECT source_user_id FROM channels WHERE tg_id = ?", (channel_id,)
    )
    if not channel or not channel["source_user_id"]:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    client = await telegram.get_client(int(channel["source_user_id"]))
    if client is None:
        raise HTTPException(status_code=404, detail="No image for this deal.")

    try:
        entity = await client.get_entity(channel_id)
        message = await client.get_messages(entity, ids=message_id)
        if not message or not getattr(message, "photo", None):
            # The channel deleted the post (or its photo): the card has no
            # picture any more and the deal is most likely over — retire it.
            store.drop_image(deal_id)
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
