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
import { COLORS, LIVE_HOST, getBaseUrl, saveBaseUrl } from './src/native/config';
import { setRoutingReady, startNotificationRouting } from './src/native/deepLinks';
import { configureNotificationHandler, pollNotifications } from './src/native/notifications';
import { useQuickActionRouting } from './src/native/quickActions';
import { setPublicMode, startVisitorSession } from './src/native/session';
import { useShareIntentRouting } from './src/native/shareIntent';
import { checkForUpdateOnLaunch, stopImmediateUpdates } from './src/native/updates';
import { AppProviders } from './src/components/AppProviders';
import { TelegramInvite } from './src/components/TelegramInvite';
import { useTheme } from './src/theme';

SplashScreen.preventAutoHideAsync().catch(() => {});
SystemUI.setBackgroundColorAsync(COLORS.bg).catch(() => {});
configureNotificationHandler();
checkForUpdateOnLaunch();

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
  const lastBack = useRef(0);

  const boot = useCallback(async () => {
    // Real users never see a server address: the app always points at the
    // one production DealRadar. Setup only reappears via the hidden gesture
    // in Settings (dev/testing use), which pre-fills a *different* saved URL.
    const server = (await getBaseUrl()) ?? (await saveBaseUrl(LIVE_HOST));
    setPhase({ kind: 'connecting' });
    try {
      // Reachability check only: visitors never sign in — the server reads the
      // channels with its own account, so the app opens straight to the deals.
      await checkAuth(server);
      setPublicMode(true);
      stopImmediateUpdates();
      setPhase({ kind: 'ready', initial: 'Main' });
      startVisitorSession().catch((e) => console.warn('[app] visitor session failed:', e?.message ?? e));
    } catch (e: any) {
      // Logged for you, never rendered: a visitor has no use for a server
      // address or a raw network error, only "it isn't working right now".
      console.warn('[app] boot failed:', server, e?.message ?? e);
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
    if (phase.kind !== 'loading') SplashScreen.hideAsync().catch(() => {});
  }, [phase.kind]);

  useEffect(() => startNotificationRouting(), []);
  useShareIntentRouting();
  useQuickActionRouting();

  // Poll the alert feed every time the app comes to the foreground.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active') pollNotifications().catch(() => {});
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

  let body: React.ReactNode = null;
  if (phase.kind === 'connecting') body = <ConnectingView />;
  else if (phase.kind === 'offline') body = <OfflineView onRetry={boot} />;
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
