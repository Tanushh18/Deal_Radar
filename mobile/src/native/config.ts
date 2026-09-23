/**
 * Where the DealRadar backend lives, persisted across launches.
 * Port of legacy/android-kotlin/.../ServerConfig.kt.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';

export const LIVE_HOST: string = 'https://dealradar-0oza.onrender.com';
export const EMULATOR_HOST = 'http://10.0.2.2:8765';

export const COLORS = {
  bg: '#080b12',
  bgSunk: '#05070c',
  surface: '#111827',
  surface2: '#172033',
  border: '#1f2a3c',
  borderStrong: '#33405a',
  text: '#e9eef7',
  text2: '#9ba7bc',
  text3: '#6b7789',
  accent: '#5b93f7',
  accentStrong: '#2563eb',
  accentText: '#06101f',
  hot: '#f87171',
  good: '#34d399',
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
    return v && v.trim() ? v : null;
  } catch {
    return null;
  }
}

export async function saveBaseUrl(url: string): Promise<string> {
  const normalized = normalize(url);
  await AsyncStorage.setItem(KEYS.baseUrl, normalized);
  return normalized;
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
