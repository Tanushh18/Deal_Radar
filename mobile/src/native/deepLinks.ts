/**
 * One entry point for everything that opens the app somewhere specific:
 * notification taps (Expo push, Notifee local, warm or cold), share intents
 * and launcher shortcuts. Targets that arrive before the navigator is mounted
 * on Main are held until setRoutingReady(true).
 */
import { Linking } from 'react-native';
import * as Notifications from 'expo-notifications';
import { CommonActions, StackActions } from '@react-navigation/native';

import { navigationRef } from '../navigation/types';
import { getNotifee, markPushDelivered, targetFromData, targetFromResponse, type NotificationTarget } from './notifications';
import { NOT_INTERESTED_ACTION_ID, recordNotificationOutcome, type DealFields } from './smartNotify';

export type RouteTarget =
  | { kind: 'deal'; id: string }
  | { kind: 'path'; path: string }
  | { kind: 'check'; url?: string }
  | { kind: 'search' }
  | { kind: 'saved' }
  | { kind: 'home' };

// Hosts this app answers for when Android/iOS hand it a tapped link instead of
// opening a browser — the custom scheme always works; the https ones only
// actually reach us once Android has verified the App Link (see LIVE_HOST's
// assetlinks.json and app.json's android.intentFilters — both need a native
// build to take effect). The retired Render host is kept here too: harmless,
// and covers anyone who taps an old share link cached from before the move.
const LINK_HOSTS = new Set(['dealradar.ggnhome.com', 'dealradar-0oza.onrender.com']);

/** A tapped `dealradar://…` or `https://dealradar.ggnhome.com/…` link, as a RouteTarget. */
export function parseExternalUrl(url: string | null | undefined): RouteTarget | null {
  if (!url) return null;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const scheme = parsed.protocol.replace(':', '').toLowerCase();
  if (scheme !== 'dealradar' && !(scheme === 'https' && LINK_HOSTS.has(parsed.hostname.toLowerCase()))) {
    return null;
  }
  // dealradar:// URLs have no real host — the first path segment (host or
  // pathname, RN's URL polyfill is inconsistent about which) carries it.
  const segments = `${parsed.hostname}${parsed.pathname}`.split('/').filter(Boolean);
  const dealId = parsed.searchParams.get('deal')
    ?? (segments[0] === 'd' || segments[0] === 'deal' ? segments[1] : null);
  if (dealId) return { kind: 'deal', id: decodeURIComponent(dealId) };
  const q = parsed.searchParams.get('q');
  if (q) return { kind: 'search' };
  if (segments[0] === 'saved') return { kind: 'saved' };
  if (segments[0] === 'check') return { kind: 'check', url: parsed.searchParams.get('url') ?? undefined };
  if (!segments.length) return { kind: 'home' };
  return { kind: 'path', path: `/${segments.join('/')}` };
}

let pending: RouteTarget | null = null;
let ready = false;
const handledIds = new Set<string>();

function fromNotification(t: NotificationTarget | null): RouteTarget | null {
  if (!t) return null;
  if (t.dealId) return { kind: 'deal', id: t.dealId };
  if (t.url && t.url !== '/') return { kind: 'path', path: t.url };
  return { kind: 'home' };
}

function open(target: RouteTarget) {
  const onMain = navigationRef.getRootState()?.routes?.some((r) => r.name === 'Main');
  if (!onMain) {
    navigationRef.dispatch(CommonActions.reset({ index: 0, routes: [{ name: 'Main' }] }));
  }
  switch (target.kind) {
    case 'deal':
      navigationRef.dispatch(StackActions.push('DealDetail', { id: target.id }));
      break;
    case 'check':
      navigationRef.dispatch(StackActions.push('CheckPrice', { url: target.url }));
      break;
    case 'path':
      navigationRef.navigate('Website', { path: target.path });
      break;
    case 'search':
      navigationRef.navigate('Search');
      break;
    case 'saved':
      navigationRef.navigate('Main', { screen: 'Saved' });
      break;
    case 'home':
      navigationRef.navigate('Main', { screen: 'Deals' });
      break;
  }
}

export function routeTo(target: RouteTarget | null): void {
  if (!target) return;
  if (ready && navigationRef.isReady()) open(target);
  else pending = target;
}

function once(id: string | undefined | null): boolean {
  if (!id) return true;
  if (handledIds.has(id)) return false;
  handledIds.add(id);
  return true;
}

