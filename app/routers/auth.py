"""Telegram sign-in: phone -> code -> optional 2FA password."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .. import auth, db
from ..config import settings
from ..services import sheets, telegram

router = APIRouter(prefix="/api/auth", tags=["auth"])


class PhonePayload(BaseModel):
    phone: str = Field(..., min_length=6, max_length=24)


class CodePayload(BaseModel):
    login_id: str
    code: str = Field(..., min_length=3, max_length=12)


class PasswordPayload(BaseModel):
    login_id: str
    password: str = Field(..., min_length=1, max_length=256)


@router.get("/config")
async def auth_config():
    """Lets the UI explain what's missing before the user tries to sign in."""
    return {
        "telegram_configured": settings.telegram_configured,
        "sheets_configured": settings.sheets_configured,
    }


@router.post("/send-code")
async def send_code(payload: PhonePayload):
    try:
        return await telegram.start_login(payload.phone)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/verify-code")
async def verify_code(payload: CodePayload, response: Response):
    try:
        result = await telegram.verify_code(payload.login_id, payload.code)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    if result.get("status") == "password_required":
        return result
    return _complete(result, response)


@router.post("/verify-password")
async def verify_password(payload: PasswordPayload, response: Response):
    try:
        result = await telegram.verify_password(payload.login_id, payload.password)
    except telegram.TelegramError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _complete(result, response)


def _complete(result: dict, response: Response) -> dict:
    user = result["user"]
    token = auth.create_session(user["id"])
    auth.set_session_cookie(response, token)
    if sheets.is_enabled():
        try:
            sheets.sync_users()
        except Exception:  # noqa: BLE001 - a Sheets hiccup must not block login
            pass
    return {"status": "ok", "user": user}


@router.get("/me")
async def me(request: Request):
    user = auth.resolve_user(request)
    if not user:
        return {"authenticated": False}
    channels = db.query_one(
        "SELECT COUNT(*) AS c FROM user_channels WHERE user_id = ? AND enabled = 1",
        (user["id"],),
    )
    return {
        "authenticated": True,
        "user": {
            "id": user["id"],
            "telegram_id": user["telegram_id"],
            "username": user["username"],
            "first_name": user["first_name"],
        },
        "tracked_channels": channels["c"] if channels else 0,
    }


@router.post("/logout")
async def logout(request: Request, response: Response, user=Depends(auth.optional_user)):
    auth.destroy_session(request)
    auth.clear_session_cookie(response)
    if user:
        await telegram.drop_client(user["id"])
    return {"status": "ok"}


@router.delete("/account")
async def delete_account(request: Request, response: Response, user=Depends(auth.current_user)):
    """Full erasure: Telegram session, web sessions, tracked channels, watchlists."""
    user_id = user["id"]
    await telegram.drop_client(user_id)
    db.execute("DELETE FROM app_sessions WHERE user_id = ?", (user_id,))
    db.execute("DELETE FROM user_channels WHERE user_id = ?", (user_id,))
    db.execute(
        "DELETE FROM notified WHERE watchlist_id IN (SELECT id FROM watchlists WHERE user_id = ?)",
        (user_id,),
    )
    db.execute("DELETE FROM watchlists WHERE user_id = ?", (user_id,))
    db.execute("UPDATE channels SET active = 0 WHERE source_user_id = ?", (user_id,))
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    auth.clear_session_cookie(response)
    return {"status": "deleted"}
