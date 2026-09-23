import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Appearance, StyleSheet, useColorScheme } from 'react-native';

import { darkTheme, lightTheme, type Theme } from './tokens';

export type ThemePreference = 'system' | 'light' | 'dark';

const STORAGE_KEY = 'dr.theme';

type ThemeContextValue = {
  theme: Theme;
  preference: ThemePreference;
  setPreference: (p: ThemePreference) => void;
  ready: boolean;
};

const ThemeContext = createContext<ThemeContextValue>({
  theme: darkTheme,
  preference: 'system',
  setPreference: () => {},
  ready: false,
});

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const system = useColorScheme();
  const [preference, setPref] = useState<ThemePreference>('system');
  const [ready, setReady] = useState(false);

  useEffect(() => {
    let alive = true;
    AsyncStorage.getItem(STORAGE_KEY)
      .then((v) => {
        if (alive && (v === 'light' || v === 'dark' || v === 'system')) setPref(v);
      })
      .catch(() => {})
      .finally(() => alive && setReady(true));
    return () => {
      alive = false;
    };
  }, []);

  // Overriding Appearance keeps native chrome (Alert dialogs, pickers) in the chosen theme.
  useEffect(() => {
    try {
      Appearance.setColorScheme(preference === 'system' ? 'unspecified' : preference);
    } catch {
      /* older runtimes */
    }
  }, [preference]);

  const setPreference = useCallback((p: ThemePreference) => {
    setPref(p);
    AsyncStorage.setItem(STORAGE_KEY, p).catch(() => {});
  }, []);

  const resolved = preference === 'system' ? (system === 'light' ? 'light' : 'dark') : preference;
  const theme = resolved === 'light' ? lightTheme : darkTheme;

  const value = useMemo(
    () => ({ theme, preference, setPreference, ready }),
    [theme, preference, setPreference, ready],
  );
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): Theme {
  return useContext(ThemeContext).theme;
}

export function useThemePreference() {
  const { preference, setPreference, ready } = useContext(ThemeContext);
  return { preference, setPreference, ready };
}

/** Theme-aware StyleSheet factory; styles are built once per theme object. */
export function makeStyles<T extends StyleSheet.NamedStyles<T>>(factory: (t: Theme) => T) {
  const cache = new WeakMap<Theme, T>();
  return function useStyles(): T {
    const theme = useTheme();
    let styles = cache.get(theme);
    if (!styles) {
      styles = StyleSheet.create(factory(theme));
      cache.set(theme, styles);
    }
    return styles;
  };
}
