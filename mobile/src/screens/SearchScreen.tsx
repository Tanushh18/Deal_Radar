import { useNavigation } from '@react-navigation/native';
import React, { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, SectionList, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, isAbort, isNotFound, type Deal, type Suggestions } from '../api';
import {
  DealImage,
  Highlight,
  Icon,
  IconButton,
  PastBadge,
  PriceRow,
  categoryIcon,
  rememberDeal,
  clearRecents,
  haptic,
  loadRecents,
  pushRecent,
  storeName,
  titleCase,
  useDealFilters,
  useHideNativeHeader,
  type DealFilters,
} from '../components';
import { useTheme } from '../theme';
import type { RootNav } from './types';

type Row =
  | { kind: 'recent'; text: string }
  | { kind: 'deal'; deal: Deal; past?: boolean }
  | { kind: 'facet'; field: 'category' | 'brand' | 'store'; value: string; label: string; count: number }
  | { kind: 'all' };

type Section = { title: string; key: string; data: Row[]; action?: { label: string; onPress: () => void } };

export function SearchScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const { filters, setFilters } = useDealFilters();

  const [text, setText] = useState(filters.q);
  const [recents, setRecents] = useState<string[]>([]);
  const [results, setResults] = useState<Suggestions | null>(null);
  const [loading, setLoading] = useState(false);
  const [unsupported, setUnsupported] = useState(false);
  const [archive, setArchive] = useState<{ q: string; deals: Deal[] } | null>(null);
  const inputRef = useRef<TextInput>(null);
  const ctrlRef = useRef<AbortController | null>(null);

  useEffect(() => {
    loadRecents().then(setRecents);
    const id = setTimeout(() => inputRef.current?.focus(), 120);
    return () => clearTimeout(id);
  }, []);

  const typed = text.trim();

  useEffect(() => {
    ctrlRef.current?.abort();
    if (typed.length < 2 || unsupported) {
      setResults(null);
      setLoading(false);
      return;
    }
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setLoading(true);
    const timer = setTimeout(() => {
      api.deals
        .suggest(typed, 6, filters.all_channels, { signal: ctrl.signal })
        .then((res) => {
          if (ctrlRef.current === ctrl) setResults(res);
        })
        .catch((e) => {
          if (isAbort(e)) return;
          // Older servers have no /suggest; searching still works via "See all".
          if (isNotFound(e)) setUnsupported(true);
          setResults(null);
        })
        .finally(() => {
          if (ctrlRef.current === ctrl) setLoading(false);
        });
    }, 180);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [typed, filters.all_channels, unsupported]);

  useEffect(() => {
    if (typed.length < 2) {
      setArchive(null);
      return;
    }
    const ctrl = new AbortController();
    const timer = setTimeout(() => {
      api.deals
        .list({ q: typed, archive: true, sort: 'relevance', limit: 4, offset: 0 }, { signal: ctrl.signal })
        .then((r) => setArchive({ q: typed, deals: r.results ?? [] }))
        .catch(() => {});
    }, 260);
    return () => {
      clearTimeout(timer);
      ctrl.abort();
    };
  }, [typed]);

  const backToDeals = () => navigation.navigate('Main', { screen: 'Deals' });

  const apply = (next: (f: DealFilters) => DealFilters, recent?: string) => {
    haptic.select();
    if (recent) void pushRecent(recent);
    setFilters(next);
    backToDeals();
  };

  const submit = (q: string) => apply((f) => ({ ...f, q: q.trim() }), q);

  const sections: Section[] = [];
  if (typed.length < 2) {
    const shown = recents.filter((r) => !typed || r.toLowerCase().includes(typed.toLowerCase()));
    if (shown.length) {
      sections.push({
        title: 'Recent searches',
        key: 'recent',
        data: shown.map((r) => ({ kind: 'recent', text: r })),
        action: {
          label: 'Clear',
          onPress: () => {
            void clearRecents();
            setRecents([]);
          },
        },
      });
    }
  } else if (results) {
    if (results.deals.length) {
      sections.push({ title: 'Deals', key: 'deals', data: results.deals.map((d) => ({ kind: 'deal', deal: d })) });
    }
    const facets: [Row[], string, string][] = [
      [
        (results.categories ?? []).map((c) => ({ kind: 'facet', field: 'category', value: c.name, label: c.name, count: c.count })),
        'Categories',
        'cats',
      ],
      [
        (results.brands ?? []).map((b) => ({ kind: 'facet', field: 'brand', value: b.key, label: titleCase(b.key), count: b.count })),
        'Brands',
        'brands',
      ],
      [
        (results.stores ?? []).map((s) => ({
          kind: 'facet',
          field: 'store',
          value: s.key,
          label: storeName({ store: s.key }) || titleCase(s.key),
          count: s.count,
        })),
        'Stores',
        'stores',
      ],
    ];
    facets.forEach(([data, title, key]) => data.length && sections.push({ title, key, data }));
  }
  if (typed.length >= 2 && archive?.q === typed && archive.deals.length) {
    sections.push({
      title: 'From the deal archive',
      key: 'archive',
      data: archive.deals.map((d) => ({ kind: 'deal', deal: d, past: true })),
    });
  }
  if (typed) sections.push({ title: '', key: 'all', data: [{ kind: 'all' }] });

  const renderRow = ({ item }: { item: Row }) => {
    if (item.kind === 'recent') {
      return (
        <RowPress label={`Search ${item.text}`} onPress={() => submit(item.text)}>
          <Icon name="clock" size={18} color={t.c.text3} />
          <Text numberOfLines={1} style={{ flex: 1, color: t.c.text, fontSize: t.f.md }}>
            {item.text}
          </Text>
          <IconButton
            name="arrowRight"
            label={`Put ${item.text} in the search box`}
            size={16}
            color={t.c.text3}
            onPress={() => setText(item.text)}
          />
        </RowPress>
      );
    }
    if (item.kind === 'deal') {
      const d = item.deal;
      return (
        <RowPress
          label={`${item.past ? 'Past deal, ' : ''}${d.title}, open deal`}
          onPress={() => {
            void pushRecent(typed);
            rememberDeal(item.past ? { ...d, status: d.status && d.status !== 'live' ? d.status : 'archived' } : d);
            navigation.navigate('DealDetail', { id: d.id });
          }}
        >
          <DealImage deal={d} style={{ width: 52, height: 52, borderRadius: 10 }} emojiSize={20} />
          <View style={{ flex: 1, gap: 3 }}>
            <Highlight text={d.title} query={typed} numberOfLines={2} style={{ color: t.c.text, fontSize: t.f.sm, fontWeight: '600', lineHeight: 18 }} />
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
              {item.past ? <PastBadge small /> : null}
              <PriceRow deal={d} size="sm" />
            </View>
          </View>
        </RowPress>
      );
    }
    if (item.kind === 'facet') {
      const leading = item.field === 'category' ? categoryIcon(item.value) : null;
      return (
        <RowPress
          label={`${item.label}, ${item.count} deals`}
          onPress={() =>
            apply(
              (f) => ({
                ...f,
                q: typed,
                [item.field]: item.value,
                ...(item.field === 'category' ? { subcategory: '' } : {}),
              }),
              typed,
            )
          }
        >
          {leading ? (
            <Text style={{ width: 22, fontSize: 17, textAlign: 'center' }}>{leading}</Text>
          ) : (
            <Icon name={item.field === 'store' ? 'tag' : 'search'} size={17} color={t.c.text3} />
          )}
          <Text numberOfLines={1} style={{ flex: 1, color: t.c.text, fontSize: t.f.md }}>
            {item.label}
          </Text>
          <View style={{ paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999, backgroundColor: t.c.surface2 }}>
            <Text style={{ color: t.c.text2, fontSize: t.f.xs, fontWeight: '700' }}>{item.count}</Text>
          </View>
        </RowPress>
      );
    }
    return (
      <RowPress label={`See all results for ${typed}`} onPress={() => submit(typed)}>
        <Icon name="search" size={18} color={t.c.accent} />
        <Text numberOfLines={1} style={{ flex: 1, color: t.c.accent, fontSize: t.f.md, fontWeight: '700' }}>
          See all results for “{typed}”
        </Text>
        <Icon name="arrowRight" size={17} color={t.c.accent} />
      </RowPress>
    );
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <View
        style={{
          paddingTop: insets.top + 6,
          paddingBottom: 8,
          paddingHorizontal: 6,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 4,
          borderBottomWidth: 1,
          borderBottomColor: t.c.border,
        }}
      >
        <IconButton name="chevLeft" label="Back" size={24} onPress={() => navigation.goBack()} />
        <View
          style={{
            flex: 1,
            minHeight: 46,
            borderRadius: 999,
            backgroundColor: t.c.surface,
            borderWidth: 1,
            borderColor: t.c.accentLine,
            flexDirection: 'row',
            alignItems: 'center',
            paddingLeft: 14,
            gap: 8,
          }}
        >
          <Icon name="search" size={17} color={t.c.text3} />
          <TextInput
            ref={inputRef}
            value={text}
            onChangeText={setText}
            placeholder="What are you looking for?"
            placeholderTextColor={t.c.text3}
            selectionColor={t.c.accent}
            returnKeyType="search"
            autoCorrect={false}
            autoCapitalize="none"
            accessibilityLabel="Search deals"
            maxFontSizeMultiplier={1.4}
            onSubmitEditing={() => submit(text)}
            style={{ flex: 1, color: t.c.text, fontSize: t.f.base, paddingVertical: 8 }}
          />
          {loading ? <ActivityIndicator size="small" color={t.c.accent} /> : null}
          {text ? (
            <IconButton name="close" label="Clear search" size={16} color={t.c.text2} onPress={() => setText('')} />
          ) : (
            <View style={{ width: 8 }} />
          )}
        </View>
      </View>

      <SectionList<Row, Section>
        sections={sections}
        keyExtractor={(item, i) =>
          item.kind === 'deal' ? `${item.past ? 'a' : 'd'}-${item.deal.id}` : item.kind === 'facet' ? `${item.field}-${item.value}` : item.kind === 'recent' ? `r-${item.text}` : `all-${i}`
        }
        renderItem={renderRow}
        renderSectionHeader={({ section }) =>
          section.title ? (
            <View style={{ flexDirection: 'row', alignItems: 'center', paddingHorizontal: 16, paddingTop: 16, paddingBottom: 6, backgroundColor: t.c.bg }}>
              <Text accessibilityRole="header" style={{ flex: 1, color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.7, textTransform: 'uppercase' }}>
                {section.title}
              </Text>
              {section.action ? (
                <Pressable accessibilityRole="button" onPress={section.action.onPress} hitSlop={12} style={{ minHeight: 32, justifyContent: 'center' }}>
                  <Text style={{ color: t.c.accent, fontWeight: '700', fontSize: t.f.sm }}>{section.action.label}</Text>
                </Pressable>
              ) : null}
            </View>
          ) : (
            <View style={{ height: 8 }} />
          )
        }
        stickySectionHeadersEnabled={false}
        keyboardShouldPersistTaps="handled"
        keyboardDismissMode="on-drag"
        contentContainerStyle={{ paddingBottom: insets.bottom + 24 }}
        ListEmptyComponent={
          <View style={{ alignItems: 'center', padding: 36, gap: 8 }}>
            <Text style={{ fontSize: 34 }}>🔎</Text>
            <Text style={{ color: t.c.text2, fontSize: t.f.md, textAlign: 'center' }}>
              Search in plain words — “women kurta” finds kurti, anarkali, ethnic sets.
            </Text>
          </View>
        }
        ListFooterComponent={
          typed.length >= 2 && results && !results.deals.length && !loading ? (
            <Text style={{ color: t.c.text3, textAlign: 'center', padding: 16, fontSize: t.f.sm }}>
              No instant matches — try “See all results”.
            </Text>
          ) : null
        }
      />
    </View>
  );
}

function RowPress({ label, onPress, children }: { label: string; onPress: () => void; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      android_ripple={{ color: t.c.accentSoft }}
      style={({ pressed }) => ({
        minHeight: 52,
        paddingHorizontal: 16,
        paddingVertical: 8,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        backgroundColor: pressed ? t.c.surface2 : 'transparent',
      })}
    >
      {children}
    </Pressable>
  );
}
