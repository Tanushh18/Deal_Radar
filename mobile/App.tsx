import { useCallback, useEffect, useRef, useState } from 'react';
import { AppState, BackHandler, Platform, ToastAndroid, View } from 'react-native';
import { DarkTheme, DefaultTheme, NavigationContainer, type Theme as NavTheme } from '@react-navigation/native';
import { StatusBar } from 'expo-status-bar';
import * as SplashScreen from 'expo-splash-screen';
import * as SystemUI from 'expo-system-ui';
import { GestureHandlerRootView } from 'react-native-gesture-handler';
import { SafeAreaProvider } from 'react-native-safe-area-context';

import { ConnectingView, OfflineView } from './src/navigation/BootScreens';
import RootNavigator from './src/navigation/RootNavigator';
import { navigationRef, type RootStackParamList } from './src/navigation/types';
import { COLORS, FALLBACK_HOSTS, getBaseUrl, rememberWorkingHost } from './src/native/config';
import { loadCachedHosts, refreshHosts } from './src/native/registry';
import { setRoutingReady, startNotificationRouting } from './src/native/deepLinks';
import { configureNotificationHandler, pollNotifications } from './src/native/notifications';
import { recordOpen } from './src/native/smartNotify';
import { useQuickActionRouting } from './src/native/quickActions';
import { setPublicMode, startVisitorSession } from './src/native/session';
import { useShareIntentRouting } from './src/native/shareIntent';
import { checkForUpdateOnLaunch, stopImmediateUpdates } from './src/native/updates';
import { AppProviders } from './src/components/AppProviders';
import { TelegramInvite } from './src/components/TelegramInvite';
import { InterestsPicker } from './src/components/InterestsPicker';
import { loadInterests } from './src/native/interests';
import { loadAppFonts, useTheme } from './src/theme';

SplashScreen.preventAutoHideAsync().catch(() => {});
SystemUI.setBackgroundColorAsync(COLORS.bg).catch(() => {});
configureNotificationHandler();
checkForUpdateOnLaunch();
// Starts now so the fonts are usually ready before the first screen paints.
const fontsLoaded = loadAppFonts();

function useNavTheme(): NavTheme {
  const t = useTheme();
  const base = t.dark ? DarkTheme : DefaultTheme;
  return {
    ...base,
    colors: {
      ...base.colors,
      primary: t.c.accent,
      background: t.c.bg,
      card: t.c.bg,
      text: t.c.text,
      border: t.c.border,
      notification: t.c.hot,
    },
  };
}

type Phase =
  | { kind: 'connecting' }
  | { kind: 'offline' }
  | { kind: 'ready'; initial: keyof RootStackParamList }
  | { kind: 'loading' };

