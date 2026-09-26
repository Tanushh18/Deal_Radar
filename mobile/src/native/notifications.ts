/**
 * Deal-alert notifications for the anonymous device.
 *
 * Delivery paths:
 *  1. Remote push straight from our server to Firebase Cloud Messaging: the
 *     device registers its raw FCM token (getDevicePushTokenAsync).
 *  2. The device feed, GET /api/devices/feed, polled on every foreground and by
 *     an expo-background-task (>= 15 min). Each new item is shown locally as a
 *     rich notification. Items whose deal already arrived as a push in the last
 *     24h are skipped.
 *
 * Rich display uses Notifee (BigPictureStyle: product thumbnail collapsed, full
 * image expanded, "View deal" / "Track price" actions, countdown header,
 * auto-dismiss at the deal's expiry). If Notifee's native module is missing
 * (Expo Go) it falls back to a plain expo-notifications banner.
 *
 * Three independently-mutable channels (see CHANNELS below): Price drops,
 * Daily deals, Flash sales.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import { Platform } from 'react-native';

import { getBaseUrl, LIVE_HOST, STORAGE_KEYS } from './config';
import { getDeviceId } from './device';
import {
  NOT_INTERESTED_ACTION_ID,
  enqueue,
  setDisplayer,
  slotData,
  smartTick,
  type Scheduled,
} from './smartNotify';

/** Legacy single-channel id, kept only as an Expo-push fallback target on old installs. */
export const CHANNEL_ID = 'deal-alerts';
export const BRAND_COLOR = '#2563eb';
export const VIEW_ACTION_ID = 'view-deal';
export const TRACK_ACTION_ID = 'track-price';

export type FeedKind = 'price_drop' | 'follow' | 'digest' | 'weekly_pick' | 'broadcast';

/** Three channels, each mutable independently in system settings (spec: "Behavior › Channels"). */
export const CHANNELS = {
  priceDrops: { id: 'price-drops', name: 'Price drops', description: 'Tracked items whose price just fell' },
  dailyDeals: { id: 'daily-deals', name: 'Daily deals', description: "Today's top pick and your weekly picks" },
  flashSales: { id: 'flash-sales', name: 'Flash sales', description: 'New deals in what you follow' },
} as const;

function channelForKind(kind: string | undefined): (typeof CHANNELS)[keyof typeof CHANNELS] {
  switch (kind) {
    case 'price_drop':
      return CHANNELS.priceDrops;
    case 'digest':
    case 'weekly_pick':
      return CHANNELS.dailyDeals;
    case 'follow':
    default:
      return CHANNELS.flashSales;
  }
}

export type FeedItem = {
  id: string | number;
  kind?: FeedKind | string;
  title: string;
  body: string;
  image_url?: string | null;
  deal_id?: string | number | null;
  url?: string | null;
  expires_at?: number | string | null;
  created_at?: number | string;
  // Deal fields the server attaches so the phone can write its own copy.
  price?: number | null;
  mrp?: number | null;
  discount_pct?: number | null;
  brand?: string | null;
  category?: string | null;
  store?: string | null;
  score?: number | null;
  deal_title?: string | null;
};

/** Shown the moment they arrive; everything else waits for the phone's own schedule (smartNotify.ts). */
const INSTANT_KINDS = new Set(['price_drop', 'broadcast']);

/** "Ends in 2h 14m" — spec: header shows this instead of the timestamp. Null once expired or with no end time. */
export function countdownLabel(expiresAt: number | string | null | undefined): string | null {
  const ms = toEpochMs(expiresAt);
  if (!ms) return null;
  const remaining = ms - Date.now();
  if (remaining <= 0) return null;
  const totalMin = Math.floor(remaining / 60_000);
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  if (h <= 0) return `Ends in ${m}m`;
  return `Ends in ${h}h ${m}m`;
}

function toEpochMs(value: number | string | null | undefined): number | null {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return null;
  return n < 1e12 ? n * 1000 : n;
}

type NotifeeModule = typeof import('@notifee/react-native');

let notifeeMod: NotifeeModule | null | undefined;

/** Lazily required so a build or client without the native module still boots. */
export function getNotifee(): NotifeeModule | null {
  if (notifeeMod !== undefined) return notifeeMod;
  if (Platform.OS !== 'android') return (notifeeMod = null);
  try {
    const mod = require('@notifee/react-native') as NotifeeModule;
    // The JS package loads fine without the native side; probe it.
    const { NativeModules } = require('react-native');
    notifeeMod = NativeModules?.NotifeeApiModule ? mod : null;
  } catch {
    notifeeMod = null;
  }
  return notifeeMod;
}

