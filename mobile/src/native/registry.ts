/**
 * Server registry: DealRadar's backend URLs are kept on Stashr (project "Deal Radar" Server URLs), so moving the
 * backend needs no rebuild or OTA. Stashr is the ONLY source: the cached list is applied first (a local read), then
 * a fresh one is fetched. There is no built-in server. If Stashr says everything is off, the list is empty and the
 * app does not connect; if Stashr cannot be reached, the last list it gave is kept.
 *
 * The app is also watched while it is open: an empty list from Stashr flips the status to 'off' and the app shows a
 * blocked screen (see startRegistryWatch).
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { AppState } from 'react-native';
import { applyHostList, FALLBACK_HOSTS } from './config';

const APP = 'dealradar';
const CACHE_KEY = 'dr.registry';
// The Stashr address comes from the build env (EXPO_PUBLIC_STASHR_SERVER_URL, written by Stashr when its main
// server changes). The old address is only the default when that variable is not set.
const REGISTRY_HOST = String(process.env.EXPO_PUBLIC_STASHR_SERVER_URL || 'https://password-manager-server-xxdr.onrender.com').trim().replace(/\/+$/, '');

function cleanList(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((u): u is string => typeof u === 'string')
    .map((u) => u.trim().replace(/\/+$/, ''))
    .filter((u) => /^https:\/\//i.test(u));
}

/** 'off' = Stashr lists no server for this app, so the app must not be usable. */
export type RegistryStatus = 'unknown' | 'on' | 'off';

/** While the app is open Stashr is asked this often, so switching the servers off there takes effect within about this long. */
export const WATCH_INTERVAL_MS = 15_000;

let status: RegistryStatus = 'unknown';
const listeners = new Set<(next: RegistryStatus) => void>();

function setStatus(next: RegistryStatus): void {
  if (next === status) return;
  status = next;
  listeners.forEach((fn) => fn(next));
}

export function getRegistryStatus(): RegistryStatus {
  return status;
}

export function subscribeRegistryStatus(fn: (next: RegistryStatus) => void): () => void {
  listeners.add(fn);
  return () => {
    listeners.delete(fn);
  };
}

/** Apply the list saved on this device by the last successful refresh. Fast; safe to await at boot. */
export async function loadCachedHosts(): Promise<void> {
  try {
    const saved = await AsyncStorage.getItem(CACHE_KEY);
    if (saved !== null) {
      const urls = cleanList(JSON.parse(saved));
      applyHostList(urls); // "[]" = switched off
      if (urls.length === 0) setStatus('off');
    }
  } catch {
    /* no cache yet */
  }
}

/** Fetch the current list from Stashr and apply + cache it. Resolves to the hosts (empty = switched off), or null if unreachable. */
export async function refreshHosts(timeoutMs = 60_000): Promise<string[] | null> {
  const controller = new AbortController();
  // Long enough (by default) for a sleeping free-tier server to wake up.
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${REGISTRY_HOST}/registry/${APP}?t=${Date.now()}`, {
      headers: { Accept: 'application/json', 'Cache-Control': 'no-cache' },
      signal: controller.signal,
    });
    if (!res.ok) return null;
    const urls = cleanList(((await res.json()) as { urls?: unknown }).urls);
    applyHostList(urls);
    setStatus(urls.length ? 'on' : 'off');
    AsyncStorage.setItem(CACHE_KEY, JSON.stringify(urls)).catch(() => {});
    return [...FALLBACK_HOSTS];
  } catch {
    return null;
  } finally {
    clearTimeout(timer);
  }
}

let watching = false;

/** Keeps asking Stashr while the app is open and whenever it returns to the foreground. Calling it twice does nothing. */
export function startRegistryWatch(): void {
  if (watching) return;
  watching = true;
  setInterval(() => void refreshHosts(15_000), WATCH_INTERVAL_MS);
  AppState.addEventListener('change', (state) => {
    if (state === 'active') void refreshHosts(15_000);
  });
}
