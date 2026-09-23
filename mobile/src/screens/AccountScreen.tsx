import { useFocusEffect, useNavigation } from '@react-navigation/native';
import * as Notifications from 'expo-notifications';
import React, { useCallback, useState } from 'react';
import { Alert, Linking, ScrollView, Text, View } from 'react-native';

import { api, errorMessage, isNotFound, type User } from '../api';
import {
  Button,
  Card,
  Divider,
  Row,
  ScreenHeader,
  Segmented,
  haptic,
  useToast,
} from '../components';
import { onSignedOut } from '../native/session';
import { isPublicMode } from '../native/session';
import { useTheme, useThemePreference, type ThemePreference } from '../theme';
import type { TabNav } from './types';

type NotifState = 'granted' | 'denied' | 'blocked' | 'unknown';

export function AccountScreen() {
  const t = useTheme();
  const navigation = useNavigation<TabNav<'Account'>>();
  const toast = useToast();
  const { preference, setPreference } = useThemePreference();

  const [user, setUser] = useState<User | null>(null);
  const [tracked, setTracked] = useState<number | null>(null);
  const [notif, setNotif] = useState<NotifState>('unknown');
  const [testing, setTesting] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

  const refreshPermission = useCallback(async () => {
    try {
      const p = await Notifications.getPermissionsAsync();
      setNotif(p.granted ? 'granted' : p.canAskAgain ? 'denied' : 'blocked');
    } catch {
      setNotif('unknown');
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      api.auth
        .me()
        .then((me) => {
          if (me.authenticated && !isPublicMode()) {
            setUser(me.user);
            setTracked(me.tracked_channels);
          }
        })
        .catch(() => {});
      refreshPermission();
    }, [refreshPermission]),
  );

  const enableNotifications = async () => {
    if (notif === 'blocked') {
      Linking.openSettings().catch(() => {});
      return;
    }
    try {
      await Notifications.requestPermissionsAsync();
    } catch {
      /* fall through to the refreshed status */
    }
    refreshPermission();
  };

  const sendTest = async () => {
    setTesting(true);
    try {
      await api.notifications.test();
      haptic.success();
      toast('Test notification sent.', 'ok');
    } catch (e) {
      toast(isNotFound(e) ? 'This server doesn’t support app notifications yet.' : errorMessage(e), 'err');
    } finally {
      setTesting(false);
    }
  };

  const signOut = () => {
    Alert.alert('Sign out?', 'You’ll need a new Telegram login code to sign back in.', [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Sign out',
        style: 'destructive',
        onPress: async () => {
          setSigningOut(true);
          // onSignedOut must run first: unregistering the push token needs the
          // session cookie. It also resets navigation to Login.
          try {
            await onSignedOut();
          } finally {
            await api.auth.logout().catch(() => {});
          }
        },
      },
    ]);
  };

  const name = user?.first_name || 'Visitor';
  const initial = (user?.first_name || user?.username || 'U').slice(0, 1).toUpperCase();
  const notifLabel = {
    granted: 'On — alerts arrive on this phone',
    denied: 'Off — tap to allow notifications',
    blocked: 'Blocked in system settings',
    unknown: 'Checking…',
  }[notif];

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Account" large onBack={null} />
      <ScrollView contentContainerStyle={{ padding: 14, gap: 16, paddingBottom: 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}>
        <Card style={{ flexDirection: 'row', alignItems: 'center', gap: 14 }}>
          <View
            style={{ width: 54, height: 54, borderRadius: 27, backgroundColor: t.c.accent, alignItems: 'center', justifyContent: 'center' }}
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
          >
            <Text maxFontSizeMultiplier={1.1} style={{ color: t.c.accentText, fontSize: 22, fontWeight: '800' }}>
              {initial}
            </Text>
          </View>
          <View style={{ flex: 1, gap: 2 }}>
            <Text style={{ color: t.c.text, fontSize: t.f.lg, fontWeight: '700' }}>{name}</Text>
            {user?.username ? <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>@{user.username}</Text> : null}
            {tracked != null ? (
              <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>
                Tracking {tracked} {tracked === 1 ? 'channel' : 'channels'}
              </Text>
            ) : null}
          </View>
        </Card>

        <Group title="Notifications">
          <Row
            icon="bell"
            title="App notifications"
            sub={notifLabel}
            onPress={notif === 'granted' ? undefined : enableNotifications}
            right={
              notif === 'granted' ? (
                <View style={{ paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, backgroundColor: t.c.goodSoft }}>
                  <Text style={{ color: t.c.good, fontSize: 11, fontWeight: '800' }}>ON</Text>
                </View>
              ) : undefined
            }
          />
          <View style={{ padding: 14, paddingTop: 4 }}>
            <Button title="Send test notification" icon="bell" variant="soft" loading={testing} onPress={sendTest} />
          </View>
        </Group>

        <Group title="Appearance">
          <View style={{ padding: 14 }}>
            <Segmented<ThemePreference>
              accessibilityLabel="Theme"
              value={preference}
              onChange={(v) => {
                haptic.select();
                setPreference(v);
              }}
              options={[
                { value: 'system', label: 'System', icon: 'settings' },
                { value: 'light', label: 'Light', icon: 'sun' },
                { value: 'dark', label: 'Dark', icon: 'moon' },
              ]}
            />
          </View>
        </Group>

        <Group title="More">
          <Row icon="globe" title="Open website" sub="The full DealRadar site, in the app" onPress={() => navigation.navigate('Website', {})} />
          <Divider />
          <Row icon="activity" title="Settings" sub="Server, version, system status" onPress={() => navigation.navigate('Settings')} />
        </Group>

        <Button title="Sign out" icon="logout" variant="danger" loading={signingOut} onPress={signOut} />
      </ScrollView>
    </View>
  );
}

function Group({ title, children }: { title: string; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View style={{ gap: 8 }}>
      <Text accessibilityRole="header" style={{ color: t.c.text3, fontSize: t.f.xs, fontWeight: '800', letterSpacing: 0.7, textTransform: 'uppercase', paddingHorizontal: 4 }}>
        {title}
      </Text>
      <View style={{ borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.surface, overflow: 'hidden' }}>
        {children}
      </View>
    </View>
  );
}
