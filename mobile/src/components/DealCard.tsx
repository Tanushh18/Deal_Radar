import { Image } from 'expo-image';
import React, { memo } from 'react';
import { Pressable, Text, View, type StyleProp, type TextStyle, type ViewStyle } from 'react-native';

import { api } from '../api';
import type { Deal } from '../api/types';
import { makeStyles, useTheme } from '../theme';
import { Sparkline } from './Charts';
import { dealBadge, highlightParts, money, storeName, timeAgo, type BadgeKind } from './format';
import { Icon } from './Icon';
import { useDealActions } from './DealActions';
import { openExternal, useImageUri } from './native';
import { recordDealSignal } from '../native/smartNotify';
import { HeartButton } from './Saved';
import { Skeleton } from './ui';

// A small module-level cache: cards remount on scroll (FlatList recycling),
// and there's no reason to refetch the same product's sparkline every time.
const sparklineCache = new Map<string, number[]>();

function useCardSparkline(deal: Deal): number[] {
  const [points, setPoints] = React.useState<number[]>(() => sparklineCache.get(deal.id) ?? []);
  React.useEffect(() => {
    if (isPastDeal(deal) || sparklineCache.has(deal.id)) return;
    let live = true;
    api.deals
      .history(deal.id)
      .then((h) => {
        const series = (h.points || []).map((p) => p.price).slice(-8);
        sparklineCache.set(deal.id, series);
        if (live) setPoints(series);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, [deal.id]);
  return points;
}

const recent = new Map<string, Deal>();

/** Lets the detail screen paint instantly (and show archive deals the server no longer has). */
export function rememberDeal(deal: Deal): void {
  recent.delete(deal.id);
  recent.set(deal.id, deal);
  if (recent.size > 80) recent.delete(recent.keys().next().value as string);
}
export const peekDeal = (id: string): Deal | undefined => recent.get(id);

export const isPastDeal = (deal: Pick<Deal, 'status'>): boolean => !!deal.status && deal.status !== 'live';

export function PastBadge({ small }: { small?: boolean }) {
  const t = useTheme();
  return (
    <View style={{ backgroundColor: t.c.surface3, borderRadius: 7, paddingHorizontal: small ? 6 : 8, paddingVertical: 3, alignSelf: 'flex-start', borderWidth: 1, borderColor: t.c.borderStrong }}>
      <Text numberOfLines={1} maxFontSizeMultiplier={1.2} style={{ color: t.c.text2, fontSize: small ? 9.5 : 10.5, fontWeight: '800', letterSpacing: 0.2 }}>
        Past deal
      </Text>
    </View>
  );
}

export function Highlight({
  text,
  query,
  style,
  numberOfLines,
}: {
  text: string;
  query: string;
  style?: StyleProp<TextStyle>;
  numberOfLines?: number;
}) {
  const t = useTheme();
  const parts = highlightParts(text, query);
  return (
    <Text style={style} numberOfLines={numberOfLines} maxFontSizeMultiplier={1.4}>
      {parts.map((p, i) =>
        p.match ? (
          <Text key={i} style={{ color: t.c.accent, fontWeight: '800' }}>
            {p.text}
          </Text>
        ) : (
          p.text
        ),
      )}
    </Text>
  );
}

export function StatusBadge({ kind, label, small }: { kind: BadgeKind; label: string; small?: boolean }) {
  const t = useTheme();
  const bg = kind === 'low' ? t.c.badgeLowBg : kind === 'hot' ? t.c.badgeHotBg : t.c.badgeNewBg;
  const fg = kind === 'low' ? t.c.badgeLowText : t.c.badgeText;
  return (
    <View style={{ backgroundColor: bg, borderRadius: 7, paddingHorizontal: small ? 6 : 8, paddingVertical: 3, alignSelf: 'flex-start' }}>
      <Text
        numberOfLines={1}
        maxFontSizeMultiplier={1.2}
        style={{ color: fg, fontSize: small ? 9.5 : 10.5, fontWeight: '800', letterSpacing: 0.2 }}
      >
        {label}
      </Text>
    </View>
  );
}

export function PriceRow({ deal, size = 'md' }: { deal: Pick<Deal, 'price' | 'mrp' | 'discount_pct'> & { flags?: string[] }; size?: 'sm' | 'md' | 'lg' }) {
  const t = useTheme();
  const now = size === 'lg' ? 30 : size === 'md' ? 18.5 : 16;
  const flags = deal.flags ?? [];
  // A sale ("up to 87%") or a floor price ("from ₹509") must not read as one exact product price.
  const upto = flags.includes('upto_discount');
  const from = flags.includes('price_from') && deal.price != null;
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', columnGap: 7, rowGap: 2 }}>
      {deal.discount_pct >= 5 ? (
        <View style={{ backgroundColor: t.c.hotSoft, borderRadius: 6, paddingHorizontal: 7, paddingVertical: 2 }}>
          <Text maxFontSizeMultiplier={1.2} style={{ color: t.c.hot, fontWeight: '800', fontSize: size === 'lg' ? 14 : 12 }}>
            {upto ? 'Up to ' : '-'}{deal.discount_pct}%
          </Text>
        </View>
      ) : null}
      <Text
        maxFontSizeMultiplier={1.3}
        style={{ color: t.c.text, fontSize: now, fontWeight: '800', letterSpacing: -0.5, fontVariant: ['tabular-nums'] }}
      >
        {from ? <Text style={{ fontSize: now * 0.62, fontWeight: '600', color: t.c.text2 }}>From </Text> : null}
        {money(deal.price)}
      </Text>
      {deal.mrp ? (
        <Text
          maxFontSizeMultiplier={1.3}
          style={{ color: t.c.text3, fontSize: size === 'lg' ? 15 : 12, textDecorationLine: 'line-through' }}
        >
          {money(deal.mrp)}
        </Text>
      ) : null}
    </View>
  );
}

export function DealImage({ deal, style, emojiSize = 34 }: { deal: Pick<Deal, 'image_url'>; style?: StyleProp<ViewStyle>; emojiSize?: number }) {
  const t = useTheme();
  const uri = useImageUri(deal.image_url);
  const [failed, setFailed] = React.useState(false);
  return (
    <View style={[{ backgroundColor: t.c.mediaBg, overflow: 'hidden', alignItems: 'center', justifyContent: 'center' }, style]}>
      <Text style={{ fontSize: emojiSize, position: 'absolute' }} accessibilityElementsHidden importantForAccessibility="no">
        🛍️
      </Text>
      {uri && !failed ? (
        <Image
          source={{ uri }}
          // contain, not cover: a cropped product photo is the fastest way to look amateur.
          contentFit="contain"
          transition={200}
          recyclingKey={uri}
          cachePolicy="memory-disk"
          onError={() => setFailed(true)}
          style={{ position: 'absolute', top: 8, left: 8, right: 8, bottom: 8, backgroundColor: t.c.mediaBg }}
          accessible={false}
        />
      ) : null}
    </View>
  );
}

type CardProps = {
  deal: Deal;
  layout: 'grid' | 'list';
  query?: string;
  width?: number;
  onOpen: (deal: Deal) => void;
};

export const DealCard = memo(function DealCard({ deal, layout, query = '', width, onOpen }: CardProps) {
  const s = useCardStyles();
  const t = useTheme();
  const { open: openActions } = useDealActions();
  const past = isPastDeal(deal);
  const badge = past ? null : dealBadge(deal);
  const store = storeName(deal);
  const list = layout === 'list';
  const suspicious = (deal.flags ?? []).includes('suspicious_mrp');
  const sparkline = useCardSparkline(deal);
  const a11y = [
    past ? 'Past deal' : '',
    deal.title,
    money(deal.price),
    deal.discount_pct >= 5 ? `${deal.discount_pct} percent off` : '',
    badge?.label.replace(/^\S+\s/, '') ?? '',
    store,
  ]
    .filter(Boolean)
    .join(', ');

  return (
    <View style={[s.card, list && s.cardList, width ? { width } : null]}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={a11y}
        accessibilityHint="Opens deal details. Long-press for quick actions"
        accessibilityActions={[{ name: 'longpress', label: 'Quick actions' }]}
        onAccessibilityAction={(e) => e.nativeEvent.actionName === 'longpress' && openActions(deal)}
        onPress={() => {
          rememberDeal(deal);
          onOpen(deal);
        }}
        onLongPress={() => openActions(deal)}
        delayLongPress={350}
        android_ripple={{ color: t.c.accentSoft, foreground: true }}
        style={({ pressed }) => [list ? s.pressList : s.pressGrid, { transform: [{ scale: pressed ? 0.98 : 1 }] }]}
      >
        <View style={list ? s.mediaList : s.mediaGrid}>
          <DealImage deal={deal} style={{ flex: 1 }} emojiSize={list ? 26 : 34} />
          {badge || past ? (
            <View style={s.badges}>
              {past ? <PastBadge small={list} /> : badge ? <StatusBadge kind={badge.kind} label={badge.label} small={list} /> : null}
            </View>
          ) : null}
          {!list ? <HeartButton deal={deal} size={17} style={s.heart} /> : null}
          {store && !list ? (
            <View style={s.storeTag}>
              <Text numberOfLines={1} maxFontSizeMultiplier={1.2} style={s.storeText}>
                {store}
              </Text>
            </View>
          ) : null}
        </View>
        <View style={list ? s.bodyList : s.body}>
          {list && store ? (
            <Text style={s.storeInline} maxFontSizeMultiplier={1.2} numberOfLines={1}>
              {store.toUpperCase()}
            </Text>
          ) : null}
          <View style={list ? s.titleRowList : undefined}>
            <Highlight text={deal.title} query={query} numberOfLines={2} style={[s.title, list && { flex: 1 }]} />
            {list ? <HeartButton deal={deal} size={17} style={s.heartList} /> : null}
          </View>
          <PriceRow deal={deal} size={list ? 'sm' : 'md'} />
          {deal.saving ? (
            <View style={s.saveRow}>
              <Icon name="down" size={12} color={t.c.good} strokeWidth={2.4} />
              <Text maxFontSizeMultiplier={1.3} style={s.save}>
                Save {money(deal.saving)}
              </Text>
            </View>
          ) : null}
          {deal.coupon ? (
            <View style={s.coupon}>
              <Text numberOfLines={1} maxFontSizeMultiplier={1.2} style={s.couponText}>
                🏷 {deal.coupon}
              </Text>
            </View>
          ) : null}
          <View style={s.metaRow}>
            <Text numberOfLines={1} maxFontSizeMultiplier={1.3} style={[s.meta, { flex: 1 }]}>
              {timeAgo(deal.posted_at)}
              {deal.repost_count > 1 ? (
                <Text style={s.reposts}>{`  ·  Posted ${deal.repost_count}×`}</Text>
              ) : null}
            </Text>
            {sparkline.length >= 2 ? <Sparkline points={sparkline} /> : null}
          </View>
          {suspicious ? (
            <View style={s.warnRow}>
              <Icon name="alert" size={11} color={t.c.warn} />
              <Text numberOfLines={1} maxFontSizeMultiplier={1.2} style={s.warnText}>
                {deal.ai_mrp_reason || 'Check the MRP'}
              </Text>
            </View>
          ) : null}
          {list && deal.url ? (
            <Pressable
              accessibilityRole="link"
              accessibilityLabel={`Buy now on ${store || 'store'}`}
              onPress={() => {
                void recordDealSignal('buy', deal);
                openExternal(deal.url);
              }}
              hitSlop={8}
              style={({ pressed }) => [s.buyInline, { opacity: pressed ? 0.8 : 1 }]}
            >
              <Text maxFontSizeMultiplier={1.3} style={s.buyInlineText}>
                Buy now
              </Text>
              <Icon name="external" size={12} color={t.c.accent} />
            </Pressable>
          ) : null}
        </View>
      </Pressable>
      {deal.url && !list ? (
        <View style={s.actions}>
          <Pressable
            accessibilityRole="link"
            accessibilityLabel={`Buy now on ${store || 'store'}`}
            onPress={() => openExternal(deal.url)}
            style={({ pressed }) => [s.buy, { opacity: pressed ? 0.85 : 1 }]}
          >
            <Text maxFontSizeMultiplier={1.3} style={s.buyText}>
              Buy now
            </Text>
            <Icon name="external" size={14} color={t.c.accentText} />
          </Pressable>
        </View>
      ) : null}
    </View>
  );
});

const useCardStyles = makeStyles((t) => ({
  card: {
    backgroundColor: t.c.surface,
    borderRadius: t.r.md,
    borderWidth: 1,
    borderColor: t.c.border,
    overflow: 'hidden',
  },
  cardList: { flexDirection: 'row' },
  pressGrid: { flex: 1 },
  pressList: { flex: 1, flexDirection: 'row' },
  mediaGrid: { aspectRatio: 1, width: '100%' },
  mediaList: { width: 112, minHeight: 124 },
  badges: { position: 'absolute', top: 7, left: 7, right: 46 },
  heart: { position: 'absolute', top: 6, right: 6 },
  titleRowList: { flexDirection: 'row', alignItems: 'flex-start', gap: 4 },
  heartList: { marginTop: -4, marginRight: -4 },
  storeTag: {
    position: 'absolute',
    bottom: 7,
    left: 7,
    maxWidth: '80%',
    paddingHorizontal: 8,
    paddingVertical: 3,
    borderRadius: t.r.full,
    backgroundColor: t.dark ? 'rgba(17,24,39,0.88)' : 'rgba(255,255,255,0.92)',
    borderWidth: 1,
    borderColor: t.c.border,
  },
  storeText: { color: t.c.text, fontSize: 10.5, fontWeight: '700' },
  storeInline: { color: t.c.text3, fontSize: 10, fontWeight: '800', letterSpacing: 0.6 },
  body: { padding: 10, paddingBottom: 8, gap: 5, flex: 1 },
  bodyList: { flex: 1, padding: 11, gap: 4, justifyContent: 'center' },
  title: { color: t.c.text, fontSize: 13, fontWeight: '600', lineHeight: 18, minHeight: 36 },
  saveRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  save: { color: t.c.good, fontSize: 11.5, fontWeight: '700' },
  coupon: {
    alignSelf: 'flex-start',
    backgroundColor: t.c.warnSoft,
    borderRadius: 6,
    paddingHorizontal: 7,
    paddingVertical: 2,
    maxWidth: '100%',
  },
  couponText: { color: t.c.warn, fontSize: 11, fontWeight: '700', fontFamily: 'monospace' },
  meta: { color: t.c.text3, fontSize: 11 },
  metaRow: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  reposts: { color: t.c.text2, fontWeight: '600' },
  warnRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  warnText: { color: t.c.warn, fontSize: 10.5, fontWeight: '600', flexShrink: 1 },
  actions: { paddingHorizontal: 10, paddingBottom: 10 },
  buy: {
    minHeight: 40,
    borderRadius: t.r.sm,
    backgroundColor: t.c.accent,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
  },
  buyText: { color: t.c.accentText, fontWeight: '700', fontSize: 13 },
  buyInline: {
    alignSelf: 'flex-start',
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    marginTop: 2,
    paddingHorizontal: 10,
    paddingVertical: 6,
    borderRadius: t.r.full,
    backgroundColor: t.c.accentSoft,
  },
  buyInlineText: { color: t.c.accent, fontWeight: '700', fontSize: 12 },
}));

export const RailCard = memo(function RailCard({
  deal,
  note,
  noteTone = 'good',
  onOpen,
}: {
  deal: Deal;
  note: string;
  noteTone?: 'good' | 'hot' | 'muted';
  onOpen: (deal: Deal) => void;
}) {
  const t = useTheme();
  const { open: openActions } = useDealActions();
  const noteColor = noteTone === 'hot' ? t.c.hot : noteTone === 'muted' ? t.c.text3 : t.c.good;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${deal.title}, ${money(deal.price)}, ${note}`}
      onPress={() => {
        rememberDeal(deal);
        onOpen(deal);
      }}
      onLongPress={() => openActions(deal)}
      delayLongPress={350}
      style={({ pressed }) => ({
        width: 156,
        borderRadius: t.r.md,
        borderWidth: 1,
        borderColor: t.c.border,
        backgroundColor: t.c.surface,
        overflow: 'hidden',
        transform: [{ scale: pressed ? 0.97 : 1 }],
      })}
    >
      <View>
        <DealImage deal={deal} style={{ height: 124 }} emojiSize={28} />
        {isPastDeal(deal) ? (
          <View style={{ position: 'absolute', top: 6, left: 6 }}>
            <PastBadge small />
          </View>
        ) : null}
      </View>
      <View style={{ padding: 9, gap: 4 }}>
        <Text numberOfLines={2} maxFontSizeMultiplier={1.3} style={{ color: t.c.text, fontSize: 12.5, fontWeight: '600', lineHeight: 17, minHeight: 34 }}>
          {deal.title}
        </Text>
        <PriceRow deal={deal} size="sm" />
        <Text numberOfLines={1} maxFontSizeMultiplier={1.2} style={{ color: noteColor, fontSize: 11, fontWeight: '700' }}>
          {note}
        </Text>
      </View>
    </Pressable>
  );
});

export function DealCardSkeleton({ layout, width }: { layout: 'grid' | 'list'; width?: number }) {
  const s = useCardStyles();
  if (layout === 'list') {
    return (
      <View style={[s.card, s.cardList, width ? { width } : null]}>
        <Skeleton style={{ width: 112, height: 124, borderRadius: 0 }} />
        <View style={{ flex: 1, padding: 12, gap: 8 }}>
          <Skeleton style={{ height: 12, width: '90%' }} />
          <Skeleton style={{ height: 12, width: '60%' }} />
          <Skeleton style={{ height: 18, width: '45%' }} />
        </View>
      </View>
    );
  }
  return (
    <View style={[s.card, width ? { width } : null]}>
      <Skeleton style={{ aspectRatio: 1, width: '100%', borderRadius: 0 }} />
      <View style={{ padding: 10, gap: 8 }}>
        <Skeleton style={{ height: 11, width: '95%' }} />
        <Skeleton style={{ height: 11, width: '65%' }} />
        <Skeleton style={{ height: 18, width: '50%' }} />
        <Skeleton style={{ height: 36, width: '100%', marginTop: 4 }} />
      </View>
    </View>
  );
}

export function RailSkeleton() {
  const t = useTheme();
  return (
    <View style={{ width: 156, borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.border, overflow: 'hidden', backgroundColor: t.c.surface }}>
      <Skeleton style={{ height: 124, borderRadius: 0 }} />
      <View style={{ padding: 9, gap: 7 }}>
        <Skeleton style={{ height: 10, width: '90%' }} />
        <Skeleton style={{ height: 16, width: '55%' }} />
      </View>
    </View>
  );
}
