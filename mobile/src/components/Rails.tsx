import React, { useCallback } from 'react';
import { FlatList, Pressable, Text, View } from 'react-native';

import type { Deal, PriceVerdict, VerdictLevel } from '../api/types';
import { useTheme, type Theme } from '../theme';
import { RailCard, RailSkeleton } from './DealCard';
import { SectionHead } from './ui';

const RAIL_ITEM = 166;
const PAD = 14;

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
              <Text style={{ color: t.c.accent, fontWeight: '700', fontSize: t.f.sm }}>See all</Text>
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

const LEVELS: VerdictLevel[] = ['high', 'fair', 'good', 'great'];
const LEVEL_NAME: Record<VerdictLevel, string> = { great: 'Great', good: 'Good', fair: 'Fair', high: 'High' };

/** Four-step "is this a good price?" meter from the server's price_verdict. */
export function PriceVerdictMeter({ verdict }: { verdict: PriceVerdict }) {
  const t = useTheme();
  const { fg, bg } = verdictColors(t, verdict.level);
  const at = LEVELS.indexOf(verdict.level);
  return (
    <View
      accessible
      accessibilityLabel={`Price check: ${LEVEL_NAME[verdict.level]} price. ${verdict.label}`}
      style={{ padding: 14, borderRadius: t.r.md, backgroundColor: bg, borderWidth: 1, borderColor: fg + '55', gap: 10 }}
    >
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={{ color: fg, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.7, textTransform: 'uppercase' }}>
          Good-price meter
        </Text>
        <View style={{ flex: 1 }} />
        <View style={{ paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, backgroundColor: fg }}>
          <Text style={{ color: t.dark ? '#06101f' : '#ffffff', fontSize: 11, fontWeight: '800' }}>
            {LEVEL_NAME[verdict.level].toUpperCase()}
          </Text>
        </View>
      </View>
      <View style={{ flexDirection: 'row', gap: 4 }}>
        {LEVELS.map((l, i) => {
          const c = verdictColors(t, l).fg;
          return (
            <View key={l} style={{ flex: 1, gap: 4 }}>
              <View style={{ height: 6, borderRadius: 3, backgroundColor: i <= at ? c : t.c.border, opacity: i === at ? 1 : i < at ? 0.55 : 1 }} />
              <Text style={{ color: i === at ? c : t.c.text3, fontSize: 10.5, fontWeight: i === at ? '800' : '600', textAlign: 'center' }}>
                {LEVEL_NAME[l]}
              </Text>
            </View>
          );
        })}
      </View>
      <Text style={{ color: t.c.text, fontSize: t.f.sm, lineHeight: 19, fontWeight: '600' }}>{verdict.label}</Text>
    </View>
  );
}