// ---------------------------------------------------------------- setup

let handlerConfigured = false;

/** Foreground pushes with a product image are re-shown via Notifee so they get the big picture too. */
export function configureNotificationHandler(): void {
  if (handlerConfigured) return;
  handlerConfigured = true;
  Notifications.setNotificationHandler({
    handleNotification: async (n) => {
      const data = (n.request.content.data ?? {}) as Record<string, unknown>;
      void markPushDelivered(data.deal_id);
      const image = typeof data.image_url === 'string' ? data.image_url : '';
      if (n.request.trigger && (n.request.trigger as any).type === 'push' && image && getNotifee()) {
        const shown = await displayRich({
          id: `push-${n.request.identifier}`,
          title: n.request.content.title ?? 'DealRadar',
          body: n.request.content.body ?? '',
          image_url: image,
          deal_id: (data.deal_id as string | number | null) ?? null,
          url: typeof data.url === 'string' ? data.url : null,
          kind: typeof data.kind === 'string' ? data.kind : undefined,
        }).catch(() => false);
        if (shown) return { shouldShowBanner: false, shouldShowList: false, shouldPlaySound: false, shouldSetBadge: false };
      }
      return { shouldShowBanner: true, shouldShowList: true, shouldPlaySound: true, shouldSetBadge: false };
    },
  });
}

let channelReady: Promise<void> | null = null;

/**
 * Android channels are immutable once created, so the id stays 'deal-alerts'
 * for both Notifee and Expo pushes (FCM default channel in the manifest).
 */
export function ensureChannel(): Promise<void> {
  if (Platform.OS !== 'android') return Promise.resolve();
  if (!channelReady) {
    channelReady = (async () => {
      const nf = getNotifee();
      const channels = Object.values(CHANNELS);
      if (nf) {
        const { default: notifee, AndroidImportance, AndroidVisibility } = nf;
        for (const c of channels) {
          await notifee.createChannel({
            id: c.id,
            name: c.name,
            description: c.description,
            importance: AndroidImportance.HIGH,
            sound: 'default',
            vibration: true,
            vibrationPattern: [200, 120, 200, 120],
            lights: true,
            lightColor: BRAND_COLOR,
            visibility: AndroidVisibility.PUBLIC,
            badge: true,
          });
        }
        return;
      }
      for (const c of channels) {
        await Notifications.setNotificationChannelAsync(c.id, {
          name: c.name,
          description: c.description,
          importance: Notifications.AndroidImportance.HIGH,
          vibrationPattern: [0, 200, 120, 200],
          enableLights: true,
          lightColor: BRAND_COLOR,
          lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
          showBadge: true,
        });
      }
    })().catch((e) => {
      channelReady = null;
      console.warn('[notifications] channel failed:', (e as Error)?.message ?? e);
    });
  }
  return channelReady;
}

export async function hasPermission(): Promise<boolean> {
  try {
    return (await Notifications.getPermissionsAsync()).granted;
  } catch {
    return false;
  }
}

/** Asks for POST_NOTIFICATIONS on Android 13+; no prompt if already decided. */
export async function requestPermission(): Promise<boolean> {
  // The Android 13 prompt only appears once a channel exists.
  await ensureChannel();
  const current = await Notifications.getPermissionsAsync();
  if (current.granted) return true;
  if (!current.canAskAgain) return false;
  const next = await Notifications.requestPermissionsAsync();
  return next.granted;
}

/**
 * The raw FCM device token — the server sends to Firebase Cloud Messaging
 * directly (no Expo push service). Null if Firebase isn't available.
 */
export async function getPushToken(): Promise<string | null> {
  if (Platform.OS !== 'android') return null;
  try {
    const { data } = await Notifications.getDevicePushTokenAsync();
    return typeof data === 'string' && data ? data : null;
  } catch (e) {
    console.warn('[notifications] push token unavailable:', (e as Error)?.message ?? e);
    return null;
  }
}

// ---------------------------------------------------------------- rich display

function absoluteImage(url: string | null | undefined): string | null {
  return url && /^https?:\/\//i.test(url) ? url : null;
}

type RichOptions = { smartData?: Record<string, string>; fireAt?: number };

