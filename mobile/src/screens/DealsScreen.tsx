import AsyncStorage from '@react-native-async-storage/async-storage';
import { useFocusEffect, useNavigation, useScrollToTop } from '@react-navigation/native';
import { LinearGradient } from 'expo-linear-gradient';
import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Animated,
  Easing,
  FlatList,
  Pressable,
  RefreshControl,
  ScrollView,
  Text,
  View,
  useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, errorMessage, isAbort, isOffline, type Deal, type NamedCount, type Stats } from '../api';
import {
  BrandMark,
  Chip,
  DealCard,
  DealCardSkeleton,
  EmptyState,
  Icon,
  IconButton,
  RailCard,
  RailSkeleton,
  SectionHead,
  activeFilterChips,
  activeFilterCount,
  categoryIcon,
  clearedFilters,
  greeting,
  haptic,
  isBrowseMode,
  money,
  num,
  plural,
  timeAgo,
  toQuery,
  useCategories,
  useDealFilters,
  useHideNativeHeader,
  useToast,
  withoutFilter,
  type DealFilters,
} from '../components';
import { isPublicMode } from '../native/session';
import { useTheme } from '../theme';
import type { SortKey } from '../api/types';
import type { TabNav } from './types';

const PAGE = 30;
const PAD = 14;
const GAP = 10;
const VIEW_KEY = 'dr.view';

const SORT_TABS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest' },
  { key: 'best', label: 'Top rated' },
  { key: 'discount', label: 'Biggest discount' },
  { key: 'price_low', label: 'Lowest price' },
];

const SORT_HEADINGS: Record<SortKey, [string, string]> = {
  newest: ['🕘 Latest deals', 'Freshly posted deals'],
  best: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
  relevance: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
  discount: ['⚡ Biggest discounts', 'Largest drop from the quoted MRP'],
  price_low: ['💸 Cheapest first', 'Lowest prices first'],
  price_high: ['💎 Priciest first', 'Highest prices first'],
};

function gridHeading(f: DealFilters): [string, string] {
  if (f.q) return [`🔎 Results for “${f.q}”`, 'Best matches'];
  if (activeFilterCount(f)) return ['🏷️ Filtered deals', 'Matching your filters'];
  return SORT_HEADINGS[f.sort] ?? SORT_HEADINGS.newest;
}

type FeedStatus = 'loading' | 'ready' | 'error';

