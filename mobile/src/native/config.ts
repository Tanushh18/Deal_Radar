/**
 * Where the DealRadar backend lives, persisted across launches.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export const LIVE_HOST: string = 'https://dealradar.ggnhome.com';
// The old Render URL, kept only so a phone that explicitly saved it (via the
// Setup screen's "Live server" chip) migrates to the new domain automatically —
// see getBaseUrl(). Render keeps this address alive, so it's a safe fallback,
// but every fresh install and OTA now points at LIVE_HOST above.
const RETIRED_HOST = 'https://dealradar-0oza.onrender.com';
export const EMULATOR_HOST = 'http://10.0.2.2:8765';

export const COLORS = {
  bg: '#14120e',
  bgSunk: '#0f0d0a',
  surface: '#1c1914',
  surface2: '#24201a',
  border: '#2d2922',
  borderStrong: '#3b362d',
  text: '#f1ece2',
  text2: '#b4ad9f',
  text3: '#857f73',
  accent: '#f1ece2',
  accentStrong: '#ffffff',
  accentText: '#14120e',
  hot: '#ff7a45',
  good: '#52c48c',
};

const KEYS = {
  baseUrl: 'dr.baseUrl',
  signedIn: 'dr.signedIn',
  lastSeen: 'dr.feed.lastSeen',
  seenIds: 'dr.feed.seenIds',
  pushToken: 'dr.pushToken',
};
export { KEYS as STORAGE_KEYS };

export async function getBaseUrl(): Promise<string | null> {
  try {
    const v = await AsyncStorage.getItem(KEYS.baseUrl);
    if (!v || !v.trim()) return null;
    // Migration: a phone that explicitly saved the old Render URL would
    // otherwise keep using it forever, since an explicit save always wins
    // over LIVE_HOST. Retire it silently onto the new domain.
    if (v.replace(/\/+$/, '') === RETIRED_HOST) {
      await AsyncStorage.removeItem(KEYS.baseUrl).catch(() => {});
      return null;
    }
    // Older builds saved the default host as if it were a manual choice, which
    // would pin the app to it and ignore the server registry. A saved default
    // is not a choice, so treat it as unset.
    if (v.replace(/\/+$/, '') === LIVE_HOST) {
      await AsyncStorage.removeItem(KEYS.baseUrl).catch(() => {});
      return null;
    }
    return v;
  } catch {
    return null;
  }
}

export async function saveBaseUrl(url: string): Promise<string> {
  const normalized = normalize(url);
  await AsyncStorage.setItem(KEYS.baseUrl, normalized);
  return normalized;
}

/**
 * Extra hosts worth trying, in order, if LIVE_HOST can't be reached at all —
 * some phones' DNS-level filtering or on-device ad/tracker blockers block
 * specific hostnames outright, and a second real host routes around that
 * without needing the user to do anything. RETIRED_HOST is deliberately NOT
 * here: its own routing was disabled once the custom domain took over, so it
 * now answers every request with a blanket 404 rather than failing to
 * connect — a "success" a naive retry would happily latch onto forever (see
 * the res.ok check in api/client.ts). Only list a host here once it's a real,
 * currently-serving backend.
 */
export const FALLBACK_HOSTS: string[] = [LIVE_HOST];

/**
 * Replace the host list with the one from the Stashr server registry (see
 * registry.ts). Edited in place so existing imports stay valid. LIVE_HOST
 * stays at the end as a last resort so a bad registry entry can't strand the app.
 */
