"""Push notifications straight to Firebase Cloud Messaging (HTTP v1), no Expo.

The app registers its raw FCM device token; we send each message to FCM
ourselves using the Firebase service-account key in FCM_SERVICE_ACCOUNT_JSON
(Render env var — never in the repo).

Messages are data-only and shaped exactly like the ones Expo's push service
used to send (title / message / body / channelId), so the app's notification
handling — channels, tap-to-open-deal, the rich in-app display — is unchanged.

The OAuth token is minted from the key with a self-signed JWT (RS256 via the
`cryptography` package we already depend on) and cached until shortly before
it expires.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..config import settings

log = logging.getLogger("dealradar.fcm")

SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
SEND_URL = "https://fcm.googleapis.com/v1/projects/{project}/messages:send"
CONCURRENCY = 10
TIMEOUT = 10.0
TTL_SECONDS = 24 * 3600  # an undelivered deal alert is stale after a day

# FCM registration tokens: long, URL-safe base64 plus ':' — never an Expo token.
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-:]{100,4096}$")

_access: Tuple[str, float] = ("", 0.0)
_lock = asyncio.Lock()


def is_fcm_token(token: str) -> bool:
    return bool(token) and bool(_TOKEN_RE.match(token))


def _account() -> Optional[Dict[str, Any]]:
    raw = settings.fcm_service_account_json
    if not raw:
        return None
    try:
        data = json.loads(raw if raw.lstrip().startswith("{") else base64.b64decode(raw).decode())
    except (ValueError, TypeError):
        log.warning("FCM_SERVICE_ACCOUNT_JSON isn't valid JSON (or base64 JSON)")
        return None
    if not all(data.get(k) for k in ("client_email", "private_key", "project_id")):
        log.warning("FCM_SERVICE_ACCOUNT_JSON is missing client_email / private_key / project_id")
        return None
    return data


def configured() -> bool:
    return _account() is not None


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _signed_jwt(account: Dict[str, Any]) -> str:
    now = int(time.time())
    header = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
    claims = _b64(json.dumps({
        "iss": account["client_email"], "scope": SCOPE,
        "aud": account.get("token_uri") or "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }).encode())
    key = serialization.load_pem_private_key(account["private_key"].encode(), password=None)
    signature = key.sign(f"{header}.{claims}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{claims}.{_b64(signature)}"


async def _access_token(account: Dict[str, Any]) -> str:
    global _access
    async with _lock:
        token, expires = _access
        if token and time.time() < expires - 120:
            return token
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(
                account.get("token_uri") or "https://oauth2.googleapis.com/token",
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                      "assertion": _signed_jwt(account)},
            )
        resp.raise_for_status()
        body = resp.json()
        _access = (body["access_token"], time.time() + int(body.get("expires_in", 3600)))
        return _access[0]


def build_message(token: str, title: str, body: str, data: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    """Data-only, in the exact shape expo-notifications reads (see its NotificationData)."""
    return {
        "message": {
            "token": token,
            "data": {
                "title": title,
                "message": body,
                "body": json.dumps(data),
                "channelId": channel_id,
                "sound": "default",
            },
            "android": {"priority": "HIGH", "ttl": f"{TTL_SECONDS}s"},
        }
    }


async def _post(client: httpx.AsyncClient, url: str, access: str, message: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
    """One send; returns (HTTP status, JSON body). Tests monkeypatch this."""
    resp = await client.post(url, json=message, headers={"Authorization": f"Bearer {access}"})
    try:
        return resp.status_code, resp.json()
    except ValueError:
        return resp.status_code, {}


def _error_code(status: int, body: Dict[str, Any]) -> str:
    err = body.get("error") or {}
    for detail in err.get("details") or []:
        if detail.get("errorCode"):
            return str(detail["errorCode"])
    return str(err.get("status") or f"HTTP{status}")


async def send(tokens: List[str], title: str, body: str, data: Dict[str, Any], channel_id: str) -> Dict[str, Any]:
    """Send to every FCM token. Never raises.

    Returns {"tokens", "accepted", "failed", "errors": {code: n}, "dead": [token, ...]}
    — dead tokens (app uninstalled / token rotated) should be forgotten.
    """
    report: Dict[str, Any] = {"tokens": len(tokens), "accepted": 0, "failed": 0, "errors": {}, "dead": []}
    if not tokens:
        return report

    def _error(code: str) -> None:
        report["failed"] += 1
        report["errors"][code] = report["errors"].get(code, 0) + 1

    account = _account()
    if account is None:
        for _ in tokens:
            _error("FcmNotConfigured")
        return report
    try:
        access = await _access_token(account)
    except Exception as exc:  # noqa: BLE001
        log.warning("FCM auth failed: %s", exc)
        for _ in tokens:
            _error("FcmAuthFailed")
        return report

    url = SEND_URL.format(project=account["project_id"])
    semaphore = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        async def one(token: str) -> None:
            async with semaphore:
                try:
                    status, resp = await _post(client, url, access, build_message(token, title, body, data, channel_id))
                except Exception as exc:  # noqa: BLE001
                    log.info("FCM send failed: %s", exc)
                    _error("RequestFailed")
                    return
            if status == 200:
                report["accepted"] += 1
                return
            code = _error_code(status, resp)
            _error(code)
            # Only "this app instance is gone" drops a token; INVALID_ARGUMENT can
            # mean a bad message, and dropping good tokens over that would be silent loss.
            if code == "UNREGISTERED" or status == 404:
                report["dead"].append(token)

        await asyncio.gather(*(one(t) for t in tokens))
    return report
