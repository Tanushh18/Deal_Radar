# DealRadar: Deal Discovery Platform

A comprehensive deal aggregation and search platform that transforms Telegram marketplace channels into a centralized, searchable catalog with intelligent deduplication, price tracking, and real-time notifications.

**Live Demo:** [dealradar.ggnhome.com](https://dealradar.ggnhome.com) | **Android App:** [DealRadar on Google Play](https://play.google.com/store/apps/details?id=com.tanush.dealradar)

---

## Table of Contents

1. [Project Description](#project-description)
2. [Tech Stack](#tech-stack)
3. [Key Features](#key-features)
4. [Project Architecture](#project-architecture)
5. [Project Structure](#project-structure)
6. [Installation Guide](#installation-guide)
7. [Usage Guide](#usage-guide)
8. [Configuration Reference](#configuration-reference)
9. [API Documentation](#api-documentation)
10. [Development](#development)
11. [Database Setup](#database-setup)
12. [Deployment](#deployment)
13. [Troubleshooting](#troubleshooting)
14. [Security & Limits](#security--limits)
15. [Contributing Guide](#contributing-guide)
16. [Dependencies](#dependencies)
17. [License](#license)

---

## Project Description

DealRadar aggregates real-time deals from multiple Telegram marketplace channels and presents them through an intelligent web interface and mobile app. By parsing structured deal data from channel posts, deduplicating across channels, tracking price history, and applying sophisticated ranking algorithms, DealRadar transforms an overwhelming firehose of deal notifications into a curated, searchable catalog.

**The Problem:** Telegram has become the primary marketplace for deals in India, with hundreds of channels posting thousands of messages daily. Users follow multiple channels but miss deals and can't easily search or compare prices.

**The Solution:** DealRadar automatically fetches, parses, deduplicates, and indexes deals across all your followed channels, backing them with permanent price history and alert notifications delivered directly to your Telegram account.

### Core Workflow

```
Telegram Channels              DealRadar Pipeline              Users
├── @loot_deals          ┌──────────────────────┐       ┌────────────┐
├── @amazon_offers   ──►│ 1. Fetch (watermark) │      │  Web UI    │
├── @fk_deals        │  2. Parse → deal       │   ──►│  Search    │
└── @fashion_loot    │  3. Dedup → 1 card     │      │  Filters   │
                     │  4. Expire → TTL       │      │  Alerts    │
                     │  5. Verify → live?     │      └────────────┘
                     │  6. Alert → Saved      │
                     │  7. Flush → Turso      │
                     └──────────────────────┘
                              ▼
                     ┌──────────────────┐
                     │ Turso + MongoDB  │
                     │ + SQLite cache   │
                     └──────────────────┘
```

---

## Tech Stack

### Backend
- **Framework:** FastAPI 0.115.6
- **Async Server:** Uvicorn with ASGI
- **Python:** 3.9+ (3.11 recommended)
- **Telegram Client:** Telethon 1.38.1 (MTProto protocol)

### Databases
- **Local Cache:** SQLite (ephemeral, 15-day rolling cache)
- **Permanent Deals Store:** Turso (LibSQL) with optional split for price history
- **User/Channel State:** MongoDB Atlas (optional but recommended for production)

### Frontend
- **Web:** Vanilla JavaScript + HTML5/CSS3 (SPA, bundled static assets)
- **Mobile:** React Native (Expo SDK 57, New Architecture)
- **Auth:** Session cookies (httpOnly, SameSite=Lax, Secure on HTTPS)

### Core Libraries
- **HTTP Client:** httpx (async requests)
- **String Matching:** rapidfuzz (fuzzy title matching for dedup)
- **Encryption:** cryptography + Fernet (session encryption)
- **Data Validation:** Pydantic 2.10.4
- **Environment:** python-dotenv

### Deployment
- **Production:** Render (free tier: Python 3 runtime, 512MB RAM, ephemeral disk)
- **Build:** Docker via Render blueprint
- **Health Checks:** Liveness probes, automated keepalive
- **Monitoring:** Custom `/api/health` and `/api/stats` endpoints

---

## Key Features

### 1. Deal Aggregation & Deduplication
- **Multi-channel fetch:** Watermarked incremental polling (one request per channel per cycle)
- **Cross-channel dedup:** Collapses identical products across channels into single cards
- **Product key matching:** ASIN/Flipkart pid for exact matches, fuzzy title matching (token_sort_ratio ≥ 88) for shortlinks
- **Affiliate link scrubbing:** Strips utm_*, affid, gclid, tag params for accurate comparison

### 2. Price Intelligence
- **Price history tracking:** Every price change recorded per product (historical low badge, trend analysis)
- **Turso split database:** Optional second Turso DB just for price_points (the heaviest table)
- **All-time low detection:** Automatic flagging and ranking boost for new lows
- **Fake MRP detection:** Suspicious markups (>2.5× median) flagged and ranked down

### 3. Quality & Ranking
- **Quality filter:** Only posts with price + store link + photo become deals
- **Repost count signal:** Deal carried by 6 channels = almost certainly real
- **Composite deal score:** discount + corroboration + freshness (36h half-life) + penalties
- **Spam filtering:** Join-our-channel, giveaway, refer-and-earn posts auto-filtered

### 4. Search & Discovery
- **Synonym-expanded search:** "women dress" matches gown, maxi, one-piece (12 categories, 53 subcategories)
- **Full-text search:** Query across titles, descriptions, store names
- **Faceted filters:** Store, brand, category, price range, discount range
- **Sort options:** relevance, best, newest, discount, price (low/high)
- **Trending:** Most-reposted recent deals

### 5. Real-Time Alerts
- **Watchlist system:** Save searches with optional price/discount alerts
- **Telegram delivery:** Alerts via Saved Messages (your own account, no external service)
- **Hot deal broadcasts:** Live listener posts breaking deals to your channel within seconds
- **Deal age filtering:** Posted within 15 minutes considered "hot"

### 6. Link Liveness Probing
- **Automated verification:** Periodically checks if deal links are still valid
- **Early retirement:** 404/out-of-stock responses retire deals before TTL
- **Extended TTL:** Live links get extended beyond base 96-hour TTL
- **SSRF protection:** Blocks private/loopback/reserved IP ranges

### 7. Performance Optimizations
- **Batched writes:** One `batch_update` + one `append_rows` per Sheets cycle
- **Single-source reads:** Shared channel fetches across users
- **In-memory caching:** BuyHatke price history cached 3 days
- **Local SQLite:** Fast 15-day rolling cache with cold-start restoration from Turso

### 8. Mobile & Offline Support
- **Visitor mode:** Browse without sign-in (device-based tracking)
- **Native Android:** React Native (Expo), offline caching
- **Push notifications:** FCM integration with Notifee rich display
- **Deep links:** Notification taps, share intents, launcher shortcuts

---

## Project Architecture

### High-Level System Design

```
┌─────────────────────────────────────────────────────────────┐
│                    User Interfaces                          │
├─────────────────────────────────────────────────────────────┤
│  Web SPA (JS)  │  Android App (React Native)  │  API Docs   │
└────────┬───────────────────────────────────────────────┬────┘
         │                                               │
         └───────────────────┬───────────────────────────┘
                             │
                             ▼
         ┌───────────────────────────────────────┐
         │       FastAPI + Uvicorn              │
         │   (Single-worker, stateful Telethon)│
         ├───────────────────────────────────────┤
         │  Routers:                             │
         │  • auth.py (phone/2FA login)          │
         │  • channels.py (discovery, tracking) │
         │  • deals.py (search, detail, history)│
         │  • watchlists.py (alerts)            │
         │  • health.py (ping, stats)           │
         │  • devices.py (visitor tracking)     │
         │  • notifications.py (push logic)     │
         └───────────────┬───────────────────────┘
                         │
        ┌────────────────┼────────────────┐
        ▼                ▼                ▼
┌─────────────┐  ┌────────────────┐  ┌──────────┐
│   Telethon  │  │    Services    │  │ Ingest   │
│  (MTProto   │  │ (parse, score, │  │ Cycle    │
│  sessions)  │  │  search, rank) │  │ (async)  │
└─────────────┘  └────────────────┘  └──────────┘
        ▲                │
        └────────┬───────┴──────────┐
                 ▼                  ▼
        ┌──────────────────────────────────┐
        │     Data Layer (3-tier cache)    │
        ├──────────────────────────────────┤
        │  Layer 1: SQLite (15-day local)  │
        │  Layer 2: Turso (permanent)      │
        │  Layer 3: MongoDB (state)        │
        └──────────────────────────────────┘
```

### Data Flow

1. **Ingest Cycle** (every 40 minutes):
   - Poll each channel for new messages (watermarked)
   - Parse posts into structured deals
   - Dedup across channels (ASIN/Flipkart ID or fuzzy title match)
   - Score & rank (discount, corroboration, freshness, penalties)
   - Probe link liveness, retire dead links
   - Write to Turso (permanent) and local SQLite (cache)

2. **Real-Time Listener** (background task):
   - Subscribe to MTProto updates for followed channels
   - Parse hot deals (new all-time low, high discount, multiple reposts)
   - Post to user's channel via bot
   - Write to databases

3. **User Interaction**:
   - Search / filter deals (read from SQLite + fallback to Turso)
   - Save price alerts (store in MongoDB)
   - Receive notifications (Telegram Saved Messages or FCM push)

### Database Schema Overview

**SQLite (Local Cache):**
- `deals` - product cards with aggregate info
- `products` - unique products with pricing summary
- `price_points` - every price change (optional: moved to Turso DB 02)
- `price_alerts` - user watchlist items

**Turso (Permanent Store):**
- `deals` - canonical deal records
- `products` - product price history
- `price_points` - full price change log
- `price_alerts` - searchable watchlist state

**MongoDB (Durable User State):**
- `users` - accounts (id, username, login times)
- `channels` - tracked channels with watermarks
- `user_channels` - user-to-channel mappings
- `watchlists` - saved searches
- `settings` - admin config (priority rules, poll interval)
- `devices` - visitor app installs, push tokens

---

## Project Structure

```
deal-radar/
├── README.md                          # Main documentation
├── DATABASE.md                        # Database schema & setup
├── Makefile                           # Development commands
├── requirements.txt                   # Python dependencies
├── requirements-dev.txt               # Dev/test dependencies
├── render.yaml                        # Render blueprint (deployment)
├── .env.example                       # Configuration template
├── .gitignore                         # Git exclusions
│
├── app/                               # FastAPI application
│   ├── main.py                        # App entry, lifespan, SPA fallback
│   ├── config.py                      # Environment-driven settings
│   ├── db.py                          # SQLite schema & helpers
│   ├── auth.py                        # Session management, auth guards
│   │
│   ├── routers/                       # API endpoint handlers
│   │   ├── auth.py                    # Phone login, 2FA, logout
│   │   ├── channels.py                # Channel discovery & tracking
│   │   ├── deals.py                   # Search, detail, history, image proxy
│   │   ├── watchlists.py              # Saved searches & alerts
│   │   ├── price_alerts.py            # Price alert management
│   │   ├── notifications.py           # Push notification routing
│   │   ├── devices.py                 # Visitor device registration
│   │   ├── health.py                  # Ping, health, stats
│   │   ├── admin.py                   # Admin-only endpoints
│   │   ├── lookup.py                  # Price lookups, BuyHatke integration
│   │   └── sale_events.py             # Sale calendar
│   │
│   └── services/                      # Business logic & integrations
│       ├── telegram.py                # Telethon clients, session encryption
│       ├── parser.py                  # Message → structured deal parsing
│       ├── taxonomy.py                # Categories, synonyms, brands, spam
│       ├── store.py                   # Dedup, scoring, expiry logic
│       ├── search.py                  # Query engine, facets, ranking
│       ├── quality.py                 # Post quality filtering
│       ├── mongo_store.py             # MongoDB ops (users, channels, etc)
│       ├── turso_backup.py            # Turso sync (permanent store)
│       ├── price_alerts.py            # Price alert matching
│       ├── price_store.py             # Price history management
│       ├── ingest.py                  # Automation cycle + schedulers
│       ├── live.py                    # Real-time MTProto listener
│       ├── tg_post.py                 # Hot deal poster (bot)
│       ├── hot_push.py                # Mobile push broadcaster
│       ├── fcm.py                     # Firebase Cloud Messaging
│       ├── devices.py                 # Device registration & feed
│       ├── notify_auto.py             # Automatic notifications
│       ├── links.py                   # Link liveness probing
│       ├── buyhatke.py                # BuyHatke price API integration
│       ├── offers.py                  # Offers & promotions
│       ├── ai_enrich.py               # AI product enrichment
│       ├── public_reader.py           # Public-mode channel reader
│       ├── autopoll.py                # Polling scheduler
│       ├── activity.py                # User activity tracking
│       ├── priority.py                # Audience priority ranking
│       ├── sale_events.py             # Sale calendar logic
│       ├── ratelimit.py               # Rate limiting
│       └── push.py                    # Push notification service
│
├── static/                            # Frontend assets
│   ├── index.html                     # SPA entry point
│   ├── manifest.json                  # PWA manifest
│   └── assets/
│       ├── app.js                     # Main application logic
│       └── styles.css                 # Styling
│
├── mobile/                            # React Native (Expo) app
│   ├── README.md                      # Mobile-specific docs
│   ├── app.json                       # Expo configuration
│   ├── App.tsx                        # Root component, boot logic
│   ├── package.json                   # Node dependencies
│   ├── eas.json                       # EAS build config
│   ├── google-services.json           # Firebase config (gitignored)
│   │
│   ├── src/
│   │   ├── navigation/                # React Navigation setup
│   │   ├── screens/                   # UI screens (Deals, Search, etc)
│   │   ├── components/                # Reusable UI components
│   │   ├── api/                       # API client wrapper
│   │   ├── theme/                     # Colors, typography
│   │   ├── native/                    # Platform-specific logic
│   │   │   ├── config.ts              # Server URL, branding
│   │   │   ├── session.ts             # Auth state management
│   │   │   ├── device.ts              # Device ID, registration
│   │   │   ├── notifications.ts       # Push setup, Notifee
│   │   │   ├── backgroundTask.ts      # Background job scheduling
│   │   │   ├── deepLinks.ts           # Deep link routing
│   │   │   ├── shareIntent.ts         # Share-to-app intent
│   │   │   └── updates.ts             # OTA update check
│   │   └── hooks/                     # Custom React hooks
│   │
│   ├── assets/
│   │   ├── icons/                     # App icon (multiple sizes)
│   │   ├── notification_icon.png      # Monochrome notification icon
│   │   └── shortcut-*.png             # Launcher shortcut icons
│   │
│   └── scripts/
│       └── make-mono-icon.js          # Icon generator
│
├── tests/                             # Test suites
│   ├── test_pipeline.py               # Full pipeline (46 checks)
│   ├── test_search.py                 # Search & faceting
│   ├── test_notifications.py          # Notification logic
│   ├── test_reparse.py                # Parser edge cases
│   ├── test_devices.py                # Device tracking
│   ├── test_quality.py                # Quality filtering
│   ├── test_ingest_cycle.py           # Full ingest cycle
│   ├── test_turso.py                  # Turso integration
│   ├── test_turso_split.py            # Turso split DB handling
│   └── e2e/                           # Selenium-based browser tests
│
├── tools/                             # Utilities & scripts
│   ├── make_session.py                # Generate public-mode session string
│   ├── status.py                      # Full pipeline diagnostic
│   └── ...                            # Other maintenance scripts
│
└── .github/                           # GitHub configuration
    └── workflows/                     # CI/CD (if configured)
```

---

## Installation Guide

### Prerequisites

- **Python:** 3.9+ (3.11+ recommended)
- **Telegram Account:** Required for the reader account
- **Telegram API Credentials:** From [my.telegram.org](https://my.telegram.org)
- **Optional (Production):**
  - MongoDB Atlas cluster (free tier)
  - Turso account (free tier)
  - Firebase project (for push notifications)
  - Render account (for hosting)

### Step 1: Get Telegram API Credentials

1. Visit [https://my.telegram.org](https://my.telegram.org) and log in
2. Navigate to **API development tools**
3. Create an app (e.g., "DealRadar")
4. Copy your **api_id** and **api_hash**

### Step 2: Set Up the Project

```bash
# Clone the repository
git clone https://github.com/Tanushh18/Deal_Radar.git
cd Deal_Radar

# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# Install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# Create .env file
cp .env.example .env

# Generate a secure SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Step 3: Configure Environment

Edit `.env` and set at minimum:

```bash
TELEGRAM_API_ID=<your_api_id>
TELEGRAM_API_HASH=<your_api_hash>
SECRET_KEY=<generated_secret_key>
```

### Step 4: Run Locally

```bash
# Development (with auto-reload)
make dev
# Or manually:
uvicorn app.main:app --reload --port 8000

# Production-style (single worker)
make start
# Or manually:
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open [http://localhost:8000](http://localhost:8000)

### Step 5: Verify Installation

```bash
# Test core pipeline (no Telegram needed)
python -m tests.test_pipeline

# Check if server is running
curl http://localhost:8000/api/ping

# View interactive API docs
open http://localhost:8000/api/docs

# Run full test suite
make test
```

---

## Usage Guide

### Web Interface

1. **Sign In:** Phone number → Telegram sends code (in-app, not SMS) → optional 2FA
2. **Select Channels:** Check boxes for deal channels you follow → Save selection
3. **First Sync:** ~120 recent messages per channel fetched, then polls every 40 minutes
4. **Search:** Type anything ("charger", "women kurta", "shoes") → results ranked by relevance
5. **Filters:** By category, subcategory, store, brand, price range, discount
6. **Save Alerts:** Search → click "Save search" → get matches in Telegram Saved Messages
7. **Price Charts:** Click a deal → see price history, all-time low status, trending indicator

### Android App

1. **Auto-login:** First launch auto-registers as anonymous "visitor" device
2. **Browse Deals:** No login required; swipe through deals, search, filter
3. **Notifications:** Opt-in to deal alerts → receive hot deals as push notifications
4. **Saved Deals:** Bookmark items → accessed from your visitor device ID
5. **Price Alerts:** Watch an item → get notified when price drops
6. **Settings:** Change server URL, enable notifications, manage digest preferences

### Command Reference

```bash
make help        # Show all available commands
make setup       # One-time setup (venv, deps, .env)
make dev         # Run with auto-reload (development)
make start       # Run production-style (single worker)
make test        # Run all test suites
make e2e         # Browser automation (Chrome/Selenium)
make ping        # Test /api/ping endpoint
make health      # Deep health check
make stats       # Deal & ingest statistics
make status      # Full pipeline diagnostic
make sync        # Force an ingest cycle (needs ADMIN_TOKEN)
make secret      # Generate a new SECRET_KEY
make clean       # Remove __pycache__ only
make clean-data  # Delete local SQLite cache
```

---

## Configuration Reference

### Required Environment Variables

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_API_ID` | — | **Required.** From my.telegram.org |
| `TELEGRAM_API_HASH` | — | **Required.** 32-char hex from my.telegram.org |
| `SECRET_KEY` | `dev-insecure-change-me` | **Set this.** Signs cookies, encrypts sessions. Changing it logs everyone out. |

### Recommended for Production

| Variable | Default | Notes |
|----------|---------|-------|
| `MONGODB_URI` | empty | `mongodb+srv://user:pass@cluster.mongodb.net/` for durable user/channel state |
| `TURSO_DATABASE_URL` | empty | `libsql://<db>-<org>.turso.io` for permanent deal storage |
| `TURSO_AUTH_TOKEN` | empty | Auth token for Turso |
| `PUBLIC_URL` | empty | `https://your-domain.com` enables HTTPS-only cookies & self-ping |

### Ingestion Tuning

| Variable | Default | Notes |
|----------|---------|-------|
| `POLL_INTERVAL_SECONDS` | `2400` | Seconds between ingest cycles (40 minutes) |
| `DEAL_TTL_HOURS` | `96` | 4 days; live links get extended beyond this |
| `BACKFILL_LIMIT` | `120` | Messages fetched on first channel poll |
| `INCREMENTAL_LIMIT` | `60` | Messages fetched per poll thereafter |
| `MAX_CHANNELS_PER_USER` | `40` | User can track at most this many channels |
| `LOCAL_CACHE_DAYS` | `15` | Local SQLite keeps this many days (capped by the Turso retention below) |
| `TURSO_DEAL_RETENTION_DAYS` | `5` | Turso auto-deletes deals not seen for this many days; `0` keeps forever |
| `LIVENESS_CHECK` | `true` | Enable link probing (retire dead deals early) |
| `LIVENESS_BATCH` | `40` | Links probed per cycle |
| `QUALITY_FILTER` | `true` | Only posts with price + link + photo become deals |

### Telegram Bot Setup (Hot Deals)

| Variable | Default | Notes |
|----------|---------|-------|
| `TG_BOT_TOKEN` | empty | From @BotFather; bot must be channel admin |
| `TG_POST_CHANNEL` | empty | `@channel_name` or numeric ID `-100…` |
| `TG_HOT_MIN_DISCOUNT` | `60` | % off that counts as "hot" (all-time lows always hot) |
| `TG_HOT_MIN_REPOSTS` | `3` | Or: carried by this many channels |
| `TG_MAX_POSTS_PER_HOUR` | `20` | Rate limit for bot posts |
| `TG_REPOST_COOLDOWN_HOURS` | `48` | Min hours between reposts of same product |
| `TG_POST_MAX_AGE_MINUTES` | `15` | Only post deals posted within this many minutes |

### Security & Limits

| Variable | Default | Notes |
|----------|---------|-------|
| `ADMIN_TOKEN` | empty | Enables admin-only endpoints; empty = disabled |
| `SESSION_TTL_DAYS` | `180` | Web session lifetime in days |
| `SESSION_COOKIE` | `tgdeals_session` | Cookie name |
| `ALLOWED_ORIGINS` | `*` | CORS origins (for separate frontend) |

### Advanced

| Variable | Default | Notes |
|----------|---------|-------|
| `MONGODB_DB_NAME` | `dealradar` | Database name within MongoDB cluster |
| `DB_PATH` | `data/deals.db` | Local SQLite cache path |
| `LOG_LEVEL` | `INFO` | Logging verbosity (DEBUG, INFO, WARNING, ERROR) |
| `KEEPALIVE_ENABLED` | `true` | Self-ping keepalive (requires PUBLIC_URL) |
| `KEEPALIVE_SECONDS` | `600` | Keepalive interval (10 min) |
| `PRIORITY_AUDIENCE` | `women` | Priority category (affects ranking) |
| `BUYHATKE_ENABLED` | `true` | Enable BuyHatke price API integration |
| `TURSO_DB_02` | empty | Optional second Turso DB for price_points |
| `TURSO_DB_02_AUTH_TOKEN` | empty | Auth token for second DB |
| `FCM_SERVICE_ACCOUNT_JSON` | empty | Firebase service account (base64 or raw JSON) |

---

## API Documentation

### Interactive Docs

Swagger UI available at `http://localhost:8000/api/docs` (when running locally)

### System Endpoints

```
GET/HEAD  /api/ping                  # Liveness check (no DB)
GET       /api/health                # Deep health check
GET       /api/stats                 # Deal counts, ingest state
```

### Auth Endpoints

```
GET       /api/auth/config           # Server has credentials?
POST      /api/auth/send-code        # {phone} → login_id + code sent
POST      /api/auth/verify-code      # {login_id, code} → session
POST      /api/auth/verify-password  # {login_id, password} → session (2FA)
GET       /api/auth/me               # Current user info
POST      /api/auth/logout           # End web session
DELETE    /api/auth/account          # Erase account + data
```

### Channels (auth required)

```
GET       /api/channels/available    # Your followed broadcast channels
GET       /api/channels              # Channels you're tracking
POST      /api/channels/track        # {tg_ids: [...]} → replace selection
POST      /api/channels/add-public   # {username} → resolve & join
DELETE    /api/channels/{tg_id}      # Stop tracking
POST      /api/channels/sync         # Force ingest cycle now
```

### Deals (public)

```
GET       /api/deals                 # Search: q, category, store, price range, sort, etc.
GET       /api/deals/categories      # Full taxonomy
GET       /api/deals/facets          # Filter counts by store/brand/category
GET       /api/deals/trending        # Most-reposted recent
GET       /api/deals/{id}            # Deal detail + price stats
GET       /api/deals/{id}/history    # Price history points
GET       /api/deals/{id}/image      # Proxied Telegram photo
```

**Query Parameters (Deals):**
- `q` - search query
- `category` - filter by category
- `subcategory` - filter by subcategory
- `store` - filter by store
- `brand` - filter by brand
- `min_price`, `max_price` - price range
- `min_discount` - min discount %
- `only_lowest` - only show lowest-price items per product
- `include_expired` - include expired deals
- `all_channels` - search across all channels, not just your subscriptions
- `sort` - relevance, best, newest, discount, price_low, price_high
- `limit`, `offset` - pagination

**Example:**
```bash
curl "https://your-app.com/api/deals?q=women%20kurta&max_price=800&min_discount=50&sort=best"
```

### Watchlists / Price Alerts (auth required)

```
GET       /api/watchlists            # Your saved searches
POST      /api/watchlists            # {query, category, store, max_price, notify}
PATCH     /api/watchlists/{id}       # ?notify=true/false to toggle
DELETE    /api/watchlists/{id}       # Delete watchlist
POST      /api/watchlists/{id}/test  # Send test alert
```

### Admin (requires `X-Admin-Token` header)

```
POST      /api/admin/ingest          # Force an ingest cycle
POST      /api/admin/sheets/flush    # Force Sheets write
POST      /api/admin/sheets/restore  # Rebuild SQLite from Sheets
```

**Example:**
```bash
curl -X POST https://your-app.com/api/admin/ingest \
  -H "X-Admin-Token: $ADMIN_TOKEN"
```

### Devices (visitor mode, no auth)

```
POST      /api/devices/register      # {device_id, platform, push_token?, digest?}
GET       /api/devices/feed          # New deals for this device
```

---

## Development

### Development Workflow

```bash
# Activate environment
source .venv/bin/activate

# Run with hot-reload
make dev

# In another terminal, run tests
make test

# Run specific test
python -m tests.test_pipeline

# Check type hints
mypy app/
```

### Testing

- **Unit/Integration:** `tests/test_*.py` (no Telegram needed)
- **E2E:** `tests/e2e/` (browser automation)
- **Ad-hoc:** `make ping`, `make health`, `make stats`

Run all tests:
```bash
make test
```

### Code Structure Guidelines

- **Routers** (`app/routers/`): API endpoint handlers (thin, delegate to services)
- **Services** (`app/services/`): Business logic, integrations (stateless where possible)
- **Config** (`app/config.py`): Environment-driven settings (single source of truth)
- **DB** (`app/db.py`): Schema + query helpers (SQLite only)
- **Auth** (`app/auth.py`): Session & permission guards

### Adding a New Endpoint

1. Create handler in `app/routers/feature.py`
2. Import and include router in `app/main.py`
3. Delegate business logic to `app/services/`
4. Test with `curl` or `/api/docs`

Example (new endpoint):
```python
# app/routers/feature.py
from fastapi import APIRouter, Depends

router = APIRouter(prefix="/api/feature", tags=["Feature"])

@router.get("/something")
async def get_something(session: Session = Depends(get_session)):
    # Get current user, check auth
    if not session:
        raise HTTPException(status_code=401)
    return {"result": "..."}
```

---

## Database Setup

### SQLite (Local Cache)

Created automatically on first run. Schema defined in `app/db.py`.

**Key tables:**
- `deals` - product cards
- `products` - unique products with pricing summary
- `price_points` - every price change
- `price_alerts` - watchlists

**Features:**
- 15-day rolling retention (older deals dropped)
- Cold-start restoration from Turso
- Indexed on natural keys for fast lookup

### MongoDB (Durable User State)

**Optional but recommended for production.**

1. **Create free cluster:**
   - Go to [mongodb.com/atlas](https://mongodb.com/atlas)
   - Create account, free M0 cluster
   - Create database user (username + password)
   - Allow access from anywhere: `0.0.0.0/0`

2. **Get connection string:**
   - Drivers → copy URI template
   - Format: `mongodb+srv://user:password@cluster.mongodb.net/`

3. **Set in .env:**
   ```bash
   MONGODB_URI=mongodb+srv://user:password@cluster.mongodb.net/
   MONGODB_DB_NAME=dealradar
   ```

4. **Collections auto-created:**

| Collection | Indexed on | Purpose |
|---|---|---|
| `users` | `telegram_id` | Accounts (id, username, login times) |
| `channels` | `tg_id` | Tracked channels + watermarks |
| `user_channels` | `(user_id, channel_id)` | Who tracks what |
| `watchlists` | `(user_id, query)` | Saved searches |
| `settings` | `key` | Admin config |

### Turso (Permanent Deal Store)

**Highly recommended for production (free tier: 5GB storage, 500M row-reads/month).**

1. **Create account:** [turso.tech](https://turso.tech)
2. **Create database:**
   ```bash
   turso db create dealradar
   ```
3. **Get credentials:**
   ```bash
   turso db show dealradar --json
   ```
4. **Set in .env:**
   ```bash
   TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io
   TURSO_AUTH_TOKEN=<token>
   ```

**Split price history (optional):**
```bash
turso db create dealradar_prices
turso db tokens create dealradar_prices
```

Then in .env:
```bash
TURSO_DB_02=libsql://<db2>-<org>.turso.io
TURSO_DB_02_AUTH_TOKEN=<token>
```

---

## Deployment

### Render (Recommended for Free Tier)

**Why Render?**
- Free Python 3 runtime
- 512MB RAM, ephemeral disk (fine for deals cache)
- Auto-redeploy on git push
- PostgreSQL, Redis add-ons available
- Blueprint deployment (infrastructure as code)

**Deploy with Blueprint:**

1. Push repo to GitHub
2. Go to [render.com](https://render.com)
3. Dashboard → **New** → **Blueprint**
4. Select your repo
5. Fill in required environment variables:
   - `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
   - `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - `MONGODB_URI`
6. Deploy
7. After first boot, set `PUBLIC_URL` to your Render URL and redeploy

**Keepalive (prevent sleep):**
- Set `PUBLIC_URL` → app pings itself every 10 min
- Also set up external pinger ([UptimeRobot](https://uptimerobot.com), free tier)

### Alternative: Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

```bash
docker build -t dealradar .
docker run -e TELEGRAM_API_ID=... -e TELEGRAM_API_HASH=... -p 8000:8000 dealradar
```

### Environment Variables (Render)

All variables from `.env.example` can be set in Render dashboard under **Environment**.

**Critical (mark `sync: false` in render.yaml):**
- `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`
- `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
- `MONGODB_URI`
- `PUBLIC_URL` (set after first deploy)

**Optional but recommended:**
- `ADMIN_TOKEN` (for `/api/admin/*` endpoints)
- `TG_BOT_TOKEN`, `TG_POST_CHANNEL` (for hot deal posting)

---

## Troubleshooting

### "Telegram API credentials are missing"

**Cause:** `TELEGRAM_API_ID` or `TELEGRAM_API_HASH` not set.

**Fix:** Check `.env` file and Render environment variables. Verify at `GET /api/health`.

### Login code never arrives

**Cause:** Telegram sends the code *inside the app* (Saved Messages), not SMS.

**Fix:** Open Telegram app → Saved Messages → look for login code there.

### "Your Telegram session expired"

**Cause:** `SECRET_KEY` changed (makes stored sessions undecryptable).

**Fix:** Sign in again. Keep `SECRET_KEY` stable to avoid re-logins.

### No channels listed

**Cause:** Only *broadcast channels* are listed (groups/private chats excluded by design).

**Fix:** Join some public deal channels in Telegram first, then hit **Refresh**.

### No deals after syncing

**Cause:** Some channels post only images with unstructured captions.

**Fix:** Check `/api/stats` for `deals_total`. Enable unstructured posts in admin. Check logs.

### Rate limit errors (`FloodWaitError`)

**Cause:** Polling too frequently or tracking too many channels.

**Fix:**
- Raise `POLL_INTERVAL_SECONDS` (e.g., 3600 = 1 hour)
- Lower `INCREMENTAL_LIMIT` (messages per poll)
- Track fewer channels
- App already backs off automatically

### Render deploy is slow / sleeping

**Expected on free tier:** 15-minute idle sleep, ~30s cold starts.

**Mitigation:**
- Set `PUBLIC_URL` and `KEEPALIVE_ENABLED=true` → self-ping every 10 min
- Add external uptime monitor (UptimeRobot) → pings every 5 min
- This keeps the dyno awake

---

## Security & Limits

### Encryption & Sessions

- **Telegram session:** Encrypted with Fernet (key derived from `SECRET_KEY`)
- **Web sessions:** httpOnly cookies, `SameSite=Lax`, `Secure` on HTTPS
- **Login codes & 2FA:** Never stored, only in memory for seconds

### Rate Limiting

Per IP, in-memory (per-process, requires `--workers 1`):

| Endpoint | Limit | Window |
|----------|-------|--------|
| `/api/auth/send-code` | 5 | 15 min |
| `/api/auth/verify-code` | 15 | 15 min |
| `/api/auth/verify-password` | 15 | 15 min |
| `/api/channels/add-public` | 20 | 10 min |
| Deal image proxy (cache miss) | 90 | 1 min |

### SSRF Protection

Link liveness probing blocks:
- Private IP ranges (10.x, 172.16-31.x, 192.168.x)
- Loopback (127.x)
- Link-local (169.254.x)
- Reserved (0.x, 255.x)

Redirects followed manually with per-hop validation.

### Data Retention

- **SQLite (local):** 15 days (configurable via `LOCAL_CACHE_DAYS`)
- **Turso:** deals not seen for 5 days are auto-deleted (configurable via `TURSO_DEAL_RETENTION_DAYS`, `0` = keep forever); still-live deals are kept. Alerts and devices are unaffected. The local cache is capped to the same window.
- **MongoDB (state):** Forever or until user deletes account

Account deletion (`DELETE /api/auth/account`):
- Erases user record from MongoDB
- Removes all watchlists & alerts
- Clears web sessions
- **Does not** delete deals (shared across users)

### Compliance Notes

- **Terms of Service:** Automating a user account violates a strict reading of Telegram ToS if abused
- **Recommended:** Polling a handful of channels every few minutes for personal use is normal behavior
- **Not recommended:** Scraping hundreds of channels aggressively
- **Defaults:** Deliberately conservative (40-min poll interval, limited channels, rate-limited)

---

## Contributing Guide

### Code Standards

1. **Python Style:** PEP 8 + Black formatter
2. **Type Hints:** Annotate function arguments & returns
3. **Docstrings:** Module, class, and public method docstrings
4. **Testing:** New features require tests

### Setting Up for Development

```bash
# Install dev dependencies
pip install -r requirements-dev.txt

# Run tests
make test

# Format code
black app/ tests/

# Type check
mypy app/
```

### Pull Request Process

1. **Branch:** Create a feature branch from `main`
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Test:** Run full test suite
   ```bash
   make test
   ```

3. **Commit:** Clear, descriptive messages
   ```bash
   git commit -m "Add feature X to support Y"
   ```

4. **Push & PR:** Push branch and create pull request with description

### Reporting Issues

- **Bug:** Describe reproduction steps, expected vs. actual behavior
- **Feature:** Explain use case and expected behavior
- **Question:** Use discussions or contact maintainer

### Areas for Contribution

- **Parser:** Improve deal detection (new store formats, shortlinks)
- **Dedup:** Better title matching for shortlinks
- **Taxonomy:** Add categories, brands, synonyms
- **Frontend:** UI/UX improvements
- **Mobile:** React Native enhancements
- **Tests:** Increase test coverage

---

## Dependencies

### Core Runtime

```
fastapi==0.115.6             # Web framework
uvicorn[standard]==0.34.0    # ASGI server (includes uvloop, httptools)
telethon==1.38.1             # Telegram MTProto client
cryptg==0.5.0.post0          # Optional: faster cryptography
pymongo==4.10.1              # MongoDB driver
dnspython==2.7.0             # DNS support for MongoDB SRV
cryptography==44.0.0         # Encryption (Fernet for session storage)
httpx==0.28.1                # Async HTTP client (link probing)
python-dotenv==1.0.1         # .env file support
pydantic==2.10.4             # Data validation
rapidfuzz==3.11.0            # Fuzzy string matching (dedup)
python-multipart==0.0.20     # Form parsing
```

### Development

```
pytest                        # Testing framework
pytest-asyncio               # Async test support
selenium                     # E2E browser testing
black                        # Code formatter
mypy                         # Type checker
```

### Optional (Production)

- **Firebase Admin SDK** (for push notifications)
- **Google Sheets API** (legacy support)
- **Turso Python SDK** (if not using libsql)

### Python Version

- **Minimum:** 3.9
- **Recommended:** 3.11+

---

## License

This project is provided as-is for personal use and deal aggregation. Compliance with Telegram's Terms of Service is the user's responsibility.

---

## Contact & Support

- **Issues:** GitHub issues for bugs and feature requests
- **Discussions:** GitHub discussions for questions
- **Maintainer:** [Tanushh18](https://github.com/Tanushh18)

---

## Changelog & Version History

See [CHANGELOG.md](./CHANGELOG.md) (if available) for version history and breaking changes.

---

## Additional Resources

- **Database Schema:** See [DATABASE.md](./DATABASE.md)
- **Mobile App Docs:** See [mobile/README.md](./mobile/README.md)
- **Telegram Bot API:** [core.telegram.org/bots](https://core.telegram.org/bots)
- **Telethon Docs:** [telethon.readthedocs.io](https://telethon.readthedocs.io)
- **FastAPI:** [fastapi.tiangolo.com](https://fastapi.tiangolo.com)
- **Render Docs:** [render.com/docs](https://render.com/docs)

---

**Last Updated:** September 2026

Made with ❤️ for deal hunters everywhere.
