import { useNavigation } from '@react-navigation/native';
import React, { useEffect, useRef } from 'react';
import {
  ActivityIndicator,
  Animated,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
  type PressableProps,
  type StyleProp,
  type TextInputProps,
  type TextStyle,
  type ViewStyle,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';

import { MIN_TOUCH, makeStyles, useTheme } from '../theme';
import { Icon, type IconName } from './Icon';

/* ---------------- Text ---------------- */

type TxtProps = React.ComponentProps<typeof Text> & {
  variant?: 'title' | 'h2' | 'h3' | 'body' | 'muted' | 'small' | 'fine' | 'label';
  color?: string;
  weight?: TextStyle['fontWeight'];
};

export function Txt({ variant = 'body', color, weight, style, ...rest }: TxtProps) {
  const s = useTextStyles();
  return (
    <Text
      maxFontSizeMultiplier={1.6}
      {...rest}
      style={[s[variant], color ? { color } : null, weight ? { fontWeight: weight } : null, style]}
    />
  );
}

const useTextStyles = makeStyles((t) => ({
  title: { fontSize: t.f.xxl, fontWeight: '800', color: t.c.text, letterSpacing: -0.6 },
  h2: { fontSize: t.f.xl, fontWeight: '700', color: t.c.text, letterSpacing: -0.4 },
  h3: { fontSize: t.f.base, fontWeight: '700', color: t.c.text, letterSpacing: -0.2 },
  body: { fontSize: t.f.md, color: t.c.text, lineHeight: 21 },
  muted: { fontSize: t.f.md, color: t.c.text2, lineHeight: 21 },
  small: { fontSize: t.f.sm, color: t.c.text2, lineHeight: 18 },
  fine: { fontSize: t.f.xs, color: t.c.text3, lineHeight: 17 },
  label: {
    fontSize: t.f.xs,
    color: t.c.text3,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.6,
  },
}));

/* ---------------- Buttons ---------------- */

type ButtonProps = Omit<PressableProps, 'style' | 'children'> & {
  title: string;
  variant?: 'primary' | 'soft' | 'ghost' | 'danger';
  size?: 'md' | 'sm';
  icon?: IconName;
  iconRight?: IconName;
  loading?: boolean;
  block?: boolean;
  style?: StyleProp<ViewStyle>;
};

export function Button({
  title,
  variant = 'primary',
  size = 'md',
  icon,
  iconRight,
  loading,
  block,
  disabled,
  style,
  ...rest
}: ButtonProps) {
  const t = useTheme();
  const bg = {
    primary: t.c.accent,
    soft: t.c.surface2,
    ghost: 'transparent',
    danger: t.c.hotSoft,
  }[variant];
  const fg = {
    primary: t.c.accentText,
    soft: t.c.text,
    ghost: t.c.text2,
    danger: t.c.hot,
  }[variant];
  const off = disabled || loading;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      accessibilityState={{ disabled: !!off, busy: !!loading }}
      disabled={off}
      android_ripple={{ color: t.c.accentSoft, borderless: false }}
      style={({ pressed }) => [
        {
          minHeight: size === 'md' ? 48 : MIN_TOUCH,
          paddingHorizontal: size === 'md' ? 18 : 14,
          borderRadius: t.r.sm,
          backgroundColor: bg,
          borderWidth: variant === 'soft' ? StyleSheet.hairlineWidth * 2 : 0,
          borderColor: t.c.border,
          flexDirection: 'row',
          alignItems: 'center',
          justifyContent: 'center',
          gap: 8,
          opacity: off ? 0.55 : 1,
          transform: [{ scale: pressed ? 0.97 : 1 }],
          overflow: 'hidden',
        },
        block && { alignSelf: 'stretch' },
        style,
      ]}
      {...rest}
    >
      {loading ? (
        <ActivityIndicator size="small" color={fg} />
      ) : icon ? (
        <Icon name={icon} size={17} color={fg} />
      ) : null}
      <Text
        maxFontSizeMultiplier={1.4}
        numberOfLines={1}
        style={{ color: fg, fontWeight: '700', fontSize: size === 'md' ? t.f.md : t.f.sm, flexShrink: 1 }}
      >
        {title}
      </Text>
      {iconRight && !loading ? <Icon name={iconRight} size={16} color={fg} /> : null}
    </Pressable>
  );
}