export function DealsScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<TabNav<'Deals'>>();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const { filters, setFilters, patchFilters, refreshTick } = useDealFilters();
  const categories = useCategories();

  const [layout, setLayout] = useState<'grid' | 'list'>('grid');
  useEffect(() => {
    AsyncStorage.getItem(VIEW_KEY)
      .then((v) => v === 'list' && setLayout('list'))
      .catch(() => {});
  }, []);
  const toggleLayout = (next: 'grid' | 'list') => {
    if (next === layout) return;
    haptic.select();
    setLayout(next);
    AsyncStorage.setItem(VIEW_KEY, next).catch(() => {});
  };

  const cols = layout === 'grid' ? (width >= 900 ? 4 : width >= 600 ? 3 : 2) : width >= 720 ? 2 : 1;
  const cardW = Math.floor((width - PAD * 2 - GAP * (cols - 1)) / cols);

  /* ---------------- feed ---------------- */
  const [items, setItems] = useState<Deal[]>([]);
  const [total, setTotal] = useState(0);
  const [status, setStatus] = useState<FeedStatus>('loading');
  const [error, setError] = useState<unknown>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [resultCats, setResultCats] = useState<NamedCount[]>([]);
  const ctrlRef = useRef<AbortController | null>(null);
  const moreBusy = useRef(false);
  const listRef = useRef<FlatList<Deal>>(null);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  useScrollToTop(listRef);

  const filtersKey = JSON.stringify(filters);

  const load = useCallback(
    async (mode: 'reset' | 'refresh') => {
      ctrlRef.current?.abort();
      const ctrl = new AbortController();
      ctrlRef.current = ctrl;
      moreBusy.current = false;
      setLoadingMore(false);
      if (mode === 'reset') setStatus('loading');
      else setRefreshing(true);
      try {
        const res = await api.deals.list({ ...toQuery(filters), limit: PAGE, offset: 0 }, { signal: ctrl.signal });
        setItems(res.results);
        setTotal(res.total);
        setResultCats(Array.isArray(res.categories) ? res.categories : []);
        setError(null);
        setStatus('ready');
      } catch (e) {
        if (isAbort(e)) return;
        if (mode === 'refresh' && itemsRef.current.length) {
          toast(isOffline(e) ? 'You’re offline — showing the last loaded deals.' : errorMessage(e), 'err');
        } else {
          setError(e);
          setStatus('error');
        }
      } finally {
        if (ctrlRef.current === ctrl) setRefreshing(false);
      }
    },
    [filtersKey],
  );

  const loadMore = useCallback(async () => {
    if (moreBusy.current || status !== 'ready' || items.length >= total) return;
    const ctrl = ctrlRef.current;
    moreBusy.current = true;
    setLoadingMore(true);
    try {
      const res = await api.deals.list(
        { ...toQuery(filters), limit: PAGE, offset: items.length },
        { signal: ctrl?.signal },
      );
      if (ctrlRef.current !== ctrl) return;
      setItems((prev) => {
        const seen = new Set(prev.map((d) => d.id));
        return prev.concat(res.results.filter((d) => !seen.has(d.id)));
      });
      setTotal(res.total);
    } catch (e) {
      if (!isAbort(e)) toast(isOffline(e) ? 'You’re offline.' : 'Couldn’t load more deals — try again.', 'err');
    } finally {
      if (ctrlRef.current === ctrl) {
        moreBusy.current = false;
        setLoadingMore(false);
      }
    }
  }, [filters, items.length, status, total, toast]);

  useEffect(() => {
    load('reset');
    return () => ctrlRef.current?.abort();
  }, [load]);

  const lastQ = useRef(filters.q);
  useEffect(() => {
    if (lastQ.current !== filters.q) {
      lastQ.current = filters.q;
      listRef.current?.scrollToOffset({ offset: 0, animated: false });
    }
  }, [filters.q]);

  /* ---------------- stats, user, rails ---------------- */
  const [stats, setStats] = useState<Stats | null>(null);
  const [statusOverride, setStatusOverride] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [tracked, setTracked] = useState<number | null>(null);
  const [trending, setTrending] = useState<Deal[] | null>(null);
  const [lows, setLows] = useState<Deal[] | null>(null);

  const loadStats = useCallback(() => {
    api.system
      .stats()
      .then(setStats)
      .catch(() => {});
  }, []);

  const loadRails = useCallback(() => {
    api.deals
      .trending(12)
      .then((r) => setTrending(r.results ?? []))
      .catch(() => setTrending([]));
    api.deals
      .list({ only_lowest: true, sort: 'best', limit: 12, offset: 0 })
      .then((r) => setLows(r.results ?? []))
      .catch(() => setLows([]));
  }, []);

  useEffect(() => {
    loadStats();
    loadRails();
    api.auth
      .me()
      .then((me) => {
        if (me.authenticated && !isPublicMode()) {
          setName(me.user.first_name || me.user.username || '');
          setTracked(me.tracked_channels);
        }
      })
      .catch(() => {});
  }, [loadStats, loadRails]);

  const firstTick = useRef(refreshTick);
  useEffect(() => {
    if (refreshTick === firstTick.current) return;
    load('refresh');
    loadStats();
    loadRails();
    setTracked(null);
  }, [refreshTick, load, loadStats, loadRails]);

  useFocusEffect(
    useCallback(() => {
      const id = setInterval(loadStats, 60_000);
      return () => clearInterval(id);
    }, [loadStats]),
  );

  const onRefresh = () => {
    haptic.light();
    setStatusOverride(null);
    load('refresh');
    loadStats();
    loadRails();
  };

  /* ---------------- sync ---------------- */
  const [syncing, setSyncing] = useState(false);
  const spin = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (!syncing) return;
    spin.setValue(0);
    const loop = Animated.loop(
      Animated.timing(spin, { toValue: 1, duration: 900, easing: Easing.linear, useNativeDriver: true }),
    );
    loop.start();
    return () => loop.stop();
  }, [syncing, spin]);

  const syncNow = async () => {
    if (syncing) return;
    haptic.light();
    setSyncing(true);
    setStatusOverride('Scanning for new deals…');
    try {
      const res = await api.channels.sync();
      const added = (res.new || 0) + (res.merged || 0);
      haptic.success();
      toast(
        added
          ? `Synced: ${res.new ?? 0} new deals, ${res.merged ?? 0} matched to existing ones.`
          : 'Sync complete — no new deals yet.',
        'ok',
      );
      setStatusOverride(
        added ? `Updated just now · ${res.new ?? 0} new, ${res.merged ?? 0} matched` : 'Updated just now · no new deals',
      );
      load('refresh');
      loadStats();
      loadRails();
    } catch (e) {
      haptic.error();
      toast(errorMessage(e), 'err');
      setStatusOverride('Sync failed — pull down or tap sync to retry');
    } finally {
      setSyncing(false);
    }
  };

  /* ---------------- actions ---------------- */
  const openDeal = useCallback((d: Deal) => navigation.navigate('DealDetail', { id: d.id }), [navigation]);

  const pickCategory = (name: string) => {
    if (name === filters.category) return;
    haptic.select();
    setFilters((f) => ({ ...f, category: name, subcategory: '' }));
  };

  const saveSearchAlert = async () => {
    if (!filters.q) return;
    try {
      await api.watchlists.create({
        query: filters.q,
        category: filters.category,
        store: filters.store,
        max_price: filters.max_price,
        min_discount: filters.min_discount,
      });
      haptic.success();
      toast(`Alert saved for “${filters.q}”.`, 'ok');
    } catch (e) {
      toast(errorMessage(e), 'err');
    }
  };

  const browse = isBrowseMode(filters);
  const chips = activeFilterChips(filters);
  const filterCount = activeFilterCount(filters);
  const [gridTitle, gridSub] = gridHeading(filters);

  /* ---------------- header ---------------- */
  const header = (
    <View style={{ gap: 16, paddingBottom: 12 }}>
      <StatsCard stats={stats} override={statusOverride} syncing={syncing} />

      {tracked === 0 && !isPublicMode() ? (
        <Pressable
          accessibilityRole="button"
          onPress={() => navigation.navigate('Channels')}
          style={{
            flexDirection: 'row',
            alignItems: 'center',
            gap: 10,
            padding: 14,
            borderRadius: t.r.md,
            backgroundColor: t.c.accentSoft,
            borderWidth: 1,
            borderColor: t.c.accentLine,
          }}
        >
          <Icon name="radio" size={20} color={t.c.accent} />
          <Text style={{ flex: 1, color: t.c.accent, fontWeight: '600', fontSize: t.f.sm }}>
            Pick the deal channels you want DealRadar to read.
          </Text>
          <Icon name="chevRight" size={18} color={t.c.accent} />
        </Pressable>
      ) : null}

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        style={{ marginHorizontal: -PAD }}
        contentContainerStyle={{ paddingHorizontal: PAD, gap: 6 }}
        accessibilityRole="tablist"
      >
        {[{ name: '', label: 'All deals' }, ...categories.map((c) => ({ name: c.name, label: c.name }))].map((c) => (
          <CategoryTile
            key={c.name || 'all'}
            icon={c.name ? categoryIcon(c.name) : '✨'}
            label={c.label}
            active={filters.category === c.name}
            onPress={() => pickCategory(c.name)}
          />
        ))}
      </ScrollView>

      {browse ? (
        <>
          <Rail
            title="🔥 Trending right now"
            sub="Reposted across your channels"
            deals={trending}
            note={(d) => [`${d.repost_count}× posted`, 'hot']}
            onOpen={openDeal}
          />
          <Rail
            title="📉 All-time lows"
            sub="Cheapest we have ever recorded"
            deals={lows}
            note={(d) => (d.saving ? [`Save ${money(d.saving)}`, 'good'] : ['All-time low', 'good'])}
            onOpen={openDeal}
            onSeeAll={() => {
              haptic.select();
              patchFilters({ only_lowest: true });
            }}
          />
        </>
      ) : null}

      <SectionHead
        title={gridTitle}
        sub={gridSub}
        right={
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {filters.q ? (
              <IconButton name="bell" label="Alert me about this search" variant="soft" onPress={saveSearchAlert} />
            ) : null}
            <IconButton
              name="sliders"
              label={filterCount ? `Filters, ${filterCount} active` : 'Filters'}
              variant="soft"
              badge={filterCount}
              onPress={() => navigation.navigate('Filters')}
            />
          </View>
        }
      />

      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={{ flex: 1, marginLeft: -PAD }}
          contentContainerStyle={{ paddingLeft: PAD, gap: 6, paddingRight: 8 }}
          accessibilityRole="tablist"
        >
          {SORT_TABS.map((s) => {
            const on = filters.sort === s.key;
            return (
              <Pressable
                key={s.key}
                accessibilityRole="tab"
                accessibilityState={{ selected: on }}
                onPress={() => {
                  if (on) return;
                  haptic.select();
                  patchFilters({ sort: s.key });
                }}
                style={{
                  minHeight: 38,
                  paddingHorizontal: 14,
                  borderRadius: t.r.full,
                  borderWidth: 1,
                  justifyContent: 'center',
                  borderColor: on ? t.c.text : t.c.border,
                  backgroundColor: on ? t.c.text : t.c.surface,
                }}
              >
                <Text maxFontSizeMultiplier={1.3} style={{ color: on ? t.c.bg : t.c.text2, fontWeight: '600', fontSize: t.f.sm }}>
                  {s.label}
                </Text>
              </Pressable>
            );
          })}
        </ScrollView>
        <View
          accessibilityRole="radiogroup"
          accessibilityLabel="Layout"
          style={{ flexDirection: 'row', padding: 3, gap: 2, borderRadius: t.r.sm, backgroundColor: t.c.surface2, borderWidth: 1, borderColor: t.c.border }}
        >
          {(['grid', 'list'] as const).map((v) => (
            <Pressable
              key={v}
              accessibilityRole="radio"
              accessibilityLabel={v === 'grid' ? 'Grid view' : 'List view'}
              accessibilityState={{ selected: layout === v }}
              onPress={() => toggleLayout(v)}
              style={{
                width: 40,
                height: 38,
                borderRadius: t.r.xs,
                alignItems: 'center',
                justifyContent: 'center',
                backgroundColor: layout === v ? t.c.surface : 'transparent',
              }}
            >
              <Icon name={v} size={17} color={layout === v ? t.c.text : t.c.text3} />
            </Pressable>
          ))}
        </View>
      </View>

      {chips.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {chips.map((c) => (
            <Chip
              key={c.key}
              label={c.label}
              removable
              onPress={() => {
                haptic.select();
                setFilters((f) => withoutFilter(f, c.key));
              }}
            />
          ))}
        </View>
      ) : null}

      <ResultCats
        cats={resultCats}
        active={filters.category}
        hidden={browse}
        onPick={(name) => pickCategory(name)}
      />

      {status === 'ready' && total > 0 && !browse ? (
        <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>
          <Text style={{ fontWeight: '800', color: t.c.text }}>{num(total)}</Text> {plural(total, 'deal')}
          {filters.q ? ` for “${filters.q}”` : ''} · showing {Math.min(items.length, total)}
        </Text>
      ) : null}
    </View>
  );

  /* ---------------- empty / error ---------------- */
  const hasFilters = !!(filters.q || filterCount);
  let empty: React.ReactElement | null = null;
  if (status === 'loading') {
    empty = (
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: GAP }}>
        {Array.from({ length: cols * 3 }, (_, i) => (
          <DealCardSkeleton key={i} layout={layout} width={cardW} />
        ))}
      </View>
    );
  } else if (status === 'error') {
    const offline = isOffline(error);
    empty = (
      <EmptyState
        emoji={offline ? '📶' : '⚠️'}
        title={offline ? 'You’re offline' : 'Something went wrong'}
        message={offline ? 'Check your connection, then try again.' : 'We couldn’t load the deals right now.'}
        detail={errorMessage(error, '')}
        actions={[{ title: 'Try again', variant: 'primary', onPress: () => load('reset') }]}
      />
    );
  } else {
    empty = (
      <EmptyState
        emoji={hasFilters ? '🔎' : '📭'}
        title="No products found"
        message={
          hasFilters
            ? 'Nothing matches your search and filters right now. Try fewer filters or a broader term.'
            : 'New deals arrive every few minutes — check back soon.'
        }
        actions={[
          ...(hasFilters
            ? [{ title: 'Clear search & filters', variant: 'primary' as const, onPress: () => setFilters((f) => ({ ...clearedFilters(f), q: '' })) }]
            : []),
        ]}
      />
    );
  }

  const footer =
    loadingMore ? (
      <View style={{ paddingVertical: 20 }}>
        <ActivityIndicator color={t.c.accent} />
      </View>
    ) : status === 'ready' && items.length > 0 && items.length >= total ? (
      <Text style={{ textAlign: 'center', color: t.c.text3, fontSize: t.f.xs, paddingVertical: 20 }}>
        {stats?.deal_ttl_hours ? `You’re all caught up · deals are kept ${stats.deal_ttl_hours}h or until the link goes dead.` : 'You’re all caught up'}
      </Text>
    ) : null;

  const data = status === 'loading' ? [] : items;

  const renderItem = useCallback(
    ({ item }: { item: Deal }) => (
      <DealCard deal={item} layout={layout} query={filters.q} width={cardW} onOpen={openDeal} />
    ),
    [layout, filters.q, cardW, openDeal],
  );

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <TopBar
        name={name}
        query={filters.q}
        topInset={insets.top}
        syncing={syncing}
        spin={spin}
        onSearch={() => navigation.navigate('Search')}
        onClearQuery={() => {
          haptic.select();
          patchFilters({ q: '' });
        }}
        onSync={isPublicMode() ? undefined : syncNow}
      />
      <FlatList
        ref={listRef}
        key={`${layout}-${cols}`}
        data={data}
        keyExtractor={(d) => d.id}
        renderItem={renderItem}
        numColumns={cols}
        columnWrapperStyle={cols > 1 ? { gap: GAP } : undefined}
        ItemSeparatorComponent={Separator}
        ListHeaderComponent={header}
        ListEmptyComponent={empty}
        ListFooterComponent={footer}
        contentContainerStyle={{ paddingHorizontal: PAD, paddingTop: 12, paddingBottom: 24 }}
        onEndReached={loadMore}
        onEndReachedThreshold={0.8}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={onRefresh}
            colors={[t.c.accent]}
            tintColor={t.c.accent}
            progressBackgroundColor={t.c.surface}
          />
        }
        keyboardShouldPersistTaps="handled"
        initialNumToRender={8}
        windowSize={9}
        removeClippedSubviews
      />
    </View>
  );
}

