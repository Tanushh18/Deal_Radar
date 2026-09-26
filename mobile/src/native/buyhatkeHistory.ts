/**
 * BuyHatke's price history, read on this phone.
 *
 * BuyHatke refuses cloud servers (our backend gets HTTP 403) but serves
 * ordinary connections, so the app reads the same public page a user would
 * open from the "Price history & stock" button — only when a deal is opened.
 * The server supplies the exact page (`history_lookup_url`); BuyHatke's
 * /api/ (disallowed in its robots.txt) is never touched.
 *
 * Kept only on this phone, for 3 days (12 h when BuyHatke has no data), then
 * dropped and re-read the next time the product is opened. Never sent to our
 * server. One request at a time, a few seconds apart, and an hour's pause if
 * BuyHatke ever pushes back.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import type { PricePoint } from '../api/types';

const CACHE_KEY = 'dr.bh.v1';
const PAUSE_KEY = 'dr.bh.pausedUntil';
const HIT_TTL_MS = 3 * 24 * 60 * 60 * 1000;
const MISS_TTL_MS = 12 * 60 * 60 * 1000;
const MAX_PRODUCTS = 150;
const MAX_POINTS = 250;
const MIN_GAP_MS = 2500;
const PAUSE_MS = 60 * 60 * 1000;
const TIMEOUT_MS = 15_000;
const UA =
  'Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Mobile Safari/537.36';
const ENTRY = /\{from:"([^"]+)",to:"([^"]+)",price:([\d.]+)\}/g;
const BLOCK_MARKERS = ['cf-chl', 'just a moment...', 'attention required'];

export type BuyHatkeHistory = {
  points: PricePoint[];
  lowest: number;
  highest: number;
  since: number; // unix seconds
  pageUrl: string;
};

type Entry = { exp: number; p: number[] | null; url: string };
type Cache = Record<string, Entry>;

// ---------------------------------------------------------------- parsing

/** "2024-11-09 01:10:26" in IST -> unix seconds. */
function istSeconds(stamp: string): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})$/.exec(stamp);
  if (!m) return null;
  const utc = Date.UTC(+m[1], +m[2] - 1, +m[3], +m[4], +m[5], +m[6]);
  return Math.round(utc / 1000) - (5 * 3600 + 30 * 60);
}

export function parseHistory(html: string): PricePoint[] {
  const byTime = new Map<number, number>();
  ENTRY.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = ENTRY.exec(html))) {
    const price = Number(m[3]);
    if (!(price > 0)) continue;
    for (const stamp of [m[1], m[2]]) {
      const at = istSeconds(stamp);
      if (at != null) byTime.set(at, price);
    }
  }
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([at, price]) => ({ at, price }));
}

function downsample(points: PricePoint[]): PricePoint[] {
  if (points.length <= MAX_POINTS) return points;
  const keep = new Set<number>();
  const step = points.length / MAX_POINTS;
  for (let i = 0; i < MAX_POINTS; i++) keep.add(Math.floor(i * step));
  let lo = 0;
  let hi = 0;
  points.forEach((p, i) => {
    if (p.price < points[lo].price) lo = i;
    if (p.price > points[hi].price) hi = i;
  });
  // The lowest and highest prices are the whole point — never drop them.
  keep.add(lo).add(hi).add(points.length - 1);
  return [...keep].sort((a, b) => a - b).map((i) => points[i]);
}

function summarize(points: PricePoint[], pageUrl: string): BuyHatkeHistory {
  const prices = points.map((p) => p.price);
  return { points, lowest: Math.min(...prices), highest: Math.max(...prices), since: points[0].at, pageUrl };
}

// ---------------------------------------------------------------- storage (this phone only)

async function readCache(): Promise<Cache> {
  try {
    const raw = await AsyncStorage.getItem(CACHE_KEY);
    const cache = raw ? (JSON.parse(raw) as Cache) : {};
    const now = Date.now();
    for (const k of Object.keys(cache)) if (cache[k].exp <= now) delete cache[k]; // auto-delete after its time
    return cache;
  } catch {
    return {};
  }
}

let writeChain: Promise<void> = Promise.resolve();

function remember(key: string, entry: Entry): Promise<void> {
  writeChain = writeChain.then(async () => {
    const cache = await readCache();
    cache[key] = entry;
    const keys = Object.keys(cache);
    if (keys.length > MAX_PRODUCTS) {
      keys.sort((a, b) => cache[a].exp - cache[b].exp);
      for (const k of keys.slice(0, keys.length - MAX_PRODUCTS)) delete cache[k];
    }
    await AsyncStorage.setItem(CACHE_KEY, JSON.stringify(cache)).catch(() => {});
  });
  return writeChain;
}

function unpack(entry: Entry): BuyHatkeHistory | null {
  if (!entry.p || entry.p.length < 4) return null;
  const points: PricePoint[] = [];
  for (let i = 0; i < entry.p.length; i += 2) points.push({ at: entry.p[i], price: entry.p[i + 1] });
  return summarize(points, entry.url);
}

