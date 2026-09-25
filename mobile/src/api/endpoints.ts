import { request } from './client';
import type {
  AppNotification,
  AuthConfig,
  AvailableChannel,
  Category,
  CouponDeadResult,
  DealDetail,
  DealHistory,
  DealQuery,
  DealsPage,
  Deal,
  Facets,
  DeviceSettings,
  Follow,
  FollowKind,
  Health,
  LookupResult,
  PriceAlert,
  SaleEvent,
  MeResult,
  SendCodeResult,
  Sparklines,
  Stats,
  Suggestions,
  SyncResult,
  TrackedChannel,
  User,
  VerifyResult,
  Watchlist,
  WatchlistInput,
} from './types';

type Sig = { signal?: AbortSignal };

const enc = encodeURIComponent;

export const saleEvents = {
  list: (o: Sig = {}) => request<{ events: SaleEvent[] }>('/api/sale-events', o),
};

export const auth = {
  config: (o: Sig = {}) => request<AuthConfig>('/api/auth/config', o),
  sendCode: (phone: string) =>
    request<SendCodeResult>('/api/auth/send-code', { method: 'POST', body: { phone }, timeoutMs: 40_000 }),
  verifyCode: (login_id: string, code: string) =>
    request<VerifyResult>('/api/auth/verify-code', {
      method: 'POST',
      body: { login_id, code },
      timeoutMs: 40_000,
    }),
  verifyPassword: (login_id: string, password: string) =>
    request<{ status: 'ok'; user: User }>('/api/auth/verify-password', {
      method: 'POST',
      body: { login_id, password },
      timeoutMs: 40_000,
    }),
  me: (o: Sig = {}) => request<MeResult>('/api/auth/me', o),
  logout: () => request<{ status: string }>('/api/auth/logout', { method: 'POST' }),
};

function dealParams(q: DealQuery) {
  return {
    q: q.q?.trim() || undefined,
    category: q.category,
    subcategory: q.subcategory,
    store: q.store,
    brand: q.brand,
    size: q.size || undefined,
    min_price: q.min_price ?? undefined,
    max_price: q.max_price ?? undefined,
    min_discount: q.min_discount || undefined,
    has_coupon: q.has_coupon || undefined,
    archive: q.archive || undefined,
    only_lowest: q.only_lowest || undefined,
    all_channels: q.all_channels || undefined,
    sort: q.sort ?? 'newest',
    device_id: q.sort === 'for_you' ? q.device_id : undefined,
    limit: q.limit ?? 30,
    offset: q.offset ?? 0,
  };
}

export const deals = {
  list: (q: DealQuery, o: Sig = {}) =>
    request<DealsPage>('/api/deals', { query: dealParams(q), signal: o.signal }),
  sparklines: (ids: string[], o: Sig = {}) =>
    ids.length
      ? request<{ sparklines: Sparklines }>('/api/deals/sparklines', { query: { ids: ids.join(',') }, signal: o.signal })
      : Promise.resolve({ sparklines: {} }),
  reportCouponDead: (id: string, device_id: string) =>
    request<CouponDeadResult>(`/api/deals/${enc(id)}/coupon-dead`, { method: 'POST', query: { device_id } }),
  suggest: (q: string, limit = 6, allChannels = false, o: Sig = {}) =>
    request<Suggestions>('/api/deals/suggest', {
      query: { q, limit, all_channels: allChannels || undefined },
      signal: o.signal,
      timeoutMs: 10_000,
    }),
  categories: (o: Sig = {}) => request<{ categories: Category[] }>('/api/deals/categories', o),
  facets: (allChannels = false, o: Sig = {}) =>
    request<Facets>('/api/deals/facets', { query: { all_channels: allChannels || undefined }, signal: o.signal }),
  trending: (limit = 12, o: Sig = {}) =>
    request<{ results: Deal[] }>('/api/deals/trending', { query: { limit }, signal: o.signal }),
  get: (id: string, o: Sig = {}) => request<DealDetail>(`/api/deals/${enc(id)}`, o),
  history: (id: string, o: Sig = {}) => request<DealHistory>(`/api/deals/${enc(id)}/history`, o),
  similar: (id: string, limit = 8, o: Sig = {}) =>
    request<{ results: Deal[] }>(`/api/deals/${enc(id)}/similar`, { query: { limit }, signal: o.signal }),
  lookup: (url: string, o: Sig = {}) =>
    request<LookupResult>('/api/lookup', { query: { url }, signal: o.signal, timeoutMs: 45_000 }),
};

