# DealRadar

Turns the firehose of Telegram marketplace-deal channels into a searchable, de-duplicated catalog backed by Turso and MongoDB.

You sign in with your own Telegram account, pick the deal channels you already follow, and DealRadar reads them on a schedule: parsing each post into a structured deal, collapsing the same product posted across a dozen channels into one card, tracking price history, retiring dead links, and pinging you when something you're watching shows up.

**Live Demo:** [DealRadar on Render](https://dealradar.onrender.com)

---

## Table of Contents

- [Features](#features)
- [Tech Stack](#tech-stack)
- [Project Structure](#project-structure)
- [Installation](#installation)
- [Usage](#usage)
- [API Documentation](#api-documentation)
- [Configuration](#configuration)
- [Dependencies](#dependencies)
- [Architecture Deep Dive](#architecture-deep-dive)
- [Contributing](#contributing)
- [Troubleshooting](#troubleshooting)
- [Security & Limits](#security--limits)
- [Deployment](#deployment)
- [FAQ](#faq)

---

## Features

DealRadar combines intelligent automation with sophisticated data processing to deliver a unique deal-discovery experience:

### Core Features

- **One-Click Deal Channel Setup** — Sign in with Telegram and select from channels you already follow. No manual URL entry needed.
- **Real-time Deal Monitoring** — Automatically fetches from deal channels every 5 minutes (configurable).
- **Smart Deduplication** — The same product posted across 10 channels collapses into a single card with unified price history.
- **Price History Tracking** — Every price change is recorded and visualized over time.
- **Deal Search & Filters** — Full-text search with category, store, brand, price, and discount filters.
- **Trending Deals** — Automatically highlights the most-reposted recent deals across channels.
- **Alerts & Watchlists** — Save searches and get notified in your Telegram Saved Messages when matches appear.
- **All-Time Low Badges** — Automatically detects and highlights products at their lowest-ever price.
- **Live Link Verification** — Verifies deal links are still active; retires dead or out-of-stock listings.
- **Fake Discount Detection** — Flags suspicious MRP values that don't match historical pricing patterns.

### Advanced Features

| # | Technique | Why it matters |
|---|-----------|----------------|
| 1 | **Watermarked incremental fetch** | Each channel stores its last-seen message id. A poll costs one small request per channel instead of re-reading history — the difference between viable and impossible on a free tier. |
| 2 | **Cross-channel dedup** | The same product hits 10 channels in 10 minutes. Deals collapse onto a canonical product key (ASIN / Flipkart pid, else a normalised-title hash) into one card. |
| 3 | **Fuzzy title fallback** | Shortlinks (`amzn.to/…`) hide the product id, so id matching alone misses duplicates. A `token_sort_ratio ≥ 88` match on normalised titles at near-identical prices catches them. |
| 4 | **Repost count as a quality signal** | A deal 6 channels independently posted is almost always real. That count feeds ranking and the "Trending" row. |
| 5 | **Affiliate-link scrubbing** | `tag`, `affid`, `utm_*`, `gclid` and ~30 more params are stripped so the same URL from two channels compares equal. |
| 6 | **Price history + all-time-low flag** | Every price change is recorded per product. A new low earns an ALL-TIME LOW badge and a ranking boost. |
| 7 | **Fake-discount detection** | An "MRP" more than 2.5× the historical median price gets flagged `suspicious_mrp` and pushed down the rankings. |
| 8 | **Link liveness probing** | Live links get their expiry extended past the base TTL; 404s and "out of stock" pages are retired early. Deals live as long as they're real, not a fixed timer. |
| 9 | **Synonym-expanded search** | "women dress" also matches gown, maxi, one-piece; "kurta" matches kurti and anarkali. 12 categories / 53 subcategories, all data-driven in `taxonomy.py`. |
| 10 | **Spam filtering** | Join-our-channel, giveaway, and refer-and-earn posts never become deals. Posts with neither a price nor a link are dropped. |
| 11 | **Composite deal score** | discount + corroboration + freshness (36h half-life) + all-time-low − penalties, recomputed each cycle so recency stays honest. |
| 12 | **Alerts via your own Saved Messages** | We already hold your session, so alerts arrive in Telegram itself — no email service, no push infra, no extra cost. |
| 13 | **Batched database writes** | One batch write + one append per cycle using an in-memory row map, instead of one API call per deal. |
| 14 | **Single-source channel reads** | If five users track the same channel, it's still fetched once globally. |
| 15 | **Hybrid cold-start optimization** | Deals older than the cache window still open from Turso; local SQLite serves hot queries and cold-start rebuild is automatic. |

---

## Tech Stack

### Backend Framework
- **FastAPI** (`==0.115.6`) — Modern async Python web framework
- **Uvicorn** (`==0.34.0`) — ASGI server with auto-reload and multi-worker support

### Telegram Integration
- **Telethon** (`==1.38.1`) — Python Telegram client for MTProto protocol
- **cryptg** (`==0.5.0.post0`) — Crypto acceleration for Telegram
- **cryptography** (`==44.0.0`) — Fernet encryption for session storage

### Data Storage
- **MongoDB** (`pymongo==4.10.1`) — User sessions, channel tracking, watchlists
- **Turso** (via httpx) — SQLite cloud for distributed data (deals, price history, alerts)
- **SQLite** (built-in) — Local cache for fast queries and cold-start

### HTTP & Networking
- **httpx** (`==0.28.1`) — Async HTTP client for API calls and link probing
- **python-dotenv** (`==1.0.1`) — Environment configuration
- **python-multipart** (`==0.0.20`) — Form/file upload handling

### Data Processing
- **Pydantic** (`==2.10.4`) — Type hints and validation
- **rapidfuzz** (`==3.11.0`) — Fuzzy string matching for deduplication
- **dnspython** (`==2.7.0`) — DNS resolution for network operations

### Development & Testing
- **pytest** — Testing framework (in requirements-dev.txt)
- **Selenium** — E2E browser testing (in requirements-dev.txt)
- **Black** — Code formatting

### Frontend
- **HTML5 / CSS3 / Vanilla JavaScript** — SPA with no build step
- **Responsive design** — Works on mobile and desktop

---

## Project Structure

```
DealRadar/
├── app/                                    # Main application package
│   ├── main.py                            # FastAPI app initialization, lifespan hooks, static mount
│   ├── config.py                          # Environment configuration & settings
│   ├── auth.py                            # Web session management, cookie handling, admin guards
│   ├── db.py                              # SQLite schema definition & query helpers
│   ├── routers/                           # API endpoint handlers
│   │   ├── auth.py                        # Phone code login, 2FA verification
│   │   ├── channels.py                    # Channel discovery, tracking, sync
│   │   ├── deals.py                       # Deal search, filtering, detail views, history
│   │   ├── watchlists.py                  # Saved searches & price alerts
│   │   ├── devices.py                     # Push notification device registration
│   │   ├── sale_events.py                 # Sale calendar & event management
│   │   └── health.py                      # Health checks, stats, admin endpoints
│   └── services/                          # Business logic & integrations
│       ├── telegram.py                    # Telethon clients, encrypted sessions
│       ├── parser.py                      # Message → structured deal parsing
│       ├── taxonomy.py                    # Categories, synonyms, brand database, filters
│       ├── store.py                       # In-memory deal store, scoring, expiry logic
│       ├── search.py                      # Query engine & faceted search
│       ├── mongo_store.py                 # MongoDB user/channel/watchlist persistence
│       ├── turso_backup.py                # Turso distributed SQLite backup
│       ├── price_store.py                 # Price history & statistics
│       ├── price_alerts.py                # Alert evaluation & notifications
│       ├── ingest.py                      # Main automation cycle & schedulers
│       ├── live.py                        # Real-time Telegram update listener
│       ├── tg_post.py                     # Hot deal posting to your channel
│       ├── links.py                       # URL parsing & parameter scrubbing
│       ├── quality.py                     # Deal quality scoring
│       ├── hot_push.py                    # FCM push notification logic
│       ├── push.py                        # Push notification handling
│       ├── fcm.py                         # Firebase Cloud Messaging integration
│       ├── devices.py                     # Device management
│       ├── ai_enrich.py                   # AI-based product enrichment
│       ├── buyhatke.py                    # Integration with BuyHatke store
│       ├── sale_events.py                 # Sale event calendar
│       ├── offers.py                      # Offer management
│       ├── activity.py                    # User activity tracking
│       ├── priority.py                    # Deal priority/ranking logic
│       ├── autopoll.py                    # Scheduled polling automation
│       ├── public_reader.py                # Public channel reading mode
│       ├── notify_auto.py                 # Automatic notifications
│       └── ratelimit.py                   # Rate limiting enforcement
├── static/                                 # Frontend assets
│   ├── index.html                         # Main SPA entry point
│   ├── manifest.json                      # PWA manifest
│   └── assets/                            # CSS, JavaScript, images
│       ├── styles.css                     # Responsive styling
│       └── app.js                         # Frontend logic
├── mobile/                                 # React Native mobile app
│   ├── app.json                           # Expo app configuration
│   ├── eas.json                           # EAS (Expo Application Services) config
│   ├── package.json                       # Node dependencies
│   ├── tsconfig.json                      # TypeScript configuration
│   ├── src/                               # React Native source code
│   └── README.md                          # Mobile-specific docs
├── tests/                                  # Test suites
│   ├── test_pipeline.py                   # End-to-end pipeline tests (no Telegram required)
│   ├── test_search.py                     # Search & filtering tests
│   ├── test_notifications.py              # Alert notification tests
│   ├── test_reparse.py                    # Parser edge cases
│   ├── test_devices.py                    # Device management tests
│   ├── test_quality.py                    # Quality scoring tests
│   ├── test_ingest_cycle.py               # Ingest automation tests
│   ├── test_turso.py                      # Turso integration tests
│   ├── test_turso_split.py                # Split database tests
│   └── e2e/                               # End-to-end browser tests
│       └── test_app.py                    # Selenium-based UI tests
├── tools/                                  # Utility scripts
│   ├── make_session.py                    # Generate Telegram session files
│   └── status.py                          # Full pipeline diagnostic
├── requirements.txt                        # Python production dependencies
├── requirements-dev.txt                    # Development & testing dependencies
├── .env.example                            # Environment configuration template
├── Makefile                                # Development commands
├── render.yaml                             # Render.com deployment configuration
├── DATABASE.md                             # Database schema documentation
└── README.md                               # This file
```

---

## Installation

### Prerequisites

- **Python 3.9+** (3.11 recommended)
- **pip** or **uv** for package management
- **Telegram account** (to sign in and access deal channels)
- **Git** (for cloning the repository)
- **Make** (optional, for convenient commands)

### Step 1: Get Telegram API Credentials

1. Visit [my.telegram.org](https://my.telegram.org) and log in with your phone number
2. Navigate to **API development tools**
3. Create an application (any name, e.g., "DealRadar")
4. Copy the **api_id** and **api_hash** — you'll need these in `.env`

### Step 2: Clone & Set Up

```bash
# Clone the repository
git clone https://github.com/yourusername/DealRadar.git
cd DealRadar

# Create a virtual environment
python3 -m venv .venv
source .venv/bin/activate          # On Windows: .venv\Scripts\activate

# Upgrade pip and install dependencies
pip install --upgrade pip
pip install -r requirements.txt
```

### Step 3: Configure Environment

```bash
# Copy the example environment file
cp .env.example .env

# Or use make shortcut (requires Makefile)
make setup
```

Edit `.env` and fill in the required variables:

```bash
# === REQUIRED ===
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
SECRET_KEY=<generate a random string>

# Generate a SECRET_KEY:
python -c "import secrets; print(secrets.token_urlsafe(48))"
# Or: make secret
```

**Minimum viable `.env`:**

```bash
TELEGRAM_API_ID=<from my.telegram.org>
TELEGRAM_API_HASH=<from my.telegram.org>
SECRET_KEY=<random string>
MONGODB_URI=              # Optional: leave empty for SQLite-only mode
PUBLIC_URL=               # Optional: leave empty for local development
```

### Step 4: Initialize Databases

```bash
# SQLite database is created automatically on first run
# MongoDB setup (optional) — see MongoDB Setup section below
```

---

## Usage

### Running Locally

```bash
# Development mode (auto-reloads on code changes)
make dev
# Or: uvicorn app.main:app --reload --port 8000

# Production mode (single worker — important!)
make start
# Or: uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1

# For Android emulator/device
make android
```

The app will be available at `http://localhost:8000`.

### Testing

```bash
# Run all test suites (no Telegram needed)
make test

# Run specific tests
python -m tests.test_pipeline
python -m tests.test_search
python -m tests.test_ingest_cycle

# E2E tests (requires running server + Chrome)
make e2e
E2E_HEADLESS=1 make e2e
```

### Common Development Tasks

```bash
# Health check (running server required)
make health

# Deal statistics
make stats

# Force an ingest cycle (requires ADMIN_TOKEN)
make sync

# Full pipeline diagnostic
make status

# Generate SECRET_KEY
make secret

# Clean Python cache
make clean

# Remove local SQLite database (careful!)
make clean-data
```

### Web Interface Workflow

1. **Sign In** → Phone number → Code (from Telegram app, not SMS) → 2FA if needed
2. **Channels** → Browse channels you follow → Select which to track → Save
3. **Search** → Type `charger`, `women kurta`, `running shoes`, etc.
4. **Deals** → Click any deal for full price history, original post, reviews
5. **Alerts** → Save searches → Get notified in Telegram Saved Messages when matches appear
6. **Admin** (if `ADMIN_TOKEN` set) → Force ingestion, refresh metadata, view logs

---

## API Documentation

Interactive API docs available at **`/api/docs`** (Swagger UI) and **`/api/redoc`** (ReDoc).

### Authentication

Most endpoints require a valid web session (created via login). Admin endpoints require an `X-Admin-Token` header.

### System Endpoints

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET/HEAD | `/api/ping` | — | Liveness check (no DB access). Used by uptime monitors. |
| GET | `/api/health` | — | Deep health check: DB connectivity, ingest freshness |
| GET | `/api/stats` | — | Deal counts, channel counts, ingest state |

**Example:**
```bash
curl https://dealradar.onrender.com/api/ping
# {"status": "ok", "service": "dealradar", "timestamp": 1726308821, "uptime_seconds": 42}
```

### Authentication Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/config` | Check if Telegram credentials are configured |
| POST | `/api/auth/send-code` | Send login code. Body: `{"phone": "+919876543210"}` |
| POST | `/api/auth/verify-code` | Verify code. Body: `{"login_id": "...", "code": "12345"}` |
| POST | `/api/auth/verify-password` | 2FA verification. Body: `{"login_id": "...", "password": "..."}` |
| GET | `/api/auth/me` | Get current user info |
| POST | `/api/auth/logout` | End web session |
| DELETE | `/api/auth/account` | Permanently delete account, all data |

### Channel Endpoints (Auth Required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels/available` | List broadcast channels you follow |
| GET | `/api/channels` | Your tracked channels |
| POST | `/api/channels/track` | Set tracked channels. Body: `{"tg_ids": [123, 456]}` |
| POST | `/api/channels/add-public` | Join & track public channel. Body: `{"username": "loot_deals"}` |
| DELETE | `/api/channels/{tg_id}` | Stop tracking a channel |
| POST | `/api/channels/sync` | Trigger ingest cycle now |

### Deal Endpoints

| Method | Path | Parameters | Description |
|--------|------|------------|-------------|
| GET | `/api/deals` | See below | Search deals |
| GET | `/api/deals/categories` | — | Full category taxonomy |
| GET | `/api/deals/facets` | `q` | Filter options: stores, brands, categories |
| GET | `/api/deals/trending` | — | Most-reposted recent deals |
| GET | `/api/deals/{id}` | — | Single deal with stats & original post |
| GET | `/api/deals/{id}/history` | — | Price history points |
| GET | `/api/deals/{id}/image` | — | Proxied Telegram photo (cached) |

**Search Parameters:**
- `q` — Query string (e.g., "women kurta")
- `category` — Filter by category (e.g., "fashion")
- `subcategory` — Filter by subcategory (e.g., "dresses")
- `store` — Filter by store (e.g., "Amazon")
- `brand` — Filter by brand (e.g., "Nike")
- `min_price` — Minimum price in rupees
- `max_price` — Maximum price in rupees
- `min_discount` — Minimum discount percentage
- `only_lowest` — Show only all-time lows (true/false)
- `include_expired` — Include expired deals (true/false)
- `all_channels` — Include deals from untracked channels (true/false)
- `sort` — `relevance`, `best`, `newest`, `discount`, `price_low`, `price_high`
- `limit` — Results per page (default 20, max 100)
- `offset` — Pagination offset

**Example:**
```bash
curl "https://dealradar.onrender.com/api/deals?q=women%20kurta&max_price=800&min_discount=50&sort=best&limit=10"
```

**Response:**
```json
{
  "deals": [
    {
      "id": "prod_abc123",
      "title": "Women Cotton Kurta",
      "price": 299,
      "mrp": 999,
      "discount": 70,
      "store": "Amazon",
      "link": "https://amazon.in/dp/B123456",
      "image_url": "/api/deals/prod_abc123/image",
      "repost_count": 5,
      "all_time_low": true,
      "score": 92,
      "created_at": "2024-09-29T10:30:00Z",
      "updated_at": "2024-09-29T15:45:00Z",
      "channels": ["@loot_deals", "@fashion_loot"]
    }
  ],
  "total": 237,
  "limit": 10,
  "offset": 0
}
```

### Watchlist (Alert) Endpoints (Auth Required)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlists` | Your saved searches |
| POST | `/api/watchlists` | Create alert. Body: `{"query": "kurta", "category": "fashion", "max_price": 500, "min_discount": 50, "notify": true}` |
| PATCH | `/api/watchlists/{id}?notify=true` | Enable/disable notifications |
| DELETE | `/api/watchlists/{id}` | Delete alert |
| POST | `/api/watchlists/{id}/test` | Send test alert to Saved Messages |

### Admin Endpoints (Requires X-Admin-Token Header)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/admin/ingest` | Force an ingest cycle |
| POST | `/api/admin/sheets/flush` | Flush data to external sheets |
| POST | `/api/admin/sheets/restore` | Restore data from backup |
| POST | `/api/admin/sheets/sync-meta` | Sync metadata |

**Example:**
```bash
curl -X POST https://dealradar.onrender.com/api/admin/ingest \
  -H "X-Admin-Token: your-admin-token-here"
```

---

## Configuration

### Environment Variables

**Required:**
| Variable | Example | Notes |
|----------|---------|-------|
| `TELEGRAM_API_ID` | `1234567` | From https://my.telegram.org |
| `TELEGRAM_API_HASH` | `0123456789abcdef...` | From https://my.telegram.org |
| `SECRET_KEY` | `CdL9xf7kpQ...` | Signs cookies, encrypts sessions. Changing it logs everyone out. |

**Recommended:**
| Variable | Default | Notes |
|----------|---------|-------|
| `ADMIN_TOKEN` | empty | Leave empty to disable admin endpoints. Set for production. |
| `PUBLIC_URL` | empty | Set to `https://your-domain.com` in production for secure cookies. |
| `MONGODB_URI` | empty | `mongodb+srv://user:pass@cluster.mongodb.net/` for persistent storage. |

**Optional (Tuning):**
| Variable | Default | Notes |
|----------|---------|-------|
| `POLL_INTERVAL_SECONDS` | `2400` | How often to fetch new deals (2400 = 40 min). Lower = more fresh, higher = less load. |
| `DEAL_TTL_HOURS` | `96` | Deals expire after 4 days. Live links get extended past this. |
| `BACKFILL_LIMIT` | `120` | Messages fetched on first channel sync. |
| `INCREMENTAL_LIMIT` | `60` | Messages fetched per poll thereafter. |
| `MAX_CHANNELS_PER_USER` | `40` | Limit channels per user. |
| `LIVENESS_CHECK` | `true` | Verify deal links are still live. |
| `LIVENESS_BATCH` | `40` | How many links to verify per cycle. |
| `KEEPALIVE_ENABLED` | `false` | Enable self-ping keepalive (requires `PUBLIC_URL`). |
| `KEEPALIVE_SECONDS` | `600` | Ping interval (600 = 10 min). |
| `DB_PATH` | `data/deals.db` | Local SQLite cache location. |
| `LOG_LEVEL` | `INFO` | Logging level: DEBUG, INFO, WARNING, ERROR. |

### MongoDB Setup (Optional)

Without MongoDB, all data is stored in SQLite locally and Turso in the cloud. **On Render, set up MongoDB** for persistent user sessions and watchlists.

#### 1. Create MongoDB Cluster

1. Register at [MongoDB Atlas](https://www.mongodb.com/cloud/atlas/register)
2. Create a free (M0) cluster
3. **Database Access** → Create user with username & password
4. **Network Access** → Add `0.0.0.0/0` (Render's IP is dynamic)
5. **Connect → Drivers** → Copy connection string

#### 2. Configure in `.env`

```bash
MONGODB_URI=mongodb+srv://username:password@cluster0.xxxxx.mongodb.net/
MONGODB_DB_NAME=dealradar
```

**Note:** URL-encode special chars in password (`@` → `%40`, `#` → `%23`, etc.)

#### Collections Created Automatically

| Collection | Contents | Index |
|---|---|---|
| **users** | Telegram account info, login times | `telegram_id` |
| **channels** | Tracked channels, fetch watermarks | `tg_id` |
| **user_channels** | User-channel mappings | `(user_telegram_id, channel_tg_id)` |
| **watchlists** | Saved searches & alerts | `(user_telegram_id, query)` |
| **settings** | Admin configuration | `key` |

---

## Dependencies

### Production Dependencies

```
fastapi==0.115.6                  # Web framework
uvicorn[standard]==0.34.0         # ASGI server
telethon==1.38.1                  # Telegram client
cryptg==0.5.0.post0               # Crypto acceleration
pymongo==4.10.1                   # MongoDB driver
dnspython==2.7.0                  # DNS resolution
cryptography==44.0.0              # Encryption utilities
httpx==0.28.1                     # Async HTTP client
python-dotenv==1.0.1              # .env file support
pydantic==2.10.4                  # Data validation
rapidfuzz==3.11.0                 # Fuzzy string matching
python-multipart==0.0.20          # Form parsing
```

### Development Dependencies

See `requirements-dev.txt`:
- `pytest` — Test runner
- `pytest-asyncio` — Async test support
- `selenium` — Browser automation for E2E tests
- `black` — Code formatter
- `flake8` — Linter

### Install Development Dependencies

```bash
pip install -r requirements-dev.txt
```

---

## Architecture Deep Dive

### How It Works

```
Telegram Channels          DealRadar                            Users
─────────────────          ─────────                            ─────
  @loot_deals ──┐     ┌─────────────────────┐
  @amazon_deals┼────►│ 1. Fetch (incremental)│
  @fashion_loot┼─────│ 2. Parse → Deal struct│        ┌──────────────┐
  @fk_deals ───┘     │ 3. Dedup cross-channel│───────►│  Web UI      │
                     │ 4. Score & rank       │        │  Search      │
                     │ 5. Verify links       │        │  Filters     │
                     │ 6. Alert watchers     │        │  Alerts      │
                     │ 7. Flush to databases │───────►│  Mobile app  │
                     └─────────┬─────────────┘        └──────────────┘
                               │
                     ┌─────────▼──────────┐
                     │  Turso (Cloud DB)  │ ← permanent deals
                     │  + MongoDB (users) │   & price history
                     │  + SQLite (cache)  │   fast local index
                     └────────────────────┘
```

### Data Storage Strategy

**Three-tier approach:**

1. **Turso** (Production/Backup) — Distributed SQLite with georeplication
   - All deals, price history (price_points)
   - Price alerts, product metadata
   - Survives server restarts
   - Split into 2 databases: main (deals/alerts) + secondary (price history)

2. **MongoDB** (User State) — Document store for user-specific data
   - User accounts & sessions
   - Tracked channels & watchlists
   - Admin settings
   - Optional; without it, data persists in Turso but user sessions don't

3. **SQLite** (Local Cache) — Fast read-through cache
   - Keeps ~15 days of deals
   - Rebuilt from Turso on cold start
   - Wiped on server restart (Render free tier)

### Ingestion Pipeline

Each cycle (every 5 min):

```python
1. Fetch
   ├─ For each channel: watermarked message fetch
   └─ New messages only (incremental)

2. Parse
   ├─ Extract: title, price, MRP, link, image
   ├─ Normalize: remove duplicates, clean URLs
   └─ Enrich: category, brand detection

3. Dedup & Score
   ├─ ASIN/store-ID match (exact)
   ├─ Fuzzy title match (88%+ similarity)
   ├─ Normalize prices → canonical deal
   └─ Score: discount + repost_count + freshness

4. Quality Checks
   ├─ Spam filter: giveaway, joins, refer-earn
   ├─ Price validation: vs historical MRP
   ├─ Link verification: live? (404? out of stock?)
   └─ Skip if suspicious

5. Store & Alert
   ├─ Update: price_points, product stats
   ├─ Check watchlists for matches
   ├─ Notify user in Saved Messages
   └─ Flush to Turso

6. Cleanup
   ├─ Expire old deals (TTL: 4 days)
   ├─ Remove dead links
   └─ Recompute rankings
```

### Real-Time Features

**Live Listener** (`app/services/live.py`):
- Subscribes to Telegram updates in background
- Processes new posts the moment they're published
- Posts "hot deals" to your notification channel instantly

**Hot Deal Definition:**
- New all-time low price
- ≥60% discount (configurable)
- ≥3 channel reposts
- Not flagged as fake discount

---

## Contributing

We welcome contributions! Areas of interest:

### Ways to Contribute

1. **Bug Reports** — Found an issue? Open a GitHub issue with:
   - Steps to reproduce
   - Expected vs actual behavior
   - Environment (OS, Python version, etc.)
   - Logs (if available)

2. **Feature Requests** — Have an idea?
   - Search existing issues first
   - Describe the problem you're solving
   - Provide use-case examples

3. **Code Contributions**

   **Setup for Development:**
   ```bash
   git clone https://github.com/yourusername/DealRadar.git
   cd DealRadar
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt -r requirements-dev.txt
   cp .env.example .env
   # Add your TELEGRAM_API_ID, TELEGRAM_API_HASH, SECRET_KEY
   ```

   **Creating a PR:**
   - Create a feature branch: `git checkout -b feature/my-feature`
   - Make changes with clear commit messages
   - Add/update tests as needed: `make test`
   - Format code: `black app/ tests/`
   - Push and open a PR with a clear description

4. **Documentation** — Improve READMEs, add docstrings, create guides

5. **Localization** — Add language support for search & category synonyms

### Code Style

- Use **Black** for formatting
- Follow **PEP 8**
- Type hints encouraged
- Docstrings for public functions/classes
- Tests required for new features

### Before Submitting

```bash
# Run tests
make test

# Format code
black app/ tests/

# Check linting
flake8 app/

# Verify no Telegram issues
python -m tests.test_pipeline
```

---

## Troubleshooting

### "Telegram API credentials are missing"

**Error:** `Telegram API credentials are missing`

**Cause:** `TELEGRAM_API_ID` or `TELEGRAM_API_HASH` not set

**Fix:**
1. Get credentials from [my.telegram.org](https://my.telegram.org)
2. Add to `.env`:
   ```bash
   TELEGRAM_API_ID=1234567
   TELEGRAM_API_HASH=0123456789abcdef...
   ```
3. Restart the server

### "Login code never arrives"

**Error:** No code received during login

**Cause:** Telegram sends the code *within the Telegram app*, not by SMS

**Fix:**
1. Open Telegram app on your phone
2. Look for a message from Telegram service chat
3. The 5-digit code is in there
4. Enter it in the browser

### "Your Telegram session expired"

**Error:** Login expired, need to sign in again

**Cause:** Typically `SECRET_KEY` changed

**Fix:**
- Keep `SECRET_KEY` stable (changing it invalidates all sessions)
- Sign in again: phone → code → 2FA
- To restore sessions, don't change `SECRET_KEY`

### "No channels listed"

**Error:** Channels tab is empty

**Cause:** DealRadar only shows *broadcast channels* (where you can't post)

**Fix:**
1. Join deal channels in Telegram app (e.g., `@loot_deals`, `@amazon_offers`)
2. Come back to DealRadar
3. Click "Refresh list" or reload the page
4. Channels should now appear

### "No deals after syncing"

**Error:** Clicked sync but no deals appeared

**Causes:**
- Channel posts don't have extractable prices
- Messages are image-only with no captions
- Parser doesn't recognize the format

**Fix:**
1. Check `/api/stats` for `ingest_state`
2. Enable debug logging: `LOG_LEVEL=DEBUG`
3. Look at logs for per-channel message counts
4. Try a different channel
5. File an issue with example posts

### "Database connection errors"

**Error:** `MongoDB connection failed` or `Turso sync error`

**Fix - MongoDB:**
```bash
# 1. Verify connection string in .env
# 2. Check MongoDB Atlas Network Access allows 0.0.0.0/0
# 3. Verify username/password are URL-encoded
# 4. Test locally: mongosh "mongodb+srv://user:pass@cluster..."
```

**Fix - Turso:**
```bash
# 1. Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN
# 2. Verify token has read+write access
# 3. Test: curl -H "Authorization: Bearer $TOKEN" $DB_URL/hello
```

### "Rate limit / FloodWaitError"

**Error:** Telegram rate-limited warnings in logs

**Causes:**
- Polling too frequently
- Too many channels tracked
- Other apps using the same account

**Fix:**
1. Increase `POLL_INTERVAL_SECONDS` (default 300 = 5 min)
2. Reduce `INCREMENTAL_LIMIT` (default 60)
3. Track fewer channels
4. Don't use the account with bots elsewhere
5. Wait 24 hours before retrying

---

## Deployment

### Option A: Render Blueprint (Recommended)

1. Push repo to GitHub
2. Go to [Render Dashboard](https://dashboard.render.com) → New → Blueprint
3. Select your DealRadar repo
4. Fill in environment variables:
   - `TELEGRAM_API_ID` (from my.telegram.org)
   - `TELEGRAM_API_HASH` (from my.telegram.org)
   - `SECRET_KEY` (generate: `python -c "import secrets; print(secrets.token_urlsafe(48))"`)
   - `MONGODB_URI` (optional, from MongoDB Atlas)
   - `TURSO_DATABASE_URL` & `TURSO_AUTH_TOKEN` (optional)
5. Deploy
6. After first boot, add `PUBLIC_URL=https://your-app.onrender.com` to env & redeploy
7. Optionally add uptime monitor for `/api/ping`

### Option B: Manual Render Deployment

| Setting | Value |
|---------|-------|
| **Runtime** | Python 3 |
| **Build command** | `pip install -r requirements.txt` |
| **Start command** | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| **Health check path** | `/api/ping` |
| **Plan** | Free (512MB RAM, ephemeral disk) |

### Important: Always Single Worker

```bash
# WRONG: multiple workers cause Telethon issues
uvicorn app.main:app --workers 4

# RIGHT: exactly 1 worker
uvicorn app.main:app --workers 1
```

### Keeping It Awake (Free Tier)

Render sleeps free instances after 15 minutes idle. Two solutions:

**1. Built-in self-ping** (set `PUBLIC_URL` in env):
```bash
PUBLIC_URL=https://your-app.onrender.com
# App pings itself every 10 min
```

**2. External uptime monitor** (free services):
- [UptimeRobot](https://uptimerobot.com) — 5-min intervals
- [cron-job.org](https://cron-job.org) — flexible schedule
- [Better Stack](https://betterstack.com) — free tier

Point to: `https://your-app.onrender.com/api/ping`

---

## FAQ

**Q: Can I use this with a bot token instead of my personal account?**

A: No. Telegram bots can only read channels where they're an admin. Since you don't admin these deal channels, you must use your own account (MTProto login).

**Q: What if my Telegram account gets limited?**

A: Keep defaults conservative (40 min poll, 60 msg/poll). Heavy automation can trigger rate limits. Normal use (monitoring ~10-20 channels) is fine.

**Q: Does this store my passwords or codes?**

A: No. Login codes and 2FA passwords are never stored. Telegram sessions are encrypted with your `SECRET_KEY` before storage.

**Q: What if I stop paying for MongoDB/Turso?**

A: SQLite cache continues working locally. Deals older than ~15 days will expire. To back them up, export before canceling.

**Q: Can I run multiple instances?**

A: Not safely with Telethon. Run exactly 1 worker per instance, or use separate Telegram accounts per instance.

**Q: Why does a deal disappear after a few days?**

A: Base TTL is 96 hours (4 days), extended for live links. Deals expire to keep the catalog fresh. Price history is permanent in Turso.

**Q: Can I export all my deals/history?**

A: Turso supports standard SQL. Query `deals`, `products`, `price_points` tables for full data export.

**Q: Is there a mobile app?**

A: Yes! See `mobile/` folder. React Native via Expo, available as APK or via EAS builds.

**Q: Can I self-host instead of Render?**

A: Yes! Docker setup coming soon. Any server with Python 3.9+, PostgreSQL/SQLite, and outbound HTTPS works.

**Q: How do I add more deal channels?**

A: 1. Follow them in Telegram. 2. Refresh channel list in DealRadar. 3. Track them. 4. Sync.

---

## License

This project is provided as-is. Check the repository for license information.

## Support

- Open an issue: [GitHub Issues](https://github.com/yourusername/DealRadar/issues)
- Discussions: [GitHub Discussions](https://github.com/yourusername/DealRadar/discussions)
- Security issues: **Do not** open a public issue; email security concerns privately

---

## Acknowledgments

Built with:
- **FastAPI** — Modern async web framework
- **Telethon** — Telegram client
- **Turso** — Distributed SQLite
- **MongoDB Atlas** — Cloud database
- **Render** — Hosting

Special thanks to the open-source community.

---

## Additional Resources

- [DATABASE.md](DATABASE.md) — Detailed schema documentation
- [mobile/README.md](mobile/README.md) — Mobile app setup
- [Telegram API Docs](https://core.telegram.org/)
- [FastAPI Documentation](https://fastapi.tiangolo.com/)
- [Telethon Docs](https://docs.telethon.dev/)

---

**Last updated:** September 29, 2024

---

## Quick Links

- [Live Demo](https://dealradar.onrender.com)
- [GitHub Repository](https://github.com/yourusername/DealRadar)
- [Issues & Feedback](https://github.com/yourusername/DealRadar/issues)
- [Discussions](https://github.com/yourusername/DealRadar/discussions)
