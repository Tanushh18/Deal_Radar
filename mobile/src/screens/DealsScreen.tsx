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

import { api, errorMessage, isAbort, isOffline, type Deal, type DealsPage, type KeyCount, type NamedCount, type Stats } from '../api';
import {
  AnimatedBackdrop,
  BrandMark,
  Chip,
  DealCard,
  DealCardSkeleton,
  DealRail,
  EmptyState,
  FadeInItem,
  GlassContent,
  GlassHeader,
  Icon,
  IconButton,
  NewDealsPill,
  OfflineBanner,
  SectionHead,
  activeFilterChips,
  activeFilterCount,
  categoryIcon,
  clearedFilters,
  endsIn,
  greeting,
  haptic,
  isBrowseMode,
  money,
  nextCheckIn,
  num,
  plural,
  storeName,
  timeAgo,
  titleCase,
  toQuery,
  useBlurTarget,
  useCategories,
  useDealFilters,
  useHideNativeHeader,
  useOnline,
  useToast,
  withCache,
  withoutFilter,
  type DealFilters,
  type RailTone,
} from '../components';
import { isPublicMode } from '../native/session';
import { SaleEventsRail } from '../components/SaleEventsRail';
import { useTheme } from '../theme';
import type { SortKey } from '../api/types';
import type { TabNav } from './types';

const PAGE = 30;
const PAD = 14;
const GAP = 10;
const VIEW_KEY = 'dr.view';

const SORT_TABS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest' },
  { key: 'for_you', label: 'For You' },
  { key: 'best', label: 'Top rated' },
  { key: 'discount', label: 'Biggest discount' },
  { key: 'price_low', label: 'Lowest price' },
  { key: 'ending', label: 'Ending soon' },
];

const SORT_HEADINGS: Record<SortKey, [string, string]> = {
  newest: ['🕘 Latest deals', 'Freshly posted deals'],
  best: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
  relevance: ['🏆 Top deals', 'Ranked by DealRadar’s deal score'],
  for_you: ['✨ For You', 'Matched to what you follow'],
  discount: ['⚡ Biggest discounts', 'Largest drop from the quoted MRP'],
  price_low: ['💸 Cheapest first', 'Lowest prices first'],
  price_high: ['💎 Priciest first', 'Highest prices first'],
  ending: ['⏳ Ending soon', 'Grab these before they expire'],
};

function gridHeading(f: DealFilters): [string, string] {
  if (f.q) return [`🔎 Results for “${f.q}”`, 'Best matches'];
  if (activeFilterCount(f)) return ['🏷️ Filtered deals', 'Matching your filters'];
  return SORT_HEADINGS[f.sort] ?? SORT_HEADINGS.newest;
}

type RailKey = 'trending' | 'lows' | 'ending' | 'fresh' | 'coupons';
type Rails = Record<RailKey, Deal[] | null>;
const EMPTY_RAILS: Rails = { trending: null, lows: null, ending: null, fresh: null, coupons: null };

const RAIL_NOTES: Record<RailKey, (d: Deal) => [string, RailTone]> = {
  trending: (d) => [`${d.repost_count}× posted`, 'hot'],
  lows: (d) => (d.saving ? [`Save ${money(d.saving)}`, 'good'] : ['All-time low', 'good']),
  ending: (d) => [endsIn(d.expires_at) || 'Ending soon', 'hot'],
  fresh: (d) => [timeAgo(d.posted_at) || 'Just now', 'muted'],
  coupons: (d) => [`🏷 ${d.coupon ?? 'Coupon'}`, 'good'],
};

type FeedStatus = 'loading' | 'ready' | 'error';
type ArchiveState = { q: string; total: number; results: Deal[] } | null;

