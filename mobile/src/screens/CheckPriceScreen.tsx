import { useNavigation, useRoute } from '@react-navigation/native';
import * as Clipboard from 'expo-clipboard';
import * as WebBrowser from 'expo-web-browser';
import React, { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, Text, TextInput, View, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, errorMessage, isAbort, isNotFound, isOffline, type Deal, type LookupResult } from '../api';
import {
  Button,
  DealCard,
  EmptyState,
  Icon,
  IconButton,
  PriceChart,
  PriceVerdictMeter,
  ScreenHeader,
  SectionHead,
  haptic,
  money,
  plural,
  titleCase,
  useToast,
} from '../components';
import { useTheme } from '../theme';
import type { RootNav, RootRoute } from './types';

const PAD = 16;
const GAP = 10;
const URL_RE = /https?:\/\/[^\s<>"']+/i;

const extractUrl = (text: string): string => (text.match(URL_RE)?.[0] ?? text).trim();

type State =
  | { kind: 'idle' }
  | { kind: 'loading' }
  | { kind: 'done'; result: LookupResult }
  | { kind: 'error'; error: unknown };

export function CheckPriceScreen() {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const route = useRoute<RootRoute<'CheckPrice'>>();
  const toast = useToast();
  const { width } = useWindowDimensions();
  const initial = route.params?.url ?? '';

  const [text, setText] = useState(initial);
  const [state, setState] = useState<State>({ kind: 'idle' });
  const [focused, setFocused] = useState(false);
  const ctrlRef = useRef<AbortController | null>(null);

  const cols = width >= 600 ? 3 : 2;
  const cardW = Math.floor((Math.min(width, 760) - PAD * 2 - GAP * (cols - 1)) / cols);

  const check = useCallback(async (raw: string) => {
    const url = extractUrl(raw);
    if (!/^https?:\/\//i.test(url)) {
      setState({ kind: 'error', error: new Error('Paste a full product link, starting with https://') });
      return;
    }
    haptic.light();
    ctrlRef.current?.abort();
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setState({ kind: 'loading' });
    try {
      const result = await api.deals.lookup(url, { signal: ctrl.signal });
      if (ctrlRef.current !== ctrl) return;
      setState({ kind: 'done', result });
      haptic.success();
    } catch (e) {
      if (isAbort(e)) return;
      setState({ kind: 'error', error: e });
    }
  }, []);

  useEffect(() => {
    if (initial) void check(initial);
    return () => ctrlRef.current?.abort();
  }, [initial, check]);

  const paste = async () => {
    try {
      const clip = await Clipboard.getStringAsync();
      if (!clip.trim()) {
        toast('Your clipboard is empty — copy a product link first.', 'info');
        return;
      }
      const url = extractUrl(clip);
      setText(url);
      void check(url);
    } catch {
      toast('Couldn’t read the clipboard.', 'err');
    }
  };

  const openDeal = useCallback((d: Deal) => navigation.navigate('DealDetail', { id: d.id }), [navigation]);

  return (
    <View style={{ flex: 1, backgroundColor: t.c.bg }}>
      <ScreenHeader title="Check a price" />
      <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <ScrollView
          keyboardShouldPersistTaps="handled"
          contentContainerStyle={{ padding: PAD, gap: 18, paddingBottom: insets.bottom + 32, maxWidth: 760, width: '100%', alignSelf: 'center' }}
        >
          <View style={{ gap: 6 }}>
            <Text style={{ color: t.c.text, fontSize: t.f.lg, fontWeight: '800', letterSpacing: -0.3 }}>
              Is it really a good price?
            </Text>
            <Text style={{ color: t.c.text2, fontSize: t.f.sm, lineHeight: 19 }}>
              Paste any Amazon, Flipkart or Myntra product link. We’ll show its price history, whether today’s price is a good one, and any
              matching deals.
            </Text>
          </View>

          <View style={{ gap: 10 }}>
            <View
              style={{
                minHeight: 52,
                flexDirection: 'row',
                alignItems: 'center',
                gap: 8,
                paddingLeft: 14,
                paddingRight: 4,
                borderRadius: t.r.md,
                borderWidth: 1,
                borderColor: focused ? t.c.accent : t.c.border,
                backgroundColor: t.c.surface,
              }}
            >
              <Icon name="link" size={18} color={t.c.text3} />
              <TextInput
                value={text}
                onChangeText={setText}
                onFocus={() => setFocused(true)}
                onBlur={() => setFocused(false)}
                placeholder="https://www.amazon.in/…"
                placeholderTextColor={t.c.text3}
                selectionColor={t.c.accent}
                autoCapitalize="none"
                autoCorrect={false}
                keyboardType="url"
                returnKeyType="search"
                onSubmitEditing={() => check(text)}
                accessibilityLabel="Product link"
                maxFontSizeMultiplier={1.4}
                style={{ flex: 1, color: t.c.text, fontSize: t.f.md, paddingVertical: 10 }}
              />
              {text ? <IconButton name="close" label="Clear link" size={16} color={t.c.text2} onPress={() => setText('')} /> : null}
            </View>
            <View style={{ flexDirection: 'row', gap: 8 }}>
              <Button title="Paste" icon="clipboard" variant="soft" onPress={paste} style={{ flex: 1 }} />
              <Button
                title="Check price"
                icon="search"
                loading={state.kind === 'loading'}
                disabled={!text.trim()}
                onPress={() => check(text)}
                style={{ flex: 1.4 }}
              />
            </View>
          </View>

          {state.kind === 'loading' ? (
            <View style={{ alignItems: 'center', padding: 24, gap: 10 }}>
              <ActivityIndicator color={t.c.accent} size="large" />
              <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>Looking up this product…</Text>
            </View>
          ) : null}

          {state.kind === 'error' ? (
            <EmptyState
              emoji={isOffline(state.error) ? '📶' : '🔗'}
              title={
                isOffline(state.error)
                  ? 'You’re offline'
                  : isNotFound(state.error)
                    ? 'Price check isn’t available yet'
                    : 'Couldn’t check that link'
              }
              message={
                isNotFound(state.error)
                  ? 'The server doesn’t support link lookups yet — try again after the next update.'
                  : errorMessage(state.error)
              }
              actions={[{ title: 'Try again', variant: 'primary', onPress: () => check(text) }]}
            />
          ) : null}

          {state.kind === 'done' ? <LookupView result={state.result} cols={cols} cardW={cardW} onOpen={openDeal} /> : null}
        </ScrollView>
      </KeyboardAvoidingView>
    </View>
  );
}

function LookupView({
  result,
  cols,
  cardW,
  onOpen,
}: {
  result: LookupResult;
  cols: number;
  cardW: number;
  onOpen: (d: Deal) => void;
}) {
  const t = useTheme();
  const stats = result.price_stats;
  const history = result.history ?? [];
  const live = result.deals ?? [];
  const past = (result.archive ?? []).map((d) => ({ ...d, status: d.status && d.status !== 'live' ? d.status : 'archived' }));
  const store = result.store ? titleCase(result.store) : '';
  const nothing = !result.verdict && !history.length && !live.length && !past.length;

  return (
    <View style={{ gap: 18 }}>
      {store || result.resolved_url ? (
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          {store ? (
            <View style={{ paddingHorizontal: 9, paddingVertical: 3, borderRadius: 999, borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.surface }}>
              <Text style={{ color: t.c.text, fontSize: 11, fontWeight: '700' }}>{store}</Text>
            </View>
          ) : null}
          {result.resolved_url ? (
            <Text numberOfLines={1} style={{ flex: 1, color: t.c.text3, fontSize: t.f.xs }}>
              {result.resolved_url}
            </Text>
          ) : null}
        </View>
      ) : null}

      {result.verdict ? <PriceVerdictMeter verdict={result.verdict} /> : null}

      {nothing ? (
        <EmptyState
          emoji="🔍"
          title="We haven’t tracked this product yet"
          message="No deal channel has posted it so far. Check its full price history on BuyHatke below."
        />
      ) : null}

      {history.length || stats?.points ? (
        <View style={{ gap: 10 }}>
          <SectionHead
            title="📈 Our price history"
            sub={
              stats?.points
                ? `${stats.points} ${plural(stats.points, 'price point')} · low ${money(stats.min)} · typical ${money(stats.median)} · high ${money(stats.max)}`
                : undefined
            }
          />
          <View style={{ padding: 12, borderRadius: t.r.md, borderWidth: 1, borderColor: t.c.border, backgroundColor: t.c.surface }}>
            <PriceChart points={history} />
          </View>
        </View>
      ) : null}

      {result.price_history_url ? (
        <Button
          title="Full price history on BuyHatke"
          icon="trend"
          iconRight="external"
          variant="soft"
          onPress={() => {
            haptic.light();
            WebBrowser.openBrowserAsync(result.price_history_url as string).catch(() => {});
          }}
        />
      ) : null}

      {live.length ? (
        <CardGrid title="🟢 Live deals for this product" sub={`${live.length} matching ${plural(live.length, 'deal')} right now`} deals={live} cols={cols} cardW={cardW} onOpen={onOpen} />
      ) : null}
      {past.length ? (
        <CardGrid title="🗂 From the deal archive" sub="Earlier deals — prices may have changed" deals={past} cols={cols} cardW={cardW} onOpen={onOpen} />
      ) : null}
    </View>
  );
}

function CardGrid({
  title,
  sub,
  deals,
  cols,
  cardW,
  onOpen,
}: {
  title: string;
  sub: string;
  deals: Deal[];
  cols: number;
  cardW: number;
  onOpen: (d: Deal) => void;
}) {
  const rows: Deal[][] = [];
  for (let i = 0; i < deals.length; i += cols) rows.push(deals.slice(i, i + cols));
  return (
    <View style={{ gap: GAP }}>
      <SectionHead title={title} sub={sub} />
      {rows.map((row, i) => (
        <View key={i} style={{ flexDirection: 'row', gap: GAP }}>
          {row.map((d) => (
            <DealCard key={d.id} deal={d} layout="grid" width={cardW} onOpen={onOpen} />
          ))}
        </View>
      ))}
    </View>
  );
}
