/**
 * Everything that must be registered at module scope on every JS start,
 * including the headless starts Android does for background work — so
 * index.ts imports this file before registering the root component.
 */
import * as BackgroundTask from 'expo-background-task';
import * as Notifications from 'expo-notifications';
import * as TaskManager from 'expo-task-manager';

import { handleNotifeeEvent } from './deepLinks';
import { getNotifee, markPushDelivered, pollNotifications } from './notifications';

export const POLL_TASK = 'dealradar-poll-feed';
const LEGACY_POLL_TASK = 'dealradar-poll-notifications';
const PUSH_TASK = 'dealradar-push-received';

TaskManager.defineTask(POLL_TASK, async () => {
  try {
    await pollNotifications();
    return BackgroundTask.BackgroundTaskResult.Success;
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});

TaskManager.defineTask(PUSH_TASK, async ({ data }) => {
  try {
    const raw = (data as any)?.notification?.data ?? (data as any)?.data ?? {};
    let payload = raw;
    if (typeof raw.body === 'string') {
      try {
        payload = JSON.parse(raw.body);
      } catch {
        /* not JSON */
      }
    }
    await markPushDelivered(payload?.deal_id ?? raw?.deal_id);
  } catch {
    /* best effort */
  }
});

const nf = getNotifee();
if (nf) {
  nf.default.onBackgroundEvent(async ({ type, detail }) => {
    await handleNotifeeEvent(type, detail as any);
  });
}

export async function startBackgroundPolling(): Promise<void> {
  try {
    if (await TaskManager.isTaskRegisteredAsync(LEGACY_POLL_TASK)) {
      await BackgroundTask.unregisterTaskAsync(LEGACY_POLL_TASK).catch(() => {});
    }
    await Notifications.registerTaskAsync(PUSH_TASK).catch(() => {});
    const status = await BackgroundTask.getStatusAsync();
    if (status !== BackgroundTask.BackgroundTaskStatus.Available) return;
    if (await TaskManager.isTaskRegisteredAsync(POLL_TASK)) return;
    await BackgroundTask.registerTaskAsync(POLL_TASK, { minimumInterval: 15 });
  } catch (e) {
    console.warn('[background] register failed:', (e as Error)?.message ?? e);
  }
}

export async function stopBackgroundPolling(): Promise<void> {
  try {
    if (await TaskManager.isTaskRegisteredAsync(POLL_TASK)) {
      await BackgroundTask.unregisterTaskAsync(POLL_TASK);
    }
  } catch (e) {
    console.warn('[background] unregister failed:', (e as Error)?.message ?? e);
  }
}
