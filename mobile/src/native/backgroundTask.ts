/**
 * Periodic deal-alert polling while the app is in the background.
 *
 * `defineTask` must run at module scope on every JS start — including the
 * headless start Android does to run the task — so index.ts imports this file
 * before registering the root component.
 */
import * as BackgroundTask from 'expo-background-task';
import * as TaskManager from 'expo-task-manager';

import { pollNotifications } from './notifications';

export const POLL_TASK = 'dealradar-poll-notifications';

TaskManager.defineTask(POLL_TASK, async () => {
  try {
    await pollNotifications();
    return BackgroundTask.BackgroundTaskResult.Success;
  } catch {
    return BackgroundTask.BackgroundTaskResult.Failed;
  }
});

export async function startBackgroundPolling(): Promise<void> {
  try {
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
