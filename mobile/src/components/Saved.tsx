import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, memo, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Pressable, type StyleProp, type ViewStyle } from 'react-native';
import Svg, { Path } from 'react-native-svg';

import type { Deal } from '../api/types';
import { useTheme } from '../theme';
import { haptic } from './native';
import { useReduceMotion } from './motion';
import { useToast } from './Toast';

const SAVED_KEY = 'dr.saved';

// Same fields as the website's SNAPSHOT so a saved deal renders as a card when it's gone from the server.
const SNAPSHOT = [
  'id',
  'title',
  'price',
  'mrp',
  'discount_pct',
  'saving',
  'store',
  'image_url',
  'url',
  'coupon',
  'flags',
  'posted_at',
  'repost_count',
  'status',
  'is_lowest',
  'score',
  'price_history_url',
] as const;

export type SavedDeal = Pick<Deal, (typeof SNAPSHOT)[number]> & { savedAt: number };

const DEAL_DEFAULTS: Omit<Deal, 'id' | 'title'> = {
  price: null,
  mrp: null,
  saving: null,
  discount_pct: 0,
  currency: 'INR',
  store: null,
  url: null,
  image_url: null,
  coupon: null,
  category: null,
  subcategory: null,
  brand: null,
  sizes: null,
  channel_title: null,
  posted_at: null,
  expires_at: null,
  repost_count: 1,
  status: 'live',
  score: 0,
  is_lowest: false,
  flags: [],
  relevance: null,
};

export function snapshotToDeal(s: SavedDeal): Deal {
  const clean = Object.fromEntries(Object.entries(s).filter(([, v]) => v != null));
  return { ...DEAL_DEFAULTS, ...clean, id: s.id, title: s.title ?? 'Saved deal' } as Deal;
}

type SavedMap = Record<string, SavedDeal>;

type Ctx = {
  saved: SavedMap;
  ready: boolean;
  isSaved: (id: string) => boolean;
  toggle: (deal: Deal) => boolean;
  remove: (id: string) => void;
  count: number;
};

const SavedContext = createContext<Ctx | null>(null);

export function SavedProvider({ children }: { children: React.ReactNode }) {
  const [saved, setSaved] = useState<SavedMap>({});
  const [ready, setReady] = useState(false);
  const savedRef = useRef(saved);
  savedRef.current = saved;
  const toast = useToast();

  useEffect(() => {
    AsyncStorage.getItem(SAVED_KEY)
      .then((raw) => {
        const parsed = raw ? (JSON.parse(raw) as unknown) : null;
        if (parsed && typeof parsed === 'object') setSaved(parsed as SavedMap);
      })
      .catch(() => {})
      .finally(() => setReady(true));
  }, []);

  const persist = useCallback((next: SavedMap) => {
    savedRef.current = next;
    setSaved(next);
    AsyncStorage.setItem(SAVED_KEY, JSON.stringify(next)).catch(() => {});
  }, []);

  const toggle = useCallback(
    (deal: Deal) => {
      const next = { ...savedRef.current };
      let on: boolean;
      if (next[deal.id]) {
        delete next[deal.id];
        on = false;
        toast('Removed from saved.', 'info', 2200);
      } else {
        const snap = Object.fromEntries(SNAPSHOT.map((k) => [k, deal[k] ?? null])) as unknown as SavedDeal;
        next[deal.id] = { ...snap, savedAt: Date.now() };
        on = true;
        toast('Saved ♡ — find it under Saved.', 'ok', 2200);
      }
      persist(next);
      return on;
    },
    [persist, toast],
  );

  const remove = useCallback(
    (id: string) => {
      if (!savedRef.current[id]) return;
      const next = { ...savedRef.current };
      delete next[id];
      persist(next);
    },
    [persist],
  );

  const isSaved = useCallback((id: string) => !!saved[id], [saved]);

  const value = useMemo(
    () => ({ saved, ready, isSaved, toggle, remove, count: Object.keys(saved).length }),
    [saved, ready, isSaved, toggle, remove],
  );
  return <SavedContext.Provider value={value}>{children}</SavedContext.Provider>;
}

export function useSaved(): Ctx {
  const ctx = useContext(SavedContext);
  if (!ctx) throw new Error('useSaved must be used inside <SavedProvider>');
  return ctx;
}

export function HeartGlyph({ on, size = 20, color, fill }: { on: boolean; size?: number; color: string; fill: string }) {
  return (
    <Svg width={size} height={size} viewBox="0 0 24 24" accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Path
        d="M20.8 4.6a5.5 5.5 0 00-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 00-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 000-7.8z"
        fill={on ? fill : 'none'}
        stroke={on ? fill : color}
        strokeWidth={2}
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </Svg>
  );
}

export const HeartButton = memo(function HeartButton({
  deal,
  size = 18,
  variant = 'overlay',
  style,
}: {
  deal: Deal;
  size?: number;
  variant?: 'overlay' | 'soft';
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const { isSaved, toggle } = useSaved();
  const reduce = useReduceMotion();
  const on = isSaved(deal.id);
  const scale = useRef(new Animated.Value(1)).current;

  const press = () => {
    const next = toggle(deal);
    if (next) haptic.success();
    else haptic.select();
    if (reduce) return;
    scale.setValue(0.6);
    Animated.spring(scale, { toValue: 1, friction: 3, tension: 180, useNativeDriver: true }).start();
  };

  const overlay = variant === 'overlay';
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={on ? 'Remove from saved' : 'Save deal'}
      accessibilityState={{ selected: on }}
      onPress={press}
      hitSlop={overlay ? 6 : 0}
      style={({ pressed }) => [
        {
          width: overlay ? 34 : 44,
          height: overlay ? 34 : 44,
          borderRadius: overlay ? 17 : t.r.sm,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: overlay ? (t.dark ? 'rgba(17,24,39,0.78)' : 'rgba(255,255,255,0.92)') : t.c.surface2,
          borderWidth: 1,
          borderColor: on ? t.c.hotSoft : t.c.border,
          opacity: pressed ? 0.8 : 1,
        },
        style,
      ]}
    >
      <Animated.View style={{ transform: [{ scale }] }}>
        <HeartGlyph on={on} size={size} color={t.c.text2} fill="#ef4444" />
      </Animated.View>
    </Pressable>
  );
});
