import { useNavigation } from '@react-navigation/native';
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { FlatList, KeyboardAvoidingView, Platform, Pressable, RefreshControl, Switch, Text, View } from 'react-native';

import { api, errorMessage, type AvailableChannel } from '../api';
import {
  Button,
  Card,
  ChannelAvatar,
  EmptyState,
  Field,
  IconButton,
  ScreenHeader,
  Skeleton,
  haptic,
  num,
  useDealFilters,
  useToast,
} from '../components';
import { useTheme } from '../theme';
import type { TabNav } from './types';

export function ChannelsScreen() {
  const t = useTheme();
  const navigation = useNavigation<TabNav<'Channels'>>();
  const toast = useToast();
  const { requestRefresh } = useDealFilters();

  const [channels, setChannels] = useState<AvailableChannel[] | null>(null);
  const [liveDeals, setLiveDeals] = useState<Record<number, number>>({});
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const [initial, setInitial] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [filter, setFilter] = useState('');
  const [publicName, setPublicName] = useState('');
  const [adding, setAdding] = useState(false);
  const [saving, setSaving] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    api.channels
      .mine()
      .then((r) => {
        const map: Record<number, number> = {};
        (r.channels ?? []).forEach((c) => (map[c.tg_id] = c.live_deals));
        setLiveDeals(map);
      })
      .catch(() => {});
    try {
      const res = await api.channels.available();
      const list = res.channels ?? [];
      const tracked = new Set(list.filter((c) => c.tracked).map((c) => c.tg_id));
      setChannels(list);
      setSelected(tracked);
      setInitial(new Set(tracked));
    } catch (e) {
      // Telegram session problems come back as a readable `detail`; show it verbatim.
      setError(errorMessage(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const onRefresh = async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  };

  const toggle = (id: number, on: boolean) => {
    haptic.select();
    setSelected((s) => {
      const next = new Set(s);
      if (on) next.add(id);
      else next.delete(id);
      return next;
    });
  };

  const dirty = useMemo(() => {
    if (selected.size !== initial.size) return true;
    for (const id of selected) if (!initial.has(id)) return true;
    return false;
  }, [selected, initial]);

  const save = async () => {
    setSaving(true);
    try {
      await api.channels.track(Array.from(selected));
      setInitial(new Set(selected));
      haptic.success();
      toast(`Tracking ${selected.size} channels. Fetching deals…`, 'ok');
      navigation.navigate('Deals');
      try {
        const res = await api.channels.sync();
        const added = (res.new || 0) + (res.merged || 0);
        toast(
          added
            ? `Synced: ${res.new ?? 0} new deals, ${res.merged ?? 0} matched to existing ones.`
            : 'Sync complete — no new deals in your channels yet.',
          'ok',
        );
      } catch (e) {
        toast(errorMessage(e), 'err');
      }
      requestRefresh();
    } catch (e) {
      haptic.error();
      toast(errorMessage(e), 'err');
    } finally {
      setSaving(false);
    }
  };

  const addPublic = async () => {
    const username = publicName.trim();
    if (!username) return;
    setAdding(true);
    try {
      const res = await api.channels.addPublic(username);
      haptic.success();
      toast(`Added ${res.channel?.title ?? username}.`, 'ok');
      setPublicName('');
      await load();
      requestRefresh();
    } catch (e) {
      toast(errorMessage(e), 'err');
    } finally {
      setAdding(false);
    }
  };

  const q = filter.trim().toLowerCase();
  const shown = (channels ?? []).filter(
    (c) => !q || c.title.toLowerCase().includes(q) || (c.username || '').toLowerCase().includes(q),
  );

  const header = (
    <View style={{ gap: 14, paddingBottom: 10 }}>
      <Text style={{ color: t.c.text2, fontSize: t.f.md, lineHeight: 21 }}>
        Pick the deal channels DealRadar should read. Only channels you already follow are listed — plus any public
        channel you add by username.
      </Text>
      <Card style={{ gap: 10 }}>
        <Field
          label="Add a public channel by username"
          value={publicName}
          onChangeText={setPublicName}
          placeholder="@dealschannel or t.me/dealschannel"
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="go"
          onSubmitEditing={addPublic}
        />
        <Button title="Add & join" variant="soft" icon="plus" loading={adding} disabled={!publicName.trim()} onPress={addPublic} />
      </Card>
      {channels ? (
        <View style={{ gap: 6 }}>
          <Field value={filter} onChangeText={setFilter} placeholder="Filter channels…" accessibilityLabel="Filter channels" autoCorrect={false} />
          <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>
            {selected.size} selected · {channels.length} channels found
          </Text>
        </View>
      ) : null}
    </View>
  );

  let empty: React.ReactElement;
  if (error) {
    empty = (
      <EmptyState emoji="⚠️" title="Couldn’t read your channels" message={error} actions={[{ title: 'Try again', variant: 'primary', onPress: load }]} />
    );
  } else if (!channels) {
    empty = (
      <View style={{ gap: 10 }}>
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} style={{ height: 70, borderRadius: t.r.md }} />
        ))}
      </View>
    );
  } else {
    empty = (
      <EmptyState
        emoji="📡"
        title={channels.length ? 'No matches' : 'No channels found'}
        message={
          channels.length
            ? 'No channels match that filter.'
            : 'No broadcast channels found on your account. Join some deal channels in Telegram, then pull to refresh.'
        }
      />
    );
  }

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader
        title="Channels"
        large
        onBack={null}
        right={<IconButton name="sync" label="Refresh list" onPress={onRefresh} disabled={refreshing} />}
      />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <FlatList
          data={shown}
          keyExtractor={(c) => String(c.tg_id)}
          ListHeaderComponent={header}
          ListEmptyComponent={empty}
          ItemSeparatorComponent={() => <View style={{ height: 8 }} />}
          contentContainerStyle={{ padding: 14, paddingBottom: 24, maxWidth: 760, width: '100%', alignSelf: 'center' }}
          keyboardShouldPersistTaps="handled"
          refreshControl={
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} colors={[t.c.accent]} tintColor={t.c.accent} progressBackgroundColor={t.c.surface} />
          }
          renderItem={({ item }) => (
            <ChannelRow
              c={item}
              on={selected.has(item.tg_id)}
              deals={liveDeals[item.tg_id]}
              onToggle={(v) => toggle(item.tg_id, v)}
            />
          )}
        />
        {channels ? (
          <View style={{ padding: 12, paddingHorizontal: 14, borderTopWidth: 1, borderTopColor: t.c.border, backgroundColor: t.c.bg }}>
            <Button
              title={dirty ? `Save selection · ${selected.size}` : 'Save selection'}
              icon="check"
              loading={saving}
              variant={dirty ? 'primary' : 'soft'}
              onPress={save}
            />
          </View>
        ) : null}
      </KeyboardAvoidingView>
    </View>
  );
}