/** Already on this phone? `undefined` = not looked up yet; `null` = BuyHatke has nothing. */
export async function cachedHistory(lookupUrl: string): Promise<BuyHatkeHistory | null | undefined> {
  const hit = (await readCache())[lookupUrl];
  return hit ? unpack(hit) : undefined;
}

// ---------------------------------------------------------------- fetching

let queue: Promise<unknown> = Promise.resolve();
let lastRequest = 0;

/**
 * found   — history (from this phone's cache or read now)
 * none    — BuyHatke has no history for this product
 * blocked — the quick request was challenged; the caller can retry in a
 *           real browser engine (see HiddenPageReader) and hand the page to
 *           ingestPage()
 * failed  — offline, timed out, or paused after repeated pushback
 */
export type HistoryResult =
  | { kind: 'found'; history: BuyHatkeHistory }
  | { kind: 'none' | 'blocked' | 'failed' };

const inflightResults = new Map<string, Promise<HistoryResult>>();

export function isLookupUrl(url: string | null | undefined): url is string {
  return !!url && /^https:\/\/buyhatke\.com\//.test(url);
}

/** BuyHatke's history for this product, saying why when there isn't any. */
export function loadHistory(lookupUrl: string | null | undefined): Promise<HistoryResult> {
  if (!isLookupUrl(lookupUrl)) return Promise.resolve({ kind: 'none' });
  const running = inflightResults.get(lookupUrl);
  if (running) return running;
  const task = (async (): Promise<HistoryResult> => {
    const cached = await cachedHistory(lookupUrl);
    if (cached !== undefined) return cached ? { kind: 'found', history: cached } : { kind: 'none' };
    const pausedUntil = Number((await AsyncStorage.getItem(PAUSE_KEY).catch(() => null)) || 0);
    if (Date.now() < pausedUntil) return { kind: 'failed' };
    // One at a time, spaced out, however many deals are opened.
    const turn = queue.then(async () => {
      const wait = MIN_GAP_MS - (Date.now() - lastRequest);
      if (wait > 0) await new Promise((r) => setTimeout(r, wait));
      lastRequest = Date.now();
      return fetchPage(lookupUrl);
    });
    queue = turn.catch(() => {});
    return turn;
  })().finally(() => inflightResults.delete(lookupUrl));
  inflightResults.set(lookupUrl, task);
  return task;
}

export function isChallengePage(html: string): boolean {
  const head = html.slice(0, 5000).toLowerCase();
  return BLOCK_MARKERS.some((m) => head.includes(m));
}

/**
 * A BuyHatke page read some other way (the in-app browser fallback). Stores
 * and returns its history exactly like a normal fetch would.
 */
export async function ingestPage(lookupUrl: string, html: string, pageUrl: string): Promise<HistoryResult> {
  if (isChallengePage(html)) return { kind: 'blocked' };
  const url = /^https:\/\/(www\.)?buyhatke\.com\//.test(pageUrl) ? pageUrl : lookupUrl;
  const points = downsample(parseHistory(html));
  if (points.length < 2) {
    await remember(lookupUrl, { exp: Date.now() + MISS_TTL_MS, p: null, url });
    return { kind: 'none' };
  }
  await remember(lookupUrl, { exp: Date.now() + HIT_TTL_MS, p: points.flatMap((pt) => [pt.at, pt.price]), url });
  return { kind: 'found', history: summarize(points, url) };
}

/** Both the quick request and the in-app browser failed: back off for an hour. */
export function pauseAfterPushback(): Promise<void> {
  return AsyncStorage.setItem(PAUSE_KEY, String(Date.now() + PAUSE_MS)).catch(() => {});
}

async function fetchPage(lookupUrl: string): Promise<HistoryResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), TIMEOUT_MS);
  let res: Response;
  let html = '';
  try {
    res = await fetch(lookupUrl, { headers: { 'User-Agent': UA, Accept: 'text/html' }, signal: controller.signal });
    html = await res.text().catch(() => '');
  } catch {
    return { kind: 'failed' }; // offline or slow: not cached, tried again next time
  } finally {
    clearTimeout(timer);
  }
  // A challenge here isn't the end: a real browser engine usually passes it.
  if ([403, 429, 503].includes(res.status) || isChallengePage(html)) return { kind: 'blocked' };
  if (res.status !== 200) return { kind: 'failed' };
  return ingestPage(lookupUrl, html, res.url || lookupUrl);
}

/** Our points plus BuyHatke's, oldest first; ours win on an exact tie. */
export function mergeHistory(own: PricePoint[], theirs: PricePoint[]): PricePoint[] {
  const byTime = new Map<number, PricePoint>();
  for (const p of theirs) byTime.set(Math.round(p.at), p);
  for (const p of own) byTime.set(Math.round(p.at), p);
  return [...byTime.entries()].sort((a, b) => a[0] - b[0]).map(([, p]) => p);
}
