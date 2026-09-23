import { ActivityIndicator, Pressable, Text, View } from 'react-native';
import { Image } from 'expo-image';

import { makeStyles, useTheme } from '../theme';

/** No server address, no raw error text — a visitor has no reason to see either. */
export function ConnectingView() {
  const theme = useTheme();
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Image source={require('../../assets/splash-icon.png')} style={styles.logo} contentFit="contain" />
      <ActivityIndicator color={theme.c.accent} size="large" style={{ marginTop: 28 }} />
      <Text style={styles.detail}>Finding today's deals…</Text>
    </View>
  );
}

/** Offline / server-down state: never a dead end. */
export function OfflineView({ onRetry }: { onRetry: () => void }) {
  const styles = useStyles();
  return (
    <View style={styles.root}>
      <Text style={styles.title}>Server not working</Text>
      <Text style={styles.detail}>We can't reach DealRadar right now. Please try again in a moment.</Text>
      <Pressable testID="retry" onPress={onRetry} style={({ pressed }) => [styles.primary, pressed && { opacity: 0.8 }]}>
        <Text style={styles.primaryText}>Try again</Text>
      </Pressable>
    </View>
  );
}

const useStyles = makeStyles(({ c }) => ({
  root: { flex: 1, backgroundColor: c.bg, alignItems: 'center', justifyContent: 'center', padding: 32 },
  logo: { width: 96, height: 96, borderRadius: 24 },
  title: { color: c.text, fontSize: 22, fontWeight: '700', marginBottom: 10 },
  detail: { color: c.text2, fontSize: 15, textAlign: 'center', marginTop: 16, lineHeight: 21 },
  primary: {
    marginTop: 28,
    backgroundColor: c.accent,
    borderRadius: 12,
    paddingVertical: 13,
    paddingHorizontal: 36,
  },
  primaryText: { color: c.accentText, fontSize: 16, fontWeight: '700' },
}));
