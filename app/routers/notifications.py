"""Push-token registration and the in-app notification feed."""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import auth, db
from ..services import push

router = APIRouter(prefix="/api", tags=["notifications"])


class RegisterPayload(BaseModel):
    token: str = Field(..., max_length=4096)
    platform: str = Field("android", max_length=20)


class UnregisterPayload(BaseModel):
    token: str = Field(..., max_length=4096)


@router.post("/push/register")
async def register(payload: RegisterPayload, user=Depends(auth.current_user)):
    token = payload.token.strip()
    if not push.is_push_token(token):
        raise HTTPException(status_code=400, detail="Not a valid push token.")
    push.register_token(user["id"], token, (payload.platform or "android").strip().lower())
    return {"status": "ok"}


@router.post("/push/unregister")
async def unregister(payload: UnregisterPayload, user=Depends(auth.current_user)):
    push.unregister_token(user["id"], payload.token.strip())
    return {"status": "ok"}


@router.get("/notifications")
async def feed(
    since: float = Query(0.0, ge=0),
    limit: int = Query(20, ge=1, le=50),
    user=Depends(auth.current_user),
):
    now = time.time()
    return {"notifications": push.list_notifications(user["id"], since, limit), "now": now}


@router.post("/notifications/test")
async def test_notification(user=Depends(auth.current_user)):
    channel_ids = [
        int(r["tg_id"])
        for r in db.query(
            "SELECT c.tg_id FROM user_channels uc JOIN channels c ON c.id = uc.channel_id "
            "WHERE uc.user_id = ? AND uc.enabled = 1",
            (user["id"],),
        )
    ]
    deal = None
    if channel_ids:
        marks = ",".join("?" for _ in channel_ids)
        deal = db.query_one(
            f"SELECT id FROM deals WHERE status = 'live' AND channel_id IN ({marks}) "
            "ORDER BY first_seen_at DESC LIMIT 1",
            channel_ids,
        )
    if deal is None:
        deal = db.query_one(
            "SELECT id FROM deals WHERE status = 'live' ORDER BY first_seen_at DESC LIMIT 1"
        )
    deal_id = deal["id"] if deal else None
    result = await push.notify(
        user["id"],
        "DealRadar notifications are on 🎉",
        "When a new deal matches one of your saved alerts, it'll show up here.",
        f"/?deal={deal_id}" if deal_id else "/",
        deal_id,
    )
    return {"status": "ok", **result}
