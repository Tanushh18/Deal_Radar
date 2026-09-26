import React, { useCallback } from 'react';
import { FlatList, Pressable, View } from 'react-native';
import { Text } from './Text';

import type { Deal, PriceVerdict, VerdictLevel } from '../api/types';
import { MONO_FAMILY, useTheme, type Theme } from '../theme';
import { Icon } from './Icon';
import { RailCard, RailSkeleton } from './DealCard';
import { SectionHead } from './ui';

const RAIL_ITEM = 160;
const PAD = 16;

export type RailTone = 'good' | 'hot' | 'muted';

export function DealRail({
  title,
  sub,
  deals,
  note,
  onOpen,
  onSeeAll,
  pad = PAD,
}: {
  title: string;
  sub?: string;
  deals: Deal[] | null;
  note: (d: Deal) => [string, RailTone];
  onOpen: (d: Deal) => void;
  onSeeAll?: () => void;
  pad?: number;
}) {
  const t = useTheme();
  const renderItem = useCallback(
    ({ item }: { item: Deal }) => {
      const [text, tone] = note(item);
      return (
        <View style={{ width: RAIL_ITEM }}>
          <RailCard deal={item} note={text} noteTone={tone} onOpen={onOpen} />
        </View>
      );
    },
    [note, onOpen],
  );
  if (deals && !deals.length) return null;
  return (
    <View style={{ gap: 10 }}>
      <SectionHead
        title={title}
        sub={sub}
        right={
          onSeeAll ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`See all: ${title}`}
              onPress={onSeeAll}
              hitSlop={10}
              style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 6 }}
            >
              <Text style={{ color: t.c.text2, fontWeight: '600', fontSize: t.f.sm }}>See all</Text>
            </Pressable>
          ) : undefined
        }
      />
      <FlatList
        horizontal
        data={deals ?? []}
        keyExtractor={(d) => d.id}
        showsHorizontalScrollIndicator={false}
        style={{ marginHorizontal: -pad }}
        contentContainerStyle={{ paddingHorizontal: pad }}
        getItemLayout={(_, index) => ({ length: RAIL_ITEM, offset: RAIL_ITEM * index, index })}
        initialNumToRender={4}
        windowSize={5}
        ListEmptyComponent={
          <View style={{ flexDirection: 'row', gap: 10 }}>
            {[0, 1, 2].map((i) => (
              <RailSkeleton key={i} />
            ))}
          </View>
        }
        renderItem={renderItem}
      />
    </View>
  );
}

export function verdictColors(t: Theme, level: VerdictLevel): { fg: string; bg: string } {
  switch (level) {
    case 'great':
      return { fg: t.c.good, bg: t.c.goodSoft };
    case 'good':
      return { fg: t.c.cyan, bg: t.c.cyanSoft };
    case 'fair':
      return { fg: t.c.warn, bg: t.c.warnSoft };
    default:
      return { fg: t.c.hot, bg: t.c.hotSoft };
  }
}

const HEADLINE: Record<VerdictLevel, string> = {
  great: 'Great time to buy',
  good: 'Good price',
  fair: 'Fair price — it has been lower',
  high: 'Higher than usual',
};

export type PriceRange = { low: number; high: number; usual: number | null; current: number };

const inr = (n: number) => `₹${Math.round(n).toLocaleString('en-IN')}`;

/**
 * "Is this a good price?" — the server's verdict in plain words, plus where
 * today's price sits between the lowest and highest we've recorded.
 */
export function PriceVerdictMeter({
  verdict,
  range,
  lowest,
}: {
  verdict: PriceVerdict | null;
  range?: PriceRange | null;
  lowest?: boolean;
}) {
  const t = useTheme();
  const pos = range ? Math.max(0, Math.min(1, (range.current - range.low) / (range.high - range.low))) : 0;
  // Without a server verdict, judge from where today's price sits in its own history.
  const atLow = !!range && range.current <= range.low;
  const level: VerdictLevel =
    verdict?.level ?? (lowest || atLow || pos <= 0.15 ? 'great' : pos <= 0.4 ? 'good' : pos <= 0.7 ? 'fair' : 'high');
  const fg = level === 'great' || level === 'good' ? t.c.good : level === 'fair' ? t.c.warn : t.c.hot;
  const headline = (lowest || atLow) && (level === 'great' || level === 'good') ? 'Lowest price we’ve seen' : HEADLINE[level];
  const sub =
    verdict?.label ??
    (range && range.usual != null && range.current < range.usual
      ? `${inr(range.usual - range.current)} below its usual price of ${inr(range.usual)}.`
      : range && range.usual != null && range.current > range.usual
        ? `Usually around ${inr(range.usual)} — it has been cheaper.`
        : null);
  const icon = level === 'great' || level === 'good' ? 'check' : level === 'fair' ? 'info' : 'alert';
  return (
    <View
      accessible
      accessibilityLabel={`${headline}. ${verdict?.label ?? ''}${range ? ` Lowest ${inr(range.low)}, highest ${inr(range.high)}.` : ''}`}
      style={{ padding: 14, borderRadius: t.r.md, backgroundColor: t.c.surface, borderWidth: 1, borderColor: t.c.border, gap: 4 }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Icon name={icon} size={17} color={fg} strokeWidth={2.4} />
        <Text style={{ color: fg, fontSize: t.f.base, fontWeight: '700' }}>{headline}</Text>
      </View>
      {sub ? <Text style={{ color: t.c.text2, fontSize: t.f.sm, lineHeight: 19 }}>{sub}</Text> : null}
      {range ? (
        <View style={{ marginTop: 12, gap: 8 }}>
          <View style={{ height: 6, borderRadius: 3, backgroundColor: t.c.surface3 }}>
            <View style={{ position: 'absolute', left: 0, top: 0, bottom: 0, width: `${pos * 100}%`, borderRadius: 3, backgroundColor: fg }} />
            <View
              style={{
                position: 'absolute',
                top: -5,
                left: `${pos * 100}%`,
                marginLeft: -8,
                width: 16,
                height: 16,
                borderRadius: 8,
                borderWidth: 3,
                borderColor: t.c.text,
                backgroundColor: t.c.surface,
              }}
            />
          </View>
          <View style={{ flexDirection: 'row', justifyContent: 'space-between' }}>
            {([
              ['Low', range.low],
              ['Usual', range.usual],
              ['High', range.high],
            ] as const).map(([k, v]) =>
              v == null ? (
                <View key={k} />
              ) : (
                <Text key={k} style={{ color: t.c.text3, fontSize: 11, fontFamily: MONO_FAMILY }}>
                  {k} <Text style={{ color: t.c.text, fontFamily: MONO_FAMILY, fontWeight: '600' }}>{inr(v)}</Text>
                </Text>
              ),
            )}
          </View>
        </View>
      ) : null}
    </View>
  );
}
