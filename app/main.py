"""DealRadar — Telegram marketplace deal aggregator.

Single FastAPI service: JSON API + static frontend + background ingestion.
Designed to run as one Render free-tier web service.
"""
from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import db
from .config import settings
from .routers import admin as admin_router
from .routers import auth as auth_router
from .routers import channels as channels_router
from .routers import deals as deals_router
from .routers import health as health_router
from .routers import notifications as notifications_router
from .routers import devices as devices_router
from .routers import lookup as lookup_router
from .routers import price_alerts as price_alerts_router
from .routers import watchlists as watchlists_router
from .services import ingest, public_reader, sheets, store, telegram, turso_backup

logging.basicConfig(
    level=getattr(logging, settings.log_level, logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)s — %(message)s",
)
log = logging.getLogger("dealradar")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(BASE_DIR, "static")

_tasks: list = []


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting DealRadar…")
    db.connect()

    if not settings.telegram_configured:
        log.warning("TELEGRAM_API_ID / TELEGRAM_API_HASH not set — sign-in will be unavailable.")
    if settings.secret_key == "dev-insecure-change-me":
        log.warning("SECRET_KEY is the insecure default — set a real one before deploying.")

    # Render's disk is ephemeral: rebuild the cache on cold start. Turso holds
    # only price history and price alerts (see turso_backup.py) — history is
    # read from it on demand, alerts are copied back here. Deals, channels and
    # settings come back from Google Sheets.
    loop = asyncio.get_event_loop()
    try:
        await loop.run_in_executor(None, turso_backup.restore)
    except Exception as exc:  # noqa: BLE001
        log.warning("Turso restore failed: %s", exc)
    turso_backup.start()

    # Each step gets its own try/except — restore_deals() is independently
    # resilient to a single bad row now, but a step here failing for some
    # other reason (a transient Sheets error, a quota blip) must not also
    # cost every step after it in the same sequence.
    if sheets.is_enabled():
        restored = 0
        try:
            restored = await loop.run_in_executor(None, sheets.restore_deals)
        except Exception as exc:  # noqa: BLE001
            log.warning("Deal restore failed: %s", exc)

        try:
            points = await loop.run_in_executor(None, sheets.restore_price_history)
            log.info("Restored %d price-history points from Google Sheets", points)
        except Exception as exc:  # noqa: BLE001
            log.warning("Price history restore failed: %s", exc)

        try:
            await loop.run_in_executor(None, sheets.restore_settings)
        except Exception as exc:  # noqa: BLE001
            log.warning("Settings restore failed: %s", exc)

        meta = {"users": 0, "channels": 0, "user_channels": 0, "watchlists": 0}
        try:
            # Order matters — users before channels before user_channels/
            # watchlists, since the latter are resolved by looking up the
            # former's local ids.
            meta = await loop.run_in_executor(None, sheets.restore_all_meta)
        except Exception as exc:  # noqa: BLE001
            log.warning("Meta restore failed: %s", exc)

        log.info(
            "Restored from Google Sheets: %d deals, %d users, %d channels, "
            "%d tracking links, %d watchlists",
            restored, meta["users"], meta["channels"],
            meta["user_channels"], meta["watchlists"],
        )
        if meta["users"]:
            log.info("Restored users hold no session — each needs to sign in again.")

        try:
            # Deals restored above ran before channels existed locally to
            # match against, and rows written before the Deals tab tracked
            # channel_tg_id (anything from before this fix shipped) came back
            # as channel_id=0 — invisible to a signed-in user's own
            # channel-scoped view. Runs after meta so the channels table is
            # actually populated to resolve against.
            fixed = store.backfill_channel_ids()
            if fixed:
                log.info("Backfilled channel_id on %d deals restored from Sheets", fixed)
                await loop.run_in_executor(None, sheets.flush_deals)
        except Exception as exc:  # noqa: BLE001
            log.warning("channel_id backfill failed: %s", exc)
    else:
        log.info("Google Sheets not configured — running in SQLite-only mode.")

    try:
        repaired = store.reparse_stored_deals()
        if repaired:
            log.info("Re-parsed %d stored deals with the current parser", repaired)
            if sheets.is_enabled():
                await asyncio.get_event_loop().run_in_executor(None, sheets.flush_deals)
    except Exception as exc:  # noqa: BLE001
        log.warning("Re-parse of stored deals failed: %s", exc)

    if settings.telegram_configured and settings.telegram_session:
        _tasks.append(asyncio.create_task(public_reader.bootstrap()))
    _tasks.append(asyncio.create_task(ingest.scheduler_loop()))
    _tasks.append(asyncio.create_task(ingest.keepalive_loop()))
    log.info("Ready. Polling every %ss, deal TTL %sh", settings.poll_interval_seconds, settings.deal_ttl_hours)

    try:
        yield
    finally:
        log.info("Shutting down…")
        turso_backup.stop()
        for task in _tasks:
            task.cancel()
        await asyncio.gather(*_tasks, return_exceptions=True)
        if sheets.is_enabled():
            try:
                sheets.flush_deals()  # don't lose the last cycle's work
            except Exception:  # noqa: BLE001
                pass
        await telegram.shutdown_all()


