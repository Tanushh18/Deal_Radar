import { Image } from 'expo-image';
import { LinearGradient } from 'expo-linear-gradient';
import { useEffect, useState } from 'react';
import { Pressable, ScrollView, View, useWindowDimensions } from 'react-native';
import { Text } from './Text';

import { api } from '../api';
import type { SaleEvent } from '../api/types';
import { MONO_FAMILY, useTheme } from '../theme';
import { haptic, openExternal } from './native';
import { Icon } from './Icon';

/**
 * Upcoming store sales (Big Billion Days, Great Indian Festival…), fetched
 * once on mount. Renders nothing while loading or if there are none —
 * never an empty strip taking up space.
 */
export function SaleEventsRail({ onPick }: { onPick?: (store: string) => void }) {
  const [events, setEvents] = useState<SaleEvent[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    api.saleEvents
      .list()
      .then((r) => !cancelled && setEvents(r.events))
      .catch(() => !cancelled && setEvents([]));
    return () => {
      cancelled = true;
    };
  }, []);

  const { width } = useWindowDimensions();
  if (!events || !events.length) return null;
  const cardW = events.length === 1 ? width - 32 : Math.min(width - 56, 360);
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      snapToInterval={cardW + 10}
      decelerationRate="fast"
      style={{ marginHorizontal: -16 }}
      contentContainerStyle={{ paddingHorizontal: 16, gap: 10 }}
      accessibilityLabel="Upcoming sales"
    >
      {events.map((e) => (
        <SaleCard key={e.id} event={e} width={cardW} onDeals={onPick && e.store ? () => onPick(e.store.toLowerCase()) : undefined} />
      ))}
    </ScrollView>
  );
}

// Where "Open sale" goes. Stores put the running sale front and centre on their
// home page, so these always land on it; a server-provided url wins when present.
const SALE_URL: Record<string, string> = {
  amazon: 'https://www.amazon.in/',
  flipkart: 'https://www.flipkart.com/',
  myntra: 'https://www.myntra.com/',
  ajio: 'https://www.ajio.com/',
  meesho: 'https://www.meesho.com/',
  nykaa: 'https://www.nykaa.com/',
  shopsy: 'https://www.shopsy.in/',
};

// Each store's own colour on its badge, so a sale is recognisable at a glance.
// Each store's own sale look: the gradient its sale pages use, plus the accent
// for the "live" pill. Keeps a sale recognisable at a glance.
const STORE_THEME: Record<string, { colors: [string, string]; accent: string; domain: string }> = {
  amazon: { colors: ['#232f3e', '#131921'], accent: '#ff9900', domain: 'amazon.in' },
  flipkart: { colors: ['#2874f0', '#1c4fb8'], accent: '#ffe11b', domain: 'flipkart.com' },
  myntra: { colors: ['#ff3f6c', '#ff7a45'], accent: '#ffffff', domain: 'myntra.com' },
  ajio: { colors: ['#2c4152', '#1b2833'], accent: '#e8c77a', domain: 'ajio.com' },
  meesho: { colors: ['#9f2089', '#6d1560'], accent: '#ffffff', domain: 'meesho.com' },
  nykaa: { colors: ['#fc2779', '#c8175d'], accent: '#ffffff', domain: 'nykaa.com' },
  shopsy: { colors: ['#2874f0', '#1c4fb8'], accent: '#ffffff', domain: 'shopsy.in' },
};

/** The store's own site icon on a white tile; a letter if it can't load. */
function StoreLogo({ domain, letter, color }: { domain?: string; letter: string; color: string }) {
  const [failed, setFailed] = useState(false);
  return (
    <View style={{ width: 34, height: 34, borderRadius: 9, backgroundColor: '#ffffff', alignItems: 'center', justifyContent: 'center', overflow: 'hidden' }}>
      {domain && !failed ? (
        <Image
          source={{ uri: `https://www.google.com/s2/favicons?domain=${domain}&sz=128` }}
          style={{ width: 24, height: 24 }}
          contentFit="contain"
          cachePolicy="disk"
          onError={() => setFailed(true)}
          accessible={false}
        />
      ) : (
        <Text maxFontSizeMultiplier={1} style={{ color, fontSize: 16, fontWeight: '800' }}>
          {letter}
        </Text>
      )}
    </View>
  );
}

const DAY = 86_400_000;

type Timing = { status: string; detail: string; live: boolean };

function when(e: SaleEvent): Timing {
  const now = Date.now();
  const start = e.starts_at ? e.starts_at * 1000 : null;
  const end = e.ends_at ? e.ends_at * 1000 : null;
  const range = `${fmtDate(e.starts_at)}${e.ends_at ? ` – ${fmtDate(e.ends_at)}` : ''}${e.approximate ? ' (expected)' : ''}`;
  if (start && start > now) {
    const hours = Math.ceil((start - now) / 3_600_000);
    const days = Math.ceil((start - now) / DAY);
    const status = hours < 24 ? `Starts in ${hours}h` : days === 1 ? 'Starts tomorrow' : `Starts in ${days} days`;
    return { status, detail: range, live: false };
  }
  if (end && end > now) {
    const days = Math.ceil((end - now) / DAY);
    return { status: 'Live now', detail: days <= 1 ? 'Ends tomorrow' : `Ends ${fmtDate(e.ends_at)}`, live: true };
  }
  return { status: 'Live now', detail: range, live: true };
}

function fmtDate(ts: number | null): string {
  if (!ts) return '';
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}