export function DealsScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<TabNav<'Deals'>>();
  const toast = useToast();
  const online = useOnline();
  const blurTarget = useBlurTarget();
  const { width } = useWindowDimensions();
  const { filters, setFilters, patchFilters, refreshTick } = useDealFilters();
  const categories = useCategories();
  const [headerH, setHeaderH] = useState(insets.top + 112);

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
  const [fromCache, setFromCache] = useState(false);
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
        const { value: res, cached } = await withCache<DealsPage>('feed', () =>
          api.deals.list({ ...toQuery(filters), limit: PAGE, offset: 0 }, { signal: ctrl.signal }),
        );
        if (ctrlRef.current !== ctrl) return;
        setItems(res.results);
        setTotal(cached ? res.results.length : res.total);
        setResultCats(Array.isArray(res.categories) ? res.categories : []);
        setFromCache(cached);
        setError(null);
        setStatus('ready');
        if (cached && mode === 'refresh') toast('You’re offline — showing saved results.', 'err');
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
    if (moreBusy.current || status !== 'ready' || fromCache || items.length >= total) return;
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
  }, [filters, items.length, status, total, toast, fromCache]);

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

  /* ---------------- archive (past deals for a search) ---------------- */
  const [archive, setArchive] = useState<ArchiveState>(null);
  useEffect(() => {
    const q = filters.q.trim();
    setArchive(null);
    if (!q) return;
    const ctrl = new AbortController();
    api.deals
      .list({ q, archive: true, sort: 'relevance', limit: 24, offset: 0 }, { signal: ctrl.signal })
      .then((r) => setArchive({ q, total: r.total, results: r.results ?? [] }))
      .catch(() => {});
    return () => ctrl.abort();
  }, [filters.q, refreshTick]);

  /* ---------------- stats, rails, stores ---------------- */
  const [stats, setStats] = useState<Stats | null>(null);
  const [statusOverride, setStatusOverride] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [tracked, setTracked] = useState<number | null>(null);
  const [rails, setRails] = useState<Rails>(EMPTY_RAILS);
  const [stores, setStores] = useState<KeyCount[]>([]);
  const [newCount, setNewCount] = useState(0);
  const seenLive = useRef<number | null>(null);

  const loadStats = useCallback(() => {
    api.system
      .stats()
      .then((s) => {
        setStats(s);
        const live = s.deals_live || 0;
        if (seenLive.current != null && live > seenLive.current) setNewCount(live - seenLive.current);
        if (seenLive.current == null || live < seenLive.current) seenLive.current = live;
      })
      .catch(() => {});
  }, []);

  const loadRails = useCallback(() => {
    const put = (key: RailKey) => (r: { value: { results: Deal[] } }) =>
      setRails((prev) => ({ ...prev, [key]: r.value.results ?? [] }));
    const fail = (key: RailKey) => () => setRails((prev) => ({ ...prev, [key]: [] }));
    const rail = (key: RailKey, fetcher: () => Promise<{ results: Deal[] }>) =>
      withCache(`rail:${key}`, fetcher).then(put(key), fail(key));
    rail('trending', () => api.deals.trending(12));
    rail('lows', () => api.deals.list({ only_lowest: true, sort: 'best', limit: 12, offset: 0 }));
    rail('ending', () => api.deals.list({ sort: 'ending', limit: 12, offset: 0 }));
    rail('fresh', () => api.deals.list({ sort: 'newest', limit: 12, offset: 0 }));
    rail('coupons', () => api.deals.list({ has_coupon: true, sort: 'best', limit: 12, offset: 0 }));
    withCache('facets', () => api.deals.facets())
      .then((r) => setStores((r.value.stores ?? []).filter((s) => s.key && s.key !== 'unknown').slice(0, 14)))
      .catch(() => {});
  }, []);

  useEffect(() => {
    loadStats();
    loadRails();
    if (!isPublicMode()) {
      api.auth
        .me()
        .then((me) => {
          if (me.authenticated) {
            setName(me.user.first_name || me.user.username || '');
            setTracked(me.tracked_channels);
          }
        })
        .catch(() => {});
    }
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
      loadStats();
      const id = setInterval(loadStats, 60_000);
      return () => clearInterval(id);
    }, [loadStats]),
  );

  const wasOnline = useRef(online);
  useEffect(() => {
    if (online && !wasOnline.current && (fromCache || status === 'error')) {
      load('refresh');
      loadRails();
      loadStats();
    }
    wasOnline.current = online;
  }, [online, fromCache, status, load, loadRails, loadStats]);

  const reloadAll = () => {
    setStatusOverride(null);
    load('refresh');
    loadStats();
    loadRails();
  };

  const onRefresh = () => {
    haptic.light();
    reloadAll();
  };

  const showNew = () => {
    haptic.light();
    setNewCount(0);
    seenLive.current = stats?.deals_live ?? seenLive.current;
    listRef.current?.scrollToOffset({ offset: 0, animated: true });
    reloadAll();
  };

  /* ---------------- sync (signed-in mode only) ---------------- */
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

  const pickStore = (key: string) => {
    haptic.select();
    patchFilters({ store: filters.store === key ? '' : key });
  };

  const browse = isBrowseMode(filters);
  const chips = activeFilterChips(filters);
  const filterCount = activeFilterCount(filters);
  const [gridTitle, gridSub] = gridHeading(filters);
  const offline = !online || fromCache;

  /* ---------------- header ---------------- */
  const header = (
    <View style={{ gap: 16, paddingBottom: 12 }}>
      {offline ? <OfflineBanner /> : null}
      <StatsCard stats={stats} override={statusOverride} syncing={syncing} />

      <SaleEventsRail />

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

      {stores.length ? (
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          style={{ marginHorizontal: -PAD }}
          contentContainerStyle={{ paddingHorizontal: PAD, gap: 6 }}
          accessibilityLabel="Stores"
        >
          {stores.map((s) => (
            <Chip
              key={s.key}
              label={storeName({ store: s.key }) || titleCase(s.key)}
              count={s.count}
              leading="🛍"
              active={filters.store === s.key}
              accessibilityLabel={`${titleCase(s.key)}, ${s.count} deals`}
              onPress={() => pickStore(s.key)}
            />
          ))}
        </ScrollView>
      ) : null}

      {browse ? (
        <>
          <DealRail
            title="🔥 Trending right now"
            sub="Reposted across deal channels"
            deals={rails.trending}
            note={RAIL_NOTES.trending}
            onOpen={openDeal}
          />
          <DealRail
            title="⏳ Ending soon"
            sub="Grab these before they expire"
            deals={rails.ending}
            note={RAIL_NOTES.ending}
            onOpen={openDeal}
            onSeeAll={() => {
              haptic.select();
              patchFilters({ sort: 'ending' });
            }}
          />
          <DealRail
            title="⚡ Just dropped"
            sub="The newest deals, straight off the wire"
            deals={rails.fresh}
            note={RAIL_NOTES.fresh}
            onOpen={openDeal}
          />
          <DealRail
            title="📉 All-time lows"
            sub="Cheapest we have ever recorded"
            deals={rails.lows}
            note={RAIL_NOTES.lows}
            onOpen={openDeal}
            onSeeAll={() => {
              haptic.select();
              patchFilters({ only_lowest: true });
            }}
          />
          <DealRail
            title="🏷 Coupons"
            sub="Deals with a code to stack on top"
            deals={rails.coupons}
            note={RAIL_NOTES.coupons}
            onOpen={openDeal}
            onSeeAll={() => {
              haptic.select();
              patchFilters({ has_coupon: true });
            }}
          />
        </>
      ) : null}

      <SectionHead
        title={gridTitle}
        sub={gridSub}
        right={
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {filters.q && !isPublicMode() ? (
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

      <ResultCats cats={resultCats} active={filters.category} hidden={browse} onPick={(n) => pickCategory(n)} />

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
  const archiveHits = archive && archive.q === filters.q.trim() ? archive : null;
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
    const off = isOffline(error);
    empty = (
      <EmptyState
        emoji={off ? '📶' : '⚠️'}
        title={off ? 'You’re offline' : 'Something went wrong'}
        message={off ? 'We’ll refresh as soon as you’re back online.' : 'We couldn’t load the deals right now.'}
        detail={errorMessage(error, '')}
        actions={[{ title: 'Try again', variant: 'primary', onPress: () => load('reset') }]}
      />
    );
  } else if (!archiveHits?.total) {
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
            ? [
                {
                  title: 'Clear search & filters',
                  variant: 'primary' as const,
                  onPress: () => setFilters((f) => ({ ...clearedFilters(f), q: '' })),
                },
              ]
            : []),
        ]}
      />
    );
  }

  const footer = (
    <View>
      {loadingMore ? (
        <View style={{ paddingVertical: 20 }}>
          <ActivityIndicator color={t.c.accent} />
        </View>
      ) : status === 'ready' && items.length > 0 && items.length >= total && !archiveHits?.total ? (
        <Text style={{ textAlign: 'center', color: t.c.text3, fontSize: t.f.xs, paddingVertical: 20 }}>
          {stats?.deal_ttl_hours
            ? `You’re all caught up · deals are kept ${stats.deal_ttl_hours}h or until the link goes dead.`
            : 'You’re all caught up'}
        </Text>
      ) : null}
      {archiveHits?.total && status === 'ready' ? (
        <ArchiveSection
          archive={archiveHits}
          liveTotal={total}
          layout={layout}
          cols={cols}
          cardW={cardW}
          onOpen={openDeal}
        />
      ) : null}
    </View>
  );

  const data = status === 'loading' ? [] : items;

  const renderItem = useCallback(
    ({ item, index }: { item: Deal; index: number }) => (
      <FadeInItem index={index}>
        <DealCard deal={item} layout={layout} query={filters.q} width={cardW} onOpen={openDeal} />
      </FadeInItem>
    ),
    [layout, filters.q, cardW, openDeal],
  );

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <GlassContent target={blurTarget}>
        <AnimatedBackdrop />
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
          contentContainerStyle={{ paddingHorizontal: PAD, paddingTop: headerH + 12, paddingBottom: 24 }}
          scrollIndicatorInsets={{ top: headerH }}
          onEndReached={loadMore}
          onEndReachedThreshold={0.8}
          refreshControl={
            <RefreshControl
              refreshing={refreshing}
              onRefresh={onRefresh}
              progressViewOffset={headerH}
              colors={[t.c.accent]}
              tintColor={t.c.accent}
              progressBackgroundColor={t.c.surface}
            />
          }
          keyboardShouldPersistTaps="handled"
          initialNumToRender={8}
          maxToRenderPerBatch={8}
          windowSize={9}
          removeClippedSubviews
        />
      </GlassContent>
      <GlassHeader target={blurTarget} onHeight={setHeaderH}>
        <TopBar
          name={name}
          query={filters.q}
          topInset={insets.top}
          syncing={syncing}
          spin={spin}
          onSearch={() => navigation.navigate('Search')}
          onCheckPrice={() => navigation.navigate('CheckPrice', {})}
          onClearQuery={() => {
            haptic.select();
            patchFilters({ q: '' });
          }}
          onSync={isPublicMode() ? undefined : syncNow}
        />
      </GlassHeader>
      <NewDealsPill count={newCount} top={headerH + 8} onPress={showNew} />
    </View>
  );
}

