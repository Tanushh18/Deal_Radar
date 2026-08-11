"""Web session handling (separate from the Telegram session).

Sessions are a self-verifying signed cookie — telegram_id + expiry, HMAC'd
with SECRET_KEY — rather than a token row in SQLite. Render's free tier disk
is ephemeral and gets wiped on every redeploy/restart, so a DB-backed session
used to log everyone out on every deploy even though their cookie was still
"valid" for its full TTL. A signed cookie needs no DB lookup to trust, so it
survives restarts as long as SECRET_KEY is stable (Render generates it once
at service creation and keeps it — see render.yaml). The tradeoff: unlike a
DB-backed token, an issued cookie can't be revoked before it expires, so
`destroy_session` is just a client-side cookie clear.
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request, Response, status

from . import db
from .config import settings


def _sign(telegram_id: int, expires_at: int) -> str:
    msg = f"{telegram_id}.{expires_at}".encode()
    return hmac.new(settings.secret_key.encode(), msg, hashlib.sha256).hexdigest()


def create_session(telegram_id: int) -> str:
    expires_at = int(time.time()) + settings.session_ttl_days * 86400
    return f"{telegram_id}.{expires_at}.{_sign(telegram_id, expires_at)}"


def _verify_token(token: str) -> Optional[int]:
    """Return the telegram_id embedded in a valid, unexpired token."""
    parts = token.split(".")
    if len(parts) != 3:
        return None
    tid_str, exp_str, sig = parts
    try:
        telegram_id = int(tid_str)
        expires_at = int(exp_str)
    except ValueError:
        return None
    if not hmac.compare_digest(_sign(telegram_id, expires_at), sig):
        return None
    if expires_at < time.time():
        return None
    return telegram_id


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
    telegram_id = _verify_token(token)
    if telegram_id is None:
        return None
    row = db.query_one("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
    if not row:
        return None
    user = db.row_to_dict(row) or {}
    user.pop("session_enc", None)  # never leak the encrypted Telegram session
    return user


def destroy_session(request: Request) -> None:
    """No-op: stateless tokens can't be revoked server-side before they expire.

    The actual logout is clear_session_cookie removing the browser's copy.
    """


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
