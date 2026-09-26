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

        # --- Public mode: one server-side Telegram account reads a fixed list
        # of public channels, and everyone browses without signing in. ---
        self.telegram_session: str = os.getenv("TELEGRAM_SESSION", "").strip()
        self.public_channels: List[str] = [
            c.split("t.me/")[-1].lstrip("@").strip("/ ")
            for c in _split(os.getenv("PUBLIC_CHANNELS", ""))
        ]

        # --- Ranking: this audience's deals lead browsing (empty = neutral) ---
        self.priority_audience: str = os.getenv("PRIORITY_AUDIENCE", "women").strip().lower()

        # --- Security ---
        # SECRET_KEY signs session cookies and derives the key that encrypts
        # each user's Telethon session string at rest.
        self.secret_key: str = os.getenv("SECRET_KEY", "dev-insecure-change-me")
        self.session_cookie: str = os.getenv("SESSION_COOKIE", "tgdeals_session")
        self.session_ttl_days: int = int(os.getenv("SESSION_TTL_DAYS", "180"))

        # --- MongoDB ---
        # Durable store for users, channels, channel-tracking links, watchlists
        # and admin settings — everything that used to live in Google Sheets.
        # Deals, price history and price alerts stay in Turso; MongoDB never
        # touches those. A mongodb+srv:// Atlas URI works as-is.
        self.mongo_uri: str = os.getenv("MONGODB_URI", "").strip()
        self.mongo_db_name: str = os.getenv("MONGODB_DB_NAME", "dealradar")

        # --- Storage ---
        self.db_path: str = os.getenv("DB_PATH", "data/deals.db")
        # Turso (libSQL): if set, deals.db becomes a local embedded replica
        # that syncs with this remote — durable across Render deploys/restarts
        # without needing a paid disk. Falls back to a plain local SQLite
        # file (ephemeral on free hosting) when unset.
        self.turso_url: str = os.getenv("TURSO_DATABASE_URL", "").strip()
        self.turso_auth_token: str = os.getenv("TURSO_AUTH_TOKEN", "").strip()
        self.turso_sync_seconds: int = int(os.getenv("TURSO_SYNC_SECONDS", "60"))
        # Second Turso database, just for price_points (the heaviest table —
        # every price change, forever). Optional: without it, price_points
        # stays in the main Turso database as before.
        self.turso2_url: str = os.getenv("TURSO_DB_02", "").strip()
        self.turso2_auth_token: str = os.getenv("TURSO_DB_02_AUTH_TOKEN", "").strip()

        # --- Ingestion ---
        self.poll_interval_seconds: int = int(os.getenv("POLL_INTERVAL_SECONDS", "2400"))  # 40 min
        self.deal_ttl_hours: int = int(os.getenv("DEAL_TTL_HOURS", "96"))  # 4 days
        # Local SQLite is a short-term cache; older data lives in Turso/Sheets.
        self.local_cache_days: float = float(os.getenv("LOCAL_CACHE_DAYS", "15"))
        self.backfill_limit: int = int(os.getenv("BACKFILL_LIMIT", "120"))
        self.incremental_limit: int = int(os.getenv("INCREMENTAL_LIMIT", "60"))
        self.max_channels_per_user: int = int(os.getenv("MAX_CHANNELS_PER_USER", "40"))
        self.liveness_check_enabled: bool = _bool(os.getenv("LIVENESS_CHECK", "true"))
        self.liveness_batch: int = int(os.getenv("LIVENESS_BATCH", "40"))
        # Stores whose product pages the server never opens (stock/price checks).
        # Amazon by default: its Associates agreement forbids scraping. Short-link
        # redirects (amzn.to) are still followed to learn the product id.
        self.scrape_skip_stores: set = {
            s.strip().lower() for s in os.getenv("SCRAPE_SKIP_STORES", "amazon").split(",") if s.strip()
        }
        # Noise gate (services/quality.py): only single-product posts with a
        # price, a store link and a photo become cards. "false" shows everything.
        self.quality_filter: bool = _bool(os.getenv("QUALITY_FILTER", "true"))

        # --- Hot-deal pushes: the best live deals (women's items first), pushed to
        # every registered device regardless of digest/follow settings, at random
        # moments inside each cycle (see hot_push.py). BROADCAST_MIN_SCORE is the
        # floor a deal's 0-100 score must clear to be eligible at all. ---
        self.broadcast_hot_deal_enabled: bool = _bool(os.getenv("BROADCAST_HOT_DEAL", "true"))
        self.broadcast_min_score: float = float(os.getenv("BROADCAST_MIN_SCORE", "30"))
        # How many "hot deal" pushes each ingest cycle schedules, fired at random
        # moments inside the cycle (never on a fixed clock), and the IST hours
        # when none go out ("23-8" = quiet from 11pm to 8am; "" = never quiet).
        self.pushes_per_cycle: int = int(os.getenv("PUSHES_PER_CYCLE", "2"))
        # Floor between two hot-deal pushes, whatever the cycle length — a short
        # POLL_INTERVAL_SECONDS must never turn into a push every few minutes.
        self.hot_push_min_gap_minutes: int = int(os.getenv("HOT_PUSH_MIN_GAP_MINUTES", "60"))
        self.push_quiet_hours: str = os.getenv("PUSH_QUIET_HOURS", "23-8").strip()

        # --- Keepalive (Render free tier sleeps after ~15 min idle) ---
        # Render sets RENDER_EXTERNAL_URL on every web service. Falling back to it
        # means notification images (which need an absolute URL) and the
        # keep-awake self-ping both work even when PUBLIC_URL was never set.
        self.public_url: str = (os.getenv("PUBLIC_URL", "") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")
        self.keepalive_enabled: bool = _bool(os.getenv("KEEPALIVE_ENABLED", "true"))
        self.keepalive_seconds: int = int(os.getenv("KEEPALIVE_SECONDS", "600"))

        # --- Your own Telegram channel: hot deals posted the moment they arrive ---
        # A bot (from @BotFather) that is an admin of the channel, allowed to post.
        self.tg_bot_token: str = os.getenv("TG_BOT_TOKEN", "").strip()
        # "@yourchannel" for a public channel, or its numeric id "-100…".
        self.tg_post_channel: str = _channel_id(os.getenv("TG_POST_CHANNEL", ""))
        # Link the app/website popup opens. Optional for a public channel (built
        # from @username); needed for a private one (its t.me/+invite link).
        self.tg_channel_url: str = os.getenv("TG_CHANNEL_URL", "").strip()
        # Real-time listener on the reader account (seconds, not the poll cycle).
        self.live_listener: bool = _bool(os.getenv("LIVE_LISTENER", "true"))
        # What's good enough to post (see tg_post.assess — fakes never are).
        # Women's deals (the channel's focus) need less; everything else more.
        self.tg_women_min_discount: int = int(os.getenv("TG_WOMEN_MIN_DISCOUNT", "50"))
        self.tg_hot_min_discount: int = int(os.getenv("TG_HOT_MIN_DISCOUNT", "70"))
        self.tg_hot_min_reposts: int = int(os.getenv("TG_HOT_MIN_REPOSTS", "3"))
        # "🤯 Crazy deal": a women's accessory at or under this price, 70%+ off.
        self.tg_crazy_max_price: float = float(os.getenv("TG_CRAZY_MAX_PRICE", "299"))
        self.tg_max_posts_per_hour: int = int(os.getenv("TG_MAX_POSTS_PER_HOUR", "20"))
        # Of those, at most this many non-women deals — the rest are kept for women's.
        self.tg_other_max_per_hour: int = int(os.getenv("TG_OTHER_MAX_PER_HOUR", "6"))
        # Same product isn't re-posted within this window unless it got cheaper.
        self.tg_repost_cooldown_hours: float = float(os.getenv("TG_REPOST_COOLDOWN_HOURS", "48"))
        # The poll cycle only posts what the live listener missed if it's this fresh.
        self.tg_post_max_age_minutes: float = float(os.getenv("TG_POST_MAX_AGE_MINUTES", "15"))

        # --- AI enrichment (Groq, optional — silently disabled if unset) ---
        self.groq_api_key: str = os.getenv("GROQ_API_KEY", "").strip()
        self.groq_model: str = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        # Free-tier ceiling for this model is 30 RPM / 1K RPD / 8K TPM / 200K TPD;
        # kept well under all four so a counting drift never trips the real limit.
        self.groq_max_requests_per_day: int = int(os.getenv("GROQ_MAX_REQUESTS_PER_DAY", "900"))
        self.groq_max_tokens_per_day: int = int(os.getenv("GROQ_MAX_TOKENS_PER_DAY", "180000"))
        self.groq_max_requests_per_minute: int = int(os.getenv("GROQ_MAX_REQUESTS_PER_MINUTE", "25"))
        self.groq_max_calls_per_cycle: int = int(os.getenv("GROQ_MAX_CALLS_PER_CYCLE", "20"))

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
    def public_mode(self) -> bool:
        return bool(self.telegram_configured and self.telegram_session and self.public_channels)

    @property
    def mongo_configured(self) -> bool:
        return bool(self.mongo_uri)

    @property
    def ai_enrich_enabled(self) -> bool:
        return bool(self.groq_api_key)

    @property
    def turso_configured(self) -> bool:
        return bool(self.turso_url and self.turso_auth_token)

    @property
    def tg_channel_link(self) -> Optional[dict]:
        """{"url", "username"} for the "join our channel" popup, or None."""
        name = self.tg_post_channel.lstrip("@") if self.tg_post_channel.startswith("@") else ""
        url = self.tg_channel_url or (f"https://t.me/{name}" if name else "")
        return {"url": url, "username": name} if url else None

    @property
    def tg_post_configured(self) -> bool:
        return bool(self.tg_bot_token and self.tg_post_channel)

    @property
    def turso2_configured(self) -> bool:
        return bool(self.turso2_url and self.turso2_auth_token)



def _channel_id(value: str) -> str:
    """Accept "dealradar18", "@dealradar18" or a t.me link for a public channel,
    and "-100…" for a private one — the Bot API wants "@name" or the number."""
    value = value.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/"):
        if value.lower().startswith(prefix):
            value = value[len(prefix):].split("/")[0].split("?")[0]
    if value and not value.startswith(("@", "-")) and not value.lstrip("-").isdigit():
        value = "@" + value
    return value


def _bool(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
