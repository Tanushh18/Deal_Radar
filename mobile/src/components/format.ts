import type { Deal, DealDetail } from '../api/types';

export const money = (n: number | null | undefined): string =>
  n == null ? '—' : '₹' + Math.round(n).toLocaleString('en-IN');

export const num = (n: number | null | undefined): string => (n || 0).toLocaleString('en-IN');

// The parser stores store/brand names lowercased ("amazon", "cuttli").
export const titleCase = (s: string | null | undefined): string =>
  String(s || '').replace(/\b[a-z]/g, (c) => c.toUpperCase());

export const storeName = (deal: Pick<Deal, 'store'>): string =>
  deal.store && deal.store !== 'unknown' ? titleCase(deal.store) : '';

export function timeAgo(ts: number | null | undefined): string {
  if (!ts) return '';
  const secs = Math.max(0, Date.now() / 1000 - ts);
  if (secs < 60) return 'just now';
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

export function greeting(): string {
  const h = new Date().getHours();
  if (h < 5) return 'Still up? Fresh deals below';
  if (h < 12) return 'Good morning';
  if (h < 17) return 'Good afternoon';
  if (h < 22) return 'Good evening';
  return 'Good night';
}

const FRESH_SECONDS = 5400;

export const isFresh = (deal: Deal): boolean =>
  !!deal.posted_at && Date.now() / 1000 - deal.posted_at < FRESH_SECONDS;

export type BadgeKind = 'low' | 'hot' | 'new';

export function dealBadge(deal: Deal): { kind: BadgeKind; label: string } | null {
  if (deal.is_lowest) return { kind: 'low', label: '🟢 LOWEST EVER' };
  if (deal.score >= 80) return { kind: 'hot', label: '🏆 GREAT DEAL' };
  if (isFresh(deal)) return { kind: 'new', label: '🆕 NEW' };
  return null;
}

export function scoreLabel(score: number): [string, string] {
  if (score >= 80) return ['Excellent deal', 'Among the strongest we have seen'];
  if (score >= 60) return ['Good deal', 'Better than most deals in this category'];
  if (score >= 40) return ['Fair deal', 'Reasonable, but not exceptional'];
  return ['Average deal', 'Worth comparing before you buy'];
}

export function dealReasons(deal: DealDetail): string[] {
  const history = deal.price_history;
  const out: string[] = [];
  if (deal.is_lowest) out.push('Lowest price we have recorded for this product');
  if (deal.discount_pct >= 10) out.push(`${deal.discount_pct}% below the quoted MRP`);
  if (history?.median && deal.price && history.points >= 3 && history.median > deal.price) {
    const below = Math.round((1 - deal.price / history.median) * 100);
    if (below >= 5) out.push(`${below}% below its typical price (${history.points} price points)`);
  }
  if (deal.repost_count > 1) out.push(`Posted in ${deal.repost_count} of the channels you track`);
  return out;
}

export const CATEGORY_ICON: Record<string, string> = {
  '': '✨',
  Electronics: '📱',
  'Women Fashion': '👗',
  'Men Fashion': '👔',
  Footwear: '👟',
  Appliances: '🔌',
  'Home & Kitchen': '🏠',
  Beauty: '💄',
  Grocery: '🛒',
  'Baby & Kids': '🧸',
  'Bags & Luggage': '🎒',
  'Books & Stationery': '📚',
  'Sports & Fitness': '🏋️',
  Other: '🎁',
};

export const categoryIcon = (name: string): string => CATEGORY_ICON[name] ?? '🏷️';

/** Splits `text` into plain / matched runs for every query token of 2+ chars. */
export function highlightParts(text: string, query: string): { text: string; match: boolean }[] {
  const tokens = query
    .trim()
    .split(/\s+/)
    .filter((t) => t.length >= 2)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'));
  if (!tokens.length || !text) return [{ text, match: false }];
  const re = new RegExp(`(${tokens.join('|')})`, 'ig');
  // With a capturing group, split() puts the captured matches at odd indices.
  return text
    .split(re)
    .map((s, i) => ({ text: s, match: i % 2 === 1 }))
    .filter((p) => p.text !== '');
}

const AVATAR_HUES = [212, 258, 168, 24, 340, 190, 45, 285];

export function avatarHues(title: string): [number, number] {
  let hash = 0;
  for (let i = 0; i < title.length; i++) hash = (hash * 31 + title.charCodeAt(i)) >>> 0;
  const hue = AVATAR_HUES[hash % AVATAR_HUES.length];
  return [hue, (hue + 28) % 360];
}

export const plural = (n: number, one: string, many = one + 's') => (n === 1 ? one : many);

/** Live "next check in Xm Ys" from a unix-seconds target — real server timing, not decorative. */
export function nextCheckIn(nextAtSeconds: number | null | undefined, nowMs: number = Date.now()): string {
  if (!nextAtSeconds) return '';
  const remainingMs = nextAtSeconds * 1000 - nowMs;
  if (remainingMs <= 0) return 'checking now';
  const h = Math.floor(remainingMs / 3600000);
  const m = Math.floor((remainingMs % 3600000) / 60000);
  const s = Math.floor((remainingMs % 60000) / 1000);
  return h > 0 ? `${h}h ${m}m` : `${m}m ${String(s).padStart(2, '0')}s`;
}

export function endsIn(ts: number | null | undefined): string {
  if (!ts) return '';
  const secs = ts - Date.now() / 1000;
  if (secs <= 0) return 'Ending now';
  if (secs < 3600) return `Ends in ${Math.max(1, Math.floor(secs / 60))}m`;
  if (secs < 86400) return `Ends in ${Math.floor(secs / 3600)}h`;
  return `Ends in ${Math.floor(secs / 86400)}d`;
}
