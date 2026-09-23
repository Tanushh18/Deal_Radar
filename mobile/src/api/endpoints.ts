import { request } from './client';
import type {
  AppNotification,
  AuthConfig,
  AvailableChannel,
  Category,
  DealDetail,
  DealHistory,
  DealQuery,
  DealsPage,
  Deal,
  Facets,
  Health,
  MeResult,
  SendCodeResult,
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
    max_price: q.max_price ?? undefined,
    min_discount: q.min_discount || undefined,
    only_lowest: q.only_lowest || undefined,
    all_channels: q.all_channels || undefined,
    sort: q.sort ?? 'newest',
    limit: q.limit ?? 30,
    offset: q.offset ?? 0,
  };
}

export const deals = {
  list: (q: DealQuery, o: Sig = {}) =>
    request<DealsPage>('/api/deals', { query: dealParams(q), signal: o.signal }),
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
