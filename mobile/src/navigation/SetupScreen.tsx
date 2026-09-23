/**
 * First-run (and "Change server") screen: point the app at a DealRadar backend
 * and confirm it answers before committing to it. Port of SetupActivity.kt.
 */
import { useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Pressable,
  ScrollView,
  Text,
  TextInput,
  View,
} from 'react-native';
import { Image } from 'expo-image';
import { SafeAreaView } from 'react-native-safe-area-context';
import type { NativeStackScreenProps } from '@react-navigation/native-stack';

import { EMULATOR_HOST, getBaseUrl, LIVE_HOST, normalize, probeServer, saveBaseUrl } from '../native/config';
import { makeStyles, useTheme } from '../theme';
import type { RootStackParamList } from './types';

type Props = NativeStackScreenProps<RootStackParamList, 'Setup'> & {
  /** Called after the URL is saved; the root decides where to go next. */
  onSaved: (url: string) => void;
};

export default function SetupScreen({ onSaved, navigation, route }: Props) {
  const theme = useTheme();
  const styles = useStyles();
  const [url, setUrl] = useState(LIVE_HOST);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null);
  const ticker = useRef<ReturnType<typeof setInterval> | null>(null);
  const fromSettings = !!route.params?.fromSettings || navigation.canGoBack();

  useEffect(() => {
    getBaseUrl().then((saved) => saved && setUrl(saved));
    return () => {
      if (ticker.current) clearInterval(ticker.current);
    };
  }, []);

  const commit = async (value: string) => {
    const saved = await saveBaseUrl(value);
    onSaved(saved);
  };

  const testAndSave = async () => {
    const target = normalize(url);
    if (!target) {
      setStatus({ text: 'Enter your server address first.', error: true });
      return;
    }
    setUrl(target);
    setBusy(true);
    // A silent "Checking…" that sits for the whole timeout looks frozen.
    const startedAt = Date.now();
    const tick = () =>
      setStatus({
        text: `Checking server… a sleeping free-tier server can take up to a minute to wake.  (${Math.floor(
          (Date.now() - startedAt) / 1000,
        )}s)`,
        error: false,
      });
    tick();
    ticker.current = setInterval(tick, 1000);
    const problem = await probeServer(target);
    if (ticker.current) clearInterval(ticker.current);
    ticker.current = null;
    setBusy(false);
    if (problem == null) {
      setStatus({ text: 'Connected. Loading DealRadar…', error: false });
      await commit(target);
    } else {
      setStatus({ text: `${problem}\n\nTap “Save without testing” to use it regardless.`, error: true });
    }
  };

  const saveAnyway = async () => {
    const target = normalize(url);
    if (!target) {
      setStatus({ text: 'Enter your server address first.', error: true });
      return;
    }
    await commit(target);
  };

  return (
    <SafeAreaView style={styles.root} edges={['top', 'bottom', 'left', 'right']}>
      <KeyboardAvoidingView style={{ flex: 1 }} behavior="padding">
        <ScrollView contentContainerStyle={styles.scroll} keyboardShouldPersistTaps="handled">
          {fromSettings && (
            <Pressable onPress={() => navigation.goBack()} hitSlop={12} style={styles.back} accessibilityRole="button">
              <Text style={styles.backText}>‹ Back</Text>
            </Pressable>
          )}
          <Image source={require('../../assets/splash-icon.png')} style={styles.logo} contentFit="contain" />
          <Text style={styles.title}>Connect to your DealRadar server</Text>
          <Text style={styles.body}>
            Defaults to the live server. For a local one: 10.0.2.2 is your computer as seen from the
            emulator — not localhost.
          </Text>

          <Text style={styles.label}>Server address</Text>
          <TextInput
            testID="server-url"
            value={url}
            onChangeText={setUrl}
            editable={!busy}
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="url"
            returnKeyType="go"
            onSubmitEditing={testAndSave}
            placeholder={LIVE_HOST}
            placeholderTextColor={theme.c.text3}
            selectionColor={theme.c.accent}
            style={styles.input}
          />

          <View style={styles.chips}>
            <Chip label="Live server" onPress={() => setUrl(LIVE_HOST)} />
            <Chip label="Local (emulator)" onPress={() => setUrl(EMULATOR_HOST)} />
            <Chip label="Local (Wi-Fi)" onPress={() => setUrl('http://192.168.')} />
          </View>

          <Pressable
            testID="connect"
            onPress={testAndSave}
            disabled={busy}
            style={({ pressed }) => [styles.primary, (pressed || busy) && { opacity: 0.75 }]}
            accessibilityRole="button"
          >
            {busy ? <ActivityIndicator color={theme.c.accentText} /> : <Text style={styles.primaryText}>Test connection</Text>}
          </Pressable>
          <Pressable
            testID="save-anyway"
            onPress={saveAnyway}
            disabled={busy}
            style={({ pressed }) => [styles.secondary, pressed && { opacity: 0.7 }]}
            accessibilityRole="button"
          >
            <Text style={styles.secondaryText}>Save without testing</Text>
          </Pressable>

          {status && (
            <Text style={[styles.status, { color: status.error ? theme.c.hot : theme.c.good }]}>{status.text}</Text>
          )}
        </ScrollView>
      </KeyboardAvoidingView>
    </SafeAreaView>
  );
}

function Chip({ label, onPress }: { label: string; onPress: () => void }) {
  const theme = useTheme();
  const styles = useStyles();
  return (
    <Pressable onPress={onPress} style={({ pressed }) => [styles.chip, pressed && { borderColor: theme.c.accent }]}>
      <Text style={styles.chipText}>{label}</Text>
    </Pressable>
  );
}

const useStyles = makeStyles(({ c }) => ({
  root: { flex: 1, backgroundColor: c.bg },
  scroll: { padding: 24, paddingTop: 32 },
  back: { alignSelf: 'flex-start', marginBottom: 12 },
  backText: { color: c.accent, fontSize: 16 },
  logo: { width: 72, height: 72, borderRadius: 18, marginBottom: 20 },
  title: { color: c.text, fontSize: 24, fontWeight: '700', marginBottom: 8 },
  body: { color: c.text2, fontSize: 15, lineHeight: 21, marginBottom: 24 },
  label: { color: c.text2, fontSize: 13, fontWeight: '600', marginBottom: 6 },
  input: {
    backgroundColor: c.surface,
    borderColor: c.border,
    borderWidth: 1,
    borderRadius: 12,
    color: c.text,
    fontSize: 16,
    paddingHorizontal: 14,
    paddingVertical: 12,
  },
  chips: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12, marginBottom: 24 },
  chip: {
    borderColor: c.borderStrong,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 12,
    paddingVertical: 6,
    backgroundColor: c.surface2,
  },
  chipText: { color: c.text2, fontSize: 13 },
  primary: {
    backgroundColor: c.accent,
    borderRadius: 12,
    paddingVertical: 14,
    alignItems: 'center',
    minHeight: 50,
    justifyContent: 'center',
  },
  primaryText: { color: c.accentText, fontSize: 16, fontWeight: '700' },
  secondary: { paddingVertical: 14, alignItems: 'center' },
  secondaryText: { color: c.accent, fontSize: 15, fontWeight: '600' },
  status: { marginTop: 12, fontSize: 14, lineHeight: 20 },
}));
