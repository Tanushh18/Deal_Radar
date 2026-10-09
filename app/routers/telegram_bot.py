"""Webhook for the Telegram link-generator bot (services/tg_linkbot.py)."""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Request

from ..services import tg_linkbot

router = APIRouter(prefix="/api/telegram", tags=["telegram"], include_in_schema=False)
log = logging.getLogger("dealradar.linkbot")
_tasks: set = set()


@router.post("/linkbot")
async def linkbot_webhook(request: Request, x_telegram_bot_api_secret_token: str = Header("")):
    if not tg_linkbot.enabled() or not tg_linkbot.valid_secret(x_telegram_bot_api_secret_token):
        raise HTTPException(status_code=403, detail="Forbidden")
    update = await request.json()
    # Answer Telegram at once; the reply (which may follow redirects) is sent in the background.
    task = asyncio.create_task(_run(update))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"ok": True}


async def _run(update: dict) -> None:
    try:
        await tg_linkbot.handle_update(update)
    except Exception:  # noqa: BLE001
        log.exception("Link bot failed on an update")
