/**
 * Server registry: DealRadar's backend URLs are kept on Stashr (its "Servers"
 * page), so moving the backend needs no rebuild or OTA. The cached list is
 * applied first (a local read), then a fresh one is fetched. If Stashr can't
 * be reached, LIVE_HOST in config.ts keeps working.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { applyHostList, FALLBACK_HOSTS } from './config';

const APP = 'dealradar';
const CACHE_KEY = 'dr.registry';
const REGISTRY_HOSTS: string[] = [
  'https://password-manager-server-xxdr.onrender.com',
  'https://password-manager-server-8gvj.onrender.com',
];

function cleanList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((u): u is string => typeof u === 'string')
    .map((u) => u.trim().replace(/\/+$/, ''))
    .filter((u) => /^https:\/\//i.test(u));
}

/** Apply the list saved on this device by the last successful refresh. Fast; safe to await at boot. */
export async function loadCachedHosts(): Promise<void> {
  try {
    const cached = cleanList(JSON.parse((await AsyncStorage.getItem(CACHE_KEY)) ?? '[]'));
    if (cached.length) applyHostList(cached);
  } catch {
    /* no cache yet */
  }
}

/** Fetch the current list from Stashr and apply + cache it. Resolves to the hosts, or null if unreachable. */
export async function refreshHosts(): Promise<string[] | null> {
  for (const host of REGISTRY_HOSTS) {
    const controller = new AbortController();
    // Long enough for a sleeping free-tier server to wake up.
    const timer = setTimeout(() => controller.abort(), 60_000);
    try {
      const res = await fetch(`${host}/registry/${APP}`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
      if (!res.ok) continue;
      const urls = cleanList(((await res.json()) as { urls?: unknown }).urls);
      if (urls.length === 0) continue;
      applyHostList(urls);
      AsyncStorage.setItem(CACHE_KEY, JSON.stringify(urls)).catch(() => {});
      return [...FALLBACK_HOSTS];
    } catch {
      /* try the next registry host */
    } finally {
      clearTimeout(timer);
    }
  }
  return null;
}
