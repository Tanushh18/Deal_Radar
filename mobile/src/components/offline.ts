import AsyncStorage from '@react-native-async-storage/async-storage';
import NetInfo from '@react-native-community/netinfo';
import { useEffect, useState } from 'react';

import { isAbort } from '../api/client';

const PREFIX = 'dr.cache.';

/** False only when the OS is sure there's no route; "unknown" counts as online. */
export function useOnline(): boolean {
  const [online, setOnline] = useState(true);
  useEffect(() => {
    const apply = (s: { isConnected: boolean | null; isInternetReachable?: boolean | null }) =>
      setOnline(!(s.isConnected === false || s.isInternetReachable === false));
    NetInfo.fetch().then(apply).catch(() => {});
    return NetInfo.addEventListener(apply);
  }, []);
  return online;
}

export async function cacheSet(key: string, value: unknown): Promise<void> {
  try {
    await AsyncStorage.setItem(PREFIX + key, JSON.stringify({ at: Date.now(), value }));
  } catch {
    /* storage full — the cache is best effort */
  }
}

export async function cacheGet<T>(key: string): Promise<{ at: number; value: T } | null> {
  try {
    const raw = await AsyncStorage.getItem(PREFIX + key);
    return raw ? (JSON.parse(raw) as { at: number; value: T }) : null;
  } catch {
    return null;
  }
}

/** Network first; on failure falls back to the last good copy (`cached: true`), else rethrows. */
export async function withCache<T>(key: string, fetcher: () => Promise<T>): Promise<{ value: T; cached: boolean }> {
  try {
    const value = await fetcher();
    void cacheSet(key, value);
    return { value, cached: false };
  } catch (e) {
    if (isAbort(e)) throw e;
    const hit = await cacheGet<T>(key);
    if (hit) return { value: hit.value, cached: true };
    throw e;
  }
}