const Separator = () => <View style={{ height: GAP }} />;

/* ============================================================ */

function TopBar({
  name,
  query,
  topInset,
  syncing,
  spin,
  onSearch,
  onClearQuery,
  onSync,
}: {
  name: string;
  query: string;
  topInset: number;
  syncing: boolean;
  spin: Animated.Value;
  onSearch: () => void;
  onClearQuery: () => void;
  onSync?: () => void;
}) {
  const t = useTheme();
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <View style={{ paddingTop: topInset + 8, paddingHorizontal: PAD, paddingBottom: 10, gap: 10, backgroundColor: t.c.bg, borderBottomWidth: 1, borderBottomColor: t.c.border }}>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 10 }}>
        <BrandMark size={34} />
        <View style={{ flex: 1 }}>
          <Text maxFontSizeMultiplier={1.3} style={{ fontSize: 18, fontWeight: '800', letterSpacing: -0.6, color: t.c.text }}>
            Deal<Text style={{ color: t.c.accent }}>Radar</Text>
          </Text>
          <Text numberOfLines={1} maxFontSizeMultiplier={1.3} style={{ color: t.c.text2, fontSize: t.f.xs }}>
            {name ? `${greeting()}, ${name} 👋` : `${greeting()} 👋`}
          </Text>
        </View>
        {onSync ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Sync deals now"
          accessibilityHint="Fetches new deals now"
          accessibilityState={{ busy: syncing }}
          onPress={onSync}
          disabled={syncing}
          style={({ pressed }) => ({
            width: 44,
            height: 44,
            borderRadius: t.r.sm,
            alignItems: 'center',
            justifyContent: 'center',
            backgroundColor: t.c.surface2,
            borderWidth: 1,
            borderColor: t.c.border,
            opacity: pressed ? 0.7 : 1,
          })}
        >
          <Animated.View style={{ transform: [{ rotate }] }}>
            <Icon name="sync" size={19} color={syncing ? t.c.accent : t.c.text} />
          </Animated.View>
        </Pressable>
        ) : null}
      </View>
      <Pressable
        accessibilityRole="search"
        accessibilityLabel={query ? `Search: ${query}` : 'Search deals'}
        accessibilityHint="Opens search"
        onPress={onSearch}
        style={{
          minHeight: 46,
          borderRadius: t.r.full,
          paddingLeft: 16,
          paddingRight: 4,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          backgroundColor: t.c.surface,
          borderWidth: 1,
          borderColor: query ? t.c.accentLine : t.c.border,
        }}
      >
        <Icon name="search" size={18} color={t.c.text3} />
        <Text numberOfLines={1} maxFontSizeMultiplier={1.3} style={{ flex: 1, color: query ? t.c.text : t.c.text3, fontSize: t.f.md, fontWeight: query ? '600' : '400' }}>
          {query || 'What are you looking for?'}
        </Text>
        {query ? <IconButton name="close" label="Clear search" size={17} color={t.c.text2} onPress={onClearQuery} /> : null}
      </Pressable>
    </View>
  );
}

