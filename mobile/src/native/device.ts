/**
 * Anonymous per-install identity for the no-login app.
 *
 *   getDeviceId()     — stable 'app_<uuid>' id; key for saved deals, price alerts, follows.
 *   registerDevice()  — POST /api/devices/register; pass digest prefs to change them.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import { Platform } from 'react-native';

import { getBaseUrl, LIVE_HOST } from './config';

const DEVICE_KEY = 'dr-device-id';
const REGISTERED_KEY = 'dr.registered';

function uuidv4(): string {
  const bytes = new Uint8Array(16);
  const c = (globalThis as any).crypto;
  if (c?.getRandomValues) c.getRandomValues(bytes);
  else for (let i = 0; i < 16; i++) bytes[i] = Math.floor(Math.random() * 256);
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const h = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
  return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

let cached: string | null = null;
let creating: Promise<string> | null = null;

/** Synchronous best-effort read — '' until getDeviceId() has resolved once. */
export function peekDeviceId(): string {
  return cached ?? '';
}

export function getDeviceId(): Promise<string> {
  if (cached) return Promise.resolve(cached);
  if (!creating) {
    creating = (async () => {
      let id = await AsyncStorage.getItem(DEVICE_KEY).catch(() => null);
      if (!id) {
        id = `app_${uuidv4()}`;
        await AsyncStorage.setItem(DEVICE_KEY, id).catch(() => {});
      }
      cached = id;
      return id;
    })().finally(() => {
      creating = null;
    });
  }
  return creating;
}

export type DeviceRegistration = {
  push_token?: string | null;
  digest?: boolean;
  digest_hour?: number;
};

export type RegisteredDevice = Record<string, unknown>;

async function server(): Promise<string> {
  return (await getBaseUrl()) ?? LIVE_HOST;
}

/**
 * Omitted fields are left unchanged on the server. Returns the server's device
 * record, or null when offline / rejected. Never throws.
 */
export async function registerDevice(opts: DeviceRegistration = {}): Promise<RegisteredDevice | null> {
  try {
    const device_id = await getDeviceId();
    const body: Record<string, unknown> = {
      device_id,
      platform: Platform.OS === 'ios' ? 'ios' : 'android',
      // This build times routine alerts on the phone (smartNotify.ts); the server
      // should only push price drops and announcements to it directly.
      smart_schedule: true,
    };
    if (opts.push_token) body.push_token = opts.push_token;
    if (typeof opts.digest === 'boolean') body.digest = opts.digest;
    if (typeof opts.digest_hour === 'number') body.digest_hour = Math.max(0, Math.min(23, Math.round(opts.digest_hour)));
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30_000);
    try {
      const res = await fetch(`${await server()}/api/devices/register`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!res.ok) return null;
      const json = (await res.json().catch(() => ({}))) as { device?: RegisteredDevice };
      if (opts.push_token) await AsyncStorage.setItem(REGISTERED_KEY, opts.push_token).catch(() => {});
      return json.device ?? {};
    } finally {
      clearTimeout(timer);
    }
  } catch (e) {
    console.warn('[device] register failed:', (e as Error)?.message ?? e);
    return null;
  }
}

/** The push token the server last accepted for this device, if any. */
export async function getRegisteredPushToken(): Promise<string | null> {
  return AsyncStorage.getItem(REGISTERED_KEY).catch(() => null);
}
