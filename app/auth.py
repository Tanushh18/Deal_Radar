"""Web session handling (separate from the Telegram session)."""
from __future__ import annotations

import secrets
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, Response, status

from . import db
from .config import settings


def create_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    db.execute(
        "INSERT INTO app_sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
        (token, user_id, now, now + settings.session_ttl_days * 86400),
    )
    db.execute("DELETE FROM app_sessions WHERE expires_at < ?", (now,))
    return token


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=settings.session_cookie,
        value=token,
        max_age=settings.session_ttl_days * 86400,
        httponly=True,
        samesite="lax",
        secure=bool(settings.public_url.startswith("https://")),
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(settings.session_cookie, path="/")


def resolve_user(request: Request) -> Optional[Dict[str, Any]]:
    token = request.cookies.get(settings.session_cookie)
    if not token:
        return None
    row = db.query_one(
        "SELECT u.* FROM app_sessions s JOIN users u ON u.id = s.user_id "
        "WHERE s.token = ? AND s.expires_at > ?",
        (token, time.time()),
    )
    if not row:
        return None
    user = db.row_to_dict(row) or {}
    user.pop("session_enc", None)  # never leak the encrypted Telegram session
    return user


def destroy_session(request: Request) -> None:
    token = request.cookies.get(settings.session_cookie)
    if token:
        db.execute("DELETE FROM app_sessions WHERE token = ?", (token,))


async def current_user(request: Request) -> Dict[str, Any]:
    """Dependency for endpoints that require a signed-in Telegram user."""
    user = resolve_user(request)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with Telegram to continue.",
        )
    return user


async def optional_user(request: Request) -> Optional[Dict[str, Any]]:
    return resolve_user(request)


def require_admin(request: Request) -> None:
    """Guards maintenance endpoints. Disabled unless ADMIN_TOKEN is set."""
    if not settings.admin_token:
        raise HTTPException(status_code=403, detail="Admin endpoints are disabled.")
    supplied = request.headers.get("x-admin-token") or request.query_params.get("token")
    if not secrets.compare_digest(supplied or "", settings.admin_token):
        raise HTTPException(status_code=403, detail="Invalid admin token.")


CurrentUser = Depends(current_user)
OptionalUser = Depends(optional_user)