/** Deal fields a phone-scheduled notification carries, for the interest weights. */
function smartDeal(data: Record<string, unknown> | null | undefined): { slot?: string; deal: DealFields } | null {
  if (!data || typeof data.smart_slot !== 'string' || !data.smart_slot) return null;
  const str = (v: unknown) => (typeof v === 'string' && v ? v : null);
  return {
    slot: data.smart_slot,
    deal: { deal_id: str(data.deal_id), category: str(data.category), brand: str(data.brand), store: str(data.store) },
  };
}

function handleExpoResponse(response: Notifications.NotificationResponse | null) {
  if (!response) return;
  if (!once(`expo:${response.notification.request.identifier}`)) return;
  const data = (response.notification.request.content.data ?? {}) as Record<string, unknown>;
  const trigger = response.notification.request.trigger as any;
  if (trigger?.type === 'push') void markPushDelivered(data.deal_id);
  const smart = smartDeal(data);
  if (smart) void recordNotificationOutcome(smart.slot, 'tap', smart.deal);
  routeTo(fromNotification(targetFromResponse(response)));
  Notifications.clearLastNotificationResponseAsync().catch(() => {});
}

type NotifeeLike = { id?: string; data?: Record<string, unknown> } | undefined;

/** Also called from the Notifee background handler registered in index.ts. */
export function handleNotifeePress(notification: NotifeeLike): void {
  if (!notification) return;
  if (!once(`notifee:${notification.id ?? ''}`)) return;
  const smart = smartDeal(notification.data);
  if (smart) void recordNotificationOutcome(smart.slot, 'tap', smart.deal);
  routeTo(fromNotification(targetFromData(notification.data ?? null)));
}

/**
 * Every Notifee event, foreground or background. Returns once the event is
 * recorded, so the background handler can await it before Android kills the
 * headless JS.
 */
export async function handleNotifeeEvent(
  type: number,
  detail: { notification?: NotifeeLike; pressAction?: { id?: string } },
): Promise<void> {
  const nf = getNotifee();
  if (!nf) return;
  const { default: notifee, EventType } = nf;
  const n = detail.notification;
  const smart = smartDeal(n?.data);
  if (type === EventType.ACTION_PRESS && detail.pressAction?.id === NOT_INTERESTED_ACTION_ID) {
    if (smart) await recordNotificationOutcome(smart.slot, 'not_interested', smart.deal);
    if (n?.id) await notifee.cancelNotification(n.id).catch(() => {});
    return;
  }
  if (type === EventType.PRESS || type === EventType.ACTION_PRESS) {
    handleNotifeePress(n);
    if (n?.id) await notifee.cancelNotification(n.id).catch(() => {});
    return;
  }
  if (type === EventType.DISMISSED && smart) await recordNotificationOutcome(smart.slot, 'dismiss', smart.deal);
}

/**
 * Product links tapped outside the app: shared "Buy on…" links, a `/d/<id>`
 * share card, or anything opened with the `dealradar://` scheme. Cold start
 * (`getInitialURL`) and warm (`addEventListener`) both route the same way.
 */
function startExternalLinkRouting(): () => void {
  Linking.getInitialURL()
    .then((url) => routeTo(parseExternalUrl(url)))
    .catch(() => {});
  const sub = Linking.addEventListener('url', ({ url }) => routeTo(parseExternalUrl(url)));
  return () => sub.remove();
}

/** Call once at startup. Returns an unsubscribe function. */
export function startNotificationRouting(): () => void {
  const unsubLinks = startExternalLinkRouting();
  const sub = Notifications.addNotificationResponseReceivedListener((r) => handleExpoResponse(r));
  const received = Notifications.addNotificationReceivedListener((n) => {
    const trigger = n.request.trigger as any;
    if (trigger?.type === 'push') void markPushDelivered((n.request.content.data as any)?.deal_id);
  });
  Notifications.getLastNotificationResponseAsync()
    .then((r) => handleExpoResponse(r))
    .catch(() => {});

  let unsubNotifee = () => {};
  const nf = getNotifee();
  if (nf) {
    const { default: notifee, EventType } = nf;
    unsubNotifee = notifee.onForegroundEvent(({ type, detail }) => {
      void handleNotifeeEvent(type, detail as any);
    });
    notifee
      .getInitialNotification()
      .then((initial) => initial && handleNotifeePress(initial.notification as NotifeeLike))
      .catch(() => {});
  }
  return () => {
    unsubLinks();
    sub.remove();
    received.remove();
    unsubNotifee();
  };
}

/** Called by the root once the navigator is mounted on Main. */
export function setRoutingReady(isReady: boolean) {
  ready = isReady;
  if (isReady && pending && navigationRef.isReady()) {
    const t = pending;
    pending = null;
    open(t);
  }
}
