import { useState } from 'react';
import { Pressable, View } from 'react-native';

import { saveInterests, INTEREST_OPTIONS } from '../native/interests';
import { useTheme } from '../theme';
import { Button, Txt } from './ui';
import { haptic } from './native';

/**
 * First-open "what are you shopping for?" screen. The answer stays on this
 * device and is sent with every deals request, so the server leads the feed
 * with those picks instead of its own default. "Show me everything" = neutral.
 */
export function InterestsPicker({ onDone, initial = [] }: { onDone: () => void; initial?: string[] }) {
  const t = useTheme();
  const [picked, setPicked] = useState<string[]>(initial);

  const toggle = (key: string) => {
    haptic.success();
    setPicked((p) => (p.includes(key) ? p.filter((k) => k !== key) : [...p, key]));
  };
  const finish = async (keys: string[]) => {
    await saveInterests(keys);
    onDone();
  };

  return (
    <View style={{ flex: 1, backgroundColor: t.c.overlay, justifyContent: 'center', padding: 24 }}>
      <View
        style={{
          backgroundColor: t.c.surface,
          borderRadius: 24,
          padding: 24,
          gap: 14,
          borderWidth: 1,
          borderColor: t.c.border,
        }}
      >
        <Txt variant="h2" style={{ textAlign: 'center' }}>What are you shopping for?</Txt>
        <Txt variant="muted" style={{ textAlign: 'center' }}>
          Pick one or more — we'll show those deals first. You can change this anytime in Settings.
        </Txt>
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8, justifyContent: 'center' }}>
          {INTEREST_OPTIONS.map((o) => {
            const on = picked.includes(o.key);
            return (
              <Pressable
                key={o.key}
                accessibilityRole="button"
                accessibilityState={{ selected: on }}
                onPress={() => toggle(o.key)}
                style={{
                  paddingHorizontal: 14,
                  paddingVertical: 9,
                  borderRadius: 999,
                  borderWidth: 1,
                  borderColor: on ? t.c.accent : t.c.border,
                  backgroundColor: on ? t.c.accent : t.c.surface2,
                }}
              >
                <Txt color={on ? t.c.accentText : t.c.text} weight="600">{o.label}</Txt>
              </Pressable>
            );
          })}
        </View>
        <Button title="Show my deals" disabled={!picked.length} onPress={() => finish(picked)} />
        <Button title="Show me everything" variant="soft" onPress={() => finish([])} />
      </View>
    </View>
  );
}
