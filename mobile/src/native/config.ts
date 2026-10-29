/**
 * Where the DealRadar backend lives, persisted across launches.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

// Canonical public address (custom domain in front of the ingest server).
export const LIVE_HOST: string = 'https://dealradar.tanush.qd.je';
// The servers themselves are NOT listed here: the app gets them from the Stashr registry (see registry.ts) and
// uses nothing else. LIVE_HOST above is only the example shown on the developer Setup screen.
// Used as the address when the registry has none (switched off, or never reached): every request then fails
// at once instead of going to some other server.
export const NO_SERVER = 'https://stashr-no-server.invalid';
// Hosts that are gone for good. Dropped from a saved manual choice, so a stale entry can never strand the app on a dead server.
const RETIRED_HOSTS = new Set<string>();
const bareHost = (h: string): string => h.trim().replace(/\/+$/, '').toLowerCase();
export const isRetiredHost = (h: string): boolean => RETIRED_HOSTS.has(bareHost(h));
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
    // Older builds saved the default host as if it were a manual choice, which
    // would pin the app to it and ignore the server registry. A saved default
    // is not a choice, so treat it as unset.
    if (v.replace(/\/+$/, '') === LIVE_HOST || isRetiredHost(v)) {
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
 * The servers to use, exactly as the Stashr registry lists them (empty until the first list arrives, and empty again
 * when everything is switched off there). Every server serves deal browsing and forwards user writes to the user
 * server, so installs are spread across all of them (see resolveHost).
 */
export const FALLBACK_HOSTS: string[] = [];

/**
 * Replace the host list with the one from the Stashr server registry (see registry.ts). Edited in place so
 * existing imports stay valid. An empty list is valid: it means the servers are switched off.
 */
export function applyHostList(fresh: string[]): void {
  const cleaned = fresh
    .map((h) => h.trim().replace(/\/+$/, ''))
    .filter((h) => /^https:\/\//i.test(h));
  FALLBACK_HOSTS.splice(0, FALLBACK_HOSTS.length, ...cleaned.filter((h, i, all) => all.indexOf(h) === i));
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
  if (explicit) return explicit;
  if (!sessionHost) {
    // Spread installs across the registry's servers: each app run sticks to one
    // random host (replicas forward writes to the primary), failing over in order.
    sessionHost = hostsInRandomOrder()[0] ?? null;
  }
  return sessionHost ?? NO_SERVER;
}

/** Every host the registry lists, starting at a random server so load is spread evenly. */
export function hostsInRandomOrder(): string[] {
  const pool = [...FALLBACK_HOSTS];
  const n = pool.length;
  if (n === 0) return [];
  const start = Math.floor(Math.random() * n);
  return Array.from({ length: n }, (_, i) => pool[(start + i) % n]);
}

/** True only when nothing was explicitly configured — i.e. the fallback chain may apply. */
export async function usingDefaultHost(): Promise<boolean> {
  return (await getBaseUrl()) == null;
}

/** The next candidate after `failed` (wrapping round), or null if there is no other host. */
export function nextFallbackHost(failed: string): string | null {
  const bare = failed.replace(/\/+$/, '');
  const i = FALLBACK_HOSTS.indexOf(bare);
  if (i < 0) return FALLBACK_HOSTS.find((h) => h !== bare) ?? null;
  const next = FALLBACK_HOSTS[(i + 1) % FALLBACK_HOSTS.length];
  return next === bare ? null : next;
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
