import React from 'react';
import { FlatList, Modal, Pressable, Text, View, useWindowDimensions } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '../theme';
import { Icon } from './Icon';
import { haptic } from './native';

export type Option = { value: string; label: string; count?: number; leading?: string };

/** Native bottom-sheet picker used where the web has a <select>. */
export function OptionSheet({
  visible,
  title,
  options,
  value,
  onSelect,
  onClose,
}: {
  visible: boolean;
  title: string;
  options: Option[];
  value: string;
  onSelect: (value: string) => void;
  onClose: () => void;
}) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const { height } = useWindowDimensions();
  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose} statusBarTranslucent>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="Close"
        onPress={onClose}
        style={{ flex: 1, backgroundColor: t.c.overlay }}
      />
      <View
        style={{
          maxHeight: height * 0.7,
          backgroundColor: t.c.surface,
          borderTopLeftRadius: t.r.xl,
          borderTopRightRadius: t.r.xl,
          paddingBottom: insets.bottom + 8,
          borderWidth: 1,
          borderColor: t.c.border,
        }}
      >
        <View style={{ alignItems: 'center', paddingTop: 8 }}>
          <View style={{ width: 40, height: 4, borderRadius: 2, backgroundColor: t.c.borderStrong }} />
        </View>
        <Text
          accessibilityRole="header"
          style={{ color: t.c.text, fontSize: t.f.lg, fontWeight: '700', paddingHorizontal: 20, paddingVertical: 14 }}
        >
          {title}
        </Text>
        <FlatList
          data={options}
          keyExtractor={(o) => o.value || '__all'}
          renderItem={({ item }) => {
            const on = item.value === value;
            return (
              <Pressable
                accessibilityRole="radio"
                accessibilityState={{ selected: on }}
                accessibilityLabel={item.count != null ? `${item.label}, ${item.count}` : item.label}
                android_ripple={{ color: t.c.accentSoft }}
                onPress={() => {
                  haptic.select();
                  onSelect(item.value);
                  onClose();
                }}
                style={{
                  minHeight: 52,
                  paddingHorizontal: 20,
                  flexDirection: 'row',
                  alignItems: 'center',
                  gap: 12,
                  backgroundColor: on ? t.c.accentSoft : 'transparent',
                }}
              >
                {item.leading ? <Text style={{ fontSize: 18 }}>{item.leading}</Text> : null}
                <Text style={{ flex: 1, color: on ? t.c.accent : t.c.text, fontSize: t.f.md, fontWeight: on ? '700' : '500' }}>
                  {item.label}
                </Text>
                {item.count != null ? (
                  <Text style={{ color: t.c.text3, fontSize: t.f.sm, fontWeight: '600' }}>{item.count}</Text>
                ) : null}
                {on ? <Icon name="check" size={18} color={t.c.accent} /> : null}
              </Pressable>
            );
          }}
        />
      </View>
    </Modal>
  );
}

/** A tappable field that opens an OptionSheet — the native stand-in for <select>. */
export function SelectField({
  label,
  title,
  value,
  options,
  onChange,
  placeholder = 'Any',
}: {
  label: string;
  title?: string;
  value: string;
  options: Option[];
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  const t = useTheme();
  const [open, setOpen] = React.useState(false);
  const current = options.find((o) => o.value === value);
  return (
    <View style={{ gap: 6 }}>
      <Text style={{ color: t.c.text2, fontSize: t.f.sm, fontWeight: '600' }}>{label}</Text>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${label}: ${current?.label ?? placeholder}`}
        accessibilityHint="Opens a list of choices"
        onPress={() => setOpen(true)}
        style={{
          minHeight: 48,
          paddingHorizontal: 14,
          borderRadius: t.r.sm,
          borderWidth: 1,
          borderColor: t.c.border,
          backgroundColor: t.c.surface2,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 8,
        }}
      >
        {current?.leading ? <Text>{current.leading}</Text> : null}
        <Text numberOfLines={1} style={{ flex: 1, color: current ? t.c.text : t.c.text3, fontSize: t.f.base }}>
          {current?.label ?? placeholder}
        </Text>
        <Icon name="chevDown" size={18} color={t.c.text3} />
      </Pressable>
      <OptionSheet
        visible={open}
        title={title ?? label}
        options={options}
        value={value}
        onSelect={onChange}
        onClose={() => setOpen(false)}
      />
    </View>
  );
}
