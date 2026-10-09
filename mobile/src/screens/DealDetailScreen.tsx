import { useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, View, useWindowDimensions } from 'react-native';
import { Text, TextInput } from '../components/Text';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';

import { api, errorMessage, isNotFound, isOffline, type Deal, type DealDetail, type PriceAlert, type PricePoint } from '../api';
import { HiddenPageReader } from '../components/HiddenPageReader';
import {
  ingestPage,
  isChallengePage,
  loadHistory,
  mergeHistory,
  pauseAfterPushback,
  type BuyHatkeHistory,
  type HistoryResult,
} from '../native/buyhatkeHistory';
import { getDeviceId } from '../native/device';
import { recordDealSignal } from '../native/smartNotify';
import {
  Button,
  DealRail,
  HeartButton,
  PastBadge,
  PriceVerdictMeter,
  type PriceRange,
  alertCreatedMessage,
  alertErrorMessage,
  createPriceAlert,
  defaultAlertTarget,
  isPastDeal,
  listPriceAlerts,
  peekDeal,
  shareDeal,
  snapshotToDeal,
  useSaved,
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
  displayTitle,
  dealReasons,
  haptic,
  money,
  openDealBuy,
  plural,
  scoreLabel,
  storeName,
  timeAgo,
  useHideNativeHeader,
  useToast,
} from '../components';
import { MONO_FAMILY, useTheme } from '../theme';
import type { RootNav, RootRoute } from './types';

