"""Visitor price-drop alerts (no account — keyed by a random device id)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import db
from ..services import price_alerts, ratelimit

router = APIRouter(prefix="/api/price-alerts", tags=["price-alerts"])
_limit_create = ratelimit.limit("price-alert-create", max_requests=30, window_seconds=600)


class AlertPayload(BaseModel):
    device_id: str
    deal_id: str
    target_price: float = Field(gt=0, lt=10_000_000)
    push_token: Optional[str] = ""


def _device(device_id: str) -> str:
    if not price_alerts.valid_device(device_id):
        raise HTTPException(status_code=400, detail="Invalid device id.")
    return device_id


@router.post("", dependencies=[Depends(_limit_create)])
async def create_alert(payload: AlertPayload):
    device = _device(payload.device_id)
    row = db.query_one("SELECT * FROM deals WHERE id = ?", (payload.deal_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Deal not found.")
    try:
        alert = price_alerts.create(device, dict(row), payload.target_price, payload.push_token or "")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"status": "ok", "alert": alert}


@router.get("")
async def list_alerts(device_id: str = Query(...)):
    return {"alerts": price_alerts.list_for(_device(device_id))}


@router.delete("/{alert_id}")
async def delete_alert(alert_id: int, device_id: str = Query(...)):
    if not price_alerts.delete(_device(device_id), alert_id):
        raise HTTPException(status_code=404, detail="Alert not found.")
    return {"status": "ok"}
