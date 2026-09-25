/**
 * On-phone notification timing and content. Everything here lives only in
 * this phone's AsyncStorage — nothing about habits or interests is sent to the
 * server.
 *
 * Learns two things from the last 7 days:
 *  - WHEN this person uses their phone: an hourly activity histogram, kept
 *    separately for weekdays and weekends (app opens, notification taps count
 *    for; swiped-away and ignored notifications count against).
 *  - WHAT they like: a weight per category/brand/store from views, saves,
 *    "Buy" taps, notification taps and "Not interested".
 *
 * For the first 7 days it explores: notifications go out at random hours so
 * there's something to learn from. After that it sends at the hours this
 * person is actually active, still exploring one random slot now and then.
 *
 * Routine deals from the feed are queued, never shown straight away; price
 * drops the user asked for and admin announcements still show instantly (that
 * stays in notifications.ts).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { LIVE_HOST, getBaseUrl } from './config';
import { TEMPLATES, type Need, type Template, type TimeOfDay } from './notificationTemplates';

const STORE_KEY = 'dr.smart.v1';
const DAY_MS = 24 * 60 * 60 * 1000;
const HOUR_MS = 60 * 60 * 1000;

const LEARN_DAYS = 7;
const EVENT_WINDOW_MS = 7 * DAY_MS;
const MAX_EVENTS = 3000;
const QUEUE_MAX = 200;
const QUEUE_MAX_AGE_MS = 36 * HOUR_MS;
const BASE_DAILY_CAP = 15;
const MIN_GAP_HOURS = 1; // at most one per clock hour...
const MIN_GAP_MS = 35 * 60 * 1000; // ...and never two within 35 minutes across an hour boundary
const EXPLORE_HOURS = [8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23];
const LATE_HOURS = [0, 1, 6, 7]; // only used once the data says this person is up then
const BLOCKED_HOURS = new Set([2, 3, 4, 5]); // nobody wants a 3am buzz, whatever the data says
const EXPLORE_CHANCE = 0.2;
const IGNORED_AFTER_MS = 3 * HOUR_MS;
const HOT_SCORE = 85;
const SLOT_HISTORY_MS = 14 * DAY_MS;
const DEAL_REPEAT_MS = 3 * DAY_MS;
const PICKS_REFRESH_MS = HOUR_MS;
const PICKS_WANTED = 40;
export const NOT_INTERESTED_ACTION_ID = 'not-interested';

export type SignalType = 'open' | 'view' | 'save' | 'buy' | 'share' | 'tap' | 'dismiss' | 'not_interested';

type Evt = { t: number; type: SignalType };

export type DealFields = {
  deal_id?: string | number | null;
  title?: string | null;
  deal_title?: string | null;
  image_url?: string | null;
  url?: string | null;
  expires_at?: number | string | null;
  price?: number | null;
  mrp?: number | null;
  discount_pct?: number | null;
  brand?: string | null;
  category?: string | null;
  store?: string | null;
  score?: number | null;
  kind?: string | null;
};

type Candidate = DealFields & { deal_id: string; queuedAt: number };

type Slot = {
  id: string;
  fireAt: number;
  dealId: string;
  templateId: string;
  category?: string;
  tapped?: boolean;
  dismissed?: boolean;
  instant?: boolean;
};

type State = {
  v: 1;
  installedAt: number;
  events: Evt[];
  interests: Record<string, number>;
  interestsDecayedAt: number;
  queue: Candidate[];
  slots: Slot[]; // scheduled (future) and sent (past) — past ones kept 14 days for repeat checks
  plannedDays: Record<string, number[]>; // 'YYYY-MM-DD' -> slot times picked for that day
  picksFetchedAt: number;
  lastHotBypassDay: string;
};

function emptyState(): State {
  return {
    v: 1,
    installedAt: Date.now(),
    events: [],
    interests: {},
    interestsDecayedAt: Date.now(),
    queue: [],
    slots: [],
    plannedDays: {},
    picksFetchedAt: 0,
    lastHotBypassDay: '',
  };
}

// ---------------------------------------------------------------- storage

let chain: Promise<unknown> = Promise.resolve();

/** Serialises read-modify-write so a tap and a background tick can't clobber each other. */
function withState<T>(fn: (s: State) => Promise<T> | T): Promise<T> {
  const run = chain.then(async () => {
    const s = await load();
    const out = await fn(s);
    await save(s);
    return out;
  });
  chain = run.catch(() => {});
  return run;
}