async function buildRich(item: FeedItem & { id: string }, nf: NotifeeModule, opts: RichOptions = {}) {
  const { AndroidImportance, AndroidStyle, AndroidVisibility } = nf;
  await ensureChannel();
  const image = absoluteImage(item.image_url);
  const dealId = item.deal_id != null && item.deal_id !== '' ? String(item.deal_id) : '';
  const created = opts.fireAt ?? Number(item.created_at);
  const channel = channelForKind(String(item.kind ?? ''));
  const countdown = opts.fireAt ? null : countdownLabel(item.expires_at);
  const expiresMs = toEpochMs(item.expires_at);

  const actions = !dealId
    ? []
    : opts.smartData
      ? [
          { title: 'View deal', pressAction: { id: VIEW_ACTION_ID, launchActivity: 'default' } },
          // No launchActivity: handled in the background without opening the app.
          { title: 'Not interested', pressAction: { id: NOT_INTERESTED_ACTION_ID } },
        ]
      : [
          { title: 'View deal', pressAction: { id: VIEW_ACTION_ID, launchActivity: 'default' } },
          { title: 'Track price', pressAction: { id: TRACK_ACTION_ID, launchActivity: 'default' } },
        ];

  return {
    id: item.id,
    title: item.title,
    body: item.body,
    data: opts.smartData ?? {
      url: item.url ?? (dealId ? `/?deal=${dealId}` : '/'),
      deal_id: dealId,
      kind: String(item.kind ?? ''),
      image_url: image ?? '',
    },
    android: {
      channelId: channel.id,
      smallIcon: 'notification_icon',
      color: BRAND_COLOR,
      importance: AndroidImportance.HIGH,
      visibility: AndroidVisibility.PUBLIC,
      // Header shows a countdown instead of the timestamp when the deal has a real end time.
      ...(countdown ? { subText: countdown, showTimestamp: false } : { showTimestamp: true }),
      ...(image ? { largeIcon: image } : {}),
      style: image
        ? // largeIcon: null hides the thumbnail once expanded so only the big picture shows.
          { type: AndroidStyle.BIGPICTURE, picture: image, largeIcon: null, summary: item.body }
        : { type: AndroidStyle.BIGTEXT, text: item.body },
      pressAction: { id: 'default', launchActivity: 'default' },
      actions,
      autoCancel: true,
      ...(Number.isFinite(created) && created > 0 ? { timestamp: created < 1e12 ? created * 1000 : created } : {}),
      // Auto-dismiss when the deal ends, per spec.
      ...(expiresMs && expiresMs > Date.now() ? { timeoutAfter: expiresMs } : {}),
      lights: [BRAND_COLOR, 600, 1800] as [string, number, number],
    },
  };
}

/** Returns false when Notifee isn't available (caller falls back). */
async function displayRich(item: FeedItem & { id: string }): Promise<boolean> {
  const nf = getNotifee();
  if (!nf) return false;
  await nf.default.displayNotification((await buildRich(item, nf)) as any);
  return true;
}

setDisplayer({
  notifee: getNotifee,
  channelFor: (kind) => channelForKind(kind).id,
  build: async (n: Scheduled) => {
    const nf = getNotifee()!;
    return (await buildRich(
      { ...n.deal, id: n.id, title: n.title, body: n.body, deal_id: n.deal.deal_id, kind: n.deal.kind ?? undefined },
      nf,
      { smartData: slotData(n), fireAt: n.fireAt },
    )) as any;
  },
});

export async function displayDealNotification(item: FeedItem): Promise<void> {
  const id = `dr-${item.id}`;
  if (await displayRich({ ...item, id }).catch(() => false)) return;
  await ensureChannel();
  await Notifications.scheduleNotificationAsync({
    identifier: id,
    content: {
      title: item.title,
      body: item.body,
      data: {
        url: item.url ?? '/',
        deal_id: item.deal_id ?? null,
        id: item.id,
        image_url: item.image_url ?? '',
        kind: item.kind ?? '',
      },
      color: BRAND_COLOR,
    },
    trigger: Platform.OS === 'android' ? { channelId: channelForKind(String(item.kind ?? '')).id } : null,
  });
}

// ---------------------------------------------------------------- push dedupe

const DELIVERED_KEY = 'dr.pushDelivered';
const DAY_MS = 24 * 60 * 60 * 1000;

async function readDelivered(): Promise<Record<string, number>> {
  try {
    const raw = await AsyncStorage.getItem(DELIVERED_KEY);
    const map = raw ? (JSON.parse(raw) as Record<string, number>) : {};
    const cutoff = Date.now() - DAY_MS;
    for (const k of Object.keys(map)) if (map[k] < cutoff) delete map[k];
    return map;
  } catch {
    return {};
  }
}

let deliveredChain: Promise<void> = Promise.resolve();

