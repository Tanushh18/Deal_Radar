import { useNavigation } from '@react-navigation/native';
import Constants from 'expo-constants';
import * as Updates from 'expo-updates';
import React, { useRef, useState } from 'react';
import { Linking, ScrollView, View } from 'react-native';
import { Text } from '../components/Text';

import { Card, Chip, Divider, Row, ScreenHeader, haptic, useDealFilters, useToast } from '../components';
import { INTEREST_OPTIONS, saveInterests, selectedInterests } from '../native/interests';
import { useTheme } from '../theme';
import type { RootNav } from './types';

/** Server address / system status are admin concerns — managed at /admin on
 * the website, never in the visitor app. Tapping the version 7x (the
 * standard "developer options" gesture) opens Setup for local/dev testing
 * only; nobody stumbles into it by accident. */
const DEV_GESTURE_TAPS = 7;

export function SettingsScreen() {
  const t = useTheme();
  const navigation = useNavigation<RootNav>();
  const toast = useToast();
  const { requestRefresh } = useDealFilters();
  const [picked, setPicked] = useState<string[]>(selectedInterests);
  const [checking, setChecking] = useState(false);
  const taps = useRef(0);
  const tapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const version = Constants.expoConfig?.version ?? '—';
  const build =
    Constants.expoConfig?.android?.versionCode != null ? ` (${Constants.expoConfig.android.versionCode})` : '';

  // Same choice as the first-open question; saved at once and the feed reloads with it.
  const setInterests = async (keys: string[]) => {
    haptic.select();
    setPicked(keys);
    await saveInterests(keys);
    requestRefresh();
  };
  const toggle = (key: string) =>
    setInterests(picked.includes(key) ? picked.filter((k) => k !== key) : [...picked, key]);

  const checkUpdate = async () => {
    if (__DEV__ || !Updates.isEnabled) {
      toast('Updates are only available in the installed app.', 'info');
      return;
    }
    setChecking(true);
    try {
      const check = await Updates.checkForUpdateAsync();
      if (!check.isAvailable) {
        toast('You’re on the latest version.', 'ok');
        return;
      }
      await Updates.fetchUpdateAsync();
      await Updates.reloadAsync();
    } catch {
      toast('Couldn’t check for updates — try again.', 'err');
    } finally {
      setChecking(false);
    }
  };

  const onVersionPress = () => {
    taps.current += 1;
    if (tapTimer.current) clearTimeout(tapTimer.current);
    tapTimer.current = setTimeout(() => {
      taps.current = 0;
    }, 1500);
    if (taps.current >= DEV_GESTURE_TAPS) {
      taps.current = 0;
      navigation.navigate('Setup', { fromSettings: true });
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Settings" />
      <ScrollView contentContainerStyle={{ padding: 14, gap: 18, paddingBottom: 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}>
        <Card style={{ gap: 10 }}>
          <Text style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '700' }}>Deals shown first</Text>
          <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>
            Choose what the Deals feed leads with when it opens. Nothing is hidden — other deals follow below.
          </Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
            <Chip label="Everything" active={!picked.length} onPress={() => setInterests([])} />
            {INTEREST_OPTIONS.map((o) => (
              <Chip key={o.key} label={o.label} active={picked.includes(o.key)} onPress={() => toggle(o.key)} />
            ))}
          </View>
        </Card>
        <Card style={{ padding: 0, overflow: 'hidden' }}>
          <Row
            icon="bell"
            title="Notifications"
            sub="Price drops, follows, digest & system permission"
            onPress={() => navigation.navigate('Main', { screen: 'Account' } as never)}
          />
          <Divider />
          <Row icon="settings" title="Phone notification settings" sub="Sound, channels, permission" onPress={() => Linking.openSettings().catch(() => {})} />
          <Divider />
          <Row icon="sync" title="Check for app update" sub={checking ? 'Checking…' : 'Get the latest version over the air'} onPress={checking ? undefined : checkUpdate} />
        </Card>
        <Card style={{ flexDirection: 'row', alignItems: 'center' }}>
          <Text style={{ flex: 1, color: t.c.text2, fontSize: t.f.md }}>App version</Text>
          <Text
            selectable
            onPress={onVersionPress}
            style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '600' }}
          >
            {version}
            {build}
          </Text>
        </Card>
        <Text style={{ color: t.c.text3, fontSize: t.f.xs, textAlign: 'center' }}>
          DealRadar checks prices across Indian stores around the clock and surfaces the deals that are actually worth it.
        </Text>
      </ScrollView>
    </View>
  );
}