async function load(): Promise<State> {
  try {
    const raw = await AsyncStorage.getItem(STORE_KEY);
    if (!raw) return emptyState();
    const parsed = JSON.parse(raw) as State;
    return parsed?.v === 1 ? { ...emptyState(), ...parsed } : emptyState();
  } catch {
    return emptyState();
  }
}

async function save(s: State): Promise<void> {
  const now = Date.now();
  s.events = s.events.filter((e) => now - e.t < EVENT_WINDOW_MS).slice(-MAX_EVENTS);
  s.slots = s.slots.filter((x) => now - x.fireAt < SLOT_HISTORY_MS);
  s.queue = s.queue.filter((c) => now - c.queuedAt < QUEUE_MAX_AGE_MS && !expired(c, now)).slice(-QUEUE_MAX);
  const today = dayKey(new Date(now - DAY_MS));
  for (const k of Object.keys(s.plannedDays)) if (k < today) delete s.plannedDays[k];
  await AsyncStorage.setItem(STORE_KEY, JSON.stringify(s)).catch(() => {});
}

// ---------------------------------------------------------------- small helpers

function dayKey(d: Date): string {
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

function isWeekend(d: Date): boolean {
  return d.getDay() === 0 || d.getDay() === 6;
}

function toMs(v: number | string | null | undefined): number | null {
  const n = Number(v);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n < 1e12 ? n * 1000 : n;
}

function expired(c: DealFields, at: number): boolean {
  const ms = toMs(c.expires_at);
  return ms != null && ms <= at;
}

function timeOfDay(hour: number): TimeOfDay {
  if (hour >= 5 && hour < 12) return 'morning';
  if (hour >= 12 && hour < 17) return 'afternoon';
  if (hour >= 17 && hour < 21) return 'evening';
  return 'night';
}

function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

function interestKeys(d: DealFields): string[] {
  const keys: string[] = [];
  if (d.category) keys.push(`c:${d.category.toLowerCase()}`);
  if (d.brand) keys.push(`b:${d.brand.toLowerCase()}`);
  if (d.store) keys.push(`s:${d.store.toLowerCase()}`);
  return keys;
}

const SIGNAL_WEIGHT: Record<SignalType, number> = {
  open: 0,
  view: 1,
  save: 3,
  buy: 4,
  share: 2,
  tap: 2,
  dismiss: -0.5,
  not_interested: -8,
};

// Brand and store count for less than category: liking one Nike deal says
// less about Nike than liking five shoes says about footwear.
const KEY_FACTOR: Record<string, number> = { c: 1, b: 0.7, s: 0.3 };

function decayInterests(s: State, now: number) {
  const days = (now - s.interestsDecayedAt) / DAY_MS;
  if (days < 1) return;
  const f = Math.pow(0.95, days);
  for (const k of Object.keys(s.interests)) {
    s.interests[k] *= f;
    if (Math.abs(s.interests[k]) < 0.05) delete s.interests[k];
  }
  s.interestsDecayedAt = now;
}

function interestOf(s: State, d: DealFields): number {
  let total = 0;
  for (const k of interestKeys(d)) total += (s.interests[k] ?? 0) * (KEY_FACTOR[k[0]] ?? 0.5);
  return total;
}

// ---------------------------------------------------------------- recording

/** App opened / came to foreground. Feeds the "when" histogram. */
export function recordOpen(): Promise<void> {
  return withState((s) => {
    const last = s.events.length ? s.events[s.events.length - 1] : null;
    // One open per 10 minutes is plenty; quick app switches shouldn't flood it.
    if (last && last.type === 'open' && Date.now() - last.t < 10 * 60 * 1000) return;
    s.events.push({ t: Date.now(), type: 'open' });
  }).catch(() => {});
}

/** Something the user did with a deal. Feeds the "what" weights (and "when" for taps). */
export function recordDealSignal(type: SignalType, deal: DealFields | null | undefined): Promise<void> {
  return withState((s) => {
    const now = Date.now();
    decayInterests(s, now);
    if (type === 'tap' || type === 'dismiss') s.events.push({ t: now, type });
    if (!deal) return;
    const w = SIGNAL_WEIGHT[type];
    for (const k of interestKeys(deal)) {
      const next = (s.interests[k] ?? 0) + w;
      s.interests[k] = Math.max(-20, Math.min(50, next));
    }
  }).catch(() => {});
}

/** A notification we scheduled was tapped / swiped away / marked not interested. */
export function recordNotificationOutcome(
  slotId: string | undefined,
  outcome: 'tap' | 'dismiss' | 'not_interested',
  deal: DealFields | null,
): Promise<void> {
  const done = withState((s) => {
    const slot = slotId ? s.slots.find((x) => x.id === slotId) : undefined;
    if (slot && outcome === 'tap') slot.tapped = true;
    if (slot && outcome !== 'tap') slot.dismissed = true;
  }).catch(() => {});
  return done.then(() => recordDealSignal(outcome, deal));
}

// ---------------------------------------------------------------- the "when"

type Histogram = number[];

function histogram(s: State, weekend: boolean | null): { hist: Histogram; opens: number } {
  const hist: Histogram = new Array(24).fill(0);
  let opens = 0;
  const now = Date.now();
  for (const e of s.events) {
    const d = new Date(e.t);
    if (weekend !== null && isWeekend(d) !== weekend) continue;
    const h = d.getHours();
    if (e.type === 'open') {
      hist[h] += 1;
      opens++;
    } else if (e.type === 'tap') hist[h] += 3;
    else if (e.type === 'dismiss') hist[h] -= 0.5;
  }
  for (const x of s.slots) {
    if (x.instant || x.fireAt > now - IGNORED_AFTER_MS || x.tapped) continue;
    const d = new Date(x.fireAt);
    if (weekend !== null && isWeekend(d) !== weekend) continue;
    hist[d.getHours()] -= 1; // sent here and nobody cared
  }
  // Spread a little into neighbouring hours: someone active at 9 is probably around at 8:45 too.
  const smooth = hist.map((v, h) => v + 0.3 * (hist[(h + 23) % 24] + hist[(h + 1) % 24]));
  return { hist: smooth, opens };
}

function consecutiveIgnored(s: State): number {
  const now = Date.now();
  const sent = s.slots
    .filter((x) => !x.instant && x.fireAt < now - IGNORED_AFTER_MS)
    .sort((a, b) => b.fireAt - a.fireAt);
  let n = 0;
  for (const x of sent) {
    if (x.tapped) break;
    n++;
  }
  return n;
}

function dailyCap(s: State): number {
  // At 15 a day most go unopened, so only a long silence counts as "ignoring":
  // about 3 full days without a single tap eases off, rather than getting muted.
  const ignored = consecutiveIgnored(s);
  if (ignored >= 60) return 8;
  if (ignored >= 45) return 10;
  return BASE_DAILY_CAP;
}

function learning(s: State): boolean {
  return Date.now() - s.installedAt < LEARN_DAYS * DAY_MS;
}

function spacedHours(ranked: number[], count: number): number[] {
  const chosen: number[] = [];
  for (const h of ranked) {
    if (chosen.length >= count) break;
    if (chosen.every((c) => Math.min(Math.abs(c - h), 24 - Math.abs(c - h)) >= MIN_GAP_HOURS)) chosen.push(h);
  }
  return chosen.sort((a, b) => a - b);
}

function shuffle<T>(arr: T[]): T[] {
  const a = [...arr];
  for (let i = a.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [a[i], a[j]] = [a[j], a[i]];
  }
  return a;
}

/** The hours (0-23) this person should hear from us on `day`, none earlier than `fromHour`. */
function chooseHours(s: State, day: Date, count: number, fromHour: number): number[] {
  if (count <= 0) return [];
  const allowed = [...Array(24).keys()].filter((h) => !BLOCKED_HOURS.has(h) && h >= fromHour);
  const explore = EXPLORE_HOURS.filter((h) => h >= fromHour);
  if (learning(s)) return spacedHours(shuffle(explore), count);

  const weekend = isWeekend(day);
  let { hist, opens } = histogram(s, weekend);
  // Only 2 weekend days in a week: if there isn't enough weekend data yet, lean on the whole week.
  if (opens < 4) hist = histogram(s, null).hist;
  const byActivity = (hs: number[]) => shuffle(hs).sort((a, b) => hist[b] - hist[a]);
  // Busiest hours first; then the normal waking hours (least-ignored first); late/early
  // hours only where this person has actually been active.
  const active = byActivity(allowed.filter((h) => hist[h] > 0));
  const daytime = byActivity(explore.filter((h) => hist[h] <= 0));
  const late = LATE_HOURS.filter((h) => allowed.includes(h) && hist[h] > 0 && !active.includes(h));
  let hours = spacedHours([...active, ...daytime, ...late], count);
  if (Math.random() < EXPLORE_CHANCE && hours.length) {
    // Keep learning: swap one slot for a random hour it hasn't tried.
    const others = shuffle(allowed.filter((h) => !hours.includes(h)));
    const swapped = spacedHours([...hours.slice(0, -1), ...others], hours.length);
    if (swapped.length === hours.length) hours = swapped;
  }
  return hours;
}

function slotTimesFor(s: State, day: Date): number[] {
  const key = dayKey(day);
  const existing = s.plannedDays[key];
  if (existing) return existing;
  const now = new Date();
  const fromHour = key === dayKey(now) ? now.getHours() + 1 : 0;
  const hours = chooseHours(s, day, dailyCap(s), fromHour);
  const times: number[] = [];
  for (const h of hours) {
    const t = new Date(day);
    // A random minute, so it never feels like a clock going off.
    t.setHours(h, 5 + Math.floor(Math.random() * 50), Math.floor(Math.random() * 60), 0);
    let at = t.getTime();
    const prev = times[times.length - 1];
    if (prev && at - prev < MIN_GAP_MS) at = prev + MIN_GAP_MS + Math.floor(Math.random() * 10 * 60 * 1000);
    const shifted = new Date(at);
    if (dayKey(shifted) !== key || BLOCKED_HOURS.has(shifted.getHours())) continue;
    times.push(at);
  }
  s.plannedDays[key] = times;
  return times;
}

// ---------------------------------------------------------------- the "what"

function rankCandidates(s: State, at: number, exclude: Set<string>): Candidate[] {
  const blocked = (c: Candidate) =>
    interestKeys(c).some((k) => k.startsWith('c:') && (s.interests[k] ?? 0) <= -6);
  const scored = s.queue
    .filter((c) => !exclude.has(c.deal_id) && !expired(c, at + HOUR_MS) && !blocked(c))
    .map((c) => {
      let v = (Number(c.score) || 50) / 100;
      v += Math.tanh(interestOf(s, c) / 10) * 0.8;
      v += Math.min(Number(c.discount_pct) || 0, 80) / 200;
      if (c.kind === 'follow') v += 0.3;
      else if (c.kind === 'weekly_pick') v += 0.2;
      else if (c.kind === 'hot_deal' || c.kind === 'digest') v += 0.1;
      if (!c.image_url) v -= 0.4;
      return { c, v };
    })
    .sort((a, b) => b.v - a.v)
    .map((x) => x.c);
  if (scored.length > 1 && Math.random() < EXPLORE_CHANCE) {
    // Now and then show something just outside their usual taste, so it never turns into a bubble.
    const i = 1 + Math.floor(Math.random() * Math.min(9, scored.length - 1));
    [scored[0], scored[i]] = [scored[i], scored[0]];
  }
  return scored;
}

function money(v: number | null | undefined): string {
  const n = Number(v);
  return Number.isFinite(n) && n > 0 ? `₹${Math.round(n).toLocaleString('en-IN')}` : '';
}

function titleCase(v: string): string {
  return v.replace(/\b\w/g, (c) => c.toUpperCase());
}

/** "Loot: boAt Rockerz 450 @1,299" -> "boAt Rockerz 450" */
export function shortName(raw: string | null | undefined): string {
  let t = (raw ?? '').replace(/\s+/g, ' ').trim();
  t = t.replace(/^(\[[^\]]*\]\s*)?([\w%&'. ]{2,25}\s?[:|\-]\s+)/, '');
  t = t.replace(/(?:\s+(?:at|for|starting|from)|\s*@)\s*(?:rs\.?|₹)?\s*[\d,]+.*$/i, '');
  t = t.replace(/\s+[-–]\s+(?:rs\.?|₹)\s*[\d,]+.*$/i, '');
  t = t.replace(/\s*[|,-]\s*$/, '').trim();
  if (t.length > 48) t = `${t.slice(0, 46).replace(/\s+\S*$/, '')}…`;
  return t || 'A deal worth a look';
}

const DAY_NAMES = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

function has(c: Candidate, need: Need, at: number): boolean {
  switch (need) {
    case 'price':
      return Number(c.price) > 0;
    case 'mrp':
      return Number(c.mrp) > Number(c.price) && Number(c.price) > 0;
    case 'discount':
      return Number(c.discount_pct) >= 10;
    case 'brand':
      return !!c.brand;
    case 'store':
      return !!c.store && c.store !== 'unknown';
    case 'endsSoon': {
      const ms = toMs(c.expires_at);
      return ms != null && ms - at < 12 * HOUR_MS;
    }
  }
}

function chooseTemplate(s: State, c: Candidate, at: number): Template {
  const d = new Date(at);
  const tod = timeOfDay(d.getHours());
  const personal = (s.interests[`c:${(c.category ?? '').toLowerCase()}`] ?? 0) >= 5;
  const lastUsed = new Map<string, number>();
  for (const x of s.slots) lastUsed.set(x.templateId, Math.max(lastUsed.get(x.templateId) ?? 0, x.fireAt));
  const fits = TEMPLATES.filter(
    (tpl) =>
      (!tpl.time || tpl.time.includes(tod)) &&
      (!tpl.day || (tpl.day === 'weekend') === isWeekend(d)) &&
      (!tpl.weekdays || tpl.weekdays.includes(d.getDay())) &&
      (!tpl.categories || (c.category != null && tpl.categories.includes(c.category))) &&
      (!tpl.kinds || (c.kind != null && tpl.kinds.includes(c.kind))) &&
      (!tpl.personal || personal) &&
      (tpl.needs ?? []).every((n) => has(c, n, at)),
  );
  // Lines never used lately first; once those run out (15 a day gets through them),
  // the ones used longest ago.
  const unused = fits.filter((tpl) => !lastUsed.has(tpl.id));
  const oldest = [...fits].sort((a, b) => (lastUsed.get(a.id) ?? 0) - (lastUsed.get(b.id) ?? 0));
  const pool = unused.length ? unused : oldest.slice(0, Math.max(3, Math.ceil(oldest.length / 3)));
  // Specific lines (category, time, personal) beat generic ones when there's a choice.
  const specific = pool.filter((tpl) => tpl.categories || tpl.personal || tpl.kinds || tpl.time || tpl.weekdays);
  const chosen = specific.length && Math.random() < 0.7 ? pick(specific) : pick(pool);
  return chosen ?? TEMPLATES[0];
}

export function fillTemplate(tpl: Template, c: DealFields, at: number): { title: string; body: string } {
  const price = Number(c.price) || 0;
  const mrp = Number(c.mrp) || 0;
  const values: Record<string, string> = {
    name: shortName(c.deal_title || c.title),
    price: money(price),
    mrp: money(mrp),
    save: mrp > price && price > 0 ? money(mrp - price) : '',
    discount: String(Math.round(Number(c.discount_pct) || 0)),
    brand: c.brand ?? '',
    category: c.category ?? 'deals',
    store: c.store ? titleCase(c.store) : '',
    day: DAY_NAMES[new Date(at).getDay()],
  };
  const fill = (text: string) =>
    text
      .replace(/\{(\w+)\}/g, (_, k: string) => values[k] ?? '')
      .replace(/\s+([.,!?])/g, '$1')
      .replace(/\s{2,}/g, ' ')
      .trim();
  return { title: fill(tpl.title).slice(0, 80), body: fill(tpl.body).slice(0, 160) };
}

// ---------------------------------------------------------------- queue

/** Routine feed items (follows, hot deals, digests) wait here for a good moment. */
export function enqueue(items: DealFields[]): Promise<void> {
  if (!items.length) return Promise.resolve();
  return withState((s) => {
    const now = Date.now();
    for (const item of items) {
      if (item.deal_id == null || item.deal_id === '') continue;
      const id = String(item.deal_id);
      const existing = s.queue.findIndex((c) => c.deal_id === id);
      const next: Candidate = { ...item, deal_id: id, queuedAt: now };
      if (existing >= 0) s.queue[existing] = { ...s.queue[existing], ...next };
      else s.queue.push(next);
    }
  }).catch(() => {});
}

async function fetchPicks(): Promise<DealFields[]> {
  const base = (await getBaseUrl()) ?? LIVE_HOST;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 20_000);
  try {
    const res = await fetch(`${base}/api/deals?sort=best&limit=100`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!res.ok) return [];
    const body = (await res.json()) as { results?: any[] };
    return (body.results ?? [])
      .filter((d) => d?.id && d?.image_url)
      .map((d) => ({
        deal_id: String(d.id),
        title: d.title,
        image_url: d.image_url,
        url: `/?deal=${d.id}`,
        expires_at: d.expires_at,
        price: d.price,
        mrp: d.mrp,
        discount_pct: d.discount_pct,
        brand: d.brand,
        category: d.category,
        store: d.store,
        score: d.score,
        kind: 'pick',
      }));
  } catch {
    return [];
  } finally {
    clearTimeout(timer);
  }
}

// ---------------------------------------------------------------- display

type NotifeeModule = typeof import('@notifee/react-native');

export type Scheduled = {
  id: string;
  title: string;
  body: string;
  deal: Candidate;
  fireAt: number;
  channelId: string;
};

/**
 * Implemented in notifications.ts (it owns channels and the rich layout);
 * injected to avoid a circular import.
 */
type Displayer = {
  notifee: () => NotifeeModule | null;
  build: (n: Scheduled) => Promise<Parameters<NotifeeModule['default']['displayNotification']>[0]>;
  channelFor: (kind: string | undefined) => string;
};

let displayer: Displayer | null = null;

export function setDisplayer(d: Displayer) {
  displayer = d;
}

async function schedule(n: Scheduled): Promise<boolean> {
  const nf = displayer?.notifee();
  if (nf && displayer) {
    const { default: notifee, TriggerType, AlarmType } = nf;
    const notification = await displayer.build(n);
    const trigger = {
      type: TriggerType.TIMESTAMP,
      timestamp: n.fireAt,
      // Fires even in Doze, without needing the exact-alarm permission.
      alarmManager: { type: AlarmType.SET_AND_ALLOW_WHILE_IDLE },
    } as const;
    try {
      await notifee.createTriggerNotification(notification, trigger as any);
    } catch {
      await notifee.createTriggerNotification(notification, { type: TriggerType.TIMESTAMP, timestamp: n.fireAt });
    }
    return true;
  }
  await Notifications.scheduleNotificationAsync({
    identifier: n.id,
    content: {
      title: n.title,
      body: n.body,
      data: slotData(n),
    },
    trigger: {
      type: Notifications.SchedulableTriggerInputTypes.DATE,
      date: new Date(n.fireAt),
      ...(Platform.OS === 'android' ? { channelId: n.channelId } : {}),
    },
  });
  return true;
}

export function slotData(n: Scheduled): Record<string, string> {
  return {
    url: n.deal.url || `/?deal=${n.deal.deal_id}`,
    deal_id: n.deal.deal_id,
    kind: String(n.deal.kind ?? ''),
    image_url: n.deal.image_url ?? '',
    smart_slot: n.id,
    category: n.deal.category ?? '',
    brand: n.deal.brand ?? '',
    store: n.deal.store ?? '',
  };
}

async function cancel(ids: string[]): Promise<void> {
  if (!ids.length) return;
  const nf = displayer?.notifee();
  if (nf) await nf.default.cancelTriggerNotifications(ids).catch(() => {});
  for (const id of ids) await Notifications.cancelScheduledNotificationAsync(id).catch(() => {});
}

// ---------------------------------------------------------------- the tick

let ticking: Promise<void> | null = null;

/**
 * Called after every feed poll (foreground and background). Keeps today's and
 * tomorrow's slots filled with the best current deal, and lets one genuinely
 * hot deal through right away per day. Never throws.
 */
export function smartTick(): Promise<void> {
  if (!ticking) {
    ticking = doTick()
      .catch((e) => console.warn('[smart] tick failed:', (e as Error)?.message ?? e))
      .finally(() => {
        ticking = null;
      });
  }
  return ticking;
}

async function doTick(): Promise<void> {
  if (!displayer) return;
  const needPicks = await withState((s) => {
    const fresh = s.queue.filter((c) => !expired(c, Date.now() + HOUR_MS)).length;
    return fresh < PICKS_WANTED && Date.now() - s.picksFetchedAt > PICKS_REFRESH_MS;
  });
  if (needPicks) {
    const picks = await fetchPicks();
    await withState((s) => {
      s.picksFetchedAt = Date.now();
      const have = new Set(s.queue.map((c) => c.deal_id));
      for (const p of picks) if (!have.has(String(p.deal_id))) s.queue.push({ ...p, deal_id: String(p.deal_id), queuedAt: Date.now() });
    });
  }

  const toCancel: string[] = [];
  const toSchedule: Scheduled[] = [];
  await withState((s) => {
    const now = Date.now();
    decayInterests(s, now);
    const future = s.slots.filter((x) => x.fireAt > now + 60_000 && !x.instant);
    // Content is re-picked every tick so a slot never fires with an expired deal.
    toCancel.push(...future.map((x) => x.id));
    s.slots = s.slots.filter((x) => !future.includes(x));

    const recentDeals = new Set(s.slots.filter((x) => now - x.fireAt < DEAL_REPEAT_MS).map((x) => x.dealId));
    const today = new Date(now);
    const tomorrow = new Date(now + DAY_MS);
    const times = [...slotTimesFor(s, today), ...slotTimesFor(s, tomorrow)].filter((t) => t > now + 60_000);

    // One truly hot deal a day can skip the queue — but not in the middle of the night.
    const todayKey = dayKey(today);
    if (s.lastHotBypassDay !== todayKey && !BLOCKED_HOURS.has(today.getHours()) && !learning(s)) {
      const hot = rankCandidates(s, now, recentDeals).find((c) => (Number(c.score) || 0) >= HOT_SCORE && c.kind !== 'pick');
      if (hot) {
        s.lastHotBypassDay = todayKey;
        const drop = times.findIndex((t) => dayKey(new Date(t)) === todayKey);
        if (drop >= 0) times.splice(drop, 1); // it takes one of today's slots, not an extra one
        times.unshift(now + 5_000);
      }
    }

    for (const at of times.sort((a, b) => a - b)) {
      const best = rankCandidates(s, at, recentDeals)[0];
      if (!best) break;
      recentDeals.add(best.deal_id);
      const tpl = chooseTemplate(s, best, at);
      const { title, body } = fillTemplate(tpl, best, at);
      const id = `smart-${at}`;
      s.slots.push({ id, fireAt: at, dealId: best.deal_id, templateId: tpl.id, category: best.category ?? undefined });
      toSchedule.push({ id, title, body, deal: best, fireAt: at, channelId: displayer!.channelFor(best.kind ?? undefined) });
    }
  });

  await cancel(toCancel);
  for (const n of toSchedule) {
    try {
      await schedule(n);
    } catch (e) {
      console.warn('[smart] schedule failed:', (e as Error)?.message ?? e);
      await withState((s) => {
        s.slots = s.slots.filter((x) => x.id !== n.id);
      });
    }
  }
}

/** For a settings/debug screen: what the phone has learned so far. */
export async function smartSummary(): Promise<{
  learning: boolean;
  daysOfData: number;
  weekdayPeakHours: number[];
  weekendPeakHours: number[];
  topInterests: [string, number][];
  upcoming: number[];
}> {
  const s = await load();
  const peaks = (weekend: boolean) => {
    const { hist } = histogram(s, weekend);
    return [...hist.keys()].filter((h) => hist[h] > 0).sort((a, b) => hist[b] - hist[a]).slice(0, 3);
  };
  return {
    learning: learning(s),
    daysOfData: Math.floor((Date.now() - s.installedAt) / DAY_MS),
    weekdayPeakHours: peaks(false),
    weekendPeakHours: peaks(true),
    topInterests: Object.entries(s.interests).sort((a, b) => b[1] - a[1]).slice(0, 5),
    upcoming: s.slots.filter((x) => x.fireAt > Date.now()).map((x) => x.fireAt).sort((a, b) => a - b),
  };
}
