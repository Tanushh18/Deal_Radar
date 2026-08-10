"""Central configuration, loaded from environment variables."""
from __future__ import annotations

import base64
import hashlib
import os
from functools import lru_cache
from typing import List, Optional

from dotenv import load_dotenv

load_dotenv()


def _split(value: str) -> List[str]:
    return [item.strip() for item in value.replace("\n", ",").split(",") if item.strip()]


class Settings:
    """Runtime settings. Everything has a sane default so the app boots bare."""

    def __init__(self) -> None:
        # --- Telegram (https://my.telegram.org -> API development tools) ---
        self.telegram_api_id: int = int(os.getenv("TELEGRAM_API_ID", "0") or 0)
        self.telegram_api_hash: str = os.getenv("TELEGRAM_API_HASH", "")

        # --- Security ---
        # SECRET_KEY signs session cookies and derives the key that encrypts
        # each user's Telethon session string at rest.
        self.secret_key: str = os.getenv("SECRET_KEY", "dev-insecure-change-me")
        self.session_cookie: str = os.getenv("SESSION_COOKIE", "tgdeals_session")
        self.session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "30"))

        # --- Google Sheets ---
        # Either paste the service-account JSON into GOOGLE_SERVICE_ACCOUNT_JSON
        # or base64 it into GOOGLE_SERVICE_ACCOUNT_B64 (easier for Render).
        self.google_sa_json: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "")
        self.google_sa_b64: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_B64", "")
        self.sheet_id: str = os.getenv("GOOGLE_SHEET_ID", "")
        self.sheet_name: str = os.getenv("GOOGLE_SHEET_NAME", "Telegram Deals")
        self.sheets_flush_seconds: int = int(os.getenv("SHEETS_FLUSH_SECONDS", "120"))

        # --- Storage ---
        self.db_path: str = os.getenv("DB_PATH", "data/deals.db")

        # --- Ingestion ---
        self.poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "300"))
        self.deal_ttl_hours: int = int(os.getenv("DEAL_TTL_HOURS", "96"))  # 4 days
        self.backfill_limit: int = int(os.getenv("BACKFILL_LIMIT", "120"))
        self.incremental_limit: int = int(os.getenv("INCREMENTAL_LIMIT", "60"))
        self.max_channels_per_user: int = int(os.getenv("MAX_CHANNELS_PER_USER", "40"))
        self.liveness_check_enabled: bool = _bool(os.getenv("LIVENESS_CHECK", "true"))
        self.liveness_batch: int = int(os.getenv("LIVENESS_BATCH", "40"))

        # --- Keepalive (Render free tier sleeps after ~15 min idle) ---
        self.public_url: str = os.getenv("PUBLIC_URL", "").rstrip("/")
        self.keepalive_enabled: bool = _bool(os.getenv("KEEPALIVE_ENABLED", "true"))
        self.keepalive_seconds: int = int(os.getenv("KEEPALIVE_SECONDS", "600"))

        # --- Misc ---
        self.allowed_origins: List[str] = _split(os.getenv("ALLOWED_ORIGINS", "*"))
        self.admin_token: str = os.getenv("ADMIN_TOKEN", "")
        self.log_level: str = os.getenv("LOG_LEVEL", "INFO").upper()

    # -- derived -------------------------------------------------------
    @property
    def fernet_key(self) -> bytes:
        """32-byte urlsafe key derived from SECRET_KEY for session encryption."""
        digest = hashlib.sha256(self.secret_key.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_api_id and self.telegram_api_hash)

    @property
    def sheets_configured(self) -> bool:
        return bool(self.sheet_id and (self.google_sa_json or self.google_sa_b64))

    def service_account_info(self) -> Optional[dict]:
        import json

        raw = self.google_sa_json
        if not raw and self.google_sa_b64:
            try:
                raw = base64.b64decode(self.google_sa_b64).decode("utf-8")
            except Exception:
                return None
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
