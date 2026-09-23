import { BlurTargetView, BlurView } from 'expo-blur';
import { LinearGradient } from 'expo-linear-gradient';
import React, { memo, useEffect, useRef, useState } from 'react';
import {
  AccessibilityInfo,
  Animated,
  Easing,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  View,
  useWindowDimensions,
  type LayoutChangeEvent,
  type StyleProp,
  type ViewStyle,
} from 'react-native';

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

/** Two slow-drifting colour blobs behind the content, like the website's backdrop. */
export const AnimatedBackdrop = memo(function AnimatedBackdrop() {
  const t = useTheme();
  const reduce = useReduceMotion();
  const { width, height } = useWindowDimensions();
  const drift = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reduce) {
      drift.setValue(0.5);
      return;
    }
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(drift, { toValue: 1, duration: 14000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
        Animated.timing(drift, { toValue: 0, duration: 14000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [reduce, drift]);

  const blob = Math.max(width, 360) * 1.1;
  const a = t.dark ? 0.22 : 0.14;
  return (
    <View pointerEvents="none" style={[StyleSheet.absoluteFill, { backgroundColor: t.c.bg, overflow: 'hidden' }]}>
      <Animated.View
        style={{
          position: 'absolute',
          width: blob,
          height: blob,
          top: -blob * 0.45,
          left: -blob * 0.35,
          opacity: a * 4,
          transform: [
            { translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, width * 0.25] }) },
            { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, height * 0.08] }) },
          ],
        }}
      >
        <LinearGradient
          colors={[t.dark ? 'rgba(91,147,247,0.30)' : 'rgba(37,99,235,0.16)', 'transparent']}
          start={{ x: 0.5, y: 0.5 }}
          end={{ x: 1, y: 1 }}
          style={{ flex: 1, borderRadius: blob / 2 }}
        />
      </Animated.View>
      <Animated.View
        style={{
          position: 'absolute',
          width: blob,
          height: blob,
          top: height * 0.35,
          right: -blob * 0.5,
          opacity: a * 3.5,
          transform: [
            { translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -width * 0.2] }) },
            { translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -height * 0.1] }) },
          ],
        }}
      >
        <LinearGradient
          colors={[t.dark ? 'rgba(34,211,238,0.22)' : 'rgba(6,182,212,0.12)', 'transparent']}
          start={{ x: 0.5, y: 0.5 }}
          end={{ x: 0, y: 1 }}
          style={{ flex: 1, borderRadius: blob / 2 }}
        />
      </Animated.View>
    </View>
  );
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

/** Content the glass header blurs. Android blur needs an explicit target sibling. */
export function GlassContent({ target, children, style }: { target: BlurTargetRef; children: React.ReactNode; style?: StyleProp<ViewStyle> }) {
  return (
    <BlurTargetView ref={target} style={[{ flex: 1 }, style]}>
      {children}
    </BlurTargetView>
  );
}

/** Frosted bar pinned over the top of a GlassContent. */
export function GlassHeader({
  target,
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
    <View onLayout={onLayout} style={[{ position: 'absolute', top: 0, left: 0, right: 0, zIndex: 20 }, style]}>
      <BlurView
        blurTarget={target}
        blurMethod={Platform.OS === 'android' && target ? 'dimezisBlurViewSdk31Plus' : 'none'}
        intensity={t.dark ? 60 : 70}
        tint={t.dark ? 'dark' : 'light'}
        style={StyleSheet.absoluteFill}
      />
      <View
        style={[
          StyleSheet.absoluteFill,
          {
            backgroundColor: t.dark ? 'rgba(8,11,18,0.62)' : 'rgba(247,248,250,0.72)',
            borderBottomWidth: StyleSheet.hairlineWidth,
            borderBottomColor: t.c.border,
          },
        ]}
      />
      {children}
    </View>
  );
}

/** For INFRA: `tabBarBackground: () => <GlassTabBarBackground />` with an absolute tab bar. */
export function GlassTabBarBackground() {
  const t = useTheme();
  return (
    <View style={StyleSheet.absoluteFill}>
      <BlurView intensity={t.dark ? 60 : 70} tint={t.dark ? 'dark' : 'light'} style={StyleSheet.absoluteFill} />
      <View
        style={[
          StyleSheet.absoluteFill,
          {
            backgroundColor: t.dark ? 'rgba(8,11,18,0.82)' : 'rgba(255,255,255,0.86)',
            borderTopWidth: StyleSheet.hairlineWidth,
            borderTopColor: t.c.border,
          },
        ]}
      />
    </View>
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
          {count} new {count === 1 ? 'deal' : 'deals'} — tap to see
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
        borderWidth: 1,
        borderColor: t.c.warn,
      }}
    >
      <Icon name="wifiOff" size={16} color={t.c.warn} />
      <Text style={{ flex: 1, color: t.c.warn, fontWeight: '700', fontSize: t.f.sm }}>{text}</Text>
    </View>
  );
}