export function IconButton({
  name,
  label,
  onPress,
  color,
  size = 20,
  variant = 'plain',
  disabled,
  badge,
  style,
}: {
  name: IconName;
  label: string;
  onPress?: () => void;
  color?: string;
  size?: number;
  variant?: 'plain' | 'soft';
  disabled?: boolean;
  badge?: number;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: !!disabled }}
      hitSlop={6}
      disabled={disabled}
      onPress={onPress}
      android_ripple={{ color: t.c.accentSoft, borderless: true, radius: 24 }}
      style={({ pressed }) => [
        {
          width: MIN_TOUCH,
          height: MIN_TOUCH,
          borderRadius: t.r.sm,
          alignItems: 'center',
          justifyContent: 'center',
          backgroundColor: variant === 'soft' ? t.c.surface2 : 'transparent',
          borderWidth: variant === 'soft' ? 1 : 0,
          borderColor: t.c.border,
          opacity: disabled ? 0.5 : pressed ? 0.7 : 1,
        },
        style,
      ]}
    >
      <Icon name={name} size={size} color={color ?? t.c.text} />
      {badge ? (
        <View
          style={{
            position: 'absolute',
            top: 4,
            right: 4,
            minWidth: 17,
            height: 17,
            paddingHorizontal: 4,
            borderRadius: 9,
            backgroundColor: t.c.accent,
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          <Text maxFontSizeMultiplier={1.1} style={{ color: t.c.accentText, fontSize: 10.5, fontWeight: '800' }}>
            {badge > 99 ? '99+' : badge}
          </Text>
        </View>
      ) : null}
    </Pressable>
  );
}

/* ---------------- Chips / segmented ---------------- */

export function Chip({
  label,
  active,
  onPress,
  removable,
  count,
  leading,
  accessibilityLabel,
}: {
  label: string;
  active?: boolean;
  onPress?: () => void;
  removable?: boolean;
  count?: number;
  leading?: string;
  accessibilityLabel?: string;
}) {
  const t = useTheme();
  const bg = removable ? t.c.accentSoft : active ? t.c.accent : t.c.surface;
  const border = removable ? t.c.accentLine : active ? t.c.accent : t.c.border;
  const fg = removable ? t.c.accent : active ? t.c.accentText : t.c.text2;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={accessibilityLabel ?? (removable ? `Remove filter ${label}` : label)}
      accessibilityState={{ selected: !!active }}
      onPress={onPress}
      hitSlop={{ top: 4, bottom: 4 }}
      style={({ pressed }) => ({
        minHeight: 36,
        paddingHorizontal: 13,
        borderRadius: t.r.full,
        borderWidth: 1,
        borderColor: border,
        backgroundColor: bg,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 6,
        opacity: pressed ? 0.75 : 1,
      })}
    >
      {leading ? <Text maxFontSizeMultiplier={1.3}>{leading}</Text> : null}
      <Text maxFontSizeMultiplier={1.4} numberOfLines={1} style={{ color: fg, fontSize: t.f.sm, fontWeight: '600' }}>
        {label}
      </Text>
      {count != null ? (
        <Text maxFontSizeMultiplier={1.3} style={{ color: active ? t.c.accentText : t.c.text3, fontSize: t.f.xs, fontWeight: '700' }}>
          {count.toLocaleString('en-IN')}
        </Text>
      ) : null}
      {removable ? <Icon name="close" size={13} color={fg} strokeWidth={2.6} /> : null}
    </Pressable>
  );
}