function StatsCard({ stats, override, syncing }: { stats: Stats | null; override: string | null; syncing: boolean }) {
  const t = useTheme();
  const last = stats?.ingest?.last_run ?? null;
  const status =
    override ??
    (stats == null
      ? 'Tuning the radar…'
      : last
        ? `Updated ${timeAgo(last)} · scanning every ${Math.round((stats.poll_interval_seconds ?? 0) / 60)} min`
        : 'Waiting for the first sync…');
  const cells: [string, number | undefined, boolean][] = [
    ['Live deals', stats?.deals_live, false],
    ['Added today', stats?.deals_today, true],
  ];
  return (
    <View style={{ borderRadius: t.r.lg, borderWidth: 1, borderColor: t.c.border, overflow: 'hidden', backgroundColor: t.c.surface }}>
      <LinearGradient
        colors={[t.c.accentSoft, 'transparent']}
        start={{ x: 1, y: 0 }}
        end={{ x: 0.3, y: 1 }}
        style={{ padding: 15, gap: 12 }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <LiveDot mode={syncing ? 'syncing' : last ? 'live' : 'idle'} />
          <Text numberOfLines={2} style={{ flex: 1, color: t.c.text2, fontSize: t.f.xs, fontWeight: '600' }}>
            {status}
          </Text>
        </View>
        <View style={{ flexDirection: 'row' }}>
          {cells.map(([label, value, up]) => (
            <View key={label} style={{ flex: 1 }} accessible accessibilityLabel={`${label}: ${value ?? 'loading'}`}>
              <Text
                maxFontSizeMultiplier={1.3}
                style={{ fontSize: 22, fontWeight: '800', letterSpacing: -0.7, color: up ? t.c.good : t.c.text, fontVariant: ['tabular-nums'] }}
              >
                {value == null ? '–' : num(value)}
              </Text>
              <Text maxFontSizeMultiplier={1.3} style={{ fontSize: 10.5, color: t.c.text3, fontWeight: '700', letterSpacing: 0.5, textTransform: 'uppercase', marginTop: 2 }}>
                {label}
              </Text>
            </View>
          ))}
        </View>
      </LinearGradient>
    </View>
  );
}

function LiveDot({ mode }: { mode: 'live' | 'syncing' | 'idle' }) {
  const t = useTheme();
  const pulse = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    if (mode === 'idle') return;
    const loop = Animated.loop(Animated.timing(pulse, { toValue: 1, duration: 2000, useNativeDriver: true }));
    loop.start();
    return () => loop.stop();
  }, [mode, pulse]);
  const color = mode === 'syncing' ? t.c.accent : mode === 'live' ? t.c.good : t.c.text3;
  return (
    <View style={{ width: 16, height: 16, alignItems: 'center', justifyContent: 'center' }}>
      {mode !== 'idle' ? (
        <Animated.View
          style={{
            position: 'absolute',
            width: 16,
            height: 16,
            borderRadius: 8,
            backgroundColor: color,
            opacity: pulse.interpolate({ inputRange: [0, 0.7, 1], outputRange: [0.45, 0, 0] }),
            transform: [{ scale: pulse.interpolate({ inputRange: [0, 1], outputRange: [0.5, 1.4] }) }],
          }}
        />
      ) : null}
      <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color }} />
    </View>
  );
}

