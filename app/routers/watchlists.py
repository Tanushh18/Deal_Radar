"""Saved searches that push alerts to the user's Telegram Saved Messages."""
from __future__ import annotations

import json
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth, db
from ..services import ingest, sheets, telegram

router = APIRouter(prefix="/api/watchlists", tags=["watchlists"])

MAX_PER_USER = 20


def _sync_meta_safely() -> None:
    if not sheets.is_enabled():
        return
    try:
        sheets.sync_watchlists()
    except Exception:  # noqa: BLE001
        pass


class WatchlistPayload(BaseModel):
    query: str = Field(..., min_length=2, max_length=100)
    category: str = ""
    store: str = ""
    max_price: Optional[float] = None
    min_discount: int = 0
    notify: bool = True


@router.get("")
async def list_watchlists(user=Depends(auth.current_user)):
    rows = db.rows_to_dicts(
        db.query("SELECT * FROM watchlists WHERE user_id = ? ORDER BY created_at DESC", (user["id"],))
    )
    for row in rows:
        if isinstance(row.get("filters"), str):
            try:
                row["filters"] = json.loads(row["filters"])
            except json.JSONDecodeError:
                row["filters"] = {}
        hits = db.query_one(
            "SELECT COUNT(*) AS c FROM notified WHERE watchlist_id = ?", (row["id"],)
        )
        row["alerts_sent"] = hits["c"] if hits else 0
        row["notify"] = bool(row["notify"])
    return {"watchlists": rows}


@router.post("")
async def create_watchlist(payload: WatchlistPayload, user=Depends(auth.current_user)):
    count = db.query_one("SELECT COUNT(*) AS c FROM watchlists WHERE user_id = ?", (user["id"],))
    if count and count["c"] >= MAX_PER_USER:
        raise HTTPException(status_code=400, detail=f"You can save up to {MAX_PER_USER} alerts.")

    filters = {
        "category": payload.category,
        "store": payload.store,
        "max_price": payload.max_price,
        "min_discount": payload.min_discount,
    }
    cur = db.execute(
        "INSERT INTO watchlists (user_id, query, filters, notify, created_at, last_notified_at) "
        "VALUES (?, ?, ?, ?, ?, 0)",
        (user["id"], payload.query.strip(), json.dumps(filters), 1 if payload.notify else 0, time.time()),
    )
    _sync_meta_safely()
    return {"status": "ok", "id": cur.lastrowid}


@router.patch("/{watchlist_id}")
async def toggle_watchlist(watchlist_id: int, notify: bool, user=Depends(auth.current_user)):
    row = db.query_one(
        "SELECT id FROM watchlists WHERE id = ? AND user_id = ?", (watchlist_id, user["id"])
    )
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    db.execute("UPDATE watchlists SET notify = ? WHERE id = ?", (1 if notify else 0, watchlist_id))
    _sync_meta_safely()
    return {"status": "ok"}


@router.delete("/{watchlist_id}")
async def delete_watchlist(watchlist_id: int, user=Depends(auth.current_user)):
    row = db.query_one(
        "SELECT id FROM watchlists WHERE id = ? AND user_id = ?", (watchlist_id, user["id"])
    )
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    db.execute("DELETE FROM notified WHERE watchlist_id = ?", (watchlist_id,))
    db.execute("DELETE FROM watchlists WHERE id = ?", (watchlist_id,))
    _sync_meta_safely()
    return {"status": "ok"}


@router.post("/{watchlist_id}/test")
async def test_watchlist(watchlist_id: int, user=Depends(auth.current_user)):
    """Send a sample alert so the user can confirm delivery works."""
    row = db.query_one(
        "SELECT * FROM watchlists WHERE id = ? AND user_id = ?", (watchlist_id, user["id"])
    )
    if not row:
        raise HTTPException(status_code=404, detail="Alert not found.")
    ok = await telegram.notify_user(
        user["id"],
        f"✅ DealRadar test alert\nYour saved search “{row['query']}” is active. "
        f"New matching deals will arrive here.",
    )
    if not ok:
        raise HTTPException(status_code=400, detail="Couldn't reach Telegram. Try signing in again.")
    return {"status": "sent"}


@router.post("/run")
async def run_alerts_now(user=Depends(auth.current_user)):
    return await ingest.run_watchlist_alerts()
