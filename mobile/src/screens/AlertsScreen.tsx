import { useFocusEffect, useNavigation } from '@react-navigation/native';
import React, { useCallback, useState } from 'react';
import { Alert, KeyboardAvoidingView, Platform, Pressable, RefreshControl, ScrollView, Switch, Text, View } from 'react-native';

import { api, errorMessage, isNotFound, type AppNotification, type Watchlist } from '../api';
import {
  Banner,
  Button,
  Card,
  EmptyState,
  Field,
  Icon,
  ScreenHeader,
  SectionHead,
  SelectField,
  Skeleton,
  categoryIcon,
  haptic,
  money,
  timeAgo,
  useCategories,
  useToast,
} from '../components';
import { useTheme } from '../theme';
import type { TabNav } from './types';

export function AlertsScreen() {
  const t = useTheme();
  const navigation = useNavigation<TabNav<'Alerts'>>();
  const toast = useToast();
  const categories = useCategories();

  const [list, setList] = useState<Watchlist[] | null>(null);
  const [listError, setListError] = useState<string | null>(null);
  const [notifs, setNotifs] = useState<AppNotification[] | null>(null);
  const [notifsMissing, setNotifsMissing] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [pending, setPending] = useState<Record<number, 'toggle' | 'test' | 'delete'>>({});

  const [query, setQuery] = useState('');
  const [category, setCategory] = useState('');
  const [maxPrice, setMaxPrice] = useState('');
  const [minDiscount, setMinDiscount] = useState('');
  const [creating, setCreating] = useState(false);
  const [testing, setTesting] = useState(false);

  const load = useCallback(async () => {
    const [w, n] = await Promise.allSettled([api.watchlists.list(), api.notifications.list(0, 20)]);
    if (w.status === 'fulfilled') {
      setList(w.value.watchlists ?? []);
      setListError(null);
    } else {
      setListError(errorMessage(w.reason));
    }
    if (n.status === 'fulfilled') {
      setNotifs(n.value.notifications ?? []);
      setNotifsMissing(false);
    } else {
      setNotifs([]);
      setNotifsMissing(isNotFound(n.reason));
    }
  }, []);

  useFocusEffect(
    useCallback(() => {
      load();
    }, [load]),
  );

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const mark = (id: number, what?: 'toggle' | 'test' | 'delete') =>
    setPending((p) => {
      const next = { ...p };
      if (what) next[id] = what;
      else delete next[id];
      return next;
    });

  const create = async () => {
    const q = query.trim();
    if (q.length < 2) {
      toast('Tell us what to watch for — at least 2 characters.', 'info');
      return;
    }
    setCreating(true);
    try {
      await api.watchlists.create({
        query: q,
        category,
        max_price: maxPrice ? Number(maxPrice) : null,
        min_discount: Number(minDiscount || 0),
      });
      haptic.success();
      toast('Alert created. You’ll get matches in Telegram Saved Messages.', 'ok');
      setQuery('');
      setCategory('');
      setMaxPrice('');
      setMinDiscount('');
      load();
    } catch (e) {
      haptic.error();
      toast(errorMessage(e), 'err');
    } finally {
      setCreating(false);
    }
  };

  const toggle = async (w: Watchlist, on: boolean) => {
    haptic.select();
    setList((l) => l?.map((x) => (x.id === w.id ? { ...x, notify: on } : x)) ?? l);
    mark(w.id, 'toggle');
    try {
      await api.watchlists.setNotify(w.id, on);
    } catch (e) {
      setList((l) => l?.map((x) => (x.id === w.id ? { ...x, notify: !on } : x)) ?? l);
      toast(errorMessage(e), 'err');
    } finally {
      mark(w.id);
    }
  };

  const test = async (w: Watchlist) => {
    mark(w.id, 'test');
    try {
      await api.watchlists.test(w.id);
      haptic.success();
      toast('Test alert sent — check Saved Messages in Telegram.', 'ok');
    } catch (e) {
      toast(errorMessage(e), 'err');
    } finally {
      mark(w.id);
    }
  };

  const remove = (w: Watchlist) => {
    Alert.alert('Delete alert?', `You’ll stop getting Telegram messages for “${w.query}”.`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete',
        style: 'destructive',
        onPress: async () => {
          mark(w.id, 'delete');
          try {
            await api.watchlists.remove(w.id);
            setList((l) => l?.filter((x) => x.id !== w.id) ?? l);
            toast('Alert deleted.', 'ok');
          } catch (e) {
            toast(errorMessage(e), 'err');
            load();
          } finally {
            mark(w.id);
          }
        },
      },
    ]);
  };

  const sendTest = async () => {
    setTesting(true);
    try {
      await api.notifications.test();
      haptic.success();
      toast('Test notification sent.', 'ok');
      load();
    } catch (e) {
      toast(isNotFound(e) ? 'This server doesn’t support app notifications yet.' : errorMessage(e), 'err');
    } finally {
      setTesting(false);
    }
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Deal alerts" large onBack={null} />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView
          contentContainerStyle={{ padding: 14, gap: 18, paddingBottom: 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}
          keyboardShouldPersistTaps="handled"
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={[t.c.accent]} tintColor={t.c.accent} progressBackgroundColor={t.c.surface} />}
        >
          <Text style={{ color: t.c.text2, fontSize: t.f.md, lineHeight: 21 }}>
            Saved searches. When a new matching deal appears, DealRadar messages you in your own Telegram{' '}
            <Text style={{ fontWeight: '700', color: t.c.text }}>Saved Messages</Text>.
          </Text>

          <Card style={{ gap: 14 }}>
            <Field label="What to watch for" value={query} onChangeText={setQuery} placeholder="e.g. 65w gan charger" returnKeyType="done" />
            <SelectField
              label="Category"
              value={category}
              onChange={setCategory}
              options={[
                { value: '', label: 'Any' },
                ...categories.map((c) => ({ value: c.name, label: c.name, leading: categoryIcon(c.name) })),
              ]}
            />
            <View style={{ flexDirection: 'row', gap: 10 }}>
              <Field
                style={{ flex: 1 }}
                label="Max price (₹)"
                value={maxPrice}
                onChangeText={(v) => setMaxPrice(v.replace(/[^\d]/g, ''))}
                placeholder="Any"
                keyboardType="number-pad"
              />
              <Field
                style={{ flex: 1 }}
                label="Min discount (%)"
                value={minDiscount}
                onChangeText={(v) => setMinDiscount(v.replace(/[^\d]/g, '').slice(0, 2))}
                placeholder="0"
                keyboardType="number-pad"
              />
            </View>
            <Button title="Create alert" icon="plus" loading={creating} onPress={create} />
          </Card>

          {listError ? <Banner kind="error">{listError}</Banner> : null}

          {list == null && !listError ? (
            <View style={{ gap: 10 }}>
              <Skeleton style={{ height: 88, borderRadius: t.r.md }} />
              <Skeleton style={{ height: 88, borderRadius: t.r.md }} />
            </View>
          ) : list && !list.length ? (
            <EmptyState
              emoji="🔔"
              title="No alerts yet"
              message="Create an alert above and DealRadar will message you in Telegram the moment a matching deal appears."
            />
          ) : (
            <View style={{ gap: 10 }}>
              {list?.map((w) => (
                <AlertRow
                  key={w.id}
                  w={w}
                  busy={pending[w.id]}
                  onToggle={(v) => toggle(w, v)}
                  onTest={() => test(w)}
                  onDelete={() => remove(w)}
                />
              ))}
            </View>
          )}

          <SectionHead
            title="📬 Recent matches"
            sub="Deals your alerts caught lately"
            style={{ marginTop: 6 }}
          />
          <Button title="Send test notification" icon="bell" variant="soft" loading={testing} onPress={sendTest} />

          {notifs == null ? (
            <Skeleton style={{ height: 64, borderRadius: t.r.md }} />
          ) : notifsMissing ? (
            <Text style={{ color: t.c.text3, fontSize: t.f.sm }}>Recent matches aren’t available on this server yet.</Text>
          ) : !notifs.length ? (
            <Text style={{ color: t.c.text3, fontSize: t.f.sm, lineHeight: 19 }}>
              Nothing yet. When one of your alerts catches a deal, it shows up here.
            </Text>
          ) : (
            <View style={{ borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.border, overflow: 'hidden', backgroundColor: t.c.surface }}>
              {notifs.map((n, i) => (
                <Pressable
                  key={n.id}
                  accessibilityRole={n.deal_id ? 'button' : undefined}
                  accessibilityLabel={`${n.title}. ${n.body}`}
                  disabled={!n.deal_id}
                  onPress={() => n.deal_id && navigation.navigate('DealDetail', { id: n.deal_id })}
                  android_ripple={{ color: t.c.accentSoft }}
                  style={{
                    minHeight: 60,
                    padding: 14,
                    flexDirection: 'row',
                    gap: 12,
                    alignItems: 'center',
                    borderTopWidth: i ? 1 : 0,
                    borderTopColor: t.c.border,
                  }}
                >
                  <View style={{ width: 36, height: 36, borderRadius: 10, backgroundColor: t.c.accentSoft, alignItems: 'center', justifyContent: 'center' }}>
                    <Icon name="bell" size={17} color={t.c.accent} />
                  </View>
                  <View style={{ flex: 1, gap: 2 }}>
                    <Text numberOfLines={2} style={{ color: t.c.text, fontWeight: '600', fontSize: t.f.sm }}>
                      {n.title}
                    </Text>
                    <Text numberOfLines={2} style={{ color: t.c.text2, fontSize: t.f.xs, lineHeight: 16 }}>
                      {n.body}
                    </Text>
                    <Text style={{ color: t.c.text3, fontSize: 11 }}>{timeAgo(n.created_at)}</Text>
                  </View>
                  {n.deal_id ? <Icon name="chevRight" size={17} color={t.c.text3} /> : null}
                </Pressable>
              ))}
            </View>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function AlertRow({
  w,
  busy,
  onToggle,
  onTest,
  onDelete,
}: {
  w: Watchlist;
  busy?: 'toggle' | 'test' | 'delete';
  onToggle: (v: boolean) => void;
  onTest: () => void;
  onDelete: () => void;
}) {
  const t = useTheme();
  const f = w.filters || {};
  const bits = [
    f.category,
    f.store,
    f.max_price ? 'under ' + money(f.max_price) : '',
    f.min_discount ? f.min_discount + '%+ off' : '',
  ].filter(Boolean);
  return (
    <Card style={{ gap: 10, opacity: busy === 'delete' ? 0.5 : 1 }}>
      <View style={{ flexDirection: 'row', alignItems: 'flex-start', gap: 12 }}>
        <View style={{ flex: 1, gap: 3 }}>
          <Text style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>{w.query}</Text>
          <Text style={{ color: t.c.text2, fontSize: t.f.xs }}>
            {bits.length ? bits.join(' · ') : 'no extra filters'} · {w.alerts_sent} sent
          </Text>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 6, marginTop: 2 }}>
            <View style={{ width: 7, height: 7, borderRadius: 4, backgroundColor: w.notify ? t.c.good : t.c.text3 }} />
            <Text style={{ color: w.notify ? t.c.good : t.c.text3, fontSize: t.f.xs, fontWeight: '700' }}>
              {w.notify ? 'Active' : 'Paused'}
            </Text>
          </View>
        </View>
        <Switch
          value={w.notify}
          onValueChange={onToggle}
          disabled={busy === 'toggle'}
          trackColor={{ false: t.c.surface3, true: t.c.accent }}
          thumbColor="#ffffff"
          accessibilityLabel={`Notify for ${w.query}`}
        />
      </View>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Button title="Test" size="sm" variant="soft" icon="bell" loading={busy === 'test'} onPress={onTest} style={{ flex: 1 }} />
        <Button title="Delete" size="sm" variant="danger" icon="trash" disabled={!!busy} onPress={onDelete} style={{ flex: 1 }} />
      </View>
    </Card>
  );
}