/** Records that a remote push already told the user about this deal. */
export function markPushDelivered(dealId: unknown): Promise<void> {
  if (dealId == null || dealId === '') return Promise.resolve();
  const key = String(dealId);
  deliveredChain = deliveredChain.then(async () => {
    const map = await readDelivered();
    map[key] = Date.now();
    await AsyncStorage.setItem(DELIVERED_KEY, JSON.stringify(map)).catch(() => {});
  });
  return deliveredChain;
}

/** Remote pushes sitting in the shade never hit JS while backgrounded; harvest them. */
async function harvestPresentedPushes(): Promise<void> {
  try {
    const shown = await Notifications.getPresentedNotificationsAsync();
    for (const n of shown) {
      const data = (n.request.content.data ?? {}) as Record<string, unknown>;
      if (n.request.trigger && (n.request.trigger as any).type === 'push') await markPushDelivered(data.deal_id);
    }
  } catch {
    /* unsupported */
  }
}

// ---------------------------------------------------------------- feed polling

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

export async function resetPollingState(): Promise<void> {
  await AsyncStorage.multiRemove([STORAGE_KEYS.lastSeen, STORAGE_KEYS.seenIds]);
}

/** Kept for the old signed-in flow; the visitor app never signs in. */
export async function isSignedIn(): Promise<boolean> {
  return (await AsyncStorage.getItem(STORAGE_KEYS.signedIn)) === '1';
}

let inFlight: Promise<number> | null = null;

/**
 * Fetches the device feed and shows each unseen item. Returns how many were
 * shown. Never throws. The very first poll on an install only records the
 * cursor, so a fresh install doesn't get a burst of old alerts.
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
    const base = (await getBaseUrl()) ?? LIVE_HOST;
    const deviceId = await getDeviceId();
    const lastSeenRaw = await AsyncStorage.getItem(STORAGE_KEYS.lastSeen);
    const priming = lastSeenRaw == null;
    const since = priming ? 0 : Number(lastSeenRaw) || 0;

    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 25_000);
    let res: Response;
    try {
      const qs = `device_id=${encodeURIComponent(deviceId)}&since=${encodeURIComponent(String(since))}&limit=20`;
      res = await fetch(`${base}/api/devices/feed?${qs}`, {
        headers: { Accept: 'application/json' },
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timer);
    }
    if (!res.ok) return 0;
    const body = (await res.json()) as { items?: FeedItem[]; now?: number };
    const items = Array.isArray(body.items) ? body.items : [];

    await harvestPresentedPushes();
    const delivered = await readDelivered();
    const seen = await readSeen();
    const canShow = !priming && (await hasPermission());
    let shown = 0;
    const queued: FeedItem[] = [];
    const ordered = [...items].sort((a, b) => Number(a.created_at ?? 0) - Number(b.created_at ?? 0));
    for (const item of ordered) {
      const key = String(item.id);
      if (seen.has(key)) continue;
      seen.add(key);
      const hasDeal = item.deal_id != null && item.deal_id !== '';
      if (hasDeal && !INSTANT_KINDS.has(String(item.kind ?? ''))) {
        // Routine alert: the phone picks the moment (smartNotify.ts), even from the priming poll.
        queued.push(item);
        continue;
      }
      if (!canShow) continue;
      if (hasDeal && delivered[String(item.deal_id)]) continue;
      try {
        await displayDealNotification(item);
        shown++;
      } catch (e) {
        console.warn('[notifications] display failed:', (e as Error)?.message ?? e);
      }
    }
    await writeSeen(seen);
    if (typeof body.now === 'number') {
      await AsyncStorage.setItem(STORAGE_KEYS.lastSeen, String(body.now));
    }
    await enqueue(queued);
    if (await hasPermission()) await smartTick();
    return shown;
  } catch (e) {
    console.warn('[notifications] poll failed:', (e as Error)?.message ?? e);
    return 0;
  }
}

// ---------------------------------------------------------------- taps

export type NotificationTarget = { dealId: string | null; url: string | null };

export function targetFromData(data: Record<string, unknown> | null | undefined): NotificationTarget | null {
  if (!data) return null;
  const url = typeof data.url === 'string' && data.url ? data.url : null;
  let dealId: string | null = data.deal_id != null && data.deal_id !== '' ? String(data.deal_id) : null;
  if (!dealId && url) {
    const m = /[?&]deal=([^&#]+)/.exec(url);
    if (m) dealId = decodeURIComponent(m[1]);
  }
  return { dealId, url };
}

export function targetFromResponse(
  response: Notifications.NotificationResponse | null | undefined,
): NotificationTarget | null {
  if (!response) return null;
  return targetFromData((response.notification.request.content.data ?? {}) as Record<string, unknown>);
}