export function DealDetailScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const { params } = useRoute<RootRoute<'DealDetail'>>();
  const toast = useToast();
  const { width } = useWindowDimensions();

  const { saved } = useSaved();
  const savedRef = useRef(saved);
  savedRef.current = saved;

  // Reads saved via a ref so toggling the heart doesn't refetch the deal.
  const fallback = useCallback((): DealDetail | null => {
    const snap = savedRef.current[params.id];
    const d = peekDeal(params.id) ?? (snap ? snapshotToDeal(snap) : null);
    return d ? { raw_text: null, price_history: null, ...d } : null; // raw_text is never shown
  }, [params.id]);

  const [deal, setDeal] = useState<DealDetail | null>(() => fallback());
  const [fresh, setFresh] = useState(false);
  const [gone, setGone] = useState(false);
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [similar, setSimilar] = useState<Deal[] | null>(null);
  const [alerts, setAlerts] = useState<PriceAlert[]>([]);

  const load = useCallback(
    (signal?: AbortSignal) => {
      setError(null);
      api.deals
        .get(params.id, { signal })
        .then((d) => {
          setDeal(d);
          setFresh(true);
          setGone(false);
          void recordDealSignal('view', d);
        })
        .catch((e) => {
          if (signal?.aborted) return;
          if (isNotFound(e)) {
            const snap = fallback();
            setGone(true);
            if (snap) setDeal({ ...snap, status: isPastDeal(snap) ? snap.status : 'expired' });
          }
          setError(e);
        });
      api.deals
        .history(params.id, { signal })
        .then((h) => setPoints(h.points ?? []))
        .catch(() => !signal?.aborted && setPoints([]));
      api.deals
        .similar(params.id, 8, { signal })
        .then((r) => setSimilar(r.results ?? []))
        .catch(() => !signal?.aborted && setSimilar([]));
      listPriceAlerts(signal)
        .then(setAlerts)
        .catch(() => {});
    },
    [params.id, fallback],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    load(ctrl.signal);
    return () => ctrl.abort();
  }, [load]);

  // BuyHatke's longer history, read on this phone (see buyhatkeHistory.ts).
  const [bh, setBh] = useState<BuyHatkeHistory | null>(null);
  const [bhState, setBhState] = useState<HistoryState>('idle');
  const lookup = deal?.history_lookup_url ?? null;
  const applyResult = useCallback((r: HistoryResult) => {
    if (r.kind === 'found') {
      setBh(r.history);
      setBhState('found');
    } else if (r.kind === 'blocked') {
      setBhState('browser'); // quick request was challenged: read it in the hidden in-app browser
    } else {
      setBhState(r.kind);
    }
  }, []);
  useEffect(() => {
    if (!lookup) return;
    let alive = true;
    setBh(null);
    setBhState('loading');
    loadHistory(lookup)
      .then((r) => alive && applyResult(r))
      .catch(() => alive && setBhState('failed'));
    return () => {
      alive = false;
    };
  }, [lookup, applyResult]);
  const onBrowserPage = useCallback(
    (html: string, pageUrl: string) => {
      if (!lookup || isChallengePage(html)) return false; // still on the check: keep waiting
      void ingestPage(lookup, html, pageUrl).then(applyResult);
      return true;
    },
    [lookup, applyResult],
  );
  const onBrowserGiveUp = useCallback(() => {
    void pauseAfterPushback();
    setBhState('failed');
  }, []);
  const chartPoints = useMemo(() => (points && bh ? mergeHistory(points, bh.points) : points), [points, bh]);

  const share = () => {
    if (deal) void shareDeal(deal);
  };

  const openDeal = useCallback((d: Deal) => navigation.push('DealDetail', { id: d.id }), [navigation]);

  const copy = async (text: string, what: string) => {
    if (await copyText(text)) toast(`${what} copied.`, 'ok', 2200);
  };

  const [couponReported, setCouponReported] = useState(false);
  const reportCouponDead = async () => {
    if (!deal || couponReported) return;
    haptic.select();
    setCouponReported(true);
    try {
      const device_id = await getDeviceId();
      const res = await api.deals.reportCouponDead(deal.id, device_id);
      toast(res.suppressed ? 'Thanks — we’ve hidden that code.' : 'Thanks for letting us know.', 'ok', 2600);
    } catch {
      setCouponReported(false);
    }
  };

  const store = deal ? storeName(deal) : '';
  const heroH = Math.min(width, 520) * 0.82;
  // Transparent over the product photo; solid once the photo scrolls away,
  // so the floating buttons never sit on top of text.
  const [headerSolid, setHeaderSolid] = useState(false);
  const onScroll = useCallback(
    (e: { nativeEvent: { contentOffset: { y: number } } }) => {
      const solid = e.nativeEvent.contentOffset.y > heroH - 8;
      setHeaderSolid((prev) => (prev === solid ? prev : solid));
    },
    [heroH],
  );

  const header = (
    <View
      style={{
        position: 'absolute',
        top: 0,
        left: 0,
        right: 0,
        paddingTop: insets.top + 4,
        paddingBottom: 6,
        paddingHorizontal: 8,
        flexDirection: 'row',
        justifyContent: 'space-between',
        zIndex: 10,
        backgroundColor: headerSolid ? t.c.bg : 'transparent',
        borderBottomWidth: headerSolid ? 1 : 0,
        borderBottomColor: t.c.border,
      }}
      pointerEvents="box-none"
    >
      <IconButton
        name="chevLeft"
        label="Back"
        size={24}
        onPress={() => navigation.goBack()}
        style={{ backgroundColor: t.c.surface, borderRadius: 22 }}
      />
      {deal ? (
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <HeartButton deal={deal} size={20} style={{ width: 44, height: 44, borderRadius: 22 }} />
          <IconButton
            name="share"
            label="Share deal"
            onPress={share}
            style={{ backgroundColor: t.c.surface, borderRadius: 22 }}
          />
        </View>
      ) : null}
    </View>
  );

  if (error && !deal) {
    const offline = isOffline(error);
    return (
      <View style={{ flex: 1, backgroundColor: t.c.bg, paddingTop: insets.top + 56 }}>
        {header}
        {isNotFound(error) ? (
          <EmptyState
            icon="clock"
            title="This deal has ended"
            message="It’s no longer live on DealRadar. Similar deals might still be around."
            actions={[{ title: 'Back to deals', variant: 'primary', onPress: () => navigation.goBack() }]}
          />
        ) : (
          <EmptyState
            icon={offline ? 'wifiOff' : 'alert'}
            title={offline ? 'You’re offline' : 'Couldn’t load this deal'}
            message={errorMessage(error)}
            actions={[{ title: 'Try again', variant: 'primary', onPress: () => load() }]}
          />
        )}
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

  const past = gone || isPastDeal(deal);
  const badge = past ? null : dealBadge(deal);
  const verdict = deal.price_verdict;
  const watching = alerts.find((a) => a.deal_id === deal.id && !a.triggered_at) ?? null;
  const [label, blurb] = scoreLabel(deal.score || 0);
  const reasons = dealReasons(deal);
  const suspicious = (deal.flags || []).includes('suspicious_mrp');
  const range = priceRange(chartPoints, deal.price_history, deal.price);

  const kv: [string, React.ReactNode][] = [
    ['Store', store || '—'],
    ['Category', deal.subcategory ? `${deal.category || '—'} › ${deal.subcategory}` : deal.category || '—'],
  ];
  if (deal.brand) kv.push(['Brand', deal.brand]);
  if (deal.sizes) kv.push(['Sizes', deal.sizes]);
  kv.push(['Found', timeAgo(deal.posted_at)]);
  if (deal.expires_at) {
    const at = new Date(deal.expires_at * 1000);
    kv.push(['Expires', `${at.toLocaleDateString('en-IN', { day: 'numeric', month: 'short' })}, ${at.toLocaleTimeString('en-IN', { hour: 'numeric', minute: '2-digit' })}`]);
  }
  kv.push(['Deal score', `${Math.round(deal.score ?? 0)} / 100`]);

  const card = { padding: 14, borderRadius: t.r.md, backgroundColor: t.c.surface, borderWidth: 1, borderColor: t.c.border } as const;

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      {header}
      <ScrollView contentContainerStyle={{ paddingBottom: 24 }} onScroll={onScroll} scrollEventThrottle={32}>
        <View style={{ paddingTop: insets.top + 56, paddingHorizontal: 16, maxWidth: 720, width: '100%', alignSelf: 'center' }}>
          <View style={{ borderRadius: t.r.lg, overflow: 'hidden', borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.mediaBg }}>
            <DealImage deal={deal} style={{ height: heroH * 0.78 }} />
            {badge || past ? (
              <View style={{ position: 'absolute', top: 10, left: 10 }}>
                {past ? <PastBadge /> : badge ? <StatusBadge kind={badge.kind} label={badge.label} /> : null}
              </View>
            ) : null}
          </View>
        </View>

        <View style={{ padding: 16, gap: 14, maxWidth: 720, width: '100%', alignSelf: 'center' }}>
          {past ? (
            <Note
              kind="warn"
              icon="clock"
              text={
                gone
                  ? 'This offer has ended. The price shown is what it was when you saw it.'
                  : 'This offer may have ended and the price may have changed.'
              }
            />
          ) : null}

          <View style={{ gap: 6 }}>
            <Text numberOfLines={1} style={{ color: t.c.text3, fontSize: 11.5, fontFamily: MONO_FAMILY, letterSpacing: 0.3, textTransform: 'uppercase' }}>
              {store ? <Text style={{ color: t.c.text2, fontFamily: MONO_FAMILY, fontWeight: '600' }}>{store}</Text> : null}
              {store ? '  ·  ' : ''}
              {`Found ${timeAgo(deal.posted_at)}`}
            </Text>
            <Text accessibilityRole="header" selectable style={{ color: t.c.text, fontSize: 21, fontWeight: '600', lineHeight: 27, letterSpacing: -0.3 }}>
              {displayTitle(deal.title)}
            </Text>
          </View>

          <View style={{ gap: 6 }}>
            <PriceRow deal={deal} size="lg" />
            {deal.saving ? (
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5 }}>
                <Icon name="down" size={14} color={t.c.good} strokeWidth={2.4} />
                <Text style={{ color: t.c.good, fontWeight: '600', fontSize: t.f.sm }}>You save {money(deal.saving)}</Text>
              </View>
            ) : null}
          </View>

          {deal.ai_hook ? <Note kind="good" icon="trend" text={deal.ai_hook} /> : null}
          {suspicious ? (
            <Note kind="warn" icon="alert" text={deal.ai_mrp_reason || 'The quoted MRP looks inflated versus this product’s price history.'} />
          ) : null}

          {verdict || range ? <PriceVerdictMeter verdict={verdict ?? null} range={range} lowest={deal.is_lowest} /> : null}

          {deal.coupon ? (
            <View style={{ padding: 14, borderRadius: t.r.md, borderWidth: 1.5, borderStyle: 'dashed', borderColor: t.c.borderStrong, backgroundColor: t.c.surface, gap: 10 }}>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
                <View style={{ flex: 1, gap: 2 }}>
                  <Text style={{ color: t.c.text3, fontSize: 11, fontFamily: MONO_FAMILY, letterSpacing: 0.4 }}>COUPON</Text>
                  <Text selectable style={{ color: t.c.text, fontSize: 19, fontFamily: MONO_FAMILY, fontWeight: '600', letterSpacing: 1 }}>
                    {deal.coupon}
                  </Text>
                </View>
                <Button title="Copy" icon="copy" size="sm" onPress={() => copy(deal.coupon ?? '', 'Coupon')} />
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Report this coupon code as not working"
                disabled={couponReported}
                onPress={reportCouponDead}
                hitSlop={8}
                style={{ alignSelf: 'flex-start' }}
              >
                <Text style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '500', textDecorationLine: couponReported ? 'none' : 'underline' }}>
                  {couponReported ? 'Reported — thanks' : 'Code not working?'}
                </Text>
              </Pressable>
            </View>
          ) : null}

          <View style={[card, { gap: 12 }]}>
            <Text style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>Price history</Text>
            <FullHistoryCard state={bhState} history={bh} merged={chartPoints} current={deal.price} lookupUrl={lookup} />
            {bhState === 'browser' && lookup ? (
              <HiddenPageReader url={lookup} onPage={onBrowserPage} onGiveUp={onBrowserGiveUp} />
            ) : null}
            {chartPoints ? <PriceChart points={chartPoints} /> : <ActivityIndicator color={t.c.text3} />}
          </View>

          {!past && fresh && deal.price ? (
            <PriceAlertBlock
              deal={deal}
              watching={watching}
              onCreated={(a) => setAlerts((prev) => [a, ...prev.filter((x) => x.id !== a.id)])}
            />
          ) : null}

          <View style={[card, { gap: 12 }]}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
              <ScoreRing score={deal.score} />
              <View style={{ flex: 1 }}>
                <Text style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>{label}</Text>
                <Text style={{ color: t.c.text2, fontSize: t.f.sm, marginTop: 2, lineHeight: 18 }}>{blurb}</Text>
              </View>
            </View>
            {reasons.length ? (
              <View style={{ gap: 8, paddingTop: 12, borderTopWidth: 1, borderTopColor: t.c.border }}>
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
          </View>

          <KeyValue rows={kv} />
        </View>

        <View style={{ paddingHorizontal: 16, paddingTop: 8, maxWidth: 720, width: '100%', alignSelf: 'center' }}>
          <DealRail title="Similar, live now" deals={similar} note={similarNote} onOpen={openDeal} pad={16} />
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
          <IconButton name="copy" label="Copy link" variant="soft" onPress={() => copy(deal.url ?? '', 'Link')} style={{ width: 52, height: 52, borderRadius: t.r.md }} />
          <View style={{ flex: 1 }}>
            <Button
              title={past ? `Check on ${store || 'store'}` : `Buy on ${store || 'store'}`}
              iconRight="external"
              onPress={() => {
                haptic.light();
                void recordDealSignal('buy', deal);
                void openDealBuy(deal);
              }}
              style={{ minHeight: 52, borderRadius: t.r.md }}
            />
          </View>
        </View>
      ) : null}
    </View>
  );
}

