import { useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Share, Text, View, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';

import { api, errorMessage, isOffline, type DealDetail, type PricePoint } from '../api';
import {
  Button,
  DealImage,
  EmptyState,
  Icon,
  IconButton,
  KeyValue,
  PriceChart,
  PriceRow,
  ScoreRing,
  StatusBadge,
  copyText,
  dealBadge,
  dealReasons,
  haptic,
  money,
  openExternal,
  plural,
  scoreLabel,
  storeName,
  timeAgo,
  useHideNativeHeader,
  useToast,
} from '../components';
import { useTheme } from '../theme';
import type { RootNav, RootRoute } from './types';

export function DealDetailScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const { params } = useRoute<RootRoute<'DealDetail'>>();
  const toast = useToast();
  const { width } = useWindowDimensions();

  const [deal, setDeal] = useState<DealDetail | null>(null);
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showRaw, setShowRaw] = useState(false);

  const load = useCallback(
    (signal?: AbortSignal) => {
      setError(null);
      api.deals
        .get(params.id, { signal })
        .then(setDeal)
        .catch((e) => !signal?.aborted && setError(e));
      api.deals
        .history(params.id, { signal })
        .then((h) => setPoints(h.points ?? []))
        .catch(() => !signal?.aborted && setPoints([]));
    },
    [params.id],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  const share = async () => {
    if (!deal) return;
    haptic.light();
    const price = deal.price != null ? ` — ${money(deal.price)}` : '';
    const off = deal.discount_pct >= 5 ? ` (-${deal.discount_pct}%)` : '';
    try {
      await Share.share({
        title: deal.title,
        message: `${deal.title}${price}${off}${deal.url ? `\n${deal.url}` : ''}`,
      });
    } catch {
      /* user dismissed */
    }
  };

  const copy = async (text: string, what: string) => {
    if (await copyText(text)) toast(`${what} copied.`, 'ok', 2200);
  };

  const store = deal ? storeName(deal) : '';
  const heroH = Math.min(width, 520) * 0.82;

  const header = (
    <View
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        paddingTop: insets.top + 4,
        paddingHorizontal: 8,
        flexDirection: 'row',
        justifyContent: 'space-between',
        zIndex: 10,
      }}
      pointerEvents="box-none"
    >
      <IconButton
        name="chevLeft"
        label="Back"
        size={24}
        onPress={() => navigation.goBack()}
        style={{ backgroundColor: t.dark ? 'rgba(17,24,39,0.82)' : 'rgba(255,255,255,0.9)', borderRadius: 22 }}
      />
      {deal ? (
        <IconButton
          name="share"
          label="Share deal"
          onPress={share}
          style={{ backgroundColor: t.dark ? 'rgba(17,24,39,0.82)' : 'rgba(255,255,255,0.9)', borderRadius: 22 }}
        />
      ) : null}
    </View>
  );

  if (error && !deal) {
    const offline = isOffline(error);
    return (
      <View style={{ flex: 1, backgroundColor: t.c.bg, paddingTop: insets.top + 56 }}>
        {header}
        <EmptyState
          emoji={offline ? '📶' : '⚠️'}
          title={offline ? 'You’re offline' : 'Couldn’t load this deal'}
          message={errorMessage(error)}
          actions={[{ title: 'Try again', variant: 'primary', onPress: () => load() }]}
        />
      </View>
    );
  }

  if (!deal) {
    return (
      <View style={{ flex: 1, backgroundColor: t.c.bg, alignItems: 'center', justifyContent: 'center' }}>
        {header}
        <ActivityIndicator color={t.c.accent} size="large" />
      </View>
    );
  }

  const badge = dealBadge(deal);
  const [label, blurb] = scoreLabel(deal.score || 0);
  const reasons = dealReasons(deal);
  const suspicious = (deal.flags || []).includes('suspicious_mrp');
  const history = deal.price_history;

  const kv: [string, React.ReactNode][] = [
    ['Store', deal.store || '—'],
    ['Category', `${deal.category || '—'} › ${deal.subcategory || '—'}`],
  ];
  if (deal.brand) kv.push(['Brand', deal.brand]);
  if (deal.sizes) kv.push(['Sizes', deal.sizes]);
  kv.push(['Posted', `${timeAgo(deal.posted_at)} in ${deal.channel_title || 'a channel'}`]);
  kv.push(['Reposted', `${deal.repost_count} ${plural(deal.repost_count, 'channel')}`]);
  kv.push(['Expires', deal.expires_at ? new Date(deal.expires_at * 1000).toLocaleString('en-IN') : '—']);
  if (history?.points) {
    kv.push(['History', `${history.points} points · low ${money(history.min)} · high ${money(history.max)}`]);
  }
  kv.push(['Deal score', `${Math.round(deal.score ?? 0)} / 100`]);

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      {header}
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }}>
        <View style={{ paddingTop: insets.top + 44, backgroundColor: t.c.mediaBg, borderBottomWidth: 1, borderBottomColor: t.c.border }}>
          <DealImage deal={deal} emojiSize={56} style={{ height: heroH }} />
        </View>

        <View style={{ padding: 16, gap: 14, maxWidth: 720, width: '100%', alignSelf: 'center' }}>
          {badge || store ? (
            <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {badge ? <StatusBadge kind={badge.kind} label={badge.label} /> : null}
              {store ? (
                <View style={{ paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.surface }}>
                  <Text style={{ color: t.c.text, fontSize: 11, fontWeight: '700' }}>{store}</Text>
                </View>
              ) : null}
            </View>
          ) : null}

          <Text accessibilityRole="header" selectable style={{ color: t.c.text, fontSize: t.f.lg, fontWeight: '700', lineHeight: 25, letterSpacing: -0.3 }}>
            {deal.title}
          </Text>

          <PriceRow deal={deal} size="lg" />
          {deal.saving ? (
            <View style={{ flexDirection: 'row', alignSelf: 'flex-start', alignItems: 'center', gap: 6, paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, backgroundColor: t.c.goodSoft }}>
              <Icon name="down" size={14} color={t.c.good} strokeWidth={2.4} />
              <Text style={{ color: t.c.good, fontWeight: '700', fontSize: t.f.sm }}>
                You save {money(deal.saving)}
                {deal.discount_pct ? ` · ${deal.discount_pct}% off` : ''}
              </Text>
            </View>
          ) : null}

          {deal.coupon ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={`Coupon ${deal.coupon}. Tap to copy`}
              onPress={() => copy(deal.coupon ?? '', 'Coupon')}
              style={({ pressed }) => ({
                flexDirection: 'row',
                alignItems: 'center',
                alignSelf: 'flex-start',
                gap: 8,
                minHeight: 44,
                paddingHorizontal: 12,
                borderRadius: 10,
                borderWidth: 1,
                borderStyle: 'dashed',
                borderColor: t.c.warn,
                backgroundColor: t.c.warnSoft,
                opacity: pressed ? 0.8 : 1,
              })}
            >
              <Text style={{ color: t.c.warn, fontFamily: 'monospace', fontWeight: '800', fontSize: t.f.md }}>🏷 {deal.coupon}</Text>
              <Icon name="copy" size={15} color={t.c.warn} />
            </Pressable>
          ) : null}

          {deal.is_lowest ? <Note kind="good" icon="trend" text="Lowest price we have recorded for this product." /> : null}
          {suspicious ? (
            <Note kind="warn" icon="alert" text="The quoted MRP looks inflated versus this product’s price history." />
          ) : null}

          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14, padding: 14, borderRadius: t.r.md, backgroundColor: t.c.surface, borderWidth: 1, borderColor: t.c.border }}>
            <ScoreRing score={deal.score} />
            <View style={{ flex: 1 }}>
              <Text style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>{label}</Text>
              <Text style={{ color: t.c.text2, fontSize: t.f.sm, marginTop: 2, lineHeight: 18 }}>
                Deal score {Math.round(deal.score || 0)} / 100 — {blurb}
              </Text>
            </View>
          </View>

          {reasons.length ? (
            <View style={{ gap: 8 }}>
              {reasons.map((r) => (
                <View key={r} style={{ flexDirection: 'row', gap: 9 }}>
                  <View style={{ paddingTop: 2 }}>
                    <Icon name="check" size={15} color={t.c.good} strokeWidth={2.4} />
                  </View>
                  <Text style={{ flex: 1, color: t.c.text2, fontSize: t.f.sm, lineHeight: 19 }}>{r}</Text>
                </View>
              ))}
            </View>
          ) : null}

          <View style={{ gap: 10, marginTop: 6 }}>
            <Text style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.7, textTransform: 'uppercase' }}>
              Price history
            </Text>
            {points ? <PriceChart points={points} /> : <ActivityIndicator color={t.c.accent} />}
          </View>

          <KeyValue rows={kv} />

          {deal.raw_text ? (
            <View style={{ borderRadius: t.r.sm, borderWidth: 1, borderColor: t.c.border, overflow: 'hidden', backgroundColor: t.c.surface }}>
              <Pressable
                accessibilityRole="button"
                accessibilityState={{ expanded: showRaw }}
                onPress={() => {
                  haptic.select();
                  setShowRaw(!showRaw);
                }}
                style={{ minHeight: 48, paddingHorizontal: 14, flexDirection: 'row', alignItems: 'center', gap: 10 }}
              >
                <Text style={{ flex: 1, color: t.c.text, fontWeight: '600', fontSize: t.f.sm }}>Original channel post</Text>
                <View style={{ transform: [{ rotate: showRaw ? '180deg' : '0deg' }] }}>
                  <Icon name="chevDown" size={18} color={t.c.text3} />
                </View>
              </Pressable>
              {showRaw ? (
                <View style={{ borderTopWidth: 1, borderTopColor: t.c.border, padding: 14, gap: 10, backgroundColor: t.c.surface2 }}>
                  <Text selectable style={{ color: t.c.text2, fontFamily: 'monospace', fontSize: 12.5, lineHeight: 19 }}>
                    {deal.raw_text}
                  </Text>
                  <Button title="Copy post" icon="copy" variant="soft" size="sm" onPress={() => copy(deal.raw_text ?? '', 'Post')} style={{ alignSelf: 'flex-start' }} />
                </View>
              ) : null}
            </View>
          ) : null}
        </View>
      </ScrollView>

      {deal.url ? (
        <View
          style={{
            paddingHorizontal: 16,
            paddingTop: 10,
            paddingBottom: insets.bottom + 10,
            borderTopWidth: 1,
            borderTopColor: t.c.border,
            backgroundColor: t.c.bg,
            flexDirection: 'row',
            gap: 10,
          }}
        >
          <View style={{ flex: 1 }}>
            <Button
              title={`Open on ${store || 'store'}`}
              iconRight="external"
              onPress={() => {
                haptic.light();
                openExternal(deal.url);
              }}
            />
          </View>
          {deal.price_history_url ? (
            <IconButton
              name="trend"
              label="Price history and stock"
              variant="soft"
              onPress={() => WebBrowser.openBrowserAsync(deal.price_history_url as string)}
              style={{ width: 48, height: 48 }}
            />
          ) : null}
          <IconButton name="copy" label="Copy link" variant="soft" onPress={() => copy(deal.url ?? '', 'Link')} style={{ width: 48, height: 48 }} />
        </View>
      ) : null}
    </View>
  );
}

function Note({ kind, icon, text }: { kind: 'good' | 'warn'; icon: 'trend' | 'alert'; text: string }) {
  const t = useTheme();
  const bg = kind === 'good' ? t.c.goodSoft : t.c.warnSoft;
  const fg = kind === 'good' ? t.c.good : t.c.warn;
  return (
    <View style={{ flexDirection: 'row', gap: 9, padding: 12, borderRadius: t.r.sm, backgroundColor: bg }}>
      <View style={{ paddingTop: 1 }}>
        <Icon name={icon} size={16} color={fg} />
      </View>
      <Text style={{ flex: 1, color: fg, fontSize: t.f.sm, lineHeight: 19 }}>{text}</Text>
    </View>
  );
}
