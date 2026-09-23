/**
 * Routes notification taps (remote push or local, warm or cold start) to the
 * deal they're about. Taps that arrive before the navigator is ready, or
 * before the auth gate has put the user on Main, are held until `flush()`.
 */
import * as Notifications from 'expo-notifications';
import { CommonActions } from '@react-navigation/native';

import { navigationRef } from '../navigation/types';
import { isSignedIn, targetFromResponse, type NotificationTarget } from './notifications';

let pending: NotificationTarget | null = null;
let lastHandledId: string | null = null;
let ready = false;

function open(target: NotificationTarget) {
  if (target.dealId) {
    navigationRef.dispatch(
      CommonActions.reset({
        index: 1,
        routes: [{ name: 'Main' }, { name: 'DealDetail', params: { id: target.dealId } }],
      }),
    );
  } else if (target.url && target.url !== '/') {
    navigationRef.navigate('Website', { path: target.url });
  } else {
    navigationRef.navigate('Main');
  }
}

async function handle(response: Notifications.NotificationResponse | null) {
  if (!response) return;
  const id = response.notification.request.identifier;
  if (id && id === lastHandledId) return;
  lastHandledId = id;
  const target = targetFromResponse(response);
  if (!target) return;
  if (ready && navigationRef.isReady() && (await isSignedIn())) {
    open(target);
  } else {
    pending = target;
  }
  Notifications.clearLastNotificationResponseAsync().catch(() => {});
}

/** Call once at startup. Returns an unsubscribe function. */
export function startNotificationRouting(): () => void {
  const sub = Notifications.addNotificationResponseReceivedListener((r) => void handle(r));
  Notifications.getLastNotificationResponseAsync()
    .then((r) => handle(r))
    .catch(() => {});
  return () => sub.remove();
}

/** Called by the root once the user is signed in and the navigator is mounted. */
export function setRoutingReady(isReady: boolean) {
  ready = isReady;
  if (isReady && pending && navigationRef.isReady()) {
    const t = pending;
    pending = null;
    open(t);
  }
}