function SaleCard({ event, width, onDeals }: { event: SaleEvent; width: number; onDeals?: () => void }) {
  const t = useTheme();
  if (event.image_url) {
    return (
      <Pressable
        accessibilityRole="link"
        accessibilityLabel={`Open ${event.name}`}
        onPress={() => {
          haptic.light();
          openExternal(event.url || SALE_URL[(event.store || '').toLowerCase()] || 'https://www.google.com/');
        }}
        style={{ width, height: width * 0.45, borderRadius: t.r.lg, overflow: 'hidden' }}
      >
        <Image source={{ uri: event.image_url }} style={{ width: '100%', height: '100%' }} contentFit="cover" cachePolicy="disk" />
      </Pressable>
    );
  }
  // Everything on the card comes from the store name typed in the admin panel:
  // known stores get their own look; any other name falls back to <name>.com.
  const key = (event.store || '').trim().toLowerCase();
  const domain = STORE_THEME[key]?.domain ?? (key ? (key.includes('.') ? key : `${key.replace(/\s+/g, '')}.com`) : undefined);
  const theme = STORE_THEME[key];
  const colors: [string, string] = theme?.colors ?? (t.dark ? [t.c.surface3, t.c.surface2] : [t.c.text, '#2d2a22']);
  const bg = colors[0];
  const accent = theme?.accent ?? t.c.hot;
  const timing = when(event);
  const url = (event as SaleEvent & { url?: string }).url || SALE_URL[key] || (domain ? `https://www.${domain}/` : null);
  const storeLabel = key ? key.replace(/\.(com|in)$/, '').replace(/\b\w/g, (c) => c.toUpperCase()) : 'Sale';
  return (
    <LinearGradient
      colors={colors}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ width, borderRadius: t.r.lg, padding: 16, gap: 10, overflow: 'hidden' }}
    >
      {/* Soft rings in the corner — the radar motif, in the store's colour. */}
      <View pointerEvents="none" style={{ position: 'absolute', right: -40, top: -40, width: 150, height: 150, borderRadius: 75, borderWidth: 18, borderColor: 'rgba(255,255,255,0.07)' }} />
      <View pointerEvents="none" style={{ position: 'absolute', right: 5, top: 5, width: 60, height: 60, borderRadius: 30, borderWidth: 10, borderColor: 'rgba(255,255,255,0.07)' }} />

      <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 9 }}>
          <StoreLogo domain={domain} letter={storeLabel.slice(0, 1)} color={bg} />
          <Text style={{ color: 'rgba(255,255,255,0.85)', fontSize: 11, fontFamily: MONO_FAMILY, letterSpacing: 0.6 }}>
            {storeLabel.toUpperCase()} · SALE
          </Text>
        </View>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, paddingHorizontal: 9, paddingVertical: 4, borderRadius: 999, backgroundColor: timing.live ? accent : 'rgba(255,255,255,0.16)' }}>
          {timing.live ? <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: accent === '#ffffff' ? bg : '#1a1a1a' }} /> : null}
          <Text style={{ color: timing.live ? (accent === '#ffffff' ? bg : '#1a1a1a') : '#ffffff', fontSize: 11.5, fontWeight: '700' }}>{timing.status}</Text>
        </View>
      </View>

      <View style={{ gap: 3 }}>
        <Text numberOfLines={2} maxFontSizeMultiplier={1.3} style={{ color: '#ffffff', fontSize: 21, fontWeight: '800', letterSpacing: -0.5, lineHeight: 25 }}>
          {event.name}
        </Text>
        <Text numberOfLines={1} maxFontSizeMultiplier={1.3} style={{ color: 'rgba(255,255,255,0.8)', fontSize: t.f.sm }}>
          {timing.detail}
        </Text>
        {event.hype ? (
          <Text numberOfLines={2} maxFontSizeMultiplier={1.3} style={{ color: 'rgba(255,255,255,0.7)', fontSize: t.f.xs, lineHeight: 17, marginTop: 2 }}>
            {event.hype}
          </Text>
        ) : null}
      </View>

      <View style={{ flexDirection: 'row', gap: 8, marginTop: 'auto', paddingTop: 2 }}>
        {url ? (
          <Pressable
            accessibilityRole="link"
            accessibilityLabel={`Open ${event.name} on ${storeLabel}`}
            onPress={() => {
              haptic.light();
              openExternal(url);
            }}
            style={({ pressed }) => ({
              flex: 1,
              minHeight: 42,
              borderRadius: t.r.sm,
              backgroundColor: '#ffffff',
              flexDirection: 'row',
              alignItems: 'center',
              justifyContent: 'center',
              gap: 6,
              opacity: pressed ? 0.85 : 1,
            })}
          >
            <Text style={{ color: bg, fontSize: t.f.sm, fontWeight: '700' }}>{timing.live ? 'Shop the sale' : 'Open sale'}</Text>
            <Icon name="external" size={14} color={bg} />
          </Pressable>
        ) : null}
        {onDeals ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`See ${storeLabel} deals on DealRadar`}
            onPress={onDeals}
            style={({ pressed }) => ({
              flex: 1,
              minHeight: 42,
              borderRadius: t.r.sm,
              borderWidth: 1,
              borderColor: 'rgba(255,255,255,0.45)',
              alignItems: 'center',
              justifyContent: 'center',
              opacity: pressed ? 0.8 : 1,
            })}
          >
            <Text style={{ color: '#ffffff', fontSize: t.f.sm, fontWeight: '600' }}>{storeLabel} deals</Text>
          </Pressable>
        ) : null}
      </View>
    </LinearGradient>
  );
}
