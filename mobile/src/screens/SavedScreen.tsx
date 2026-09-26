import { useFocusEffect, useNavigation } from '@react-navigation/native';
import React, { useCallback, useMemo, useRef, useState } from 'react';
import { FlatList, Pressable, RefreshControl, View, useWindowDimensions } from 'react-native';
import { Text } from '../components/Text';

import { api, errorMessage, isNotFound, type Deal, type PriceAlert } from '../api';
import {
  DealCard,
  DealImage,
  EmptyState,
  FadeInItem,
  OfflineBanner,
  ScreenHeader,
  SectionHead,
  haptic,
  listPriceAlerts,
  money,
  plural,
  removePriceAlert,
  snapshotToDeal,
  storeName,
  useOnline,
  useSaved,
  useToast,
} from '../components';
import { useTheme } from '../theme';
import type { RootNav } from './types';

const PAD = 16;
const GAP = 10;
const REFRESH_CAP = 60;

export function SavedScreen() {
  const t = useTheme();
  const navigation = useNavigation<RootNav>();
  const toast = useToast();
  const online = useOnline();
  const { width } = useWindowDimensions();
  const { saved, ready } = useSaved();

  const cols = width >= 720 ? 2 : 1;
  // One column runs edge to edge with hairline dividers, like the Deals list.
  const flat = cols === 1;
  const cardW = flat ? width : Math.floor((width - PAD * 2 - GAP * (cols - 1)) / cols);

  const [fresh, setFresh] = useState<Record<string, Deal | 'gone'>>({});
  const [alerts, setAlerts] = useState<PriceAlert[] | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const savedRef = useRef(saved);
  savedRef.current = saved;

  const ids = useMemo(
    () => Object.keys(saved).sort((a, b) => (saved[b].savedAt || 0) - (saved[a].savedAt || 0)),
    [saved],
  );

  const refresh = useCallback(async () => {
    const want = Object.keys(savedRef.current).slice(0, REFRESH_CAP);
    const [results] = await Promise.all([
      Promise.all(
        want.map((id) =>
          api.deals
            .get(id)
            .then((d) => [id, d] as const)
            .catch((e) => [id, isNotFound(e) ? ('gone' as const) : null] as const),
        ),
      ),
      listPriceAlerts()
        .then(setAlerts)
        .catch(() => setAlerts((prev) => prev ?? [])),
    ]);
    setFresh((prev) => {
      const next = { ...prev };
      for (const [id, v] of results) if (v) next[id] = v;
      return next;
    });
  }, []);

  useFocusEffect(
    useCallback(() => {
      void refresh();
    }, [refresh]),
  );

  const onRefresh = async () => {
    haptic.light();
    setRefreshing(true);
    await refresh().finally(() => setRefreshing(false));
  };

  // A deal gone from the server keeps its snapshot, marked past — same as the website.
  const deals: Deal[] = useMemo(
    () =>
      ids.map((id) => {
        const f = fresh[id];
        if (f && f !== 'gone') return f;
        const snap = snapshotToDeal(saved[id]);
        return f === 'gone' ? { ...snap, status: 'expired' } : snap;
      }),
    [ids, fresh, saved],
  );

  const openDeal = useCallback((d: Deal) => navigation.navigate('DealDetail', { id: d.id }), [navigation]);

  const removeAlert = async (a: PriceAlert) => {
    haptic.select();
    setAlerts((prev) => (prev ?? []).filter((x) => x.id !== a.id));
    try {
      await removePriceAlert(a.id);
      toast('Price alert removed.', 'info', 2200);
    } catch (e) {
      toast(errorMessage(e), 'err');
      void refresh();
    }
  };

  const header = (
    <View style={{ gap: 14, paddingBottom: 12 }}>
      {!online ? <OfflineBanner text="Offline — showing the prices you saved" /> : null}
      {alerts && alerts.length ? (
        <View style={{ gap: 8 }}>
          <SectionHead title="Price alerts" sub={`${alerts.length} ${plural(alerts.length, 'deal')} watched for a drop`} />
          <View style={{ borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.surface, overflow: 'hidden' }}>
            {alerts.map((a, i) => (
              <AlertRow key={a.id} alert={a} first={i === 0} onOpen={() => navigation.navigate('DealDetail', { id: a.deal_id })} onRemove={() => removeAlert(a)} />
            ))}
          </View>
        </View>
      ) : null}
      {ids.length ? (
        <SectionHead title="Saved deals" sub={`${ids.length} ${plural(ids.length, 'deal')} · prices refresh when you open this tab`} />
      ) : null}
    </View>
  );

  const renderItem = useCallback(
    ({ item, index }: { item: Deal; index: number }) => (
      <FadeInItem index={index}>
        <DealCard deal={item} layout="list" width={cardW} note={sinceSaved(savedRef.current[item.id]?.price, item, !!fresh[item.id])} onOpen={openDeal} />
      </FadeInItem>
    ),
    [cardW, openDeal, fresh],
  );

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Saved" large={!navigation.canGoBack()} />
      <FlatList
        key={`saved-${cols}`}
        data={deals}
        keyExtractor={(d) => d.id}
        renderItem={renderItem}
        numColumns={cols}
        // FlatList throws if columnWrapperStyle is set on a single-column list.
        columnWrapperStyle={cols > 1 ? { gap: GAP } : undefined}
        ItemSeparatorComponent={flat ? undefined : Separator}
        ListHeaderComponent={<View style={{ paddingHorizontal: flat ? PAD : 0 }}>{header}</View>}
        ListEmptyComponent={
          ready ? (
            <EmptyState
              icon="heart"
              title="Nothing saved yet"
              message="Tap the heart on any deal to keep it here. We’ll keep checking its price for you."
              actions={[{ title: 'Browse deals', variant: 'primary', onPress: () => navigation.navigate('Main', { screen: 'Deals' }) }]}
            />
          ) : null
        }
        contentContainerStyle={{ paddingHorizontal: flat ? 0 : PAD, paddingTop: 12, paddingBottom: 32 }}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={[t.c.accent]} tintColor={t.c.accent} progressBackgroundColor={t.c.surface} />
        }
        initialNumToRender={8}
        windowSize={9}
        removeClippedSubviews
      />
    </View>
  );
}

