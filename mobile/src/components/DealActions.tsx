import React, { createContext, useCallback, useContext, useMemo, useState } from 'react';
import { KeyboardAvoidingView, Modal, Platform, Pressable, Share, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, errorMessage, isNotFound, resolveServerUrl, type Deal, type PriceAlert } from '../api';
import { getDeviceId } from '../native/device';
import { getPushToken } from '../native/notifications';
import { useTheme } from '../theme';
import { money } from './format';
import { Icon, type IconName } from './Icon';
import { haptic } from './native';
import { HeartGlyph, useSaved } from './Saved';
import { useToast } from './Toast';
import { Button } from './ui';

export async function shareDeal(deal: Pick<Deal, 'id' | 'title' | 'price' | 'discount_pct'>): Promise<void> {
  haptic.light();
  let link = '';
  try {
    link = `${(await resolveServerUrl()).replace(/\/+$/, '')}/d/${encodeURIComponent(deal.id)}`;
  } catch {
    /* share the text alone */
  }
  const price = deal.price != null ? ` — ${money(deal.price)}` : '';
  const off = deal.discount_pct >= 5 ? ` (${deal.discount_pct}% off)` : '';
  try {
    await Share.share({ title: deal.title, message: `${deal.title}${price}${off}${link ? `\n${link}` : ''}` });
  } catch {
    /* user dismissed */
  }
}

export const defaultAlertTarget = (price: number | null | undefined): number =>
  price ? Math.max(1, Math.floor((price * 0.9) / 10) * 10) : 0;

export async function createPriceAlert(dealId: string, target: number): Promise<PriceAlert> {
  const [device_id, push_token] = await Promise.all([getDeviceId(), getPushToken().catch(() => null)]);
  const res = await api.priceAlerts.create({ device_id, deal_id: dealId, target_price: target, push_token });
  return res.alert;
}

export async function listPriceAlerts(signal?: AbortSignal): Promise<PriceAlert[]> {
  const device_id = await getDeviceId();
  const res = await api.priceAlerts.list(device_id, { signal });
  return res.alerts ?? [];
}

export async function removePriceAlert(id: number): Promise<void> {
  await api.priceAlerts.remove(id, await getDeviceId());
}

export function alertCreatedMessage(alert: PriceAlert, target: number): string {
  return alert.triggered_at
    ? `It’s already ${money(alert.triggered_price)} — at or below your target!`
    : `We’ll tell you when it drops to ${money(target)} or less.`;
}

export function alertErrorMessage(e: unknown): string {
  return isNotFound(e) ? 'This deal is no longer live, so it can’t be watched.' : errorMessage(e);
}

type Ctx = { open: (deal: Deal) => void };
const DealActionsContext = createContext<Ctx>({ open: () => {} });

export const useDealActions = () => useContext(DealActionsContext);

/** Long-press quick actions (Save, Share, Price alert) for any deal card. */
export function DealActionsProvider({ children }: { children: React.ReactNode }) {
  const [deal, setDeal] = useState<Deal | null>(null);
  const open = useCallback((d: Deal) => {
    haptic.light();
    setDeal(d);
  }, []);
  const value = useMemo(() => ({ open }), [open]);
  return (
    <DealActionsContext.Provider value={value}>
      {children}
      {deal ? <QuickActionsSheet deal={deal} onClose={() => setDeal(null)} /> : null}
    </DealActionsContext.Provider>
  );
}

