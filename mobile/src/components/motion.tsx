import React, { memo, useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Platform,
  Pressable,
  StyleSheet,
  View,
  useWindowDimensions,
  type LayoutChangeEvent,
  type StyleProp,
  type ViewStyle,
} from 'react-native';
import { Text } from './Text';

import { useTheme } from '../theme';
import { Icon } from './Icon';

let reduceMotionCached = false;

export function useReduceMotion(): boolean {
  const [reduce, setReduce] = useState(reduceMotionCached);
  useEffect(() => {
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled()
      .then((v) => {
        reduceMotionCached = v;
        if (alive) setReduce(v);
      })
      .catch(() => {});
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', (v) => {
      reduceMotionCached = v;
      setReduce(v);
    });
    return () => {
      alive = false;
      sub.remove();
    };
  }, []);
  return reduce;
}

/** Plain page background. (The old drifting colour blobs are gone on purpose.) */
export const AnimatedBackdrop = memo(function AnimatedBackdrop() {
  const t = useTheme();
  return <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: t.c.bg }]} />;
});

/**
 * Fades + lifts a list item in once. Only the first screenful is staggered so
 * paging in more results never waits on an animation.
 */
export const FadeInItem = memo(function FadeInItem({
  index,
  children,
  style,
}: {
  index: number;
  children: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const reduce = useReduceMotion();
  const skip = reduce || index > 11;
  const v = useRef(new Animated.Value(skip ? 1 : 0)).current;
  useEffect(() => {
    if (skip) return;
    Animated.timing(v, {
      toValue: 1,
      duration: 320,
      delay: index * 45,
      easing: Easing.out(Easing.cubic),
      useNativeDriver: true,
    }).start();
  }, [skip, index, v]);
  return (
    <Animated.View
      style={[
        style,
        { opacity: v, transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [14, 0] }) }] },
      ]}
    >
      {children}
    </Animated.View>
  );
});

export type BlurTargetRef = React.RefObject<View | null>;

export function useBlurTarget(): BlurTargetRef {
  return useRef<View | null>(null);
}

/** Wrapper the header sits over. Kept as a component so screens don't change shape. */
export function GlassContent({ children, style }: { target?: BlurTargetRef; children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  return <View style={[{ flex: 1 }, style]}>{children}</View>;
}

/** Solid header pinned over the top of a GlassContent, with a hairline under it. */
export function GlassHeader({
  children,
  onHeight,
  style,
}: {
  target?: BlurTargetRef;
  children: React.ReactNode;
  onHeight?: (h: number) => void;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  const onLayout = (e: LayoutChangeEvent) => onHeight?.(Math.round(e.nativeEvent.layout.height));
  return (
    <View
      onLayout={onLayout}
      style={[
        {
          position: 'absolute',
          top: 0,
          left: 0,
          right: 0,
          zIndex: 20,
          backgroundColor: t.c.bg,
          borderBottomWidth: StyleSheet.hairlineWidth,
          borderBottomColor: t.c.border,
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

/** Tab bar background: solid page colour and a hairline, no frosted glass. */
export function GlassTabBarBackground() {
  const t = useTheme();
  return (
    <View
      style={[
        StyleSheet.absoluteFill,
        { backgroundColor: t.c.bg, borderTopWidth: StyleSheet.hairlineWidth, borderTopColor: t.c.borderStrong },
      ]}
    />
  );
}

export function NewDealsPill({ count, top, onPress }: { count: number; top: number; onPress: () => void }) {
  const t = useTheme();
  const reduce = useReduceMotion();
  const v = useRef(new Animated.Value(0)).current;
  const visible = count > 0;
  useEffect(() => {
    if (reduce) {
      v.setValue(visible ? 1 : 0);
      return;
    }
    Animated.spring(v, { toValue: visible ? 1 : 0, useNativeDriver: true, damping: 16, stiffness: 200 }).start();
  }, [visible, reduce, v]);
  if (!visible) return null;
  return (
    <Animated.View
      pointerEvents="box-none"
      style={{
        position: 'absolute',
        top,
        left: 0,
        right: 0,
        alignItems: 'center',
        zIndex: 30,
        opacity: v,
        transform: [{ translateY: v.interpolate({ inputRange: [0, 1], outputRange: [-16, 0] }) }],
      }}
    >
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={`${count} new ${count === 1 ? 'deal' : 'deals'}. Tap to see`}
        accessibilityLiveRegion="polite"
        onPress={onPress}
        style={({ pressed }) => ({
          minHeight: 40,
          paddingHorizontal: 16,
          borderRadius: 999,
          flexDirection: 'row',
          alignItems: 'center',
          gap: 7,
          backgroundColor: t.c.accent,
          elevation: 6,
          shadowColor: '#000',
          shadowOpacity: 0.25,
          shadowRadius: 10,
          shadowOffset: { width: 0, height: 4 },
          opacity: pressed ? 0.85 : 1,
        })}
      >
        <Icon name="up" size={15} color={t.c.accentText} strokeWidth={2.6} />
        <Text maxFontSizeMultiplier={1.3} style={{ color: t.c.accentText, fontWeight: '800', fontSize: t.f.sm }}>
          {count} new {count === 1 ? 'deal' : 'deals'}
        </Text>
      </Pressable>
    </Animated.View>
  );
}

export function OfflineBanner({ text = 'Offline — showing saved results' }: { text?: string }) {
  const t = useTheme();
  return (
    <View
      accessibilityRole="alert"
      style={{
        flexDirection: 'row',
        alignItems: 'center',
        gap: 8,
        paddingHorizontal: 12,
        paddingVertical: 9,
        borderRadius: t.r.sm,
        backgroundColor: t.c.warnSoft,
      }}
    >
      <Icon name="wifiOff" size={16} color={t.c.warn} />
      <Text style={{ flex: 1, color: t.c.warn, fontWeight: '700', fontSize: t.f.sm }}>{text}</Text>
    </View>
  );
}
