import { useNavigation } from '@react-navigation/native';
import Constants from 'expo-constants';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, RefreshControl, ScrollView, Text, View } from 'react-native';

import { api, errorMessage, type Health } from '../api';
import { Banner, Button, Card, KeyValue, ScreenHeader, copyText, useServerUrl, useToast } from '../components';
import { useTheme } from '../theme';
import type { RootNav } from './types';

export function SettingsScreen() {
  const t = useTheme();
  const navigation = useNavigation<RootNav>();
  const server = useServerUrl();
  const toast = useToast();
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setHealth(await api.system.health());
    } catch (e) {
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const version = Constants.expoConfig?.version ?? '—';
  const build =
    Constants.expoConfig?.android?.versionCode != null ? ` (${Constants.expoConfig.android.versionCode})` : '';

  const rows: [string, string][] = [];
  if (health) {
    const sheets = health.checks.sheets ?? {};
    const ingest = health.checks.ingest ?? {};
    rows.push(
      ['Service', `${health.status} · up ${Math.floor(health.uptime_seconds / 60)} min`],
      ['Database', health.checks.database],
      ['Telegram API', health.checks.telegram_configured ? 'configured' : 'not configured'],
      ['Google Sheets', sheets.configured ? (sheets.connected ? 'connected' : 'configured, not connected') : 'not configured'],
      ['Rows in Sheets', String(sheets.rows_tracked ?? 0)],
      ['Last sheet flush', sheets.last_flush || 'never'],
      ['Ingest cycles', String(ingest.cycles ?? 0)],
      ['Last sync', ingest.last_run_ago_seconds != null ? `${Math.floor(ingest.last_run_ago_seconds / 60)} min ago` : 'not yet'],
      ['Last error', ingest.last_error || sheets.last_error || 'none'],
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Settings" />
      <ScrollView
        contentContainerStyle={{ padding: 14, gap: 18, paddingBottom: 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={async () => {
              setRefreshing(true);
              await load();
              setRefreshing(false);
            }}
            colors={[t.c.accent]}
            tintColor={t.c.accent}
            progressBackgroundColor={t.c.surface}
          />
        }
      >
        <Card style={{ gap: 12 }}>
          <Text style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' }}>Server</Text>
          <Text
            selectable
            onLongPress={() => server && copyText(server).then((ok) => ok && toast('Server address copied.', 'ok', 2000))}
            style={{ color: t.c.text, fontSize: t.f.md, fontFamily: 'monospace' }}
          >
            {server ?? '…'}
          </Text>
          <Button title="Change server" variant="soft" icon="server" onPress={() => navigation.navigate('Setup', { fromSettings: true })} />
        </Card>

        <Card style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Text style={{ flex: 1, color: t.c.text2, fontSize: t.f.md }}>App version</Text>
          <Text selectable style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '600' }}>
            {version}
            {build}
          </Text>
        </Card>

        <View style={{ gap: 10 }}>
          <Text accessibilityRole="header" style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.6, textTransform: 'uppercase' }}>
            System status
          </Text>
          {error ? (
            <>
              <Banner kind="error">{error}</Banner>
              <Button title="Try again" variant="soft" onPress={load} />
            </>
          ) : health ? (
            <KeyValue rows={rows} />
          ) : (
            <ActivityIndicator color={t.c.accent} style={{ padding: 20 }} />
          )}
          <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>Ping endpoint: /api/ping · API docs: /api/docs</Text>
        </View>
      </ScrollView>
    </View>
  );
}