export function applyHostList(fresh: string[]): void {
  const cleaned = fresh
    .map((h) => h.trim().replace(/\/+$/, ''))
    .filter((h) => /^https:\/\//i.test(h));
  if (cleaned.length === 0) return;
  const list = [...cleaned, LIVE_HOST].filter((h, i, all) => all.indexOf(h) === i);
  FALLBACK_HOSTS.splice(0, FALLBACK_HOSTS.length, ...list);
  if (sessionHost && !FALLBACK_HOSTS.includes(sessionHost)) sessionHost = null;
}

// Remembered only for this run of the app (never persisted): once a fallback
// host is found to work, later requests try it first instead of eating the
// blocked host's connection-failure delay every single time.
let sessionHost: string | null = null;

/**
 * The host to use for the next request. An explicit base the user configured
 * on the Setup screen (dev/emulator) always wins, with no substitution — the
 * fallback chain only ever applies to the default, unconfigured case that
 * real end users are in.
 */
export async function resolveHost(): Promise<string> {
  const explicit = await getBaseUrl();
  return explicit ?? sessionHost ?? FALLBACK_HOSTS[0];
}

/** True only when nothing was explicitly configured — i.e. the fallback chain may apply. */
export async function usingDefaultHost(): Promise<boolean> {
  return (await getBaseUrl()) == null;
}

/** The next candidate after `failed`, or null once every fallback has been tried. */
export function nextFallbackHost(failed: string): string | null {
  const bare = failed.replace(/\/+$/, '');
  const i = FALLBACK_HOSTS.indexOf(bare);
  if (i < 0) return FALLBACK_HOSTS.find((h) => h !== bare) ?? null;
  return i + 1 < FALLBACK_HOSTS.length ? FALLBACK_HOSTS[i + 1] : null;
}

/** Call once a fallback host answers, so the rest of this session prefers it. */
export function rememberWorkingHost(host: string): void {
  sessionHost = host;
}

/** scheme://host[:port] — tiny parser; RN's URL polyfill lacks some getters. */
export function parseUrl(url: string): { scheme: string; host: string; port: string } | null {
  const m = /^([a-z][a-z0-9+.-]*):\/\/([^/?#:]*)(?::(\d+))?/i.exec(url.trim());
  if (!m) return null;
  return { scheme: m[1].toLowerCase(), host: m[2].toLowerCase(), port: m[3] ?? '' };
}

export function schemeOf(url: string): string {
  const m = /^([a-z][a-z0-9+.-]*):/i.exec(url.trim());
  return m ? m[1].toLowerCase() : '';
}

/** Loopback, private LAN ranges, and .local — the addresses served over http. */
export function isLocalAddress(host: string): boolean {
  const h = host.toLowerCase();
  if (h === 'localhost' || h.endsWith('.local')) return true;
  if (h.startsWith('10.') || h.startsWith('127.') || h.startsWith('192.168.')) return true;
  if (h.startsWith('172.')) {
    const second = parseInt(h.split('.')[1] ?? '', 10);
    return second >= 16 && second <= 31;
  }
  return false;
}

/**
 * Turns what someone actually types ("10.0.2.2:8765", "localhost:8765/",
 * "https://deals.example.com") into a usable origin.
 *
 * A bare hostname gets https (the backend marks the session cookie Secure, so
 * guessing http against a real deployment signs you in then instantly out),
 * except local addresses, which genuinely serve plain http. "localhost" is
 * rewritten to 10.0.2.2: on a device, localhost is the phone itself.
 */
export function normalize(raw: string): string {
  let url = raw.trim().replace(/\/+$/, '');
  if (!url) return url;
  if (!/^https?:\/\//i.test(url)) {
    const host = url.split('/')[0].split(':')[0];
    url = (isLocalAddress(host) ? 'http://' : 'https://') + url;
  }
  const parsed = parseUrl(url);
  if (parsed && (parsed.host === 'localhost' || parsed.host === '127.0.0.1')) {
    url = `${parsed.scheme}://10.0.2.2${parsed.port ? ':' + parsed.port : ''}`;
  }
  return url.replace(/\/+$/, '');
}

/** True when `url` belongs to the configured server, so it stays in-app. */
export function isOwnHost(base: string, url: string): boolean {
  const a = parseUrl(base);
  const b = parseUrl(url);
  return !!a && !!b && !!a.host && a.host === b.host;
}

/** Joins the server origin with a site-relative path such as "/?deal=42". */
export function joinUrl(base: string, path: string | null | undefined): string {
  if (!path) return base;
  if (/^https?:\/\//i.test(path)) return path;
  return base.replace(/\/+$/, '') + (path.startsWith('/') ? path : '/' + path);
}

/** Returns null on success, or a human-readable reason it failed. */
export async function probeServer(base: string): Promise<string | null> {
  const controller = new AbortController();
  // Long enough to cover a free-tier cold start.
  const timer = setTimeout(() => controller.abort(), 70_000);
  try {
    const res = await fetch(`${base}/api/ping`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (res.status !== 200) {
      return `Server answered with HTTP ${res.status}. Is that the DealRadar address?`;
    }
    const body = await res.json().catch(() => null);
    if (!body || body.service !== 'dealradar') {
      return "Something is running there, but it isn't DealRadar.";
    }
    return null;
  } catch (e: any) {
    const why = e?.name === 'AbortError' ? 'timed out' : e?.message ?? String(e);
    return (
      `Couldn't reach ${base} — ${why}.\n\n` +
      "If it's the live server, check you're online and try again. " +
      "If it's a local one, check it's running, bound to 0.0.0.0, and that this " +
      'device is on the same network (the emulator reaches your computer at 10.0.2.2).'
    );
  } finally {
    clearTimeout(timer);
  }
}