/** Low / usual / high for the verdict bar, from the chart points or the server's stats. */
function priceRange(
  points: PricePoint[] | null,
  stats: DealDetail['price_history'],
  current: number | null,
): PriceRange | null {
  if (current == null) return null;
  const prices = (points ?? []).map((p) => p.price).filter((p) => p > 0);
  if (prices.length >= 3) {
    const sorted = [...prices].sort((a, b) => a - b);
    const low = Math.min(sorted[0], current);
    const high = Math.max(sorted[sorted.length - 1], current);
    if (high - low < 1) return null;
    return { low, high, usual: sorted[Math.floor(sorted.length / 2)], current };
  }
  if (stats && stats.points >= 3 && stats.min != null && stats.max != null && stats.max - stats.min >= 1) {
    return { low: Math.min(stats.min, current), high: Math.max(stats.max, current), usual: stats.median ?? null, current };
  }
  return null;
}

const monthYear = (sec: number) =>
  new Date(sec * 1000).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });

type HistoryState = 'idle' | 'loading' | 'browser' | 'found' | 'none' | 'failed';

/** The "full price history" card: loading line, then the headline numbers from BuyHatke. */
function FullHistoryCard({
  state,
  history,
  merged,
  current,
  lookupUrl,
}: {
  state: HistoryState;
  history: BuyHatkeHistory | null;
  merged: PricePoint[] | null;
  current: number | null;
  lookupUrl: string | null;
}) {
  const t = useTheme();
  const openBuyHatke = (url: string | null) => url && WebBrowser.openBrowserAsync(url).catch(() => {});
  if (state === 'loading' || state === 'browser') {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <ActivityIndicator size="small" color={t.c.text3} />
        <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>Pulling the full price history…</Text>
      </View>
    );
  }
  if (state === 'none') {
    return (
      <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>
        No longer price history on BuyHatke for this product yet — showing ours.
      </Text>
    );
  }
  if (state === 'failed') {
    return (
      <Pressable
        accessibilityRole="link"
        onPress={() => openBuyHatke(lookupUrl)}
        style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: t.r.sm, backgroundColor: t.c.surface2 }}
      >
        <Icon name="alert" size={16} color={t.c.text3} />
        <Text style={{ flex: 1, color: t.c.text2, fontSize: t.f.sm }}>
          Couldn't load the full history right now.{' '}
          <Text style={{ color: t.c.text, fontWeight: '600', textDecorationLine: 'underline' }}>Open on BuyHatke</Text>
        </Text>
      </Pressable>
    );
  }
  if (state !== 'found' || !history) return null;
  // Same numbers the chart shows: BuyHatke's history plus our own points
  // (today's price can be below BuyHatke's previous low).
  const all = merged && merged.length ? merged : history.points;
  const prices = all.map((p) => p.price);
  const lowest = Math.min(...prices);
  const highest = Math.max(...prices);
  const since = Math.min(...all.map((p) => p.at));
  const atLowest = current != null && current <= lowest;
  const stat = (label: string, value: string, color: string) => (
    <View style={{ flex: 1, gap: 2 }}>
      <Text style={{ color: t.c.text3, fontSize: 11, fontFamily: MONO_FAMILY }}>{label.toUpperCase()}</Text>
      <Text style={{ color, fontSize: t.f.md, fontWeight: '700' }}>{value}</Text>
    </View>
  );
  return (
    <View style={{ gap: 10 }}>
      <View style={{ flexDirection: 'row', gap: 10 }}>
        {stat('Lowest ever', money(lowest), t.c.good)}
        {stat('Highest', money(highest), t.c.text)}
        {stat('Tracked since', monthYear(since), t.c.text)}
      </View>
      {atLowest ? (
        <Text style={{ color: t.c.good, fontSize: t.f.sm, fontWeight: '700' }}>
          {current != null && current < history.lowest
            ? `Lowest price ever — below the previous low of ${money(history.lowest)}`
            : "Today's price matches the lowest ever"}
        </Text>
      ) : null}
      <Pressable
        accessibilityRole="link"
        onPress={() => WebBrowser.openBrowserAsync(history.pageUrl).catch(() => {})}
        hitSlop={8}
      >
        <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>
          {all.length} price points · history by <Text style={{ color: t.c.text2, fontWeight: '600', textDecorationLine: 'underline' }}>BuyHatke</Text>
        </Text>
      </Pressable>
    </View>
  );
}

