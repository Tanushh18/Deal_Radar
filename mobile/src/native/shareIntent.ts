/**
 * "Share → DealRadar" from Amazon, Flipkart, etc. Shared text usually wraps
 * the link in marketing copy ("Check out this product on Amazon https://amzn.in/…"),
 * so the first http(s) URL is pulled out of it.
 */
import { useEffect } from 'react';
import { useShareIntent } from 'expo-share-intent';

import { routeTo } from './deepLinks';

export function extractSharedUrl(text: string | null | undefined): string | null {
  if (!text) return null;
  const m = /https?:\/\/[^\s<>"']+/i.exec(text);
  return m ? m[0].replace(/[).,;!?]+$/, '') : null;
}

export function useShareIntentRouting(): void {
  const { hasShareIntent, shareIntent, resetShareIntent } = useShareIntent({ resetOnBackground: true });
  useEffect(() => {
    if (!hasShareIntent) return;
    const url = shareIntent.webUrl || extractSharedUrl(shareIntent.text) || shareIntent.text?.trim() || undefined;
    routeTo({ kind: 'check', url });
    resetShareIntent();
  }, [hasShareIntent, shareIntent, resetShareIntent]);
}