const Separator = () => <View style={{ height: GAP }} />;

function ArchiveSection({
  archive,
  liveTotal,
  layout,
  cols,
  cardW,
  onOpen,
}: {
  archive: { q: string; total: number; results: Deal[] };
  liveTotal: number;
  layout: 'grid' | 'list';
  cols: number;
  cardW: number;
  onOpen: (d: Deal) => void;
}) {
  const rows: Deal[][] = [];
  for (let i = 0; i < archive.results.length; i += cols) rows.push(archive.results.slice(i, i + cols));
  const n = archive.total.toLocaleString('en-IN');
  const sub = liveTotal
    ? `${n} earlier ${plural(archive.total, 'deal')} for “${archive.q}” — prices may have changed`
    : `No live deal for “${archive.q}” right now — ${n} earlier ${plural(archive.total, 'deal')} from our archive`;
  return (
    <View style={{ gap: GAP, paddingTop: 20 }}>
      <SectionHead title="🗂 From the deal archive" sub={sub} />
      {rows.map((row, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: GAP }}>
          {row.map((d) => (
            <DealCard key={d.id} deal={{ ...d, status: d.status && d.status !== 'live' ? d.status : 'archived' }} layout={layout} width={cardW} onOpen={onOpen} />
          ))}
        </View>
      ))}
    </View>
  );
}

/* ============================================================ */

function TopBar({
  name,
  query,
  topInset,
  syncing,
  spin,
  onSearch,
  onCheckPrice,
  onClearQuery,
  onSync,
}: {
  name: string;
  query: string;
  topInset: number;
  syncing: boolean;
  spin: Animated.Value;
  onSearch: () => void;
  onCheckPrice: () => void;
  onClearQuery: () => void;
  onSync?: () => void;
}) {
  const t = useTheme();
  const rotate = spin.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] });
  return (
    <View style={{ paddingTop: topInset + 8, paddingHorizontal: PAD, paddingBottom: 10, gap: 10 }}>
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
        <IconButton name="link" label="Check a product’s price" variant="soft" onPress={onCheckPrice} />
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
  // Live, ticking, and tied to the server's real cycle — never a fixed decorative
  // schedule. Re-rendering once a second is cheap: this is the only thing it drives.
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const nextAt = last && stats?.poll_interval_seconds ? last + stats.poll_interval_seconds : null;
  const status =
    override ??
    (stats == null
      ? 'Tuning the radar…'
      : last
        ? `Updated ${timeAgo(last)} · next check in ${nextCheckIn(nextAt, now)}`
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
