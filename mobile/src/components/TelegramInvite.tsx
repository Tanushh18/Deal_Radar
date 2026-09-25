import AsyncStorage from '@react-native-async-storage/async-storage';
import { useEffect, useState } from 'react';
import { Linking, Modal, Pressable, Text, View } from 'react-native';

import { api } from '../api';
import { useTheme } from '../theme';
import { Button, Txt } from './ui';
import { haptic, openExternal } from './native';

const JOINED_KEY = 'dr-tg-joined';
const TELEGRAM_BLUE = '#229ED9';

type Channel = { url: string; username: string };

/**
 * "Join our Telegram channel" popup, shown every time the app opens until the
 * visitor taps Join. Join opens the channel straight in the Telegram app
 * (tg://resolve), where they're one tap from joining; t.me in the browser is
 * the fallback when Telegram isn't installed. The channel comes from the
 * server (/api/auth/config), so it can change without a new build.
 */
export function TelegramInvite() {
  const t = useTheme();
  const [channel, setChannel] = useState<Channel | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        if (await AsyncStorage.getItem(JOINED_KEY)) return;
        const config = await api.auth.config();
        const ch = config.telegram_channel;
        if (cancelled || !ch?.url) return;
        setChannel(ch);
        // Let the deals render first so the popup doesn't greet a blank screen.
        setTimeout(() => !cancelled && setVisible(true), 1500);
      } catch {
        /* the popup is a nicety — never block or break the app over it */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const close = () => setVisible(false);

  const join = async () => {
    if (!channel) return;
    haptic.success();
    AsyncStorage.setItem(JOINED_KEY, String(Date.now())).catch(() => {});
    setVisible(false);
    const direct = channel.username ? `tg://resolve?domain=${encodeURIComponent(channel.username)}` : null;
    // No canOpenURL check: Android 11+ answers false for undeclared schemes
    // even when Telegram is installed. openURL itself throws if nothing can.
    if (direct) {
      try {
        await Linking.openURL(direct);
        return;
      } catch {
        /* Telegram not installed — fall through to the web link */
      }
    }
    await openExternal(channel.url);
  };

  if (!channel) return null;
  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={close} statusBarTranslucent>
      <Pressable
        accessibilityLabel="Close"
        onPress={close}
        style={{ flex: 1, backgroundColor: 'rgba(3,7,15,0.6)', justifyContent: 'center', padding: 24 }}
      >
        <Pressable
          // Taps inside the card must not fall through to the backdrop's close.
          onPress={() => {}}
          style={{
            backgroundColor: t.c.surface,
            borderRadius: 24,
            padding: 24,
            paddingTop: 32,
            gap: 12,
            borderWidth: 1,
            borderColor: t.c.border,
          }}
        >
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Close"
            hitSlop={12}
            onPress={close}
            style={{
              position: 'absolute',
              top: 12,
              right: 12,
              width: 36,
              height: 36,
              borderRadius: 18,
              alignItems: 'center',
              justifyContent: 'center',
              backgroundColor: t.c.surface2,
            }}
          >
            <Text style={{ color: t.c.text, fontSize: 16, fontWeight: '700' }}>✕</Text>
          </Pressable>
          <View
            style={{
              alignSelf: 'center',
              width: 64,
              height: 64,
              borderRadius: 32,
              backgroundColor: TELEGRAM_BLUE,
              alignItems: 'center',
              justifyContent: 'center',
            }}
          >
            <Text style={{ fontSize: 30 }}>✈️</Text>
          </View>
          <Txt variant="h2" style={{ textAlign: 'center' }}>
            Get the crazy deals first
          </Txt>
          <Txt variant="muted" style={{ textAlign: 'center' }}>
            Verified loot deals — women's accessories, fashion and more — land on our Telegram channel
            {channel.username ? ` @${channel.username}` : ''} seconds after they go live, before anywhere else.
          </Txt>
          <Button title="Join on Telegram" onPress={join} style={{ backgroundColor: TELEGRAM_BLUE }} block />
          <Button title="Maybe later" variant="soft" onPress={close} block />
        </Pressable>
      </Pressable>
    </Modal>
  );
}