export function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  accessibilityLabel,
}: {
  options: { value: T; label: string; icon?: IconName }[];
  value: T;
  onChange: (v: T) => void;
  accessibilityLabel?: string;
}) {
  const t = useTheme();
  return (
    <View
      accessibilityRole="radiogroup"
      accessibilityLabel={accessibilityLabel}
      style={{
        flexDirection: 'row',
        padding: 3,
        gap: 3,
        borderRadius: t.r.sm,
        backgroundColor: t.c.surface2,
        borderWidth: 1,
        borderColor: t.c.border,
      }}
    >
      {options.map((o) => {
        const on = o.value === value;
        return (
          <Pressable
            key={String(o.value)}
            accessibilityRole="radio"
            accessibilityState={{ selected: on }}
            accessibilityLabel={o.label}
            onPress={() => onChange(o.value)}
            style={{
              flex: 1,
              minHeight: 40,
              borderRadius: t.r.xs,
              alignItems: 'center',
              justifyContent: 'center',
              flexDirection: 'row',
              gap: 6,
              backgroundColor: on ? t.c.surface : 'transparent',
              borderWidth: on ? 1 : 0,
              borderColor: t.c.borderStrong,
            }}
          >
            {o.icon ? <Icon name={o.icon} size={15} color={on ? t.c.text : t.c.text2} /> : null}
            <Text
              maxFontSizeMultiplier={1.3}
              numberOfLines={1}
              style={{ color: on ? t.c.text : t.c.text2, fontWeight: on ? '700' : '600', fontSize: t.f.sm }}
            >
              {o.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/* ---------------- Surfaces ---------------- */

export function Card({ style, children }: { style?: StyleProp<ViewStyle>; children: React.ReactNode }) {
  const t = useTheme();
  return (
    <View
      style={[
        {
          backgroundColor: t.c.surface,
          borderRadius: t.r.md,
          borderWidth: 1,
          borderColor: t.c.border,
          padding: t.s[4],
        },
        style,
      ]}
    >
      {children}
    </View>
  );
}

export function Banner({
  kind,
  children,
  title,
}: {
  kind: 'error' | 'warn' | 'good' | 'info';
  title?: string;
  children?: React.ReactNode;
}) {
  const t = useTheme();
  const map = {
    error: [t.c.hotSoft, t.c.hot, 'alert'],
    warn: [t.c.warnSoft, t.c.warn, 'alert'],
    good: [t.c.goodSoft, t.c.good, 'check'],
    info: [t.c.accentSoft, t.c.accent, 'info'],
  } as const;
  const [bg, fg, icon] = map[kind];
  return (
    <View
      accessibilityRole={kind === 'error' ? 'alert' : undefined}
      style={{ flexDirection: 'row', gap: 10, padding: 12, borderRadius: t.r.sm, backgroundColor: bg }}
    >
      <View style={{ paddingTop: 1 }}>
        <Icon name={icon} size={17} color={fg} />
      </View>
      <View style={{ flex: 1, gap: 2 }}>
        {title ? <Text style={{ color: fg, fontWeight: '700', fontSize: t.f.sm }}>{title}</Text> : null}
        {typeof children === 'string' ? (
          <Text style={{ color: fg, fontSize: t.f.sm, lineHeight: 19 }}>{children}</Text>
        ) : (
          children
        )}
      </View>
    </View>
  );
}

export function Field({
  label,
  hint,
  style,
  inputStyle,
  ref,
  ...rest
}: TextInputProps & {
  label?: string;
  hint?: string;
  inputStyle?: StyleProp<TextStyle>;
  ref?: React.Ref<TextInput>;
}) {
  const t = useTheme();
  const [focused, setFocused] = React.useState(false);
  return (
    <View style={[{ gap: 6 }, style as StyleProp<ViewStyle>]}>
      {label ? (
        <Text maxFontSizeMultiplier={1.4} style={{ color: t.c.text2, fontSize: t.f.sm, fontWeight: '600' }}>
          {label}
        </Text>
      ) : null}
      <TextInput
        ref={ref}
        placeholderTextColor={t.c.text3}
        selectionColor={t.c.accent}
        accessibilityLabel={label}
        maxFontSizeMultiplier={1.5}
        {...rest}
        onFocus={(e) => {
          setFocused(true);
          rest.onFocus?.(e);
        }}
        onBlur={(e) => {
          setFocused(false);
          rest.onBlur?.(e);
        }}
        style={[
          {
            minHeight: 48,
            paddingHorizontal: 14,
            paddingVertical: 10,
            borderRadius: t.r.sm,
            borderWidth: 1,
            borderColor: focused ? t.c.accent : t.c.border,
            backgroundColor: t.c.surface2,
            color: t.c.text,
            fontSize: t.f.base,
          },
          inputStyle,
        ]}
      />
      {hint ? <Text style={{ color: t.c.text3, fontSize: t.f.xs }}>{hint}</Text> : null}
    </View>
  );
}

export function SectionHead({
  title,
  sub,
  right,
  style,
}: {
  title: string;
  sub?: string;
  right?: React.ReactNode;
  style?: StyleProp<ViewStyle>;
}) {
  const t = useTheme();
  return (
    <View style={[{ flexDirection: 'row', alignItems: 'center', gap: 10 }, style]}>
      <View style={{ flex: 1 }}>
        <Text accessibilityRole="header" maxFontSizeMultiplier={1.4} style={{ color: t.c.text, fontSize: t.f.base, fontWeight: '700' }}>
          {title}
        </Text>
        {sub ? (
          <Text maxFontSizeMultiplier={1.4} style={{ color: t.c.text3, fontSize: t.f.xs, marginTop: 2 }}>
            {sub}
          </Text>
        ) : null}
      </View>
      {right}
    </View>
  );
}

export function KeyValue({ rows }: { rows: [string, React.ReactNode][] }) {
  const t = useTheme();
  return (
    <View style={{ borderRadius: t.r.sm, borderWidth: 1, borderColor: t.c.border, overflow: 'hidden' }}>
      {rows.map(([k, v], i) => (
        <View
          key={k}
          style={{
            flexDirection: 'row',
            gap: 12,
            paddingHorizontal: 14,
            paddingVertical: 11,
            backgroundColor: i % 2 ? t.c.surface : t.c.surface2,
          }}
        >
          <Text maxFontSizeMultiplier={1.4} style={{ width: 104, color: t.c.text3, fontSize: t.f.sm, fontWeight: '600' }}>
            {k}
          </Text>
          <View style={{ flex: 1 }}>
            {typeof v === 'string' || typeof v === 'number' ? (
              <Text selectable style={{ color: t.c.text, fontSize: t.f.sm }}>
                {v}
              </Text>
            ) : (
              v
            )}
          </View>
        </View>
      ))}
    </View>
  );
}

/* ---------------- States ---------------- */

export function EmptyState({
  emoji,
  title,
  message,
  detail,
  actions,
}: {
  emoji: string;
  title: string;
  message?: string;
  detail?: string;
  actions?: { title: string; onPress: () => void; variant?: 'primary' | 'soft' }[];
}) {
  const t = useTheme();
  return (
    <View style={{ alignItems: 'center', paddingVertical: 40, paddingHorizontal: 24, gap: 8 }}>
      <Text style={{ fontSize: 40 }} accessibilityElementsHidden importantForAccessibility="no">
        {emoji}
      </Text>
      <Text accessibilityRole="header" style={{ color: t.c.text, fontSize: t.f.lg, fontWeight: '700', textAlign: 'center' }}>
        {title}
      </Text>
      {message ? (
        <Text style={{ color: t.c.text2, fontSize: t.f.md, textAlign: 'center', lineHeight: 21, maxWidth: 420 }}>
          {message}
        </Text>
      ) : null}
      {actions?.length ? (
        <View style={{ flexDirection: 'row', flexWrap: 'wrap', justifyContent: 'center', gap: 8, marginTop: 10 }}>
          {actions.map((a) => (
            <Button key={a.title} title={a.title} variant={a.variant ?? 'soft'} onPress={a.onPress} />
          ))}
        </View>
      ) : null}
      {detail ? (
        <Text style={{ color: t.c.text3, fontSize: t.f.xs, textAlign: 'center', marginTop: 12 }}>{detail}</Text>
      ) : null}
    </View>
  );
}

export function Skeleton({ style }: { style?: StyleProp<ViewStyle> }) {
  const t = useTheme();
  const opacity = useRef(new Animated.Value(0.55)).current;
  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(opacity, { toValue: 1, duration: 650, useNativeDriver: true }),
        Animated.timing(opacity, { toValue: 0.55, duration: 650, useNativeDriver: true }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [opacity]);
  return (
    <Animated.View
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
      style={[{ backgroundColor: t.c.skeleton, borderRadius: t.r.xs, opacity }, style]}
    />
  );
}

/* ---------------- Screen chrome ---------------- */

export function useHideNativeHeader() {
  const navigation = useNavigation();
  React.useLayoutEffect(() => {
    navigation.setOptions({ headerShown: false });
  }, [navigation]);
}

export function ScreenHeader({
  title,
  onBack,
  right,
  large,
}: {
  title: string;
  onBack?: (() => void) | null;
  right?: React.ReactNode;
  large?: boolean;
}) {
  const t = useTheme();
  const insets = useSafeAreaInsets();
  const navigation = useNavigation();
  useHideNativeHeader();
  const back =
    onBack === null ? null : onBack ?? (navigation.canGoBack() ? () => navigation.goBack() : null);
  return (
    <View
      style={{
        paddingTop: insets.top + 6,
        paddingBottom: 8,
        paddingHorizontal: back ? 4 : t.s.page,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 4,
        backgroundColor: t.c.bg,
        borderBottomWidth: large ? 0 : StyleSheet.hairlineWidth,
        borderBottomColor: t.c.border,
      }}
    >
      {back ? <IconButton name="chevLeft" label="Back" onPress={back} size={24} /> : null}
      <Text
        accessibilityRole="header"
        numberOfLines={1}
        maxFontSizeMultiplier={1.3}
        style={{
          flex: 1,
          color: t.c.text,
          fontSize: large ? t.f.xxl : t.f.lg,
          fontWeight: large ? '800' : '700',
          letterSpacing: large ? -0.6 : -0.2,
        }}
      >
        {title}
      </Text>
      {right ? <View style={{ flexDirection: 'row', alignItems: 'center', paddingRight: back ? 6 : 0 }}>{right}</View> : null}
    </View>
  );
}

export function Row({
  icon,
  title,
  sub,
  onPress,
  right,
  danger,
}: {
  icon?: IconName;
  title: string;
  sub?: string;
  onPress?: () => void;
  right?: React.ReactNode;
  danger?: boolean;
}) {
  const t = useTheme();
  const fg = danger ? t.c.hot : t.c.text;
  return (
    <Pressable
      accessibilityRole={onPress ? 'button' : undefined}
      accessibilityLabel={sub ? `${title}, ${sub}` : title}
      onPress={onPress}
      disabled={!onPress}
      android_ripple={{ color: t.c.accentSoft }}
      style={({ pressed }) => ({
        minHeight: 56,
        paddingHorizontal: 16,
        paddingVertical: 10,
        flexDirection: 'row',
        alignItems: 'center',
        gap: 14,
        opacity: pressed ? 0.8 : 1,
      })}
    >
      {icon ? <Icon name={icon} size={20} color={danger ? t.c.hot : t.c.text2} /> : null}
      <View style={{ flex: 1 }}>
        <Text maxFontSizeMultiplier={1.5} style={{ color: fg, fontSize: t.f.md, fontWeight: '600' }}>
          {title}
        </Text>
        {sub ? (
          <Text maxFontSizeMultiplier={1.5} style={{ color: t.c.text3, fontSize: t.f.xs, marginTop: 2 }}>
            {sub}
          </Text>
        ) : null}
      </View>
      {right ?? (onPress ? <Icon name="chevRight" size={18} color={t.c.text3} /> : null)}
    </Pressable>
  );
}

export function Divider() {
  const t = useTheme();
  return <View style={{ height: StyleSheet.hairlineWidth, backgroundColor: t.c.border, marginLeft: 16 }} />;
}