const Separator = () => <View style={{ height: GAP }} />;

/** How the price moved since the deal was saved; only once we have a fresh price to compare. */
function sinceSaved(savedPrice: number | null | undefined, deal: Deal, isFresh: boolean): [string, 'good' | 'hot' | 'muted'] | null {
  if (!isFresh || savedPrice == null || deal.price == null || deal.status !== 'live') return null;
  const diff = Math.round(deal.price - savedPrice);
  if (diff < 0) return [`↓ ${money(-diff)} since you saved it`, 'good'];
  if (diff > 0) return [`↑ ${money(diff)} since you saved it`, 'hot'];
  return ['Same price as when you saved it', 'muted'];
}

function AlertRow({ alert, first, onOpen, onRemove }: { alert: PriceAlert; first: boolean; onOpen: () => void; onRemove: () => void }) {
  const t = useTheme();
  const dropped = !!alert.triggered_at;
  const store = storeName({ store: alert.store });
  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', borderTopWidth: first ? 0 : 1, borderTopColor: t.c.border }}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${alert.title || 'Deal'}. Target ${money(alert.target_price)}. ${dropped ? `Dropped to ${money(alert.triggered_price)}` : 'Watching'}`}
        onPress={onOpen}
        android_ripple={{ color: t.c.accentSoft }}
        style={{ flex: 1, flexDirection: 'row', alignItems: 'center', gap: 12, padding: 12, minHeight: 72 }}
      >
        <DealImage deal={{ image_url: alert.image_url }} style={{ width: 48, height: 48, borderRadius: 10 }} emojiSize={18} />
        <View style={{ flex: 1, gap: 3 }}>
          <Text numberOfLines={2} style={{ color: t.c.text, fontSize: t.f.sm, fontWeight: '600', lineHeight: 18 }}>
            {alert.title || 'Deal'}
          </Text>
          <Text numberOfLines={1} style={{ color: t.c.text3, fontSize: t.f.xs }}>
            Target {money(alert.target_price)}
            {alert.current_price != null ? ` · now ${money(alert.current_price)}` : ''}
            {store ? ` · ${store}` : ''}
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: dropped ? t.c.good : t.c.text3 }} />
            <Text style={{ color: dropped ? t.c.good : t.c.text2, fontSize: t.f.xs, fontWeight: '600' }}>
              {dropped ? `Dropped to ${money(alert.triggered_price)}` : 'Watching'}
            </Text>
          </View>
        </View>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`Remove price alert for ${alert.title || 'deal'}`}
        onPress={onRemove}
        hitSlop={6}
        style={({ pressed }) => ({ minWidth: 72, minHeight: 44, alignItems: 'center', justifyContent: 'center', opacity: pressed ? 0.6 : 1 })}
      >
        <Text style={{ color: t.c.text2, fontWeight: '600', fontSize: t.f.sm }}>Remove</Text>
      </Pressable>
    </View>
  );
}