function ChannelRow({
  c,
  on,
  deals,
  onToggle,
}: {
  c: AvailableChannel;
  on: boolean;
  deals?: number;
  onToggle: (v: boolean) => void;
}) {
  const t = useTheme();
  const sub: { text: string; good?: boolean }[] = [{ text: c.username ? '@' + c.username : 'private channel' }];
  if (c.participants) sub.push({ text: `${num(c.participants)} members` });
  if (deals) sub.push({ text: `${num(deals)} deals`, good: true });
  return (
    <Pressable
      accessibilityRole="switch"
      accessibilityState={{ checked: on }}
      accessibilityLabel={`Track ${c.title}. ${sub.map((x) => x.text).join(', ')}`}
      onPress={() => onToggle(!on)}
      android_ripple={{ color: t.c.accentSoft }}
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 12,
        padding: 12,
        minHeight: 68,
        borderRadius: t.r.md,
        borderWidth: 1,
        borderColor: on ? t.c.accentLine : t.c.border,
        backgroundColor: on ? t.c.accentSoft : t.c.surface,
      }}
    >
      <ChannelAvatar title={c.title} />
      <View style={{ flex: 1, gap: 3 }}>
        <Text numberOfLines={1} style={{ color: t.c.text, fontWeight: '700', fontSize: t.f.md }}>
          {c.title}
        </Text>
        <Text numberOfLines={1} style={{ color: t.c.text3, fontSize: t.f.xs }}>
          {sub.map((s, i) => (
            <Text key={s.text} style={s.good ? { color: t.c.good, fontWeight: '700' } : undefined}>
              {i ? '  ·  ' : ''}
              {s.text}
            </Text>
          ))}
        </Text>
      </View>
      <Switch
        value={on}
        onValueChange={onToggle}
        trackColor={{ false: t.c.surface3, true: t.c.accent }}
        thumbColor="#ffffff"
        importantForAccessibility="no"
      />
    </Pressable>
  );
}