function QuickActionsSheet({ deal, onClose }: { deal: Deal; onClose: () => void }) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const toast = useToast();
  const { isSaved, toggle } = useSaved();
  const [alertMode, setAlertMode] = useState(false);
  const [target, setTarget] = useState(String(defaultAlertTarget(deal.price) || ''));
  const [busy, setBusy] = useState(false);
  const saved = isSaved(deal.id);
  const live = !deal.status || deal.status === 'live';

  const submitAlert = async () => {
    const n = Number(target.replace(/[^\d.]/g, ''));
    if (!n || n <= 0) {
      toast('Enter a target price.', 'err');
      return;
    }
    setBusy(true);
    try {
      const alert = await createPriceAlert(deal.id, n);
      haptic.success();
      toast(alertCreatedMessage(alert, n), 'ok', 5000);
      onClose();
    } catch (e) {
      haptic.error();
      toast(alertErrorMessage(e), 'err');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Modal visible transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <Pressable accessibilityRole="button" accessibilityLabel="Close" onPress={onClose} style={{ flex: 1, backgroundColor: t.c.overlay }} />
      <KeyboardAvoidingView behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
        <View
          style={{
            backgroundColor: t.c.surface,
            borderTopLeftRadius: t.r.xl,
            borderTopRightRadius: t.r.xl,
            paddingTop: 10,
            paddingHorizontal: 16,
            paddingBottom: insets.bottom + 16,
            gap: 6,
          }}
        >
          <View style={{ alignSelf: 'center', width: 40, height: 4, borderRadius: 2, backgroundColor: t.c.borderStrong, marginBottom: 8 }} />
          <Text numberOfLines={2} accessibilityRole="header" style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>
            {deal.title}
          </Text>
          <Text style={{ color: t.c.text2, fontSize: t.f.sm, marginBottom: 6 }}>
            {money(deal.price)}
            {deal.discount_pct >= 5 ? ` · ${deal.discount_pct}% off` : ''}
          </Text>
          {alertMode ? (
            <View style={{ gap: 10 }}>
              <Text style={{ color: t.c.text2, fontSize: t.f.sm }}>🔔 Notify me when the price drops below</Text>
              <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                <View
                  style={{
                    flex: 1,
                    minHeight: 48,
                    flexDirection: 'row',
                    alignItems: 'center',
                    paddingHorizontal: 14,
                    borderRadius: t.r.sm,
                    borderWidth: 1,
                    borderColor: t.c.accent,
                    backgroundColor: t.c.surface2,
                  }}
                >
                  <Text style={{ color: t.c.text2, fontSize: t.f.base, fontWeight: '700' }}>₹</Text>
                  <TextInput
                    value={target}
                    onChangeText={setTarget}
                    keyboardType="number-pad"
                    autoFocus
                    accessibilityLabel="Alert me below this price"
                    selectionColor={t.c.accent}
                    onSubmitEditing={submitAlert}
                    style={{ flex: 1, color: t.c.text, fontSize: t.f.base, paddingHorizontal: 6 }}
                  />
                </View>
                <Button title="Notify me" loading={busy} onPress={submitAlert} />
              </View>
            </View>
          ) : (
            <>
              <ActionRow
                label={saved ? 'Remove from saved' : 'Save deal'}
                leading={<HeartGlyph on={saved} size={20} color={t.c.text2} fill="#ef4444" />}
                onPress={() => {
                  if (toggle(deal)) haptic.success();
                  onClose();
                }}
              />
              <ActionRow
                label="Share"
                icon="share"
                onPress={() => {
                  onClose();
                  void shareDeal(deal);
                }}
              />
              {live && deal.price ? (
                <ActionRow label="Price-drop alert" icon="bell" onPress={() => setAlertMode(true)} />
              ) : null}
            </>
          )}
        </View>
      </KeyboardAvoidingView>
    </Modal>
  );
}

function ActionRow({ label, icon, leading, onPress }: { label: string; icon?: IconName; leading?: React.ReactNode; onPress: () => void }) {
  const t = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      onPress={onPress}
      android_ripple={{ color: t.c.accentSoft }}
      style={({ pressed }) => ({
        minHeight: 52,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 14,
        paddingHorizontal: 6,
        borderRadius: t.r.sm,
        backgroundColor: pressed ? t.c.surface2 : 'transparent',
      })}
    >
      {leading ?? (icon ? <Icon name={icon} size={20} color={t.c.text2} /> : null)}
      <Text style={{ color: t.c.text, fontSize: t.f.md, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}
