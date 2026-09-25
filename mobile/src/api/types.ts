export type User = {
  id?: number;
  telegram_id?: number;
  username?: string | null;
  first_name?: string | null;
};

export type SaleEvent = {
  id: string;
  name: string;
  store: string;
  starts_at: number | null;
  ends_at: number | null;
  approximate: boolean;
  hype: string;
};

export type AuthConfig = {
  telegram_configured: boolean;
  mongo_configured: boolean;
  public_mode?: boolean;
  /** Our own Telegram channel for the "join us" popup; null when not set up. */
  telegram_channel?: { url: string; username: string } | null;
};
export type SendCodeResult = { login_id: string; phone: string };
export type VerifyResult = { status: 'password_required' } | { status: 'ok'; user: User };
export type MeResult =
  | { authenticated: false }
  | { authenticated: true; user: User; tracked_channels: number };

export type Deal = {
  id: string;
  title: string;
  price: number | null;
  mrp: number | null;
  saving: number | null;
  discount_pct: number;
  currency: string;
  store: string | null;
  url: string | null;
  image_url: string | null;
  coupon: string | null;
  category: string | null;
  subcategory: string | null;
  brand: string | null;
  sizes: string | null;
  channel_title: string | null;
  posted_at: number | null;
  expires_at: number | null;
  repost_count: number;
  status: string;
  score: number;
  is_lowest: boolean;
  flags: string[];
  relevance: number | null;
  price_history_url?: string;
  price_verdict?: PriceVerdict | null;
  ai_hook?: string;
  ai_mrp_reason?: string;
};

export type VerdictLevel = 'great' | 'good' | 'fair' | 'high';
export type PriceVerdict = { level: VerdictLevel; label: string };

export type PriceStats = {
  min: number | null;
  max: number | null;
  median: number | null;
  points: number;
};

export type DealDetail = Deal & { raw_text: string | null; price_history: PriceStats | null };

export type PricePoint = { price: number; at: number };
export type DealHistory = { stats: PriceStats; points: PricePoint[] };

export type NamedCount = { name: string; count: number };
export type KeyCount = { key: string; count: number };

export type DealsPage = {
  results: Deal[];
  total: number;
  count: number;
  offset?: number;
  limit?: number;
  categories?: NamedCount[];
};

export type Suggestions = {
  query?: string;
  deals: Deal[];
  categories: NamedCount[];
  brands: KeyCount[];
  stores: KeyCount[];
};

export type Category = { name: string; subcategories: string[] };

export type Facets = {
  categories: KeyCount[];
  stores: KeyCount[];
  brands: KeyCount[];
  channels?: KeyCount[];
  price_range?: { min: number | null; max: number | null };
};

export type SortKey = 'relevance' | 'best' | 'newest' | 'discount' | 'price_low' | 'price_high' | 'ending' | 'for_you';

export type DealQuery = {
  q?: string;
  category?: string;
  subcategory?: string;
  store?: string;
  brand?: string;
  size?: string;
  min_price?: number | null;
  max_price?: number | null;
  min_discount?: number;
  has_coupon?: boolean;
  archive?: boolean;
  only_lowest?: boolean;
  all_channels?: boolean;
  sort?: SortKey;
  device_id?: string;
  limit?: number;
  offset?: number;
};

export type Sparklines = Record<string, number[]>;

export type CouponDeadResult = { reports: number; suppressed: boolean };

export type Stats = {
  deals_total: number;
  deals_live: number;
  deals_today: number;
  channels: number;
  users?: number;
  ingest?: { running: boolean; last_run: number | null; last_error?: string | null } | null;
  poll_interval_seconds?: number;
  deal_ttl_hours?: number;
};

export type Health = {
  status: string;
  uptime_seconds: number;
  checks: {
    database: string;
    telegram_configured: boolean;
    mongo?: {
      configured?: boolean;
      connected?: boolean;
      last_error?: string | null;
    };
    ingest?: {
      cycles?: number;
      last_run_ago_seconds?: number | null;
      last_error?: string | null;
      stale?: boolean;
    };
  };
};

export type TrackedChannel = {
  tg_id: number;
  username: string | null;
  title: string;
  participants: number | null;
  enabled: boolean;
  live_deals: number;
};

export type AvailableChannel = {
  tg_id: number;
  username: string | null;
  title: string;
  participants?: number | null;
  tracked: boolean;
};

export type SyncResult = { new?: number; merged?: number; fetched?: number; status?: string };

export type Watchlist = {
  id: number;
  query: string;
  filters: { category?: string; store?: string; max_price?: number | null; min_discount?: number };
  notify: boolean;
  created_at: number;
  last_notified_at: number;
  alerts_sent: number;
};

export type WatchlistInput = {
  query: string;
  category?: string;
  store?: string;
  max_price?: number | null;
  min_discount?: number;
  notify?: boolean;
};

export type AppNotification = {
  id: number;
  deal_id: string | null;
  title: string;
  body: string;
  url: string | null;
  created_at: number;
};

export type PriceAlert = {
  id: number;
  deal_id: string;
  title: string | null;
  target_price: number;
  current_price: number | null;
  triggered_at: number | null;
  triggered_price: number | null;
  image_url: string | null;
  store: string | null;
};

export type LookupResult = {
  resolved_url: string | null;
  store: string | null;
  deals: Deal[];
  archive: Deal[];
  price_stats: PriceStats | null;
  history: PricePoint[];
  price_history_url: string | null;
  verdict: PriceVerdict | null;
};

export type FollowKind = 'category' | 'brand' | 'store';
export type Follow = { id: number; kind: FollowKind; value: string; min_discount: number | null };
export type DeviceSettings = {
  device: { digest: boolean; digest_hour: number | null } | null;
  follows: Follow[];
};
