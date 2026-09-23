/**
 * One entry point for everything that opens the app somewhere specific:
 * notification taps (Expo push, Notifee local, warm or cold), share intents
 * and launcher shortcuts. Targets that arrive before the navigator is mounted
 * on Main are held until setRoutingReady(true).
 */
import * as Notifications from 'expo-notifications';
import { CommonActions, StackActions } from '@react-navigation/native';

import { navigationRef } from '../navigation/types';
import { getNotifee, markPushDelivered, targetFromData, targetFromResponse, type NotificationTarget } from './notifications';

export type RouteTarget =
  | { kind: 'deal'; id: string }
  | { kind: 'path'; path: string }
  | { kind: 'check'; url?: string }
  | { kind: 'search' }
  | { kind: 'saved' }
  | { kind: 'home' };

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

function handleExpoResponse(response: Notifications.NotificationResponse | null) {
  if (!response) return;
  if (!once(`expo:${response.notification.request.identifier}`)) return;
  const data = (response.notification.request.content.data ?? {}) as Record<string, unknown>;
  const trigger = response.notification.request.trigger as any;
  if (trigger?.type === 'push') void markPushDelivered(data.deal_id);
  routeTo(fromNotification(targetFromResponse(response)));
  Notifications.clearLastNotificationResponseAsync().catch(() => {});
}

type NotifeeLike = { id?: string; data?: Record<string, unknown> } | undefined;

/** Also called from the Notifee background handler registered in index.ts. */
export function handleNotifeePress(notification: NotifeeLike): void {
  if (!notification) return;
  if (!once(`notifee:${notification.id ?? ''}`)) return;
  routeTo(fromNotification(targetFromData(notification.data ?? null)));
}

/** Call once at startup. Returns an unsubscribe function. */
export function startNotificationRouting(): () => void {
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
      if (type === EventType.PRESS || type === EventType.ACTION_PRESS) {
        handleNotifeePress(detail.notification as NotifeeLike);
        if (detail.notification?.id) notifee.cancelNotification(detail.notification.id).catch(() => {});
      }
    });
    notifee
      .getInitialNotification()
      .then((initial) => initial && handleNotifeePress(initial.notification as NotifeeLike))
      .catch(() => {});
  }
  return () => {
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
