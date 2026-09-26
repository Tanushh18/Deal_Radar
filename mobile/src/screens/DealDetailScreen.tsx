import { useNavigation, useRoute } from '@react-navigation/native';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, ScrollView, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import * as WebBrowser from 'expo-web-browser';

import { api, errorMessage, isNotFound, isOffline, type Deal, type DealDetail, type PriceAlert, type PricePoint } from '../api';
import { buyHatkeHistory, mergeHistory, type BuyHatkeHistory } from '../native/buyhatkeHistory';
import { getDeviceId } from '../native/device';
import { recordDealSignal } from '../native/smartNotify';
import {
  Button,
  DealRail,
  HeartButton,
  PastBadge,
  PriceVerdictMeter,
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

  const { saved } = useSaved();
  const savedRef = useRef(saved);
  savedRef.current = saved;

  // Reads saved via a ref so toggling the heart doesn't refetch the deal.
  const fallback = useCallback((): DealDetail | null => {
    const snap = savedRef.current[params.id];
    const d = peekDeal(params.id) ?? (snap ? snapshotToDeal(snap) : null);
    return d ? { raw_text: null, price_history: null, ...d } : null;
  }, [params.id]);

  const [deal, setDeal] = useState<DealDetail | null>(() => fallback());
  const [fresh, setFresh] = useState(false);
  const [gone, setGone] = useState(false);
  const [points, setPoints] = useState<PricePoint[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [showRaw, setShowRaw] = useState(false);
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
  const [bhState, setBhState] = useState<'idle' | 'loading' | 'found' | 'none'>('idle');
  const lookup = deal?.history_lookup_url ?? null;
  useEffect(() => {
    if (!lookup) return;
    let alive = true;
    setBhState('loading');
    buyHatkeHistory(lookup)
      .then((h) => {
        if (!alive) return;
        setBh(h);
        setBhState(h ? 'found' : 'none');
      })
      .catch(() => alive && setBhState('none'));
    return () => {
      alive = false;
    };
  }, [lookup]);
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
        <View style={{ flexDirection: 'row', gap: 8 }}>
          <HeartButton deal={deal} size={20} style={{ width: 44, height: 44, borderRadius: 22 }} />
          <IconButton
            name="share"
            label="Share deal"
            onPress={share}
            style={{ backgroundColor: t.dark ? 'rgba(17,24,39,0.82)' : 'rgba(255,255,255,0.9)', borderRadius: 22 }}
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
            emoji="⌛"
            title="This deal has ended"
            message="It’s no longer live on DealRadar. Similar deals might still be around."
            actions={[{ title: 'Back to deals', variant: 'primary', onPress: () => navigation.goBack() }]}
          />
        ) : (
          <EmptyState
            emoji={offline ? '📶' : '⚠️'}
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
  const history = deal.price_history;

  const kv: [string, React.ReactNode][] = [
    ['Store', deal.store || '—'],
    ['Category', `${deal.category || '—'} › ${deal.subcategory || '—'}`],
  ];
  if (deal.brand) kv.push(['Brand', deal.brand]);
  if (deal.sizes) kv.push(['Sizes', deal.sizes]);
  kv.push(['Posted', timeAgo(deal.posted_at)]);
  kv.push(['Shared', `${deal.repost_count} ${plural(deal.repost_count, 'time')}`]);
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
          {past ? (
            <Note
              kind="warn"
              icon="clock"
              text={
                gone
                  ? 'Past deal — this offer has ended. The price shown is what it was when you saw it.'
                  : 'Past deal — this offer may have ended and the price may have changed.'
              }
            />
          ) : null}
          {badge || store || past ? (
            <View style={{ flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
              {past ? <PastBadge /> : null}
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
          {deal.coupon ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Report this coupon code as not working"
              disabled={couponReported}
              onPress={reportCouponDead}
              hitSlop={8}
              style={{ alignSelf: 'flex-start' }}
            >
              <Text style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '600', textDecorationLine: couponReported ? 'none' : 'underline' }}>
                {couponReported ? 'Reported — thanks' : 'Code not working?'}
              </Text>
            </Pressable>
          ) : null}

          {deal.is_lowest ? <Note kind="good" icon="trend" text="Lowest price we have recorded for this product." /> : null}
          {suspicious ? (
            <Note kind="warn" icon="alert" text={deal.ai_mrp_reason || 'The quoted MRP looks inflated versus this product’s price history.'} />
          ) : null}
          {deal.ai_hook ? <Note kind="good" icon="trend" text={deal.ai_hook} /> : null}

          <View style={{ flexDirection: 'row', gap: 8 }}>
            <View style={{ flex: 1 }}>
              <SaveButton deal={deal} />
            </View>
            <View style={{ flex: 1 }}>
              <Button title="Share" icon="share" variant="soft" onPress={share} />
            </View>
          </View>

          {verdict ? <PriceVerdictMeter verdict={verdict} /> : null}

          {!past && fresh && deal.price ? (
            <PriceAlertBlock
              deal={deal}
              watching={watching}
              onCreated={(a) => setAlerts((prev) => [a, ...prev.filter((x) => x.id !== a.id)])}
            />
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
            <FullHistoryCard state={bhState} history={bh} current={deal.price} />
            {chartPoints ? <PriceChart points={chartPoints} /> : <ActivityIndicator color={t.c.accent} />}
          </View>

          {deal.price_history_url ? (
            <Button
              title="Price history & stock"
              icon="trend"
              iconRight="external"
              variant="soft"
              onPress={() => {
                haptic.light();
                WebBrowser.openBrowserAsync(deal.price_history_url as string).catch(() => {});
              }}
            />
          ) : null}

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
                <Text style={{ flex: 1, color: t.c.text, fontWeight: '600', fontSize: t.f.sm }}>Original post</Text>
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

        <View style={{ paddingHorizontal: 16, maxWidth: 720, width: '100%', alignSelf: 'center' }}>
          <DealRail
            title="🧭 Similar deals"
            sub="More like this, live right now"
            deals={similar}
            note={similarNote}
            onOpen={openDeal}
            pad={16}
          />
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
              title={past ? `Check on ${store || 'store'}` : `Open on ${store || 'store'}`}
              iconRight="external"
              onPress={() => {
                haptic.light();
                void recordDealSignal('buy', deal);
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

const monthYear = (sec: number) =>
  new Date(sec * 1000).toLocaleDateString('en-IN', { month: 'short', year: 'numeric' });

/** The "full price history" card: loading line, then the headline numbers from BuyHatke. */
function FullHistoryCard({
  state,
  history,
  current,
}: {
  state: 'idle' | 'loading' | 'found' | 'none';
  history: BuyHatkeHistory | null;
  current: number | null;
}) {
  const t = useTheme();
  if (state === 'loading') {
    return (
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10, padding: 12, borderRadius: t.r.sm, backgroundColor: t.c.surface2 }}>
        <ActivityIndicator size="small" color={t.c.good} />
        <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>Fetching full price history…</Text>
      </View>
    );
  }
  if (state !== 'found' || !history) return null;
  const atLowest = current != null && current <= history.lowest;
  const stat = (label: string, value: string, color: string) => (
    <View style={{ flex: 1, gap: 2 }}>
      <Text style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '700' }}>{label}</Text>
      <Text style={{ color, fontSize: t.f.md, fontWeight: '800' }}>{value}</Text>
    </View>
  );
  return (
    <View style={{ gap: 12, padding: 14, borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.goodLine, backgroundColor: t.c.goodSoft }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <View style={{ width: 22, height: 22, borderRadius: 11, alignItems: 'center', justifyContent: 'center', backgroundColor: t.c.good }}>
          <Icon name="check" size={14} color={t.c.bg} strokeWidth={3} />
        </View>
        <Text style={{ flex: 1, color: t.c.text, fontSize: t.f.md, fontWeight: '800' }}>Full price history</Text>
        <Text style={{ color: t.c.good, fontSize: t.f.xs, fontWeight: '700' }}>{history.points.length} points</Text>
      </View>
      <View style={{ flexDirection: 'row', gap: 10 }}>
        {stat('Lowest ever', money(history.lowest), t.c.good)}
        {stat('Highest', money(history.highest), t.c.text)}
        {stat('Tracked since', monthYear(history.since), t.c.text)}
      </View>
      {atLowest ? (
        <Text style={{ color: t.c.good, fontSize: t.f.sm, fontWeight: '700' }}>🎉 Today's price matches the lowest ever</Text>
      ) : null}
      <Pressable
        accessibilityRole="link"
        onPress={() => WebBrowser.openBrowserAsync(history.pageUrl).catch(() => {})}
        hitSlop={8}
      >
        <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>
          History by <Text style={{ color: t.c.accent, fontWeight: '700' }}>BuyHatke</Text> ↗
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
  d.discount_pct >= 5 ? [`${d.discount_pct}% off`, 'hot'] : d.saving ? [`Save ${money(d.saving)}`, 'good'] : [storeName(d) || 'Live deal', 'muted'];

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

  return (
    <View style={{ padding: 14, gap: 10, borderRadius: t.r.md, backgroundColor: t.c.accentSoft, borderWidth: 1, borderColor: t.c.accentLine }}>
      <View style={{ gap: 2 }}>
        <Text style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '700' }}>🔔 Price-drop alert</Text>
        <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>
          {watching ? `Watching for ${money(watching.target_price)} or less` : 'Get notified when it gets cheaper'}
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
            borderColor: focused ? t.c.accent : t.c.border,
            backgroundColor: t.c.surface,
          }}
        >
          <Text style={{ color: t.c.text2, fontSize: t.f.sm, fontWeight: '600' }}>Notify me below ₹</Text>
          <TextInput
            value={target}
            onChangeText={setTarget}
            onFocus={() => setFocused(true)}
            onBlur={() => setFocused(false)}
            keyboardType="number-pad"
            returnKeyType="done"
            onSubmitEditing={submit}
            selectionColor={t.c.accent}
            accessibilityLabel="Alert me below this price, in rupees"
            maxFontSizeMultiplier={1.4}
            style={{ flex: 1, color: t.c.text, fontSize: t.f.base, fontWeight: '700', paddingHorizontal: 4 }}
          />
        </View>
        <Button title={watching ? 'Update' : 'Notify me'} loading={busy} onPress={submit} />
      </View>
    </View>
  );
}
