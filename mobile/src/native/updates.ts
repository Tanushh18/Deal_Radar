/**
 * Over-the-air JS updates (eas update --branch preview). expo-updates already
 * checks ON_LOAD and applies on the next cold start; this additionally applies
 * an update right away if it finishes downloading while the user is still on
 * the boot/connecting screen, where a reload costs nothing.
 */
import * as Updates from 'expo-updates';

let applyNow = true;

export function stopImmediateUpdates(): void {
  applyNow = false;
}

export async function checkForUpdateOnLaunch(): Promise<void> {
  if (__DEV__ || !Updates.isEnabled) return;
  try {
    const check = await Updates.checkForUpdateAsync();
    if (!check.isAvailable) return;
    const result = await Updates.fetchUpdateAsync();
    if (result.isNew && applyNow) await Updates.reloadAsync();
  } catch (e) {
    console.warn('[updates] check failed:', (e as Error)?.message ?? e);
  }
}
