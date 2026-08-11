# 📡 DealRadar

Turns the firehose of Telegram marketplace-deal channels into a searchable,
de-duplicated catalog backed by Google Sheets.

You sign in with your own Telegram account, pick the deal channels you already
follow, and DealRadar reads them on a schedule: parsing each post into a
structured deal, collapsing the same product posted across a dozen channels
into one card, tracking price history, retiring dead links, and pinging you when
something you're watching shows up.

---

## Contents

- [How it works](#how-it-works)
- [What makes the automation effective](#what-makes-the-automation-effective)
- [Quick start (local)](#quick-start-local)
- [Google Sheets setup](#google-sheets-setup)
- [Deploying to Render (free tier)](#deploying-to-render-free-tier)
- [Keeping it awake — the ping API](#keeping-it-awake--the-ping-api)
- [API reference](#api-reference)
- [Configuration](#configuration)
- [Project layout](#project-layout)
- [Troubleshooting](#troubleshooting)
- [Security & limits](#security--limits)

---

## How it works

```
Telegram channels                 DealRadar                        You
─────────────────                 ─────────                        ───
  @loot_deals    ─┐        ┌──────────────────────┐
  @amazon_offers ─┼──────► │ 1. fetch (watermark) │
  @fk_deals      ─┤        │ 2. parse   → deal    │        ┌──────────────┐
  @fashion_loot  ─┘        │ 3. dedup   → 1 card  │───────►│  Web UI      │
                           │ 4. expire  → TTL     │        │  search      │
                           │ 5. verify  → live?   │        │  filters     │
                           │ 6. alert   → Saved   │───────►│  alerts      │
                           │ 7. flush   → Sheets  │        └──────────────┘
                           └──────────┬───────────┘
                                      ▼
                            ┌──────────────────┐
                            │  Google Sheets   │  ← durable source of truth
                            │  + SQLite cache  │  ← fast local index
                            └──────────────────┘
```

**Why MTProto, not a bot.** A Telegram bot can only read a channel where it is an
admin. Public deal channels aren't yours, so the only way to read what you already
follow is to act as your own account — that's what the phone-code login is for.

**Why two storage layers.** Google Sheets is durable and human-readable, but slow
and rate-limited (~60 writes/min). Render's free disk is wiped on every restart.
So Sheets is the source of truth, SQLite is the query index, and the cache is
rebuilt from Sheets on cold start.

---

## What makes the automation effective

These are the parts that separate this from "dump messages into a spreadsheet":

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
| 13 | **Batched Sheets writes** | One `batch_update` + one `append_rows` per cycle using an in-memory row map, instead of one API call per deal. |
| 14 | **Single-source channel reads** | If five users track the same channel, it's still fetched once globally. |

---

## Quick start (local)

**Prerequisites:** Python 3.9+ (3.11 recommended) and a Telegram account.

### 1. Get Telegram API credentials

1. Visit <https://my.telegram.org> and log in with your phone number.
2. Open **API development tools**.
3. Create an app (any title, e.g. `DealRadar`).
4. Copy the **api_id** and **api_hash**.

### 2. Set up the project

```bash
cd "/Users/t/Desktop/Tanush/Projects/Telegram Automation"

# create and activate a virtualenv
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# install dependencies
pip install --upgrade pip
pip install -r requirements.txt

# create your env file
cp .env.example .env
```

Now edit `.env` and fill in at minimum:

```bash
TELEGRAM_API_ID=1234567
TELEGRAM_API_HASH=0123456789abcdef0123456789abcdef
SECRET_KEY=<paste a long random string>
```

Generate a good `SECRET_KEY`:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### 3. Run it

```bash
# development — auto-reloads on file changes
uvicorn app.main:app --reload --port 8000

# production-style
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
```

Open <http://localhost:8000>.

> **Always `--workers 1`.** Telethon holds stateful MTProto connections and the
> ingest scheduler must not run in duplicate. More workers means double-fetching
> and Telegram rate limits.

### 4. Verify everything

```bash
# full pipeline test — parsing, dedup, search, scoring, expiry (no Telegram needed)
python -m tests.test_pipeline

# health checks against a running server
curl http://localhost:8000/api/ping
curl http://localhost:8000/api/health
curl http://localhost:8000/api/stats

# interactive API docs
open http://localhost:8000/api/docs
```

### 5. Use it

1. Sign in with your phone number → enter the code Telegram sends **in the app**
   (not SMS) → 2FA password if you have one.
2. You land on **Channels**. Tick the deal channels to track, hit **Save selection**.
   Add public channels you haven't joined via `@username`.
3. The first sync backfills ~120 recent messages per channel, then polls every 5 minutes.
4. Search for anything — `charger`, `women kurta`, `running shoes`.
5. Save a search on the **Alerts** tab to get matches in your Telegram Saved Messages.

---

## Google Sheets setup

Optional — without it the app runs SQLite-only, which is fine locally but loses
data on every Render restart. **On Render, set this up.**

### 1. Create a Google Cloud service account

1. Go to <https://console.cloud.google.com/> → create or pick a project.
2. **APIs & Services → Library** → enable **Google Sheets API** and **Google Drive API**.
3. **APIs & Services → Credentials → Create credentials → Service account**.
   - Name it `dealradar`, click through, no roles needed.
4. Open the service account → **Keys → Add key → Create new key → JSON**.
   A `.json` file downloads. Keep it secret.
5. Note the `client_email` inside — something like
   `dealradar@yourproject.iam.gserviceaccount.com`.

### 2. Create and share the spreadsheet

1. Create a new Google Sheet (any name).
2. **Share** it with the service account's `client_email`, with **Editor** access.
3. Copy the sheet id from the URL:
   `https://docs.google.com/spreadsheets/d/`**`THIS_LONG_ID`**`/edit`

### 3. Wire it up

```bash
# macOS
base64 -i ~/Downloads/dealradar-abc123.json | tr -d '\n' > sa.b64
# Linux
base64 -w0 ~/Downloads/dealradar-abc123.json > sa.b64
```

Put it in `.env`:

```bash
GOOGLE_SHEET_ID=THIS_LONG_ID
GOOGLE_SERVICE_ACCOUNT_B64=<contents of sa.b64>
```

Restart. The app creates four tabs automatically:

| Tab | Contents |
|-----|----------|
| **Deals** | Every deal — 25 columns: title, price, MRP, discount, store, category, brand, url, coupon, expiry, repost count, score, flags |
| **Channels** | Tracked channels with their fetch watermarks |
| **Users** | Signed-in accounts (id, username, login times — **never** session secrets) |
| **Watchlists** | Saved searches |

Base64 is recommended over pasting raw JSON: the private key contains newlines
that env-var UIs mangle.

---

## Deploying to Render (free tier)

### Option A — Blueprint (recommended)

1. Push this repo to GitHub.
2. Render dashboard → **New → Blueprint** → select the repo.
   `render.yaml` is detected automatically.
3. Fill in the variables marked `sync: false`:
   `TELEGRAM_API_ID`, `TELEGRAM_API_HASH`, `GOOGLE_SHEET_ID`,
   `GOOGLE_SERVICE_ACCOUNT_B64`.
4. Deploy. After the first boot, copy your URL
   (`https://dealradar-xxxx.onrender.com`) into the **`PUBLIC_URL`** env var and
   redeploy — this enables the self-ping keepalive and secure cookies.

### Option B — Manual web service

| Setting | Value |
|---------|-------|
| Runtime | Python 3 |
| Build command | `pip install -r requirements.txt` |
| Start command | `uvicorn app.main:app --host 0.0.0.0 --port $PORT --workers 1` |
| Health check path | `/api/ping` |
| Plan | Free |

Then add every variable from `.env.example` under **Environment**.

### Free-tier realities

- **512 MB RAM / 0.1 CPU.** Fine for ~40 channels. The image cache is capped at
  120 thumbnails and link probing is bounded to 8 concurrent requests.
- **Ephemeral disk.** SQLite is wiped on every restart (sleep/wake, every deploy).
  This is why **Google Sheets setup isn't optional on Render** — without it,
  restarting means starting over: no deals, no tracked channels, no alerts.
  With it, on boot the app restores deals, users, channels, channel-tracking
  links, and watchlists from Sheets automatically.
  **One thing Sheets deliberately does not restore: the Telegram session
  itself** — it's a secret and is never written there. So after a restart,
  each user needs to sign in again (same phone number), but the moment they
  do, their tracked channels and saved alerts are exactly as they left them —
  nothing needs re-selecting.
- **Sleeps after 15 minutes idle**, and cold starts take ~30s. See below.
- **750 instance-hours/month** — one always-on service fits.

---

## Keeping it awake — the ping API

`GET /api/ping` is deliberately the cheapest endpoint in the app: no database,
no Telegram, no Sheets. Just a timestamp.

```bash
curl https://your-app.onrender.com/api/ping
```
```json
{ "status": "ok", "service": "dealradar", "timestamp": 1786381421, "uptime_seconds": 3 }
```

`HEAD /api/ping` also works, for monitors that prefer an empty body.

**Two layers of keepalive:**

1. **Built-in self-ping** — set `PUBLIC_URL` and the app pings itself every 10
   minutes. This keeps an awake instance awake, but can't wake a sleeping one
   (the loop is asleep too).
2. **External pinger (do this too)** — point a free uptime monitor at
   `/api/ping` every 10 minutes:
   - [UptimeRobot](https://uptimerobot.com) — free, 5-min intervals
   - [cron-job.org](https://cron-job.org) — free, flexible
   - [Better Stack](https://betterstack.com) — free tier

   This is what actually wakes a sleeping instance.

Also available: `GET /api/health` for a deeper check (database, Sheets
connectivity, whether ingestion has gone stale).

---

## API reference

Interactive docs at **`/api/docs`**.

### System
| Method | Path | Auth | Description |
|--------|------|------|-------------|
| GET/HEAD | `/api/ping` | — | Liveness / keepalive. No DB access. |
| GET | `/api/health` | — | Deep health: DB, Sheets, ingest freshness |
| GET | `/api/stats` | — | Deal counts, channel counts, ingest state |

### Auth
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/config` | Whether the server has credentials configured |
| POST | `/api/auth/send-code` | `{phone}` → sends a Telegram login code |
| POST | `/api/auth/verify-code` | `{login_id, code}` → session, or `password_required` |
| POST | `/api/auth/verify-password` | `{login_id, password}` → 2FA step |
| GET | `/api/auth/me` | Current user |
| POST | `/api/auth/logout` | Ends the web session |
| DELETE | `/api/auth/account` | Erases the account, sessions, channels, alerts |

### Channels *(auth required)*
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/channels/available` | Broadcast channels you follow |
| GET | `/api/channels` | Channels you track |
| POST | `/api/channels/track` | `{tg_ids: [...]}` — replaces your selection |
| POST | `/api/channels/add-public` | `{username}` — resolve and join a public channel |
| DELETE | `/api/channels/{tg_id}` | Stop tracking |
| POST | `/api/channels/sync` | Run an ingest cycle now |

### Deals
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/deals` | Search. Params: `q, category, subcategory, store, brand, min_price, max_price, min_discount, only_lowest, include_expired, all_channels, sort, limit, offset` |
| GET | `/api/deals/categories` | The full taxonomy |
| GET | `/api/deals/facets` | Filter counts by store / brand / category |
| GET | `/api/deals/trending` | Most-reposted recent deals |
| GET | `/api/deals/{id}` | One deal + price stats + original post |
| GET | `/api/deals/{id}/history` | Price history points |
| GET | `/api/deals/{id}/image` | Proxied Telegram photo (memory-cached) |

`sort` accepts: `relevance`, `best`, `newest`, `discount`, `price_low`, `price_high`.

```bash
curl "https://your-app.onrender.com/api/deals?q=women%20kurta&max_price=800&min_discount=50&sort=best"
```

### Alerts *(auth required)*
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/watchlists` | Your saved searches |
| POST | `/api/watchlists` | `{query, category, store, max_price, min_discount, notify}` |
| PATCH | `/api/watchlists/{id}?notify=true` | Mute / unmute |
| DELETE | `/api/watchlists/{id}` | Delete |
| POST | `/api/watchlists/{id}/test` | Send a test alert to Saved Messages |

### Admin *(requires `X-Admin-Token` header; disabled unless `ADMIN_TOKEN` is set)*
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/admin/ingest` | Force an ingest cycle |
| POST | `/api/admin/sheets/flush` | Force a Sheets write |
| POST | `/api/admin/sheets/restore` | Rebuild SQLite from Sheets |
| POST | `/api/admin/sheets/sync-meta` | Rewrite the Channels + Users tabs |

```bash
curl -X POST https://your-app.onrender.com/api/admin/ingest -H "X-Admin-Token: $ADMIN_TOKEN"
```

---

## Configuration

| Variable | Default | Notes |
|----------|---------|-------|
| `TELEGRAM_API_ID` | — | **Required.** From my.telegram.org |
| `TELEGRAM_API_HASH` | — | **Required.** |
| `SECRET_KEY` | `dev-insecure-change-me` | **Set this.** Signs cookies and encrypts stored sessions. Changing it logs everyone out. |
| `ADMIN_TOKEN` | empty | Admin routes stay disabled while empty |
| `GOOGLE_SHEET_ID` | empty | Sheet id from its URL |
| `GOOGLE_SERVICE_ACCOUNT_B64` | empty | Base64 of the service-account JSON |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | empty | Raw JSON alternative |
| `PUBLIC_URL` | empty | Enables self-ping + secure cookies |
| `POLL_INTERVAL_SECONDS` | `300` | Seconds between ingest cycles |
| `DEAL_TTL_HOURS` | `96` | 4 days. Live links get extended beyond this. |
| `BACKFILL_LIMIT` | `120` | Messages on a channel's first fetch |
| `INCREMENTAL_LIMIT` | `60` | Messages per poll after that |
| `MAX_CHANNELS_PER_USER` | `40` | |
| `LIVENESS_CHECK` | `true` | Probe deal links |
| `LIVENESS_BATCH` | `40` | Links probed per cycle |
| `KEEPALIVE_ENABLED` | `true` | Needs `PUBLIC_URL` |
| `KEEPALIVE_SECONDS` | `600` | |
| `DB_PATH` | `data/deals.db` | SQLite cache |
| `LOG_LEVEL` | `INFO` | |

---

## Project layout

```
.
├── app/
│   ├── main.py               FastAPI app, lifespan, static mount, SPA fallback
│   ├── config.py             env-driven settings
│   ├── db.py                 SQLite schema + helpers
│   ├── auth.py               web sessions, cookies, admin guard
│   ├── routers/
│   │   ├── auth.py           phone → code → 2FA login
│   │   ├── channels.py       discovery, tracking, manual sync
│   │   ├── deals.py          search, detail, history, image proxy
│   │   ├── watchlists.py     saved searches + alerts
│   │   └── health.py         ping, health, stats, admin
│   └── services/
│       ├── telegram.py       Telethon clients, encrypted sessions
│       ├── parser.py         message → structured deal
│       ├── taxonomy.py       categories, synonyms, brands, spam patterns
│       ├── store.py          dedup, price history, scoring, expiry
│       ├── search.py         query engine + facets
│       ├── sheets.py         Google Sheets read/write
│       └── ingest.py         the automation cycle + schedulers
├── static/
│   ├── index.html
│   └── assets/{styles.css, app.js}
├── tests/test_pipeline.py    46 checks, no Telegram required
├── requirements.txt
├── render.yaml
└── .env.example
```

---

## Troubleshooting

**"Telegram API credentials are missing"** — `TELEGRAM_API_ID` / `TELEGRAM_API_HASH`
aren't set, or the `.env` isn't being read. Confirm with `curl localhost:8000/api/health`.

**The login code never arrives** — Telegram sends it *inside the Telegram app*
(Saved Messages / the Telegram service chat), not by SMS. Check there first.

**"Your Telegram session expired"** — usually means `SECRET_KEY` changed, which
makes stored sessions undecryptable. Sign in again. Keep `SECRET_KEY` stable.

**No channels listed** — DealRadar only lists *broadcast channels*. Groups and
private chats are excluded by design. Join some deal channels in Telegram, then
hit **Refresh list**.

**No deals after syncing** — some channels post only images with captions in a
format the parser can't price. Check `/api/stats` for `deals_total`; try
**Include untracked channels**; look at the logs for per-channel counts.

**Sheets errors** — confirm the sheet is shared with the service account's
`client_email` as **Editor**, and that both the Sheets API and Drive API are
enabled. `/api/health` surfaces the exact error under `checks.sheets.last_error`.

**Render deploy sleeps / is slow** — expected on free tier. Set `PUBLIC_URL` and
add an external uptime pinger against `/api/ping`.

**`FloodWaitError`** — Telegram rate-limiting. Raise `POLL_INTERVAL_SECONDS`,
lower `INCREMENTAL_LIMIT`, or track fewer channels. The app already backs off
and skips affected channels rather than crashing.

---

## Security & limits

- Your Telegram **session string is encrypted with Fernet** (key derived from
  `SECRET_KEY`) before it's written anywhere. It is never sent to Google Sheets.
- Your **login code and 2FA password are never stored** — they live in memory
  only for the seconds a sign-in takes.
- Sessions are used to **read channel history and message your own Saved
  Messages**. Nothing is posted anywhere else on your behalf.
- Web sessions are httpOnly cookies, `SameSite=Lax`, `Secure` when `PUBLIC_URL`
  is https.
- `DELETE /api/auth/account` fully erases your account and session.
- Outbound deal links carry `rel="noopener noreferrer nofollow"`.
- **Rate limiting** on everything that spends a real Telegram API call under
  the app's credentials: `/api/auth/send-code` (5 / 15 min per IP — without
  this, anyone could spam a login code to an arbitrary phone number at no
  cost to them), code/2FA verification (15 / 15 min), adding a public channel
  (20 / 10 min), and the deal-image proxy on cache misses (90 / min). In-memory,
  per-process — matches the single-worker requirement above.
- **CORS**: `allow_origins=["*"]` and credentialed requests are mutually
  exclusive per spec; the app disables credentials automatically when origins
  are wildcarded, rather than sending an invalid combination. Only matters if
  you build a separate client against this API — the bundled frontend is
  same-origin and never goes through CORS at all.
- **Link-liveness probing is SSRF-guarded**: deal links come from channel
  posts DealRadar doesn't control, so before fetching one to check it's still
  live, the destination is resolved and rejected if it's private, loopback,
  link-local, or reserved (blocks a channel post pointing a link at an
  internal address or a cloud metadata endpoint). Redirects are followed
  manually, one hop at a time, re-checked at each hop. Response bodies are
  capped at 200KB read via streaming, not truncated after a full download.

**Be aware:** automating a *user* account is against a strict reading of
Telegram's ToS if abused. Polling a handful of channels every few minutes for
personal use is normal client behaviour; scraping hundreds of channels
aggressively can get an account limited. The defaults here are deliberately
conservative — keep them that way.
