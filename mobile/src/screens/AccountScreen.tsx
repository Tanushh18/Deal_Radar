import { useFocusEffect, useNavigation } from '@react-navigation/native';
import * as Notifications from 'expo-notifications';
import React, { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Alert, Linking, ScrollView, Switch, Text, View } from 'react-native';

import { api, errorMessage, isNotFound, type Follow, type FollowKind, type KeyCount, type User } from '../api';
import {
  Button,
  Card,
  Chip,
  Divider,
  Row,
  ScreenHeader,
  Segmented,
  SelectField,
  categoryIcon,
  haptic,
  storeName,
  titleCase,
  useCategories,
  useSaved,
  useToast,
} from '../components';
import { getDeviceId, registerDevice } from '../native/device';
import { getPushToken, requestPermission } from '../native/notifications';
import { onSignedOut } from '../native/session';
import { isPublicMode } from '../native/session';
import { useTheme, useThemePreference, type ThemePreference } from '../theme';
import type { TabNav } from './types';

type NotifState = 'granted' | 'denied' | 'blocked' | 'unknown';

export function AccountScreen() {
  const t = useTheme();
  const navigation = useNavigation<TabNav<'Account'>>();
  const toast = useToast();
  const { count: savedCount } = useSaved();
  const publicMode = isPublicMode();
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
      const granted = await requestPermission();
      if (granted) {
        const push_token = await getPushToken();
        if (push_token) void registerDevice({ push_token });
      }
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
        {publicMode ? null : (
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
        )}

        <Group title="Your deals">
          <Row
            icon="heart"
            title="Saved deals & price alerts"
            sub={savedCount ? `${savedCount} saved` : 'Tap ♡ on any deal to keep it here'}
            onPress={() => navigation.navigate('Saved')}
          />
          <Divider />
          <Row
            icon="link"
            title="Check a product’s price"
            sub="Paste an Amazon, Flipkart or Myntra link"
            onPress={() => navigation.navigate('CheckPrice', {})}
          />
        </Group>

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
          {publicMode ? null : (
            <View style={{ padding: 14, paddingTop: 4 }}>
              <Button title="Send test notification" icon="bell" variant="soft" loading={testing} onPress={sendTest} />
            </View>
          )}
          <Divider />
          <FollowsAndDigest notifGranted={notif === 'granted'} onNeedPermission={enableNotifications} />
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

        {publicMode ? null : (
          <Button title="Sign out" icon="logout" variant="danger" loading={signingOut} onPress={signOut} />
        )}
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

const KIND_OPTIONS: { value: FollowKind; label: string }[] = [
  { value: 'category', label: 'Category' },
  { value: 'brand', label: 'Brand' },
  { value: 'store', label: 'Store' },
];
const FOLLOW_DISCOUNTS = [0, 10, 30, 50, 70];

const hourLabel = (h: number) => {
  const suffix = h < 12 ? 'AM' : 'PM';
  const hh = h % 12 === 0 ? 12 : h % 12;
  return `${hh}:00 ${suffix}`;
};
const HOUR_OPTIONS = Array.from({ length: 24 }, (_, h) => ({ value: String(h), label: hourLabel(h) }));

const followLabel = (f: Pick<Follow, 'kind' | 'value'>) =>
  f.kind === 'store' ? storeName({ store: f.value }) || titleCase(f.value) : f.kind === 'brand' ? titleCase(f.value) : f.value;

function FollowsAndDigest({ notifGranted, onNeedPermission }: { notifGranted: boolean; onNeedPermission: () => void }) {
  const t = useTheme();
  const toast = useToast();
  const categories = useCategories();

  const [status, setStatus] = useState<'loading' | 'ready' | 'unsupported' | 'error'>('loading');
  const [follows, setFollows] = useState<Follow[]>([]);
  const [digest, setDigest] = useState(false);
  const [hour, setHour] = useState(9);
  const [savingDigest, setSavingDigest] = useState(false);

  const [kind, setKind] = useState<FollowKind>('category');
  const [value, setValue] = useState('');
  const [minOff, setMinOff] = useState(0);
  const [adding, setAdding] = useState(false);
  const [stores, setStores] = useState<KeyCount[]>([]);
  const [brands, setBrands] = useState<KeyCount[]>([]);

  const load = useCallback(async () => {
    try {
      const res = await api.devices.settings(await getDeviceId());
      setFollows(res.follows ?? []);
      setDigest(!!res.device?.digest);
      if (typeof res.device?.digest_hour === 'number') setHour(res.device.digest_hour);
      setStatus('ready');
    } catch (e) {
      setStatus(isNotFound(e) ? 'unsupported' : 'error');
    }
  }, []);

  useEffect(() => {
    void load();
    api.deals
      .facets()
      .then((f) => {
        setStores((f.stores ?? []).filter((s) => s.key && s.key !== 'unknown'));
        setBrands(f.brands ?? []);
      })
      .catch(() => {});
  }, [load]);

  const options =
    kind === 'category'
      ? categories.map((c) => ({ value: c.name, label: c.name, leading: categoryIcon(c.name) }))
      : (kind === 'brand' ? brands : stores).map((k) => ({
          value: k.key,
          label: followLabel({ kind, value: k.key }),
          count: k.count,
        }));

  const addFollow = async () => {
    if (!value) {
      toast(`Pick a ${kind} to follow.`, 'info');
      return;
    }
    if (follows.some((f) => f.kind === kind && f.value === value)) {
      toast(`You already follow ${followLabel({ kind, value })}.`, 'info');
      return;
    }
    setAdding(true);
    try {
      const res = await api.devices.follow({ device_id: await getDeviceId(), kind, value, min_discount: minOff || null });
      setFollows((prev) => [...prev, res.follow]);
      haptic.success();
      toast(`Following ${followLabel({ kind, value })}.`, 'ok');
      setValue('');
      if (!notifGranted) onNeedPermission();
    } catch (e) {
      haptic.error();
      toast(errorMessage(e), 'err');
    } finally {
      setAdding(false);
    }
  };

  const removeFollow = async (f: Follow) => {
    haptic.select();
    setFollows((prev) => prev.filter((x) => x.id !== f.id));
    try {
      await api.devices.unfollow(f.id, await getDeviceId());
    } catch (e) {
      toast(errorMessage(e), 'err');
      void load();
    }
  };

  const saveDigest = async (on: boolean, h: number) => {
    const prev = { digest, hour };
    setDigest(on);
    setHour(h);
    setSavingDigest(true);
    const push_token = await getPushToken().catch(() => null);
    const res = await registerDevice({ digest: on, digest_hour: h, push_token });
    setSavingDigest(false);
    if (!res) {
      setDigest(prev.digest);
      setHour(prev.hour);
      toast('Couldn’t save your digest setting — try again.', 'err');
      return;
    }
    haptic.success();
    if (on) {
      toast(`Daily digest on — every day at ${hourLabel(h)}.`, 'ok');
      if (!notifGranted) onNeedPermission();
    }
  };

  if (status === 'loading') {
    return (
      <View style={{ padding: 16 }}>
        <ActivityIndicator color={t.c.accent} />
      </View>
    );
  }
  if (status === 'unsupported' || status === 'error') {
    return (
      <View style={{ padding: 14, gap: 8 }}>
        <Text style={{ color: t.c.text2, fontSize: t.f.sm, lineHeight: 19 }}>
          {status === 'unsupported'
            ? 'Following categories, brands and stores is coming to this server soon.'
            : 'Couldn’t load your follows.'}
        </Text>
        {status === 'error' ? <Button title="Try again" variant="soft" size="sm" onPress={() => load()} /> : null}
      </View>
    );
  }

  return (
    <View style={{ padding: 14, gap: 16 }}>
      <View style={{ gap: 10 }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, minHeight: 48 }}>
          <View style={{ flex: 1 }}>
            <Text style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '600' }}>Daily deal digest</Text>
            <Text style={{ color: t.c.text3, fontSize: t.f.xs, marginTop: 2 }}>
              The day’s best deals from what you follow, once a day
            </Text>
          </View>
          {savingDigest ? <ActivityIndicator size="small" color={t.c.accent} /> : null}
          <Switch
            value={digest}
            onValueChange={(v) => saveDigest(v, hour)}
            disabled={savingDigest}
            trackColor={{ false: t.c.surface3, true: t.c.accent }}
            thumbColor="#ffffff"
            accessibilityLabel="Daily deal digest"
          />
        </View>
        {digest ? (
          <SelectField
            label="Send it at"
            title="Digest time"
            value={String(hour)}
            options={HOUR_OPTIONS}
            onChange={(v) => saveDigest(true, Number(v))}
          />
        ) : null}
      </View>

      <View style={{ height: 1, backgroundColor: t.c.border }} />

      <View style={{ gap: 12 }}>
        <View>
          <Text style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '600' }}>Follow</Text>
          <Text style={{ color: t.c.text3, fontSize: t.f.xs, marginTop: 2 }}>
            Get a notification when a new deal lands in a category, brand or store you follow
          </Text>
        </View>

        {follows.length ? (
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {follows.map((f) => (
              <Chip
                key={f.id}
                removable
                leading={f.kind === 'category' ? categoryIcon(f.value) : f.kind === 'store' ? '🛍' : '🏷'}
                label={`${followLabel(f)}${f.min_discount ? ` · ${f.min_discount}%+` : ''}`}
                accessibilityLabel={`Stop following ${followLabel(f)}`}
                onPress={() => removeFollow(f)}
              />
            ))}
          </View>
        ) : (
          <Text style={{ color: t.c.text3, fontSize: t.f.sm }}>You’re not following anything yet.</Text>
        )}

        <Segmented<FollowKind>
          accessibilityLabel="What to follow"
          value={kind}
          onChange={(k) => {
            haptic.select();
            setKind(k);
            setValue('');
          }}
          options={KIND_OPTIONS}
        />
        <SelectField
          label={kind === 'category' ? 'Category' : kind === 'brand' ? 'Brand' : 'Store'}
          value={value}
          options={options}
          onChange={setValue}
          placeholder={`Choose a ${kind}`}
        />
        <View style={{ gap: 8 }}>
          <Text style={{ color: t.c.text2, fontSize: t.f.sm, fontWeight: '600' }}>Only deals with at least</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 6 }}>
            {FOLLOW_DISCOUNTS.map((d) => (
              <Chip
                key={d}
                label={d ? `${d}% off` : 'Any discount'}
                active={minOff === d}
                onPress={() => {
                  haptic.select();
                  setMinOff(d);
                }}
              />
            ))}
          </View>
        </View>
        <Button title="Follow" icon="plus" loading={adding} disabled={!value} onPress={addFollow} />
      </View>
    </View>
  );
}
