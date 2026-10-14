"""Admin-managed links and the account-screen promotion card."""
from __future__ import annotations

import json
from typing import Any, Dict
from urllib.parse import urlparse

from .. import db
from ..config import settings

META_KEY = "account_app_links"


def defaults() -> Dict[str, Any]:
    return {
        "website_url": settings.public_url or "https://dealradar.tanush.qd.je",
        "app_url": settings.play_store_url or (
            f"https://play.google.com/store/apps/details?id={settings.play_store_package}"
        ),
        "show_account_promo": True,
        "promo_title": "DealRadar, wherever you shop",
        "promo_body": "Open our website or download the app for deal alerts and price tracking.",
    }


def get() -> Dict[str, Any]:
    value = db.get_meta(META_KEY)
    saved: Dict[str, Any] = {}
    if value:
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                saved = parsed
        except (TypeError, ValueError):
            pass
    return {**defaults(), **saved}


def _valid_url(value: Any, field: str) -> str:
    url = str(value or "").strip()
    if not url:
        return ""
    parsed = urlparse(url)
    if parsed.scheme not in ("https", "http") or not parsed.netloc:
        raise ValueError(f"{field} must be a full http:// or https:// URL, or left blank.")
    return url


def update(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = get()
    result = {
        "website_url": _valid_url(payload.get("website_url", current["website_url"]), "Website URL"),
        "app_url": _valid_url(payload.get("app_url", current["app_url"]), "App download URL"),
        "show_account_promo": bool(payload.get("show_account_promo", current["show_account_promo"])),
        "promo_title": str(payload.get("promo_title", current["promo_title"]) or "").strip()[:100],
        "promo_body": str(payload.get("promo_body", current["promo_body"]) or "").strip()[:300],
    }
    db.set_meta(META_KEY, json.dumps(result, ensure_ascii=False))
    return result
