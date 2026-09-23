import { useNavigation } from '@react-navigation/native';
import Constants from 'expo-constants';
import React, { useRef } from 'react';
import { ScrollView, Text, View } from 'react-native';

import { Card, ScreenHeader } from '../components';
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
  const taps = useRef(0);
  const tapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const version = Constants.expoConfig?.version ?? '—';
  const build =
    Constants.expoConfig?.android?.versionCode != null ? ` (${Constants.expoConfig.android.versionCode})` : '';

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
      <ScreenHeader title="About" />
      <ScrollView contentContainerStyle={{ padding: 14, gap: 18, paddingBottom: 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}>
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
          DealRadar aggregates deals from Telegram channels into one searchable catalog.
        </Text>
      </ScrollView>
    </View>
  );
}