app = FastAPI(
    title="DealRadar API",
    description="Aggregates marketplace deals from Telegram channels into a searchable catalog.",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url=None,
    openapi_url="/api/openapi.json",
)

app.add_middleware(GZipMiddleware, minimum_size=1000)
# allow_origins=["*"] + allow_credentials=True is an invalid combination per
# the CORS spec — browsers refuse to honor a wildcard origin on a credentialed
# request. It's silent today because the bundled frontend is same-origin and
# never goes through CORS at all; it only bites a future separate client. So:
# credentials are only enabled once real origins are configured explicitly.
_cors_wildcard = "*" in settings.allowed_origins
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=not _cors_wildcard,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router.router)
app.include_router(auth_router.router)
app.include_router(channels_router.router)
app.include_router(deals_router.router)
app.include_router(watchlists_router.router)
app.include_router(notifications_router.router)
app.include_router(admin_router.router)
app.include_router(price_alerts_router.router)
app.include_router(devices_router.router)
app.include_router(lookup_router.router)


@app.middleware("http")
async def shell_cache_headers(request: Request, call_next):
    """The app shell has no build step (no hashed filenames), so browsers must
    revalidate it on each load — otherwise a deploy can pair a cached old
    stylesheet with a new app.js. ETags keep the revalidation a cheap 304."""
    response = await call_next(request)
    path = request.url.path
    if not path.startswith("/api/") and "cache-control" not in response.headers:
        if path.startswith("/assets/icons/"):
            response.headers["Cache-Control"] = "public, max-age=604800"
        else:
            response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on our side."})


if os.path.isdir(STATIC_DIR):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))

    @app.get("/d/{deal_id}", include_in_schema=False)
    async def share_page(deal_id: str, request: Request):
        """Share link: WhatsApp/Telegram read these Open Graph tags for the
        preview card (they don't run JS), then people land on the deal."""
        from html import escape
        from fastapi.responses import HTMLResponse

        row = db.query_one("SELECT title, price, mrp, discount_pct, store, image_url FROM deals WHERE id = ?",
                           (deal_id,))
        target = f"/?deal={escape(deal_id)}"
        if not row:
            return HTMLResponse(f'<meta http-equiv="refresh" content="0;url=/">', status_code=404)
        base = str(request.base_url).rstrip("/")
        price = f"₹{int(row['price']):,}" if row["price"] else ""
        bits = [b for b in (price, f"{row['discount_pct']}% off" if row["discount_pct"] else "",
                            (row["store"] or "").title()) if b]
        image = row["image_url"] or ""
        if image.startswith("/"):
            image = base + image
        if not image.startswith("http"):
            image = f"{base}/assets/icons/icon-512.png"
        title, desc = escape(row["title"] or "DealRadar deal"), escape(" · ".join(bits) + " — on DealRadar")
        return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<title>{title}</title>
<meta property="og:type" content="product"><meta property="og:site_name" content="DealRadar">
<meta property="og:title" content="{title}"><meta property="og:description" content="{desc}">
<meta property="og:image" content="{escape(image)}"><meta property="og:url" content="{escape(base)}/d/{escape(deal_id)}">
<meta name="twitter:card" content="summary_large_image">
<meta http-equiv="refresh" content="0;url={target}"></head>
<body><a href="{target}">Open deal</a></body></html>""")

    @app.get("/admin", include_in_schema=False)
    async def admin_page():
        return FileResponse(os.path.join(STATIC_DIR, "admin.html"))

    @app.get("/privacy", include_in_schema=False)
    async def privacy_page():
        return FileResponse(os.path.join(STATIC_DIR, "privacy.html"))

    @app.get("/terms", include_in_schema=False)
    async def terms_page():
        return FileResponse(os.path.join(STATIC_DIR, "terms.html"))

    @app.get("/{path:path}", include_in_schema=False)
    async def spa_fallback(path: str):
        """Serve static files, falling back to the SPA shell for unknown routes."""
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = os.path.normpath(os.path.join(STATIC_DIR, path))
        if candidate.startswith(STATIC_DIR) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
