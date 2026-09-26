"""Visitor device endpoints (no account): registration, follows, digest, feed."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from ..services import devices, price_alerts, push, ratelimit

router = APIRouter(prefix="/api/devices", tags=["devices"])
_limit_writes = ratelimit.limit("device-writes", max_requests=60, window_seconds=600)


class RegisterPayload(BaseModel):
    device_id: str
    platform: str = ""
    push_token: Optional[str] = None
    digest: Optional[bool] = None
    digest_hour: Optional[int] = Field(None, ge=0, le=23)
    smart_schedule: Optional[bool] = None


class FollowPayload(BaseModel):
    device_id: str
    kind: str
    value: str = Field(min_length=1, max_length=80)
    # Optional[int] rather than int: "no minimum" is a meaningful client
    # choice (send null/omit it), and it should mean the same as 0, not 422.
    min_discount: Optional[int] = Field(0, ge=0, le=95)

    @field_validator("min_discount", mode="before")
    @classmethod
    def _none_means_zero(cls, v):
        return 0 if v is None else v


def _device(device_id: str) -> str:
    if not price_alerts.valid_device(device_id):
        raise HTTPException(status_code=400, detail="Invalid device id.")
    return device_id


@router.post("/register", dependencies=[Depends(_limit_writes)])
async def register(payload: RegisterPayload):
    token = payload.push_token
    if token and not push.is_push_token(token):
        raise HTTPException(status_code=400, detail="Not a valid push token.")
    return {"device": devices.register(_device(payload.device_id), payload.platform, token,
                                       payload.digest, payload.digest_hour, payload.smart_schedule)}


@router.get("/settings")
async def settings(device_id: str = Query(...)):
    device = _device(device_id)
    return {"device": devices.get(device), "follows": devices.follows(device)}


@router.post("/follows", dependencies=[Depends(_limit_writes)])
async def add_follow(payload: FollowPayload):
    try:
        return {"follow": devices.add_follow(_device(payload.device_id), payload.kind, payload.value, payload.min_discount)}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/follows/{follow_id}")
async def remove_follow(follow_id: int, device_id: str = Query(...)):
    if not devices.remove_follow(_device(device_id), follow_id):
        raise HTTPException(status_code=404, detail="Follow not found.")
    return {"status": "ok"}


@router.get("/feed")
async def feed(device_id: str = Query(...), since: float = 0.0, limit: int = Query(20, ge=1, le=50)):
    import time
    return {"items": devices.feed(_device(device_id), since, limit), "now": time.time()}
