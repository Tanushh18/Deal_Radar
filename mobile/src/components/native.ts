import * as Clipboard from 'expo-clipboard';
import * as Haptics from 'expo-haptics';
import * as WebBrowser from 'expo-web-browser';
import { useEffect, useState } from 'react';
import { Linking } from 'react-native';

import { api } from '../api';
import { absoluteUrl, resolveServerUrl } from '../api/client';

export const haptic = {
  select: () => void Haptics.selectionAsync().catch(() => {}),
  light: () => void Haptics.impactAsync(Haptics.ImpactFeedbackStyle.Light).catch(() => {}),
  success: () =>
    void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => {}),
  error: () => void Haptics.notificationAsync(Haptics.NotificationFeedbackType.Error).catch(() => {}),
};

/**
 * Store links go through the OS first so Amazon/Flipkart links land in the
 * installed shopping app; the in-app browser is only the fallback.
 */
export async function openExternal(url: string | null | undefined): Promise<void> {
  if (!url) return;
  try {
    await Linking.openURL(url);
  } catch {
    try {
      await WebBrowser.openBrowserAsync(url);
    } catch {
      /* nothing left to try */
    }
  }
}

/** Buy button: open the affiliate link the server makes; on any failure open the plain store link. */
export async function openDealBuy(deal: { id: string; url?: string | null }): Promise<void> {
  let url = deal.url;
  try {
    url = (await api.deals.buyLink(deal.id)).url || url;
  } catch {
    /* offline or server error: the plain link still works */
  }
  await openExternal(url);
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await Clipboard.setStringAsync(text);
    haptic.success();
    return true;
  } catch {
    return false;
  }
}

let cachedBase: string | null = null;

/** The configured server origin, for resolving relative image URLs from the API. */
export function useServerUrl(): string | null {
  const [base, setBase] = useState<string | null>(cachedBase);
  useEffect(() => {
    let alive = true;
    resolveServerUrl()
      .then((b) => {
        cachedBase = b;
        if (alive) setBase(b);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);
  return base;
}

export function useImageUri(path: string | null | undefined): string | null {
  const base = useServerUrl();
  if (!path) return null;
  if (/^(https?:|data:)/i.test(path)) return path;
  return base ? absoluteUrl(base, path) : null;
}
