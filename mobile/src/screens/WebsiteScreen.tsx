import { useFocusEffect, useRoute } from '@react-navigation/native';
import React, { useCallback, useRef, useState } from 'react';
import { ActivityIndicator, BackHandler, View } from 'react-native';
import { WebView } from 'react-native-webview';

import { isOwnHost, joinUrl } from '../native/config';
import { EmptyState, IconButton, ScreenHeader, openExternal, useServerUrl } from '../components';
import { useTheme } from '../theme';
import type { RootRoute } from './types';

export function WebsiteScreen() {
  const t = useTheme();
  const { params } = useRoute<RootRoute<'Website'>>();
  const server = useServerUrl();
  const ref = useRef<WebView>(null);
  const [canGoBack, setCanGoBack] = useState(false);
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState<string | null>(null);
  const [title, setTitle] = useState('DealRadar');
  const [key, setKey] = useState(0);

  // Android back walks the page history first, then leaves the screen.
  useFocusEffect(
    useCallback(() => {
      const sub = BackHandler.addEventListener('hardwareBackPress', () => {
        if (canGoBack) {
          ref.current?.goBack();
          return true;
        }
        return false;
      });
      return () => sub.remove();
    }, [canGoBack]),
  );

  const uri = server ? joinUrl(server, params?.path ?? '/') : null;

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader
        title={title}
        right={
          <IconButton
            name="sync"
            label="Reload page"
            onPress={() => {
              setFailed(null);
              setKey((k) => k + 1);
            }}
          />
        }
      />
      {failed ? (
        <EmptyState
          emoji="⚠️"
          title="Couldn’t load the website"
          message={failed}
          actions={[
            {
              title: 'Try again',
              variant: 'primary',
              onPress: () => {
                setFailed(null);
                setKey((k) => k + 1);
              },
            },
          ]}
        />
      ) : uri ? (
        <View style={{ flex: 1 }}>
          <WebView
            key={key}
            ref={ref}
            source={{ uri }}
            sharedCookiesEnabled
            thirdPartyCookiesEnabled
            pullToRefreshEnabled
            forceDarkOn={t.dark}
            style={{ flex: 1, backgroundColor: t.c.bg }}
            onNavigationStateChange={(s) => {
              setCanGoBack(s.canGoBack);
              if (s.title && !/^https?:/.test(s.title)) setTitle(s.title.split(' — ')[0]);
            }}
            onLoadStart={() => setLoading(true)}
            onLoadEnd={() => setLoading(false)}
            onError={(e) => setFailed(e.nativeEvent.description || 'The page failed to load.')}
            onShouldStartLoadWithRequest={(req) => {
              if (!server || isOwnHost(server, req.url) || req.url.startsWith('about:') || req.url.startsWith('data:')) {
                return true;
              }
              // Store links belong in the shopping app / browser, not inside this WebView.
              openExternal(req.url);
              return false;
            }}
          />
          {loading ? (
            <View pointerEvents="none" style={{ position: 'absolute', top: 16, left: 0, right: 0, alignItems: 'center' }}>
              <ActivityIndicator color={t.c.accent} />
            </View>
          ) : null}
        </View>
      ) : (
        <ActivityIndicator color={t.c.accent} style={{ marginTop: 40 }} />
      )}
    </View>
  );
}
