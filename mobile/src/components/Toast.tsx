import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { useTheme } from '../theme';
import { Icon } from './Icon';

type Kind = 'ok' | 'err' | 'info';
type ToastItem = { id: number; message: string; kind: Kind };

const ToastContext = createContext<(message: string, kind?: Kind, ms?: number) => void>(() => {});

export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);

  const dismiss = useCallback((id: number) => setItems((l) => l.filter((x) => x.id !== id)), []);

  const show = useCallback(
    (message: string, kind: Kind = 'info', ms = 3800) => {
      const id = ++seq.current;
      setItems((l) => [...l.slice(-2), { id, message, kind }]);
      AccessibilityInfo.announceForAccessibility(message);
      setTimeout(() => dismiss(id), ms);
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={show}>
      {children}
      <ToastStack items={items} onDismiss={dismiss} />
    </ToastContext.Provider>
  );
}

function ToastStack({ items, onDismiss }: { items: ToastItem[]; onDismiss: (id: number) => void }) {
  const insets = useSafeAreaInsets();
  return (
    <View
      pointerEvents="box-none"
      style={{ position: 'absolute', left: 12, right: 12, bottom: insets.bottom + 76, gap: 8, alignItems: 'center' }}
    >
      {items.map((it) => (
        <ToastView key={it.id} item={it} onDismiss={() => onDismiss(it.id)} />
      ))}
    </View>
  );
}

function ToastView({ item, onDismiss }: { item: ToastItem; onDismiss: () => void }) {
  const t = useTheme();
  const anim = useRef(new Animated.Value(0)).current;
  useEffect(() => {
    Animated.spring(anim, { toValue: 1, useNativeDriver: true, damping: 18, stiffness: 220 }).start();
  }, [anim]);
  const color = item.kind === 'ok' ? t.c.good : item.kind === 'err' ? t.c.hot : t.c.accent;
  const icon = item.kind === 'ok' ? 'check' : item.kind === 'err' ? 'alert' : 'info';
  return (
    <Animated.View
      style={{
        maxWidth: 520,
        width: '100%',
        opacity: anim,
        transform: [{ translateY: anim.interpolate({ inputRange: [0, 1], outputRange: [16, 0] }) }],
      }}
    >
      <Pressable
        accessibilityRole="alert"
        accessibilityLabel={item.message}
        accessibilityHint="Tap to dismiss"
        onPress={onDismiss}
        style={{
          flexDirection: 'row',
          alignItems: 'center',
          gap: 10,
          paddingHorizontal: 14,
          paddingVertical: 12,
          borderRadius: t.r.sm,
          backgroundColor: t.dark ? t.c.surface3 : '#111827',
          borderWidth: 1,
          borderColor: t.dark ? t.c.borderStrong : '#111827',
          elevation: 8,
          shadowColor: '#000',
          shadowOpacity: 0.25,
          shadowRadius: 12,
          shadowOffset: { width: 0, height: 6 },
        }}
      >
        <Icon name={icon} size={17} color={color} />
        <Text style={{ flex: 1, color: '#f3f6fb', fontSize: t.f.sm, lineHeight: 19, fontWeight: '600' }}>
          {item.message}
        </Text>
      </Pressable>
    </Animated.View>
  );
}
