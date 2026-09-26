import type { TextStyle } from 'react-native';

/**
 * App typefaces: Bricolage Grotesque for everything, IBM Plex Mono for the
 * "receipt" bits (store names, times, coupon codes).
 *
 * Fonts ship inside the OTA bundle and are registered at runtime with
 * expo-font. Each weight is its own family name, so `fontFor()` maps a
 * fontWeight to the right file. If the native font loader is ever missing or
 * loading fails, `ready` stays false and every Text keeps the system font —
 * the app must never crash over typography.
 */

const SANS = {
  '400': 'BricolageGrotesque_400Regular',
  '500': 'BricolageGrotesque_500Medium',
  '600': 'BricolageGrotesque_600SemiBold',
  '700': 'BricolageGrotesque_700Bold',
  '800': 'BricolageGrotesque_800ExtraBold',
} as const;

const MONO = {
  '400': 'IBMPlexMono_400Regular',
  '500': 'IBMPlexMono_500Medium',
  '600': 'IBMPlexMono_600SemiBold',
} as const;

const FILES: Record<string, number> = {
  [SANS['400']]: require('@expo-google-fonts/bricolage-grotesque/400Regular/BricolageGrotesque_400Regular.ttf'),
  [SANS['500']]: require('@expo-google-fonts/bricolage-grotesque/500Medium/BricolageGrotesque_500Medium.ttf'),
  [SANS['600']]: require('@expo-google-fonts/bricolage-grotesque/600SemiBold/BricolageGrotesque_600SemiBold.ttf'),
  [SANS['700']]: require('@expo-google-fonts/bricolage-grotesque/700Bold/BricolageGrotesque_700Bold.ttf'),
  [SANS['800']]: require('@expo-google-fonts/bricolage-grotesque/800ExtraBold/BricolageGrotesque_800ExtraBold.ttf'),
  [MONO['400']]: require('@expo-google-fonts/ibm-plex-mono/400Regular/IBMPlexMono_400Regular.ttf'),
  [MONO['500']]: require('@expo-google-fonts/ibm-plex-mono/500Medium/IBMPlexMono_500Medium.ttf'),
  [MONO['600']]: require('@expo-google-fonts/ibm-plex-mono/600SemiBold/IBMPlexMono_600SemiBold.ttf'),
};

/** Use as `fontFamily: MONO_FAMILY` to get the mono face at any weight. */
export const MONO_FAMILY = 'monospace';

let ready = false;

export const fontsReady = (): boolean => ready;

function bucket(weight: TextStyle['fontWeight']): 400 | 500 | 600 | 700 | 800 {
  const n = weight === 'bold' ? 700 : weight === 'normal' || weight == null ? 400 : Number(weight) || 400;
  if (n >= 800) return 800;
  if (n >= 700) return 700;
  if (n >= 600) return 600;
  if (n >= 500) return 500;
  return 400;
}

/** Family name for a weight, or undefined when custom fonts aren't loaded. */
export function fontFor(weight?: TextStyle['fontWeight'], mono = false): string | undefined {
  if (!ready) return undefined;
  const w = bucket(weight);
  if (mono) return MONO[w >= 600 ? '600' : w >= 500 ? '500' : '400'];
  return SANS[String(w) as keyof typeof SANS];
}

let loading: Promise<void> | null = null;

/** Resolves once fonts are usable or definitely not (never rejects, never hangs past `timeoutMs`). */
export function loadAppFonts(timeoutMs = 2500): Promise<void> {
  if (!loading) {
    let timedOut = false;
    const attempt = (async () => {
      try {
        // Required lazily: if the native module were missing, this throws here
        // (caught) instead of at app start.
        const Font = require('expo-font') as typeof import('expo-font');
        await Font.loadAsync(FILES);
        // A late success is ignored: half the screen in one font and half in
        // another looks worse than the system font throughout.
        if (!timedOut) ready = true;
      } catch (e) {
        console.warn('[fonts] falling back to system font:', (e as Error)?.message ?? e);
      }
    })();
    const timeout = new Promise<void>((r) =>
      setTimeout(() => {
        timedOut = true;
        r();
      }, timeoutMs),
    );
    loading = Promise.race([attempt, timeout]);
  }
  return loading;
}
