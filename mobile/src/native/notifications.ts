/**
 * Deal-alert notifications.
 *
 * Two delivery paths, so alerts work before any Firebase/EAS setup exists:
 *
 *  1. Remote push (preferred) — an Expo push token registered with the server
 *     via POST /api/push/register. Needs an EAS projectId in app config
 *     (`extra.eas.projectId`) and FCM credentials baked into the build; without
 *     them `getExpoPushTokenAsync` fails and we fall back silently.
 *  2. Polling — GET /api/notifications?since=<lastSeen> on every foreground and
 *     from an expo-background-task (>= 15 min, OS-scheduled), turning each new
 *     item into a local notification. RN's fetch on Android goes through
 *     OkHttp + ForwardingCookieHandler, which is backed by
 *     android.webkit.CookieManager — the same jar the WebView and native login
 *     use — so the session cookie rides along with `credentials: 'include'`.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import Constants from 'expo-constants';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { getBaseUrl, STORAGE_KEYS } from './config';

export const CHANNEL_ID = 'deal-alerts';

export type FeedItem = {
  id: string | number;
  deal_id?: string | number | null;
  title: string;
  body: string;
  url?: string | null;
  created_at?: number | string;
};

let handlerConfigured = false;

/** Show alerts as banners even while the app is open. Safe to call repeatedly. */
export function configureNotificationHandler(): void {
  if (handlerConfigured) return;
  handlerConfigured = true;
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
}

export async function ensureChannel(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(CHANNEL_ID, {
    name: 'Deal alerts',
    description: 'New deals matching your saved alerts',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: [0, 200, 120, 200],
    lightColor: '#2563eb',
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
    showBadge: true,
  });
}

/** Asks for POST_NOTIFICATIONS on Android 13+; no prompt if already decided. */
export async function requestPermission(): Promise<boolean> {
  // Channel must exist before the Android 13 prompt can appear.
  await ensureChannel();
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  if (!current.canAskAgain) return false;
  const next = await Notifications.requestPermissionsAsync();
  return next.granted;
}

export function getEasProjectId(): string | null {
  const fromExtra = (Constants.expoConfig?.extra as any)?.eas?.projectId;
  const fromEas = (Constants as any).easConfig?.projectId;
  const id = fromExtra || fromEas;
  return typeof id === 'string' && id.length > 0 ? id : null;
}

/** Expo push token, or null when push isn't configured for this build. */
export async function getPushToken(): Promise<string | null> {
  const projectId = getEasProjectId();
  if (!projectId) return null;
  try {
    const { data } = await Notifications.getExpoPushTokenAsync({ projectId });
    return data || null;
  } catch (e) {
    console.warn('[notifications] push token unavailable:', (e as Error)?.message ?? e);
    return null;
  }
}

// ---------------------------------------------------------------- polling

const MAX_SEEN = 300;

async function readSeen(): Promise<Set<string>> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEYS.seenIds);
    return new Set(raw ? (JSON.parse(raw) as string[]) : []);
  } catch {
    return new Set();
  }
}

async function writeSeen(seen: Set<string>): Promise<void> {
  const arr = Array.from(seen).slice(-MAX_SEEN);
  await AsyncStorage.setItem(STORAGE_KEYS.seenIds, JSON.stringify(arr));
}

export async function isSignedIn(): Promise<boolean> {
  return (await AsyncStorage.getItem(STORAGE_KEYS.signedIn)) === '1';
}

export async function isPushActive(): Promise<boolean> {
  return !!(await AsyncStorage.getItem(STORAGE_KEYS.pushToken));
}

export async function resetPollingState(): Promise<void> {
  await AsyncStorage.multiRemove([STORAGE_KEYS.lastSeen, STORAGE_KEYS.seenIds]);
}

let inFlight: Promise<number> | null = null;

/**
 * Fetches the feed and posts a local notification per unseen item.
 * Returns how many notifications were shown. Never throws.
 *
 * The very first poll after sign-in only records the cursor (no burst of
 * old alerts). When remote push is active the server already delivers each
 * item, so the poll only advances the cursor.
 */
export function pollNotifications(): Promise<number> {
  if (!inFlight) {
    inFlight = doPoll().finally(() => {
      inFlight = null;
    });
  }
  return inFlight;
}

async function doPoll(): Promise<number> {
  try {
    if (!(await isSignedIn())) return 0;
    const base = await getBaseUrl();
    if (!base) return 0;

    const lastSeenRaw = await AsyncStorage.getItem(STORAGE_KEYS.lastSeen);
    const priming = lastSeenRaw == null;
    const since = priming ? 0 : Number(lastSeenRaw) || 0;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25_000);
    let res: Response;
    try {
      res = await fetch(`${base}/api/notifications?since=${encodeURIComponent(String(since))}`, {
        credentials: 'include',
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (res.status === 401 || res.status === 403) return 0; // session gone; the UI will notice
    if (!res.ok) return 0;
    const body = (await res.json()) as { notifications?: FeedItem[]; now?: number };
    const items = Array.isArray(body.notifications) ? body.notifications : [];

    const seen = await readSeen();
    const pushActive = await isPushActive();
    let shown = 0;
    // Oldest first so the shade orders them naturally.
    const ordered = [...items].sort((a, b) => Number(a.created_at ?? 0) - Number(b.created_at ?? 0));
    for (const item of ordered) {
      const key = String(item.id);
      if (seen.has(key)) continue;
      seen.add(key);
      if (priming || pushActive) continue;
      await Notifications.scheduleNotificationAsync({
        identifier: `dr-${key}`,
        content: {
          title: item.title,
          body: item.body,
          data: { url: item.url ?? '/', deal_id: item.deal_id ?? null, id: item.id },
          color: '#2563eb',
        },
        trigger: Platform.OS === 'android' ? { channelId: CHANNEL_ID } : null,
      });
      shown++;
    }
    await writeSeen(seen);
    if (typeof body.now === 'number') {
      await AsyncStorage.setItem(STORAGE_KEYS.lastSeen, String(body.now));
    }
    return shown;
  } catch (e) {
    console.warn('[notifications] poll failed:', (e as Error)?.message ?? e);
    return 0;
  }
}

// ---------------------------------------------------------------- taps

export type NotificationTarget = { dealId: string | null; url: string | null };

/** Pulls the deal id (or at least a path) out of push or local notification data. */
export function targetFromResponse(
  response: Notifications.NotificationResponse | null | undefined,
): NotificationTarget | null {
  if (!response) return null;
  const data = (response.notification.request.content.data ?? {}) as Record<string, unknown>;
  const url = typeof data.url === 'string' ? data.url : null;
  let dealId: string | null =
    data.deal_id != null && data.deal_id !== '' ? String(data.deal_id) : null;
  if (!dealId && url) {
    const m = /[?&]deal=([^&#]+)/.exec(url);
    if (m) dealId = decodeURIComponent(m[1]);
  }
  return { dealId, url };
}