export const priceAlerts = {
  list: (device_id: string, o: Sig = {}) =>
    request<{ alerts: PriceAlert[] }>('/api/price-alerts', { query: { device_id }, signal: o.signal }),
  create: (input: { device_id: string; deal_id: string; target_price: number; push_token?: string | null }) =>
    request<{ status: string; alert: PriceAlert }>('/api/price-alerts', {
      method: 'POST',
      body: { ...input, push_token: input.push_token ?? '' },
    }),
  remove: (id: number, device_id: string) =>
    request<{ status: string }>(`/api/price-alerts/${id}`, { method: 'DELETE', query: { device_id } }),
};

export const devices = {
  settings: (device_id: string, o: Sig = {}) =>
    request<DeviceSettings>('/api/devices/settings', { query: { device_id }, signal: o.signal }),
  register: (input: { device_id: string; digest?: boolean; digest_hour?: number; push_token?: string | null }) =>
    request<{ status: string }>('/api/devices/register', {
      method: 'POST',
      body: { platform: 'android', ...input, push_token: input.push_token ?? undefined },
    }),
  follow: (input: { device_id: string; kind: FollowKind; value: string; min_discount?: number }) =>
    request<{ follow: Follow }>('/api/devices/follows', { method: 'POST', body: input }),
  unfollow: (id: number, device_id: string) =>
    request<{ status: string }>(`/api/devices/follows/${id}`, { method: 'DELETE', query: { device_id } }),
};

export const system = {
  stats: (o: Sig = {}) => request<Stats>('/api/stats', o),
  health: (o: Sig = {}) => request<Health>('/api/health', o),
};

export const channels = {
  mine: (o: Sig = {}) => request<{ channels: TrackedChannel[] }>('/api/channels', o),
  available: (o: Sig = {}) =>
    request<{ channels: AvailableChannel[]; tracked_count: number }>('/api/channels/available', {
      ...o,
      timeoutMs: 45_000,
    }),
  track: (tg_ids: number[]) =>
    request<{ status: string; tracked: number }>('/api/channels/track', {
      method: 'POST',
      body: { tg_ids },
      timeoutMs: 45_000,
    }),
  addPublic: (username: string) =>
    request<{ status: string; channel: AvailableChannel }>('/api/channels/add-public', {
      method: 'POST',
      body: { username },
      timeoutMs: 45_000,
    }),
  // An ingest cycle reads every tracked channel; on a cold free-tier host that is slow.
  sync: () => request<SyncResult>('/api/channels/sync', { method: 'POST', timeoutMs: 180_000 }),
};

export const watchlists = {
  list: (o: Sig = {}) => request<{ watchlists: Watchlist[] }>('/api/watchlists', o),
  create: (input: WatchlistInput) =>
    request<{ status: string; id: number }>('/api/watchlists', {
      method: 'POST',
      body: { notify: true, ...input },
    }),
  setNotify: (id: number, notify: boolean) =>
    request<{ status: string }>(`/api/watchlists/${id}`, { method: 'PATCH', query: { notify: String(notify) } }),
  remove: (id: number) => request<{ status: string }>(`/api/watchlists/${id}`, { method: 'DELETE' }),
  test: (id: number) =>
    request<{ status: string }>(`/api/watchlists/${id}/test`, { method: 'POST', timeoutMs: 40_000 }),
};

export const notifications = {
  list: (since = 0, limit = 20, o: Sig = {}) =>
    request<{ notifications: AppNotification[]; now: number }>('/api/notifications', {
      query: { since: since || undefined, limit },
      signal: o.signal,
    }),
  test: () =>
    request<{ status: string; sent?: number; stored?: boolean }>('/api/notifications/test', {
      method: 'POST',
      timeoutMs: 30_000,
    }),
};
