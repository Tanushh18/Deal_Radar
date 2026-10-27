/**
 * Server registry: DealRadar's backend URLs are kept on Stashr (project "Deal Radar" Server URLs), so moving the
 * backend needs no rebuild or OTA. Stashr is the ONLY source: the cached list is applied first (a local read), then
 * a fresh one is fetched. There is no built-in server. If Stashr says everything is off, the list is empty and the
 * app does not connect; if Stashr cannot be reached, the last list it gave is kept.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { applyHostList, FALLBACK_HOSTS } from './config';

const APP = 'dealradar';
const CACHE_KEY = 'dr.registry';
const STANDBY_KEY = 'dr.registry.standby';
const REGISTRY_HOST = 'https://password-manager-server-xxdr.onrender.com';

/** Hosts the registry marks as standby (the optional `servers` list carries each URL's role). */
function standbyOf(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((s): s is { url: string; role?: string } => !!s && typeof (s as { url?: unknown }).url === 'string')
    .filter((s) => s.role === 'standby')
    .map((s) => s.url.trim().replace(/\/+$/, ''));
}

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
    const saved = await AsyncStorage.getItem(CACHE_KEY);
    const standby = await AsyncStorage.getItem(STANDBY_KEY);
    if (saved !== null) applyHostList(cleanList(JSON.parse(saved)), standby ? cleanList(JSON.parse(standby)) : []); // "[]" = switched off
  } catch {
    /* no cache yet */
  }
}

/** Fetch the current list from Stashr and apply + cache it. Resolves to the hosts (empty = switched off), or null if unreachable. */
export async function refreshHosts(): Promise<string[] | null> {
  const controller = new AbortController();
  // Long enough for a sleeping free-tier server to wake up.
  const timer = setTimeout(() => controller.abort(), 60_000);
  try {
    const res = await fetch(`${REGISTRY_HOST}/registry/${APP}`, {
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const body = (await res.json()) as { urls?: unknown; servers?: unknown };
    const urls = cleanList(body.urls);
    const standby = standbyOf(body.servers);
    applyHostList(urls, standby);
    AsyncStorage.setItem(CACHE_KEY, JSON.stringify(urls)).catch(() => {});
    AsyncStorage.setItem(STANDBY_KEY, JSON.stringify(standby)).catch(() => {});
    return [...FALLBACK_HOSTS];
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}
