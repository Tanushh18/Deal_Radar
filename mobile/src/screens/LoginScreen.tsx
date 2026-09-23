import { useNavigation } from '@react-navigation/native';
import React, { useEffect, useRef, useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  Text,
  TextInput,
  View,
  useWindowDimensions,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { api, errorMessage, type User } from '../api';
import {
  Banner,
  BrandMark,
  Button,
  Card,
  Field,
  Icon,
  Txt,
  haptic,
  useHideNativeHeader,
  useServerUrl,
} from '../components';
import { onSignedIn } from '../native/session';
import { useTheme } from '../theme';
import type { RootNav } from './types';

type Step = 'phone' | 'code' | 'password';

const PITCH: [string, string][] = [
  ['Search in plain words', '“women kurta” finds kurti, anarkali, ethnic sets.'],
  ['Cross-channel dedup', 'one product, one card, with a “seen in N channels” badge.'],
  ['Price history', 'flags a genuine all-time low and catches inflated MRPs.'],
  ['Live-link checks', 'dead and out-of-stock deals drop out automatically.'],
  ['Alerts', 'saved searches ping you in your own Telegram Saved Messages.'],
];

export function LoginScreen() {
  useHideNativeHeader();
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation<RootNav>();
  const server = useServerUrl();
  const { width } = useWindowDimensions();

  const [step, setStep] = useState<Step>('phone');
  const [phone, setPhone] = useState('');
  const [sentTo, setSentTo] = useState('');
  const [code, setCode] = useState('');
  const [password, setPassword] = useState('');
  const [loginId, setLoginId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notConfigured, setNotConfigured] = useState(false);

  const codeRef = useRef<TextInput>(null);
  const passwordRef = useRef<TextInput>(null);

  useEffect(() => {
    const ctrl = new AbortController();
    api.auth
      .config({ signal: ctrl.signal })
      .then((c) => setNotConfigured(!c.telegram_configured))
      .catch(() => {});
    return () => ctrl.abort();
  }, []);

  const goStep = (s: Step) => {
    setError(null);
    setStep(s);
    setTimeout(() => (s === 'code' ? codeRef : s === 'password' ? passwordRef : null)?.current?.focus(), 250);
  };

  const finish = async (user: User) => {
    haptic.success();
    navigation.reset({ index: 0, routes: [{ name: 'Main' }] });
    // Not awaited: it may sit on the notification-permission prompt.
    void onSignedIn(user);
  };

  const run = async (fn: () => Promise<void>) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      haptic.error();
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  const sendCode = () =>
    run(async () => {
      const res = await api.auth.sendCode(phone.trim());
      setLoginId(res.login_id);
      setSentTo(res.phone || phone.trim());
      goStep('code');
    });

  const verifyCode = () =>
    run(async () => {
      if (!loginId) return goStep('phone');
      const res = await api.auth.verifyCode(loginId, code.trim());
      if (res.status === 'password_required') goStep('password');
      else await finish(res.user);
    });

  const verifyPassword = () =>
    run(async () => {
      if (!loginId) return goStep('phone');
      const res = await api.auth.verifyPassword(loginId, password);
      await finish(res.user);
    });

  const wide = width >= 720;

  return (
    <KeyboardAvoidingView style={{ flex: 1, backgroundColor: t.c.bg }} behavior={Platform.OS === 'ios' ? 'padding' : undefined}>
      <ScrollView
        keyboardShouldPersistTaps="handled"
        contentContainerStyle={{
          paddingTop: insets.top + 24,
          paddingBottom: insets.bottom + 32,
          paddingHorizontal: 20,
          gap: 22,
          maxWidth: wide ? 560 : undefined,
          width: '100%',
          alignSelf: 'center',
        }}
      >
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <BrandMark size={46} />
          <Text style={{ fontSize: 24, fontWeight: '800', letterSpacing: -0.8, color: t.c.text }}>
            Deal<Text style={{ color: t.c.accent }}>Radar</Text>
          </Text>
        </View>

        <Txt variant="title" style={{ fontSize: 25, lineHeight: 31 }}>
          Every deal from your Telegram channels. Searchable, de-duplicated, ranked.
        </Txt>

        <Card style={{ gap: 14, padding: 18, borderRadius: t.r.lg }}>
          {notConfigured ? (
            <Banner kind="warn" title="Server not configured.">
              TELEGRAM_API_ID and TELEGRAM_API_HASH are missing, so sign-in is disabled. Add them in your
              environment and redeploy.
            </Banner>
          ) : null}

          {step === 'phone' ? (
            <>
              <Txt variant="h2">Sign in with Telegram</Txt>
              <Txt variant="muted">
                We send a login code to your Telegram app — the same one you use to log in on a new device.
              </Txt>
              <Field
                label="Phone number"
                value={phone}
                onChangeText={setPhone}
                placeholder="+91 98765 43210"
                keyboardType="phone-pad"
                autoComplete="tel"
                textContentType="telephoneNumber"
                returnKeyType="send"
                editable={!notConfigured}
                onSubmitEditing={() => phone.trim().length >= 6 && sendCode()}
              />
              <Button
                title="Send login code"
                loading={busy}
                disabled={notConfigured || phone.trim().length < 6}
                onPress={sendCode}
              />
              <View style={{ flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 6 }}>
                <Icon name="lock" size={14} color={t.c.good} />
                <Text style={{ color: t.c.text2, fontSize: t.f.sm, fontWeight: '600' }}>Secure Telegram authentication</Text>
              </View>
              <Txt variant="fine">
                Your Telegram session is encrypted before storage and is used only to read the channels you choose.
                Your code and 2FA password are never stored.
              </Txt>
            </>
          ) : null}

          {step === 'code' ? (
            <>
              <Txt variant="h2">Enter your code</Txt>
              <Txt variant="muted">
                Telegram sent a code to <Text style={{ fontWeight: '700', color: t.c.text }}>{sentTo}</Text>. Check your
                Telegram app, not SMS.
              </Txt>
              <Field
                ref={codeRef}
                label="Login code"
                value={code}
                onChangeText={(v) => setCode(v.replace(/\D/g, ''))}
                placeholder="12345"
                keyboardType="number-pad"
                autoComplete="one-time-code"
                textContentType="oneTimeCode"
                maxLength={12}
                returnKeyType="done"
                inputStyle={{ fontSize: 24, letterSpacing: 8, fontWeight: '700', textAlign: 'center' }}
                onSubmitEditing={() => code.length >= 3 && verifyCode()}
              />
              <Button title="Verify" loading={busy} disabled={code.length < 3} onPress={verifyCode} />
              <Button title="Use a different number" variant="ghost" onPress={() => goStep('phone')} />
            </>
          ) : null}

          {step === 'password' ? (
            <>
              <Txt variant="h2">Two-step verification</Txt>
              <Txt variant="muted">Your account has a cloud password. Enter it to finish signing in.</Txt>
              <Field
                ref={passwordRef}
                label="Password"
                value={password}
                onChangeText={setPassword}
                secureTextEntry
                autoComplete="current-password"
                textContentType="password"
                returnKeyType="go"
                onSubmitEditing={() => password && verifyPassword()}
              />
              <Button title="Sign in" loading={busy} disabled={!password} onPress={verifyPassword} />
            </>
          ) : null}

          {error ? <Banner kind="error">{error}</Banner> : null}
        </Card>

        <View style={{ gap: 10 }}>
          {PITCH.map(([b, rest]) => (
            <View key={b} style={{ flexDirection: 'row', gap: 10 }}>
              <View style={{ paddingTop: 2 }}>
                <Icon name="check" size={16} color={t.c.good} />
              </View>
              <Text style={{ flex: 1, color: t.c.text2, fontSize: t.f.md, lineHeight: 21 }}>
                <Text style={{ color: t.c.text, fontWeight: '700' }}>{b}</Text> — {rest}
              </Text>
            </View>
          ))}
          <Txt variant="fine" style={{ marginTop: 4 }}>
            Stored in Google Sheets · deals kept 4 days or until they go dead
          </Txt>
        </View>

        {server ? (
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
            <Icon name="server" size={15} color={t.c.text3} />
            <Text numberOfLines={1} style={{ flex: 1, color: t.c.text3, fontSize: t.f.xs }}>
              {server}
            </Text>
            <Button title="Change server" size="sm" variant="ghost" onPress={() => navigation.navigate('Setup')} />
          </View>
        ) : null}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}