function CategoryTile({ icon, label, active, onPress }: { icon: string; label: string; active: boolean; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected: active }}
      onPress={onPress}
      style={({ pressed }) => ({ width: 72, alignItems: 'center', gap: 6, transform: [{ scale: pressed ? 0.93 : 1 }] })}
    >
      <View
        style={{
          width: 56,
          height: 56,
          borderRadius: 18,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: active ? t.c.accentSoft : t.c.surface,
          borderWidth: active ? 1.5 : 1,
          borderColor: active ? t.c.accent : t.c.border,
        }}
      >
        <Text maxFontSizeMultiplier={1} style={{ fontSize: 23 }}>
          {icon}
        </Text>
      </View>
      <Text
        numberOfLines={1}
        maxFontSizeMultiplier={1.2}
        style={{ fontSize: 11, fontWeight: '600', color: active ? t.c.accent : t.c.text2, maxWidth: 72 }}
      >
        {label}
      </Text>
    </Pressable>
  );
}

function Rail({
  title,
  sub,
  deals,
  note,
  onOpen,
  onSeeAll,
}: {
  title: string;
  sub: string;
  deals: Deal[] | null;
  note: (d: Deal) => [string, 'good' | 'hot' | 'muted'];
  onOpen: (d: Deal) => void;
  onSeeAll?: () => void;
}) {
  const t = useTheme();
  if (deals && !deals.length) return null;
  return (
    <View style={{ gap: 10 }}>
      <SectionHead
        title={title}
        sub={sub}
        right={
          onSeeAll ? (
            <Pressable accessibilityRole="button" onPress={onSeeAll} hitSlop={10} style={{ minHeight: 44, justifyContent: 'center', paddingHorizontal: 6 }}>
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
        style={{ marginHorizontal: -PAD }}
        contentContainerStyle={{ paddingHorizontal: PAD, gap: 10 }}
        ListEmptyComponent={
          <View style={{ flexDirection: 'row', gap: 10 }}>
            {[0, 1, 2].map((i) => (
              <RailSkeleton key={i} />
            ))}
          </View>
        }
        renderItem={({ item }) => {
          const [text, tone] = note(item);
          return <RailCard deal={item} note={text} noteTone={tone} onOpen={onOpen} />;
        }}
      />
    </View>
  );
}

function ResultCats({
  cats,
  active,
  hidden,
  onPick,
}: {
  cats: NamedCount[];
  active: string;
  hidden: boolean;
  onPick: (name: string) => void;
}) {
  const list = useMemo(() => {
    const l = cats.filter((c) => c && c.name && c.count > 0);
    if (active && !l.some((c) => c.name === active)) l.push({ name: active, count: 0 });
    return l;
  }, [cats, active]);
  if (hidden || !list.length || (list.length < 2 && !active)) return null;
  const totalCount = list.reduce((s, c) => s + c.count, 0);
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      style={{ marginHorizontal: -PAD }}
      contentContainerStyle={{ paddingHorizontal: PAD, gap: 6 }}
    >
      {[{ name: '', count: totalCount }, ...list].map((c) => (
        <Chip
          key={c.name || 'all'}
          label={c.name || 'All'}
          leading={c.name ? categoryIcon(c.name) : undefined}
          count={c.count}
          active={active === c.name}
          onPress={() => onPick(c.name)}
        />
      ))}
    </ScrollView>
  );
}
