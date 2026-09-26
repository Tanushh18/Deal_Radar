import { Image } from 'expo-image';
import React, { useState } from 'react';
import { View } from 'react-native';

import { useTheme } from '../theme';
import { Text } from './Text';

// Store key -> its site, for the store's own icon. Any other store name
// falls back to "<name>.com", so a new store needs no app update.
const DOMAINS: Record<string, string> = {
  amazon: 'amazon.in',
  flipkart: 'flipkart.com',
  myntra: 'myntra.com',
  ajio: 'ajio.com',
  meesho: 'meesho.com',
  nykaa: 'nykaa.com',
  shopsy: 'shopsy.in',
  tatacliq: 'tatacliq.com',
  croma: 'croma.com',
  jiomart: 'jiomart.com',
  firstcry: 'firstcry.com',
  snapdeal: 'snapdeal.com',
};

export function storeDomain(store: string | null | undefined): string | undefined {
  const key = (store || '').trim().toLowerCase().replace(/\s+/g, '');
  if (!key || key === 'unknown') return undefined;
  return DOMAINS[key] ?? (key.includes('.') ? key : `${key}.com`);
}

/** The store's own site icon on a white tile; its first letter if the icon can't load. */
export function StoreLogo({ store, size = 20, radius }: { store: string | null | undefined; size?: number; radius?: number }) {
  const t = useTheme();
  const [failed, setFailed] = useState(false);
  const domain = storeDomain(store);
  if (!domain) return null;
  return (
    <View
      accessible={false}
      style={{
        width: size,
        height: size,
        borderRadius: radius ?? size * 0.28,
        backgroundColor: '#ffffff',
        borderWidth: 1,
        borderColor: t.c.border,
        alignItems: 'center',
        justifyContent: 'center',
        overflow: 'hidden',
      }}
    >
      {!failed ? (
        <Image
          source={{ uri: `https://www.google.com/s2/favicons?domain=${domain}&sz=64` }}
          style={{ width: size * 0.7, height: size * 0.7 }}
          contentFit="contain"
          cachePolicy="disk"
          recyclingKey={domain}
          onError={() => setFailed(true)}
        />
      ) : (
        <Text maxFontSizeMultiplier={1} style={{ color: '#1a1a1a', fontSize: size * 0.5, fontWeight: '700' }}>
          {domain.slice(0, 1).toUpperCase()}
        </Text>
      )}
    </View>
  );
}
