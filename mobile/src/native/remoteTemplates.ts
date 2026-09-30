/**
 * The notification copy, refreshed from the server (GET /api/notification-templates).
 *
 * The app ships with a built-in set (notificationTemplates.ts). The server holds
 * a much larger one that it also writes its own notifications from, so a copy
 * change reaches every install without a store release. This file keeps a copy
 * on the phone, refreshes it in the background, and turns it into the shape
 * smartNotify.ts already understands.
 *
 * Nothing here can stop a notification: no cache, an offline phone, a bad
 * response or an empty list all leave the built-in set in charge.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

import { resolveHost } from './config';
import { TEMPLATES, type Need, type Template, type TimeOfDay } from './notificationTemplates';

const KEY = 'dr.pitara.v1';
const REFRESH_EVERY_MS = 6 * 60 * 60 * 1000;
const FETCH_TIMEOUT_MS = 20_000;
/** A response smaller than this is treated as broken and ignored; the built-in set stays. */
const MIN_REMOTE = 200;

/** What fillTemplate() in smartNotify.ts can fill. A line using anything else is server-only. */
const SUPPORTED_PLACEHOLDERS = new Set(['name', 'price', 'mrp', 'save', 'discount', 'brand', 'category', 'store', 'day']);
/** Needs has() in smartNotify.ts can check. Anything else (e.g. "lowest") can't be promised on the phone. */
const SUPPORTED_NEEDS = new Set<string>(['price', 'mrp', 'discount', 'brand', 'store', 'endsSoon']);
/** Kinds of deal-carrying notification the phone writes itself. */
const PHONE_KINDS = new Set(['hot_deal', 'crazy_deal', 'nudge', 'follow', 'digest', 'weekly_pick']);
const TIMES = new Set(['morning', 'afternoon', 'evening', 'night']);

type Cache = { version: string; etag: string; fetchedAt: number; templates: Template[] };

let active: Template[] | null = null; // remote lines in use (null = none loaded)
let loading: Promise<void> | null = null;
let refreshing: Promise<void> | null = null;

const isStr = (v: unknown): v is string => typeof v === 'string' && v.length > 0;

/** Server lines -> phone templates; anything the phone can't honestly say is dropped. Pure. */
export function convert(raw: unknown): Template[] {
  if (!Array.isArray(raw)) return [];
  const out: Template[] = [];
  for (const r of raw as any[]) {
    if (!r || !isStr(r.id) || !isStr(r.title) || !isStr(r.body)) continue;
    const used = [...`${r.title} ${r.body}`.matchAll(/\{(\w+)\}/g)].map((m) => m[1]);
    if (used.some((k) => !SUPPORTED_PLACEHOLDERS.has(k))) continue;
    const needs: string[] = Array.isArray(r.needs) ? r.needs.filter(isStr) : [];
    if (needs.some((n) => !SUPPORTED_NEEDS.has(n))) continue;
    const kinds: string[] = Array.isArray(r.kinds) ? r.kinds.filter(isStr) : [];
    const own = kinds.filter((k) => PHONE_KINDS.has(k));
    if (kinds.length && !own.length) continue; // price-drop / watchlist copy: the server sends those itself
    const generic = own.length === 0 || (own.length === 1 && own[0] === 'hot_deal'); // "hot_deal only" = ordinary deal copy
    const tpl: Template = { id: `p:${r.id}`, title: r.title, body: r.body };
    if (needs.length) tpl.needs = needs as Need[];
    if (r.personal === true) tpl.personal = true;
    if (Array.isArray(r.time)) {
      const time = r.time.filter((t: unknown) => typeof t === 'string' && TIMES.has(t as string));
      if (time.length) tpl.time = time as TimeOfDay[];
    }
    if (r.day === 'weekday' || r.day === 'weekend') tpl.day = r.day;
    if (Array.isArray(r.weekdays)) {
      const days = r.weekdays.filter((d: unknown) => Number.isInteger(d) && (d as number) >= 0 && (d as number) <= 6);
      if (days.length) tpl.weekdays = days as number[];
    }
    if (isStr(r.category) && r.category !== 'Any') tpl.categories = [r.category];
    if (!generic) tpl.kinds = own;
    if (Number.isFinite(r.min_discount) && r.min_discount > 0) tpl.minDiscount = Number(r.min_discount);
    out.push(tpl);
  }
  return out;
}

async function readCache(): Promise<Cache | null> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return null;
    const c = JSON.parse(raw) as Cache;
    return c && Array.isArray(c.templates) && typeof c.version === 'string' ? c : null;
  } catch {
    return null;
  }
}

/** Reads the saved copy into memory (once). Safe to call from a headless background start. */
export function loadCached(): Promise<void> {
  if (!loading) {
    loading = readCache()
      .then((c) => {
        if (c && c.templates.length >= MIN_REMOTE) active = c.templates;
      })
      .catch(() => {});
  }
  return loading;
}

/** Background refresh: conditional GET, at most every few hours. Never throws. */
export function refresh(force = false): Promise<void> {
  if (!refreshing) {
    refreshing = doRefresh(force)
      .catch((e) => console.warn('[templates] refresh failed:', (e as Error)?.message ?? e))
      .finally(() => {
        refreshing = null;
      });
  }
  return refreshing;
}

async function doRefresh(force: boolean): Promise<void> {
  const cached = await readCache();
  if (!force && cached && Date.now() - cached.fetchedAt < REFRESH_EVERY_MS) return;
  const base = await resolveHost();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const headers: Record<string, string> = { Accept: 'application/json' };
    if (cached?.etag) headers['If-None-Match'] = cached.etag;
    const res = await fetch(`${base}/api/notification-templates`, { headers, signal: controller.signal });
    if (res.status === 304 && cached) {
      await AsyncStorage.setItem(KEY, JSON.stringify({ ...cached, fetchedAt: Date.now() })).catch(() => {});
      return;
    }
    if (!res.ok) return;
    const body = (await res.json()) as { version?: string; templates?: unknown };
    const templates = convert(body.templates);
    if (!isStr(body.version) || templates.length < MIN_REMOTE) return; // broken or partial: keep what we have
    // Built from the version, not read from the header: the server's gzip layer turns its ETag into a weak
    // one (W/"…") that it wouldn't match on the way back, so an unchanged list would download every time.
    const next: Cache = { version: body.version, etag: `"${body.version}"`, fetchedAt: Date.now(), templates };
    await AsyncStorage.setItem(KEY, JSON.stringify(next)).catch(() => {});
    active = templates; // used from the next notification it plans
  } finally {
    clearTimeout(timer);
  }
}

/** Load what's saved, then refresh in the background. Await it before planning notifications. */
export async function warmTemplates(): Promise<void> {
  await loadCached();
  void refresh();
}

/** The copy in use right now: the server's set once we have it, the built-in set until then. */
export function activeTemplates(): Template[] {
  return active && active.length >= MIN_REMOTE ? active : TEMPLATES;
}
