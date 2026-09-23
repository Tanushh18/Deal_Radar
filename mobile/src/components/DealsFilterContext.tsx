import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../api';
import type { Category, DealQuery, SortKey } from '../api/types';
import { money } from './format';

export type DealFilters = {
  q: string;
  category: string;
  subcategory: string;
  store: string;
  brand: string;
  min_price: number | null;
  max_price: number | null;
  min_discount: number;
  has_coupon: boolean;
  only_lowest: boolean;
  all_channels: boolean;
  sort: SortKey;
};

export const DEFAULT_FILTERS: DealFilters = {
  q: '',
  category: '',
  subcategory: '',
  store: '',
  brand: '',
  min_price: null,
  max_price: null,
  min_discount: 0,
  has_coupon: false,
  only_lowest: false,
  all_channels: false,
  sort: 'newest',
};

export type FilterKey = Exclude<keyof DealFilters, 'q' | 'sort'>;

export function activeFilterCount(f: DealFilters): number {
  return [
    f.category,
    f.subcategory,
    f.store,
    f.brand,
    f.min_price ? 1 : 0,
    f.max_price ? 1 : 0,
    f.min_discount ? 1 : 0,
    f.has_coupon ? 1 : 0,
    f.only_lowest ? 1 : 0,
    f.all_channels ? 1 : 0,
  ].filter(Boolean).length;
}

export const isBrowseMode = (f: DealFilters) => !f.q && activeFilterCount(f) === 0;

export function activeFilterChips(f: DealFilters): { key: FilterKey; label: string }[] {
  const chips: { key: FilterKey; label: string }[] = [];
  if (f.category) chips.push({ key: 'category', label: f.category });
  if (f.subcategory) chips.push({ key: 'subcategory', label: f.subcategory });
  if (f.store) chips.push({ key: 'store', label: f.store });
  if (f.brand) chips.push({ key: 'brand', label: f.brand });
  if (f.min_price) chips.push({ key: 'min_price', label: `Over ${money(f.min_price)}` });
  if (f.max_price) chips.push({ key: 'max_price', label: `Under ${money(f.max_price)}` });
  if (f.min_discount) chips.push({ key: 'min_discount', label: `${f.min_discount}%+ off` });
  if (f.has_coupon) chips.push({ key: 'has_coupon', label: 'Has coupon' });
  if (f.only_lowest) chips.push({ key: 'only_lowest', label: 'All-time lows' });
  if (f.all_channels) chips.push({ key: 'all_channels', label: 'All channels' });
  return chips;
}

export function withoutFilter(f: DealFilters, key: FilterKey): DealFilters {
  if (key === 'category') return { ...f, category: '', subcategory: '' };
  return { ...f, [key]: DEFAULT_FILTERS[key] };
}

export function clearedFilters(f: DealFilters): DealFilters {
  return { ...DEFAULT_FILTERS, q: f.q, sort: f.sort };
}

export const toQuery = (f: DealFilters): DealQuery => ({ ...f });

type Ctx = {
  filters: DealFilters;
  setFilters: (next: DealFilters | ((prev: DealFilters) => DealFilters)) => void;
  patchFilters: (patch: Partial<DealFilters>) => void;
  categories: Category[];
  loadCategories: () => void;
  /** Bumped whenever something (a sync, a channel change) should make the feed reload. */
  refreshTick: number;
  requestRefresh: () => void;
};

const FilterContext = createContext<Ctx | null>(null);

export function DealsFilterProvider({ children }: { children: React.ReactNode }) {
  const [filters, setFilters] = useState<DealFilters>(DEFAULT_FILTERS);
  const [categories, setCategories] = useState<Category[]>([]);
  const [refreshTick, setTick] = useState(0);
  const loading = useRef(false);

  const loadCategories = useCallback(() => {
    if (loading.current) return;
    loading.current = true;
    api.deals
      .categories()
      .then((res) => setCategories(res.categories ?? []))
      .catch(() => {})
      .finally(() => {
        loading.current = false;
      });
  }, []);

  const patchFilters = useCallback((patch: Partial<DealFilters>) => {
    setFilters((prev) => ({ ...prev, ...patch }));
  }, []);

  const requestRefresh = useCallback(() => setTick((n) => n + 1), []);

  const value = useMemo(
    () => ({ filters, setFilters, patchFilters, categories, loadCategories, refreshTick, requestRefresh }),
    [filters, patchFilters, categories, loadCategories, refreshTick, requestRefresh],
  );
  return <FilterContext.Provider value={value}>{children}</FilterContext.Provider>;
}

export function useDealFilters(): Ctx {
  const ctx = useContext(FilterContext);
  if (!ctx) throw new Error('useDealFilters must be used inside <DealsFilterProvider>');
  return ctx;
}

/** Loads the taxonomy on first use; every consumer shares the one copy. */
export function useCategories(): Category[] {
  const { categories, loadCategories } = useDealFilters();
  useEffect(() => {
    if (!categories.length) loadCategories();
  }, [categories.length, loadCategories]);
  return categories;
}

/* ---------------- recent searches ---------------- */

const RECENTS_KEY = 'dr.recentSearches';

export async function loadRecents(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(RECENTS_KEY);
    const list = raw ? (JSON.parse(raw) as unknown) : [];
    return Array.isArray(list) ? list.filter((x): x is string => typeof x === 'string').slice(0, 8) : [];
  } catch {
    return [];
  }
}

export async function pushRecent(q: string): Promise<void> {
  const term = q.trim();
  if (!term) return;
  const list = [term, ...(await loadRecents()).filter((r) => r.toLowerCase() !== term.toLowerCase())].slice(0, 8);
  await AsyncStorage.setItem(RECENTS_KEY, JSON.stringify(list)).catch(() => {});
}

export async function clearRecents(): Promise<void> {
  await AsyncStorage.removeItem(RECENTS_KEY).catch(() => {});
}
