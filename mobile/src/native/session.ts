/**
 * The contract between the screens and the native layer.
 *
 *   getServerUrl()  — the configured DealRadar origin (no trailing slash).
 *   onSignedIn(u)   — call after a successful login, and it is also called by
 *                     the auth gate on every start while signed in. Asks for
 *                     notification permission, registers a push token when the
 *                     build supports push, and starts alert polling.
 *   onSignedOut()   — call BEFORE POST /api/auth/logout (the push-unregister
 *                     call needs the session cookie); it is harmless after.
 *                     Stops polling, forgets the cursor, resets nav to Login.
 *   startVisitorSession() — the no-login path, called by App once public mode is on.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import * as Notifications from 'expo-notifications';
import { CommonActions } from '@react-navigation/native';

import { navigationRef } from '../navigation/types';
import { startBackgroundPolling, stopBackgroundPolling } from './backgroundTask';
import { getBaseUrl, LIVE_HOST, STORAGE_KEYS } from './config';
import { setRoutingReady } from './deepLinks';
import { getRegisteredPushToken, registerDevice } from './device';
import {
  configureNotificationHandler,
  ensureChannel,
  getPushToken,
  hasPermission,
  pollNotifications,
  requestPermission,
  resetPollingState,
} from './notifications';

export type SessionUser = {
  id?: number | string;
  username?: string | null;
  first_name?: string | null;
  [k: string]: unknown;
};

let publicMode = false;
export const isPublicMode = () => publicMode;
export function setPublicMode(on: boolean): void {
  publicMode = on;
}

export async function getServerUrl(): Promise<string> {
  return (await getBaseUrl()) ?? LIVE_HOST;
}

async function post(path: string, body: unknown): Promise<Response | null> {
  try {
    const base = await getServerUrl();
    return await fetch(base + path, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
      body: JSON.stringify(body),
    });
  } catch {
    return null;
  }
}

let signInWork: Promise<void> | null = null;

export function onSignedIn(_user?: SessionUser | null): Promise<void> {
  if (!signInWork) {
    signInWork = doSignedIn().finally(() => {
      signInWork = null;
    });
  }
  return signInWork;
}

async function doSignedIn(): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEYS.signedIn, '1');
  configureNotificationHandler();
  setRoutingReady(true); // deliver any notification tap held during login
  let granted = false;
  try {
    granted = await requestPermission();
  } catch (e) {
    console.warn('[session] permission request failed:', (e as Error)?.message ?? e);
  }

  let pushRegistered = false;
  if (granted) {
    const token = await getPushToken();
    if (token) {
      const res = await post('/api/push/register', { token, platform: 'android' });
      if (res?.ok) {
        await AsyncStorage.setItem(STORAGE_KEYS.pushToken, token);
        pushRegistered = true;
      }
    }
  }
  if (!pushRegistered) await AsyncStorage.removeItem(STORAGE_KEYS.pushToken);

  // Without push, polling is the delivery path (background task + every
  // foreground). With push active the server delivers; foreground polls then
  // only advance the cursor, so nothing is shown twice.
  if (!pushRegistered) await startBackgroundPolling();
  else await stopBackgroundPolling();
  await pollNotifications();
}

let signOutWork: Promise<void> | null = null;

/** Idempotent: concurrent calls (e.g. several 401s at once) share one run. */
export function onSignedOut(): Promise<void> {
  if (!signOutWork) {
    signOutWork = doSignedOut().finally(() => {
      signOutWork = null;
    });
  }
  return signOutWork;
}

async function doSignedOut(): Promise<void> {
  setRoutingReady(false);
  const token = await AsyncStorage.getItem(STORAGE_KEYS.pushToken);
  if (token) await post('/api/push/unregister', { token });
  await AsyncStorage.multiRemove([STORAGE_KEYS.signedIn, STORAGE_KEYS.pushToken]);
  await resetPollingState();
  await stopBackgroundPolling();
  try {
    await Notifications.dismissAllNotificationsAsync();
  } catch {
    /* nothing presented */
  }
  // Guests have no session to lose; a stray 401 must not strand them on a login screen.
  if (!publicMode && navigationRef.isReady() && navigationRef.getCurrentRoute()?.name !== 'Login') {
    navigationRef.dispatch(CommonActions.reset({ index: 0, routes: [{ name: 'Login' }] }));
  }
}

// ---------------------------------------------------------------- visitor (no login)

const PERMISSION_ASKED_KEY = 'dr.notifAsked';
const PERMISSION_DELAY_MS = 6_000;

let visitorStarted = false;
let tokenSub: { remove(): void } | null = null;
let permissionTimer: ReturnType<typeof setTimeout> | null = null;

async function syncPushToken(): Promise<void> {
  if (!(await hasPermission())) return;
  const token = await getPushToken();
  if (!token) return;
  if (token === (await getRegisteredPushToken())) return;
  const device = await registerDevice({ push_token: token });
  if (device) await AsyncStorage.setItem(STORAGE_KEYS.pushToken, token);
}

/**
 * Registers the anonymous device, asks for notification permission a few
 * seconds after the first launch (never at cold start), keeps the push token
 * current, and starts the feed poller. Idempotent.
 */
export async function startVisitorSession(): Promise<void> {
  if (visitorStarted) return;
  visitorStarted = true;
  configureNotificationHandler();
  await ensureChannel();
  const token = await getRegisteredPushToken();
  await registerDevice(token ? { push_token: token } : {});

  if (await hasPermission()) {
    await syncPushToken();
  } else if (!(await AsyncStorage.getItem(PERMISSION_ASKED_KEY))) {
    permissionTimer = setTimeout(async () => {
      permissionTimer = null;
      await AsyncStorage.setItem(PERMISSION_ASKED_KEY, '1').catch(() => {});
      try {
        if (await requestPermission()) await syncPushToken();
      } catch (e) {
        console.warn('[session] permission request failed:', (e as Error)?.message ?? e);
      }
    }, PERMISSION_DELAY_MS);
  }

  // Fires with the native FCM token; the Expo token wrapping it changes with it.
  tokenSub = Notifications.addPushTokenListener(() => {
    syncPushToken().catch(() => {});
  });

  await startBackgroundPolling();
  await pollNotifications();
}

/** For a Settings toggle: prompts if still allowed, then registers the token. */
export async function enableNotifications(): Promise<boolean> {
  await AsyncStorage.setItem(PERMISSION_ASKED_KEY, '1').catch(() => {});
  const granted = await requestPermission().catch(() => false);
  if (granted) await syncPushToken().catch(() => {});
  return granted;
}

export function stopVisitorSession(): void {
  visitorStarted = false;
  tokenSub?.remove();
  tokenSub = null;
  if (permissionTimer) clearTimeout(permissionTimer);
  permissionTimer = null;
}
