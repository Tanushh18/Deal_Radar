export type User = {
  id?: number;
  telegram_id?: number;
  username?: string | null;
  first_name?: string | null;
};

export type AuthConfig = { telegram_configured: boolean; sheets_configured: boolean; public_mode?: boolean };
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
};

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

export type SortKey = 'relevance' | 'best' | 'newest' | 'discount' | 'price_low' | 'price_high';

export type DealQuery = {
  q?: string;
  category?: string;
  subcategory?: string;
  store?: string;
  brand?: string;
  max_price?: number | null;
  min_discount?: number;
  only_lowest?: boolean;
  all_channels?: boolean;
  sort?: SortKey;
  limit?: number;
  offset?: number;
};

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
    sheets?: {
      configured?: boolean;
      connected?: boolean;
      rows_tracked?: number;
      last_flush?: string | null;
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
