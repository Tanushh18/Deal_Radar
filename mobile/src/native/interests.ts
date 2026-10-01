import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'dr-interests';

export const INTEREST_OPTIONS: { key: string; label: string }[] = [
  { key: 'women', label: 'Women' },
  { key: 'men', label: 'Men' },
  { key: 'electronics', label: 'Electronics' },
  { key: 'home', label: 'Home & Kitchen' },
  { key: 'beauty', label: 'Beauty & Grooming' },
  { key: 'kids', label: 'Baby & Kids' },
  { key: 'fashion', label: 'Footwear & Bags' },
];

/** undefined = not asked yet; 'all' = no preference; else "women,electronics". */
let cached: string | undefined;

/** Synchronous read for the API layer — undefined until loadInterests() has resolved. */
export function peekInterests(): string | undefined {
  return cached;
}

/** The value sent as ?interests= (nothing before the visitor has answered). */
export const interestsParam = (): string | undefined => cached || undefined;

export async function loadInterests(): Promise<string | undefined> {
  const v = await AsyncStorage.getItem(KEY).catch(() => null);
  cached = v || undefined;
  return cached;
}

export async function saveInterests(keys: string[]): Promise<void> {
  cached = keys.length ? keys.join(',') : 'all';
  await AsyncStorage.setItem(KEY, cached).catch(() => {});
}
