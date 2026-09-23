import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { Image } from 'expo-image';

import { makeStyles, useTheme } from '../theme';

export function ConnectingView({ server }: { server: string }) {
  const theme = useTheme();
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Image source={require('../../assets/splash-icon.png')} style={styles.logo} contentFit="contain" />
      <ActivityIndicator color={theme.c.accent} size="large" style={{ marginTop: 28 }} />
      <Text style={styles.detail}>Connecting to {server}…</Text>
      <Text style={styles.hint}>A sleeping free-tier server can take up to a minute to wake.</Text>
    </View>
  );
}

/** Offline / server-down state: never a dead end. */
export function OfflineView({
  server,
  message,
  onRetry,
  onChangeServer,
}: {
  server: string;
  message: string;
  onRetry: () => void;
  onChangeServer: () => void;
}) {
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Text style={styles.title}>Can't reach DealRadar</Text>
      <Text style={styles.detail}>{message}</Text>
      <Text style={styles.hint}>{server}</Text>
      <Pressable testID="retry" onPress={onRetry} style={({ pressed }) => [styles.primary, pressed && { opacity: 0.8 }]}>
        <Text style={styles.primaryText}>Try again</Text>
      </Pressable>
      <Pressable testID="change-server" onPress={onChangeServer} style={styles.secondary}>
        <Text style={styles.secondaryText}>Change server</Text>
      </Pressable>
    </View>
  );
}

const useStyles = makeStyles(({ c }) => ({
  root: { flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center', padding: 32 },
  logo: { width: 96, height: 96, borderRadius: 24 },
  title: { color: c.text, fontSize: 22, fontWeight: '700', marginBottom: 10 },
  detail: { color: c.text2, fontSize: 15, textAlign: 'center', marginTop: 16, lineHeight: 21 },
  hint: { color: c.text3, fontSize: 13, textAlign: 'center', marginTop: 8 },
  primary: {
    marginTop: 28,
    backgroundColor: c.accent,
    borderRadius: 12,
    paddingVertical: 13,
    paddingHorizontal: 36,
  },
  primaryText: { color: c.accentText, fontSize: 16, fontWeight: '700' },
  secondary: { marginTop: 8, paddingVertical: 12, paddingHorizontal: 24 },
  secondaryText: { color: c.accent, fontSize: 15, fontWeight: '600' },
}));
