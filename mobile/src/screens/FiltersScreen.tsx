import { useNavigation } from '@react-navigation/native';
import React, { useEffect, useState } from 'react';
import { Pressable, ScrollView, Switch, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, isAbort, type KeyCount, type SortKey } from '../api';
import {
  Button,
  Chip,
  Field,
  IconButton,
  SelectField,
  activeFilterCount,
  categoryIcon,
  clearedFilters,
  haptic,
  money,
  num,
  plural,
  toQuery,
  useCategories,
  useDealFilters,
  useHideNativeHeader,
  type DealFilters,
} from '../components';
import { useTheme } from '../theme';
import type { RootNav } from './types';

const SORTS: { key: SortKey; label: string }[] = [
  { key: 'newest', label: 'Newest first' },
  { key: 'relevance', label: 'Best match' },
  { key: 'best', label: 'Top deals' },
  { key: 'discount', label: 'Biggest discount' },
  { key: 'price_low', label: 'Price: low to high' },
  { key: 'price_high', label: 'Price: high to low' },
];

const DISCOUNTS = [0, 20, 40, 60, 80];
const PRICE_PRESETS = [500, 1000, 2000, 5000];
const FACET_PREVIEW = 6;

export function FiltersScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const { filters, setFilters } = useDealFilters();
  const categories = useCategories();

  const [draft, setDraft] = useState<DealFilters>(filters);
  const [count, setCount] = useState<number | null>(null);
  const [counting, setCounting] = useState(false);
  const [stores, setStores] = useState<KeyCount[]>([]);
  const [brands, setBrands] = useState<KeyCount[]>([]);
  const [priceText, setPriceText] = useState(filters.max_price ? String(filters.max_price) : '');

  const patch = (p: Partial<DealFilters>) => {
    haptic.select();
    setDraft((d) => ({ ...d, ...p }));
  };

  useEffect(() => {
    const ctrl = new AbortController();
    api.deals
      .facets(draft.all_channels, { signal: ctrl.signal })
      .then((f) => {
        setStores(f.stores ?? []);
        setBrands(f.brands ?? []);
      })
      .catch(() => {});
    return () => ctrl.abort();
  }, [draft.all_channels]);

  const draftKey = JSON.stringify(draft);
  useEffect(() => {
    const ctrl = new AbortController();
    setCounting(true);
    const id = setTimeout(() => {
      api.deals
        .list({ ...toQuery(draft), limit: 1, offset: 0 }, { signal: ctrl.signal })
        .then((r) => setCount(r.total))
        .catch((e) => !isAbort(e) && setCount(null))
        .finally(() => !ctrl.signal.aborted && setCounting(false));
    }, 250);
    return () => {
      clearTimeout(id);
      ctrl.abort();
    };
    // draftKey captures every field of draft.
  }, [draftKey]);

  const commitPrice = (v: string) => {
    const n = Number(v.replace(/[^\d.]/g, ''));
    setDraft((d) => ({ ...d, max_price: v && n > 0 ? n : null }));
  };

  const apply = () => {
    haptic.light();
    setFilters(draft);
    navigation.goBack();
  };

  const category = categories.find((c) => c.name === draft.category);
  const n = activeFilterCount(draft);

  return (
    <View style={{ flex: 1, backgroundColor: t.c.surface }}>
      <View
        style={{
          paddingTop: insets.top + 6,
          paddingHorizontal: 8,
          paddingBottom: 6,
          flexDirection: 'row',
          alignItems: 'center',
          borderBottomWidth: 1,
          borderBottomColor: t.c.border,
        }}
      >
        <IconButton name="close" label="Close filters" onPress={() => navigation.goBack()} />
        <Text accessibilityRole="header" style={{ flex: 1, color: t.c.text, fontSize: t.f.lg, fontWeight: '700', marginLeft: 4 }}>
          Filters{n ? ` · ${n}` : ''}
        </Text>
        <Button
          title="Clear all"
          variant="ghost"
          size="sm"
          disabled={!n}
          onPress={() => {
            haptic.select();
            setPriceText('');
            setDraft((d) => clearedFilters(d));
          }}
        />
      </View>

      <ScrollView contentContainerStyle={{ padding: 16, gap: 22, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
        <Block label="Sort by">
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {SORTS.map((s) => (
              <Chip key={s.key} label={s.label} active={draft.sort === s.key} onPress={() => patch({ sort: s.key })} />
            ))}
          </View>
        </Block>

        <SelectField
          label="Category"
          value={draft.category}
          onChange={(v) => patch({ category: v, subcategory: '' })}
          placeholder="All deals"
          options={[
            { value: '', label: 'All deals', leading: '✨' },
            ...categories.map((c) => ({ value: c.name, label: c.name, leading: categoryIcon(c.name) })),
          ]}
        />

        {category ? (
          <SelectField
            label="Subcategory"
            value={draft.subcategory}
            onChange={(v) => patch({ subcategory: v })}
            placeholder="All"
            options={[{ value: '', label: 'All' }, ...category.subcategories.map((s) => ({ value: s, label: s }))]}
          />
        ) : null}

        <Block label="Max price (₹)">
          <Field
            value={priceText}
            onChangeText={(v) => {
              setPriceText(v);
              commitPrice(v);
            }}
            placeholder="Any"
            keyboardType="number-pad"
            accessibilityLabel="Maximum price in rupees"
          />
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {PRICE_PRESETS.map((p) => (
              <Chip
                key={p}
                label={`Under ${money(p)}`}
                active={draft.max_price === p}
                onPress={() => {
                  const next = draft.max_price === p ? null : p;
                  setPriceText(next ? String(next) : '');
                  patch({ max_price: next });
                }}
              />
            ))}
          </View>
        </Block>

        <Block label={`Minimum discount · ${draft.min_discount ? `${draft.min_discount}%+` : 'any'}`}>
          <View style={{ flexDirection: 'row', gap: 6 }}>
            {DISCOUNTS.map((d) => {
              const on = draft.min_discount === d;
              return (
                <Pressable
                  key={d}
                  accessibilityRole="radio"
                  accessibilityState={{ selected: on }}
                  accessibilityLabel={d ? `${d} percent or more` : 'Any discount'}
                  onPress={() => patch({ min_discount: d })}
                  style={{
                    flex: 1,
                    minHeight: 44,
                    borderRadius: t.r.sm,
                    alignItems: 'center',
                    justifyContent: 'center',
                    borderWidth: 1,
                    borderColor: on ? t.c.accent : t.c.border,
                    backgroundColor: on ? t.c.accent : t.c.surface2,
                  }}
                >
                  <Text maxFontSizeMultiplier={1.2} style={{ color: on ? t.c.accentText : t.c.text2, fontWeight: '700', fontSize: t.f.sm }}>
                    {d ? `${d}%+` : 'Any'}
                  </Text>
                </Pressable>
              );
            })}
          </View>
        </Block>

        <FacetBlock label="Store" items={stores} value={draft.store} onPick={(v) => patch({ store: draft.store === v ? '' : v })} />
        <FacetBlock label="Brand" items={brands} value={draft.brand} onPick={(v) => patch({ brand: draft.brand === v ? '' : v })} />

        <View style={{ gap: 4 }}>
          <ToggleRow label="Only all-time lows" value={draft.only_lowest} onChange={(v) => patch({ only_lowest: v })} />
        </View>
      </ScrollView>

      <View
        style={{
          paddingHorizontal: 16,
          paddingTop: 12,
          paddingBottom: insets.bottom + 12,
          borderTopWidth: 1,
          borderTopColor: t.c.border,
          backgroundColor: t.c.surface,
          gap: 6,
        }}
      >
        <Text style={{ color: t.c.text3, fontSize: t.f.xs, textAlign: 'center' }} accessibilityLiveRegion="polite">
          {count == null ? ' ' : count ? `${num(count)} ${plural(count, 'deal')} match` : 'No deals match'}
        </Text>
        <Button
          title={count ? `Show ${num(count)} ${plural(count, 'deal')}` : 'Show deals'}
          loading={counting && count == null}
          onPress={apply}
          block
        />
      </View>
    </View>
  );
}

function Block({ label, children }: { label: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={{ gap: 10 }}>
      <Text accessibilityRole="header" style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' }}>
        {label}
      </Text>
      {children}
    </View>
  );
}

function FacetBlock({
  label,
  items,
  value,
  onPick,
}: {
  label: string;
  items: KeyCount[];
  value: string;
  onPick: (v: string) => void;
}) {
  const t = useTheme();
  const [expanded, setExpanded] = useState(false);
  const selectedIdx = items.findIndex((i) => i.key === value);
  const cut = expanded ? items.length : Math.max(FACET_PREVIEW, selectedIdx + 1);
  return (
    <Block label={label}>
      {items.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
          {items.slice(0, cut).map((i) => (
            <Chip key={i.key} label={i.key} count={i.count} active={value === i.key} onPress={() => onPick(i.key)} />
          ))}
          {items.length > cut || expanded ? (
            <Pressable
              accessibilityRole="button"
              onPress={() => setExpanded(!expanded)}
              style={{ minHeight: 36, justifyContent: 'center', paddingHorizontal: 10 }}
            >
              <Text style={{ color: t.c.accent, fontWeight: '700', fontSize: t.f.sm }}>
                {expanded ? 'Show fewer' : `Show all ${items.length}`}
              </Text>
            </Pressable>
          ) : null}
        </View>
      ) : (
        <Text style={{ color: t.c.text3, fontSize: t.f.sm }}>No data yet</Text>
      )}
    </Block>
  );
}

function ToggleRow({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  const t = useTheme();
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityState={{ checked: value }}
      accessibilityLabel={label}
      onPress={() => onChange(!value)}
      style={{ minHeight: 52, flexDirection: 'row', alignItems: 'center', gap: 12 }}
    >
      <Text style={{ flex: 1, color: t.c.text, fontSize: t.f.md, fontWeight: '500' }}>{label}</Text>
      <Switch
        value={value}
        onValueChange={onChange}
        trackColor={{ false: t.c.surface3, true: t.c.accent }}
        thumbColor="#ffffff"
        importantForAccessibility="no"
      />
    </Pressable>
  );
}