function Note({ kind, icon, text }: { kind: 'good' | 'warn'; icon: 'trend' | 'alert' | 'clock'; text: string }) {
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

const similarNote = (d: Deal): [string, 'good' | 'hot' | 'muted'] =>
  d.saving ? [`Save ${money(d.saving)}`, 'good'] : [storeName(d) || 'Live deal', 'muted'];

function SaveButton({ deal }: { deal: Deal }) {
  const { isSaved, toggle } = useSaved();
  const on = isSaved(deal.id);
  return (
    <Button
      title={on ? 'Saved' : 'Save'}
      icon="heart"
      variant={on ? 'danger' : 'soft'}
      accessibilityLabel={on ? 'Remove from saved' : 'Save deal'}
      onPress={() => {
        if (toggle(deal)) {
          haptic.success();
          void recordDealSignal('save', deal);
        } else haptic.select();
      }}
    />
  );
}

function PriceAlertBlock({
  deal,
  watching,
  onCreated,
}: {
  deal: Deal;
  watching: PriceAlert | null;
  onCreated: (a: PriceAlert) => void;
}) {
  const t = useTheme();
  const toast = useToast();
  const [target, setTarget] = useState(String(watching?.target_price ?? defaultAlertTarget(deal.price)));
  const [busy, setBusy] = useState(false);
  const [focused, setFocused] = useState(false);

  useEffect(() => {
    if (watching) setTarget(String(Math.round(watching.target_price)));
  }, [watching]);

  const submit = async () => {
    const n = Number(target.replace(/[^\d.]/g, ''));
    if (!n || n <= 0) {
      toast('Enter a target price.', 'err');
      return;
    }
    setBusy(true);
    try {
      const alert = await createPriceAlert(deal.id, n);
      haptic.success();
      toast(alertCreatedMessage(alert, n), 'ok', 5000);
      onCreated(alert);
    } catch (e) {
      haptic.error();
      toast(alertErrorMessage(e), 'err');
    } finally {
      setBusy(false);
    }
  };

  const picks = alertPicks(deal.price);
  return (
    <View style={{ padding: 14, gap: 12, borderRadius: t.r.md, backgroundColor: t.c.surface, borderWidth: 1, borderColor: t.c.border }}>
      <View style={{ gap: 2 }}>
        <Text style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>
          {watching ? 'You’re watching this price' : 'Tell me if it gets cheaper'}
        </Text>
        <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>
          {watching ? `We’ll notify you at ${money(watching.target_price)} or less.` : 'We keep checking the price and notify you when it drops.'}
        </Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center' }}>
        <View
          style={{
            flex: 1,
            minHeight: 48,
            flexDirection: 'row',
            alignItems: 'center',
            paddingHorizontal: 12,
            borderRadius: t.r.sm,
            borderWidth: 1,
            borderColor: focused ? t.c.text : t.c.borderStrong,
            backgroundColor: t.c.surface,
          }}
        >
          <Text style={{ color: t.c.text3, fontSize: t.f.base }}>Below ₹</Text>
          <TextInput
            value={target}
            onChangeText={setTarget}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            keyboardType="number-pad"
            returnKeyType="done"
            onSubmitEditing={submit}
            selectionColor={t.c.text}
            accessibilityLabel="Alert me below this price, in rupees"
            maxFontSizeMultiplier={1.4}
            style={{ flex: 1, color: t.c.text, fontSize: 17, fontWeight: '700', paddingHorizontal: 4 }}
          />
        </View>
        <Button title={watching ? 'Update' : 'Set alert'} loading={busy} onPress={submit} />
      </View>
      {picks.length ? (
        <View style={{ flexDirection: 'row', gap: 8 }}>
          {picks.map((p) => {
            const on = Number(target) === p;
            return (
              <Pressable
                key={p}
                accessibilityRole="button"
                accessibilityLabel={`Set target to ${money(p)}`}
                onPress={() => {
                  haptic.select();
                  setTarget(String(p));
                }}
                style={{
                  minHeight: 34,
                  paddingHorizontal: 12,
                  borderRadius: 17,
                  justifyContent: 'center',
                  borderWidth: 1,
                  borderColor: on ? t.c.text : t.c.borderStrong,
                }}
              >
                <Text style={{ color: on ? t.c.text : t.c.text2, fontSize: t.f.sm, fontWeight: on ? '700' : '500' }}>{money(p)}</Text>
              </Pressable>
            );
          })}
        </View>
      ) : null}
    </View>
  );
}

/** Three sensible targets: roughly 10%, 20% and 30% under today's price, rounded to a price people type. */
function alertPicks(price: number | null): number[] {
  if (!price || price < 50) return [];
  const step = price >= 10000 ? 500 : price >= 1000 ? 50 : 10;
  const out = [0.9, 0.8, 0.7].map((f) => Math.floor((price * f) / step) * step - (step >= 50 ? 1 : 0));
  return [...new Set(out.filter((n) => n > 0 && n < price))];
}