async function checkAuth(server: string): Promise<{ authenticated: boolean; user?: any }> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 70_000);
  try {
    const res = await fetch(`${server}/api/auth/me`, {
      credentials: 'include',
      headers: { Accept: 'application/json' },
      signal: controller.signal,
    });
    if (!res.ok) throw new Error(`The server answered HTTP ${res.status}.`);
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

export default function App() {
  return (
    <GestureHandlerRootView style={{ flex: 1, backgroundColor: COLORS.bg }}>
      <SafeAreaProvider style={{ backgroundColor: COLORS.bg }}>
        <AppProviders>
          <Root />
        </AppProviders>
      </SafeAreaProvider>
    </GestureHandlerRootView>
  );
}

function Root() {
  const t = useTheme();
  const navTheme = useNavTheme();
  const [phase, setPhase] = useState<Phase>({ kind: 'loading' });
  const [navKey, setNavKey] = useState(0);
  const [fontsSettled, setFontsSettled] = useState(false);
  useEffect(() => {
    fontsLoaded.then(() => setFontsSettled(true));
  }, []);
  const lastBack = useRef(0);

  const boot = useCallback(async () => {
    // Real users never see a server address: the app points at the DealRadar
    // hosts listed in the Stashr server registry (cached on the device, with
    // LIVE_HOST as the built-in fallback). Setup only reappears via the hidden
    // gesture in Settings (dev/testing use), which saves a *different* URL.
    const explicit = await getBaseUrl();
    await loadCachedHosts();
    setPhase({ kind: 'connecting' });

    // Reachability check only: visitors never sign in — the server reads the
    // channels with its own account, so the app opens straight to the deals.
    // Tries each host in turn; if none answer, refreshes the registry once
    // (the backend may have moved) and tries any new hosts.
    const tried = new Set<string>();
    const connect = async (hosts: string[]): Promise<boolean> => {
      for (const host of hosts) {
        if (tried.has(host)) continue;
        tried.add(host);
        try {
          await checkAuth(host);
          if (!explicit) rememberWorkingHost(host);
          return true;
        } catch (e: any) {
          // Logged for you, never rendered: a visitor has no use for a server
          // address or a raw network error, only "it isn't working right now".
          console.warn('[app] boot failed:', host, e?.message ?? e);
        }
      }
      return false;
    };

    let connected = await connect(explicit ? [explicit] : [...FALLBACK_HOSTS]);
    if (!connected && !explicit) {
      const fresh = await refreshHosts();
      if (fresh) connected = await connect(fresh);
    } else if (connected && !explicit) {
      refreshHosts().catch(() => {}); // pick up changes for next time, without waiting
    }

    if (connected) {
      setPublicMode(true);
      stopImmediateUpdates();
      setPhase({ kind: 'ready', initial: 'Main' });
      startVisitorSession().catch((e) => console.warn('[app] visitor session failed:', e?.message ?? e));
    } else {
      setPhase({ kind: 'offline' });
    }
    setNavKey((k) => k + 1);
  }, []);

  useEffect(() => {
    boot();
  }, [boot]);

  useEffect(() => {
    SystemUI.setBackgroundColorAsync(t.c.bg).catch(() => {});
  }, [t.c.bg]);

  useEffect(() => {
    if (phase.kind !== 'loading' && fontsSettled) SplashScreen.hideAsync().catch(() => {});
  }, [phase.kind, fontsSettled]);

  useEffect(() => startNotificationRouting(), []);
  useShareIntentRouting();
  useQuickActionRouting();

  // Poll the alert feed every time the app comes to the foreground, and note the
  // open so the phone learns when this person is usually around (smartNotify.ts).
  useEffect(() => {
    void recordOpen();
    const sub = AppState.addEventListener('change', (s) => {
      if (s !== 'active') return;
      void recordOpen();
      pollNotifications().catch(() => {});
    });
    return () => sub.remove();
  }, []);

  // Back at the root of the app: "press back again to exit" instead of an abrupt close.
  useEffect(() => {
    if (Platform.OS !== 'android') return;
    const sub = BackHandler.addEventListener('hardwareBackPress', () => {
      if (navigationRef.isReady() && navigationRef.canGoBack()) return false;
      const now = Date.now();
      if (now - lastBack.current < 2000) return false; // let the OS close the app
      lastBack.current = now;
      ToastAndroid.show('Press back again to exit', ToastAndroid.SHORT);
      return true;
    });
    return () => sub.remove();
  }, []);

  // undefined = still reading the device; null = never asked → show the picker before any deals load.
  const [interests, setInterests] = useState<string | null | undefined>(undefined);
  useEffect(() => {
    loadInterests().then((v) => setInterests(v ?? null));
  }, []);

  let body: React.ReactNode = null;
  // Nothing renders text until the fonts have loaded (or definitely failed), so no screen mixes typefaces.
  if (!fontsSettled) body = null;
  else if (phase.kind === 'connecting') body = <ConnectingView />;
  else if (phase.kind === 'offline') body = <OfflineView onRetry={boot} />;
  else if (phase.kind === 'ready' && interests === undefined) body = null;
  else if (phase.kind === 'ready' && interests === null)
    body = <InterestsPicker onDone={() => setInterests('chosen')} />;
  else if (phase.kind === 'ready')
    body = (
      <NavigationContainer
        key={navKey}
        ref={navigationRef}
        theme={navTheme}
        onReady={() => setRoutingReady(phase.initial === 'Main')}
      >
        <RootNavigator initialRouteName={phase.initial} onServerSaved={() => boot()} />
        <TelegramInvite />
      </NavigationContainer>
    );

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <StatusBar style={t.dark ? 'light' : 'dark'} />
      {body}
    </View>
  );
}
