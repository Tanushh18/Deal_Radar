# DealRadar — Database Structure

DealRadar keeps data in **four places**. Only the local SQLite file is read by
the app and website; the other three are durable copies that refill it after a
Render restart (Render's free disk is wiped on every deploy/restart).

```
                     ┌──────────────────────────────┐
  Telegram ─ingest─► │  Local SQLite  (data/deals.db)│ ◄── app + website read ONLY this
                     └──────┬───────────────┬────────┘
               every 180s   │               │  on change / each cycle
          (background thread)│               │
                ┌───────────▼──────┐  ┌─────▼───────────────┐
                │ Turso MAIN        │  │ MongoDB Atlas        │
                │ TURSO_DATABASE_URL│  │ MONGODB_URI          │
                │ deals, products,  │  │ users, channels,     │
                │ price_alerts,     │  │ user_channels,       │
                │ devices           │  │ watchlists, settings │
                └───────────────────┘  └──────────────────────┘
                ┌───────────────────┐
                │ Turso PRICES      │   (optional — if unset, price_points
                │ TURSO_DB_02       │    lives in Turso MAIN instead)
                │ price_points      │
                └───────────────────┘
```

| Store | Env vars | Holds | Survives restart? |
|---|---|---|---|
| Local SQLite | `DB_PATH` (default `data/deals.db`) | Everything the app reads — a cache | **No** — wiped by Render |
| Turso MAIN | `TURSO_DATABASE_URL` + `TURSO_AUTH_TOKEN` | Deals, products, price alerts, devices | Yes |
| Turso PRICES | `TURSO_DB_02` + `TURSO_DB_02_AUTH_TOKEN` | Price history points | Yes |
| MongoDB Atlas | `MONGODB_URI` (+ `MONGODB_DB_NAME`, default `dealradar`) | Users, channels, tracking links, watchlists, admin settings | Yes |

Code: local schema in [app/db.py](app/db.py), Turso in
[app/services/turso_backup.py](app/services/turso_backup.py), MongoDB in
[app/services/mongo_store.py](app/services/mongo_store.py).

---

## 1. Turso MAIN (`TURSO_DATABASE_URL`)

The permanent store of every deal. Written by a background thread every
**180 s** (`BACKUP_INTERVAL_SECONDS`), up to 500 rows × 20 batches per round,
over Turso's HTTP pipeline API. Schema version 4 (table `dr_schema`).

### `deals` — every deal ever parsed
Upserted whenever it changes locally. Primary key `id`.
Indexes: `product_key`, `last_seen_at`, `expires_at`.

| Column | Meaning |
|---|---|
| `id` | Deal id (text, PK) |
| `title`, `norm_title` | Post title, and a normalised copy used for fuzzy de-duplication |
| `product_key` | Canonical product id (ASIN / Flipkart pid / title hash) — groups the same product |
| `price`, `mrp`, `discount_pct`, `currency` | Pricing |
| `store`, `url`, `clean_url`, `resolved_url` | Store and links (affiliate-scrubbed / redirect-resolved) |
| `image_url`, `coupon` | Photo and coupon code |
| `category`, `subcategory`, `brand`, `sizes` | Taxonomy |
| `channel_id`, `channel_title`, `message_id` | Where it was posted |
| `posted_at`, `first_seen_at`, `last_seen_at`, `expires_at` | Timestamps (unix seconds) |
| `repost_count`, `channels_seen` | How many channels posted it (JSON list) |
| `status` | `live` / `expired` / `dead` |
| `score`, `is_lowest`, `flags` | Ranking score 0–100, all-time-low flag, JSON flags |
| `raw_text`, `search_blob` | Original post and search text |
| `ai_hook`, `ai_mrp_reason` | AI-written one-liner and MRP explanation |

### `products` — one row per tracked product (`WITHOUT ROWID`)
`product_key` (PK), `title`, `store`, `url`, `image_url`, `category`,
`first_seen`, `last_seen`, `last_price`, `min_price`, `min_at`, `max_price` —
so "what did this cost?" is one key lookup even after the deal left the cache.

### `price_alerts` — "tell me below ₹X" (`WITHOUT ROWID`)
PK (`device_id`, `created_at`): `deal_id`, `product_key`, `title`,
`target_price`, `start_price`, `push_token`, `triggered_at`, `triggered_price`.

### `devices` — every app install (`WITHOUT ROWID`)
`device_id` (PK), `platform`, `push_token`, `digest`, `digest_hour`,
`follows` (JSON of the device's category/brand/store follows), `created_at`,
`last_seen_at`. Without this, a restart couldn't push to anyone.

### `dr_schema`
`version` — schema version marker. **Warning:** if this table is missing, the
code treats Turso as the old v1 layout and drops the tables above once.

## 2. Turso PRICES (`TURSO_DB_02`, optional)

### `price_points` — every observed price change (`WITHOUT ROWID`)
PK (`product_key`, `seen_at`), `price`. The fastest-growing table, so it can
live in its own database. If `TURSO_DB_02` is not set it stays in Turso MAIN;
setting it later triggers a one-time, resumable move.

## 3. MongoDB Atlas (`MONGODB_URI`)

Replaced Google Sheets for the small account/config state. **Does not store
deals.**

| Collection | Unique key | Fields |
|---|---|---|
| `users` | `telegram_id` | `phone`, `username`, `first_name`, `created_at`, `last_login_at` |
| `channels` | `tg_id` | `username`, `title`, `participants`, `last_message_id` (read watermark), `last_fetched_at`, `active`, `source_user_telegram_id` |
| `user_channels` | (`user_telegram_id`, `channel_tg_id`) | `enabled`, `added_at` |
| `watchlists` | (`user_telegram_id`, `query`) | `category`, `store`, `max_price`, `min_discount`, `notify`, `created_at` |
| `settings` | `key` | `value`, `updated_at` — admin priority rule, sale-events calendar, poll interval |

## 4. Local SQLite (`data/deals.db`) — the cache the app reads

| Table | Purpose | Backed up to |
|---|---|---|
| `deals` | All columns above, plus `dirty` and `turso_dirty` change flags | Turso MAIN |
| `price_history` | `product_key`, `price`, `store`, `seen_at`, `turso_synced` | Turso PRICES (`price_points`) |
| `price_alerts` | Visitor price alerts (+ `turso_dirty`) | Turso MAIN |
| `devices` | App installs: push token, digest, `smart_schedule`, cooldown timestamps (+ `turso_dirty`) | Turso MAIN |
| `device_follows` | `device_id`, `kind` (category/brand/store), `value`, `min_discount` | Turso MAIN (inside `devices.follows`) |
| `device_notifications` | Per-device notification feed the app polls | — (14-day feed) |
| `broadcasts` | Shared feed items (admin/hot-deal) every device polls | — (14-day feed) |
| `channels` | Tracked Telegram channels + read watermark | MongoDB |
| `users`, `user_channels`, `watchlists` | Signed-in users and what they track | MongoDB |
| `meta` | Key/value settings | MongoDB (`settings`) |
| `push_log` | Hot-deal pushes sent (to avoid repeats within 48h) | — |
| `push_tokens`, `notifications`, `notified` | Older signed-in push/alert flow | — |
| `coupon_reports` | "Coupon didn't work" reports | — |
| `ai_usage` | Daily AI request/token counters | — |
| `turso_outbox` | Queued deletes/renames to replay on Turso | — |

`dirty` → triggers bump `turso_dirty` on every deal write; the upload thread
sends rows with `turso_dirty > 0` and clears the flag only if it hasn't changed
mid-upload, so no edit is lost.

## Not stored anywhere: BuyHatke price history

[app/services/buyhatke.py](app/services/buyhatke.py) reads BuyHatke's public
product pages (never its `/api/`) and keeps the result **in server memory
only** for 3 days (12 h when BuyHatke has no data), then drops it. It is never
written to SQLite, Turso or MongoDB; a restart just re-fetches on first view.
It's merged into `/api/deals/{id}/history` (`"source": "buyhatke"`) and shown
on the website with a "History fetched" tick. Live status: `/api/health` →
`checks.buyhatke`.

---

## After a Render restart (startup order, [app/main.py](app/main.py))

1. **Turso restore** — `turso_backup.restore(LOCAL_CACHE_DAYS=15)` copies back
   deals seen in the last 15 days plus every still-live deal, price alerts
   (30 days) and devices (120 days). Skipped silently if `TURSO_DATABASE_URL`
   or `TURSO_AUTH_TOKEN` is missing.
2. **MongoDB restore** — settings, then users → channels → tracking links →
   watchlists (order matters). Channel read watermarks come back here.
3. Re-parse and quality sweep over the restored deals.
4. Ingest resumes from the restored channel watermarks — it only reads
   **new** posts, so it never re-fetches deals lost from the cache.

⚠️ Because of step 4, if step 1 restores nothing, the app stays nearly empty
until channels post new deals. Check the Render startup log for
`Restored from Turso: N deals, …` — no such line means the two Turso env vars
aren't set on the Render service.
