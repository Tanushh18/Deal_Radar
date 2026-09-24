import { LinearGradient } from 'expo-linear-gradient';
import React, { useMemo, useState } from 'react';
import { Text, View, type GestureResponderEvent } from 'react-native';
import Svg, { Circle, Line, Path, Text as SvgText } from 'react-native-svg';

import type { PricePoint } from '../api/types';
import { useTheme } from '../theme';
import { avatarHues, money } from './format';
import { haptic } from './native';

export function ScoreRing({ score, size = 62 }: { score: number; size?: number }) {
  const t = useTheme();
  const pct = Math.max(0, Math.min(100, Math.round(score || 0)));
  const stroke = 5.5;
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  return (
    <View style={{ width: size, height: size }} accessibilityLabel={`Deal score ${pct} out of 100`} accessible>
      <Svg width={size} height={size} style={{ transform: [{ rotate: '-90deg' }] }}>
        <Circle cx={size / 2} cy={size / 2} r={r} stroke={t.c.border} strokeWidth={stroke} fill="none" />
        <Circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          stroke={t.c.accent}
          strokeWidth={stroke}
          fill="none"
          strokeLinecap="round"
          strokeDasharray={`${c} ${c}`}
          strokeDashoffset={c * (1 - pct / 100)}
        />
      </Svg>
      <View style={{ position: 'absolute', top: 0, left: 0, right: 0, bottom: 0, alignItems: 'center', justifyContent: 'center' }}>
        <Text maxFontSizeMultiplier={1.1} style={{ color: t.c.text, fontSize: 16, fontWeight: '800' }}>
          {pct}
        </Text>
      </View>
    </View>
  );
}

const fmtDate = (ts: number, year = false) =>
  new Date(ts * 1000).toLocaleDateString('en-IN', {
    day: 'numeric',
    month: 'short',
    ...(year ? { year: 'numeric' } : {}),
  });

export function PriceChart({ points: raw }: { points: PricePoint[] }) {
  const t = useTheme();
  const [width, setWidth] = useState(0);
  const [active, setActive] = useState<number | null>(null);

  const points = useMemo(
    () => (raw || []).filter((p) => p && p.price != null && p.at).sort((a, b) => a.at - b.at),
    [raw],
  );

  if (points.length < 2) {
    return (
      <View style={{ padding: 18, borderRadius: t.r.sm, backgroundColor: t.c.surface2 }}>
        <Text style={{ color: t.c.text3, fontSize: t.f.sm, textAlign: 'center', lineHeight: 19 }}>
          Not enough price history yet — check back once this deal has been seen a few more times.
        </Text>
      </View>
    );
  }

  const H = 180;
  const padTop = 22;
  const padBottom = 26;
  const padLeft = 54;
  const padRight = 14;
  const W = Math.max(width, 1);
  const x0 = padLeft;
  const x1 = W - padRight;
  const y0 = padTop;
  const y1 = H - padBottom;

  const times = points.map((p) => p.at);
  const prices = points.map((p) => p.price);
  const tMin = times[0];
  const tMax = times[times.length - 1];
  const rawMin = Math.min(...prices);
  const rawMax = Math.max(...prices);
  let pMin = rawMin;
  let pMax = rawMax;
  if (pMin === pMax) {
    pMin -= 1;
    pMax += 1;
  }
  const pad = (pMax - pMin) * 0.12;
  pMin -= pad;
  pMax += pad;

  const xs = (tt: number) => (tMax === tMin ? (x0 + x1) / 2 : x0 + ((tt - tMin) / (tMax - tMin)) * (x1 - x0));
  const ys = (p: number) => y1 - ((p - pMin) / (pMax - pMin)) * (y1 - y0);

  // Step-after: the price holds flat until the next observation; a diagonal
  // would imply a gradual change that never happened.
  let line = `M ${xs(times[0])} ${ys(prices[0])}`;
  for (let i = 1; i < points.length; i++) line += ` H ${xs(times[i])} V ${ys(prices[i])}`;
  const area = `${line} L ${x1} ${y1} L ${x0} ${y1} Z`;

  const gridVals = rawMin === rawMax ? [rawMin] : [rawMin, (rawMin + rawMax) / 2, rawMax];
  const last = points.length - 1;
  const lowIdx = prices.indexOf(rawMin);
  const endIsLow = lowIdx === last;

  const pick = (e: GestureResponderEvent) => {
    const x = e.nativeEvent.locationX;
    let best = 0;
    let dist = Infinity;
    times.forEach((tt, i) => {
      const d = Math.abs(xs(tt) - x);
      if (d < dist) {
        dist = d;
        best = i;
      }
    });
    if (best !== active) haptic.select();
    setActive(best);
  };

  const tipLeft = active != null ? Math.min(Math.max(xs(times[active]) - 60, 0), W - 120) : 0;

  return (
    <View
      onLayout={(e) => setWidth(e.nativeEvent.layout.width)}
      accessible
      accessibilityRole="image"
      accessibilityLabel={`Price history from ${money(rawMin)} to ${money(rawMax)}, currently ${money(prices[last])}`}
      accessibilityHint="Tap the chart to inspect a price"
      onStartShouldSetResponder={() => true}
      onMoveShouldSetResponder={() => true}
      onResponderGrant={pick}
      onResponderMove={pick}
      onResponderTerminationRequest={() => true}
      style={{ height: H }}
    >
      {width > 0 ? (
        <Svg width={W} height={H}>
          {gridVals.map((v) => (
            <React.Fragment key={v}>
              <Line x1={x0} x2={x1} y1={ys(v)} y2={ys(v)} stroke={t.c.border} strokeWidth={1} />
              <SvgText x={x0 - 8} y={ys(v) + 3.5} fontSize={10} fill={t.c.text3} textAnchor="end">
                {money(v)}
              </SvgText>
            </React.Fragment>
          ))}
          <Path d={area} fill={t.c.accent} fillOpacity={0.1} />
          <Path d={line} fill="none" stroke={t.c.accent} strokeWidth={2} strokeLinejoin="round" strokeLinecap="round" />
          <SvgText x={x0} y={H - 7} fontSize={10} fill={t.c.text3} textAnchor="start">
            {fmtDate(times[0])}
          </SvgText>
          <SvgText x={x1} y={H - 7} fontSize={10} fill={t.c.text3} textAnchor="end">
            {fmtDate(times[last])}
          </SvgText>

          {!endIsLow ? (
            <>
              <Circle cx={xs(times[lowIdx])} cy={ys(rawMin)} r={5} fill={t.c.gold} stroke={t.c.surface} strokeWidth={2} />
              <SvgText
                x={xs(times[lowIdx])}
                y={ys(rawMin) - 10}
                fontSize={10}
                fontWeight="700"
                fill={t.c.gold}
                textAnchor={lowIdx < points.length / 2 ? 'start' : 'end'}
              >
                {`Lowest ${money(rawMin)}`}
              </SvgText>
            </>
          ) : null}

          <Circle
            cx={xs(times[last])}
            cy={ys(prices[last])}
            r={5}
            fill={endIsLow ? t.c.gold : t.c.accent}
            stroke={t.c.surface}
            strokeWidth={2}
          />
          <SvgText
            x={xs(times[last]) - 8}
            y={ys(prices[last]) - 10}
            fontSize={10}
            fontWeight="700"
            fill={endIsLow ? t.c.gold : t.c.text}
            textAnchor="end"
          >
            {(endIsLow ? 'Lowest · ' : '') + money(prices[last])}
          </SvgText>

          {active != null ? (
            <>
              <Line x1={xs(times[active])} x2={xs(times[active])} y1={y0} y2={y1} stroke={t.c.text3} strokeWidth={1} />
              <Circle cx={xs(times[active])} cy={ys(prices[active])} r={6} fill={t.c.accent} stroke={t.c.surface} strokeWidth={2} />
            </>
          ) : null}
        </Svg>
      ) : null}
      {active != null && width > 0 ? (
        <View
          pointerEvents="none"
          style={{
            position: 'absolute',
            top: 0,
            left: tipLeft,
            width: 120,
            alignItems: 'center',
            paddingVertical: 4,
            borderRadius: 8,
            backgroundColor: t.c.surface3,
            borderWidth: 1,
            borderColor: t.c.borderStrong,
          }}
        >
          <Text style={{ color: t.c.text, fontWeight: '800', fontSize: 13 }}>{money(prices[active])}</Text>
          <Text style={{ color: t.c.text3, fontSize: 10.5 }}>{fmtDate(times[active], true)}</Text>
        </View>
      ) : null}
    </View>
  );
}

/** Tiny inline trend line for a card — cheap enough to render per-item in a list. */
export function Sparkline({ points, width = 44, height = 16 }: { points: number[]; width?: number; height?: number }) {
  const t = useTheme();
  if (!points || points.length < 2) return null;
  const min = Math.min(...points);
  const max = Math.max(...points);
  const span = max - min || 1;
  const coords = points
    .map((p, i) => {
      const x = (i / (points.length - 1)) * width;
      const y = height - ((p - min) / span) * height;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    })
    .join(' ');
  const down = points[points.length - 1] <= points[0];
  return (
    <Svg width={width} height={height} accessibilityElementsHidden importantForAccessibility="no">
      <Path
        d={`M ${coords.replace(/ /g, ' L ')}`}
        fill="none"
        stroke={down ? t.c.good : t.c.hot}
        strokeWidth={1.6}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </Svg>
  );
}

export function ChannelAvatar({ title, size = 44 }: { title: string; size?: number }) {
  const [h1, h2] = avatarHues(title || '?');
  const initial = (title || '').trim().slice(0, 1).toUpperCase() || '#';
  return (
    <LinearGradient
      colors={[`hsl(${h1}, 72%, 52%)`, `hsl(${h2}, 72%, 44%)`]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ width: size, height: size, borderRadius: size * 0.32, alignItems: 'center', justifyContent: 'center' }}
    >
      <Text maxFontSizeMultiplier={1.1} style={{ color: '#fff', fontWeight: '800', fontSize: size * 0.4 }}>
        {initial}
      </Text>
    </LinearGradient>
  );
}

export function BrandMark({ size = 30 }: { size?: number }) {
  const t = useTheme();
  return (
    <LinearGradient
      colors={[t.c.accent, t.c.cyan]}
      start={{ x: 0, y: 0 }}
      end={{ x: 1, y: 1 }}
      style={{ width: size, height: size, borderRadius: size * 0.3, alignItems: 'center', justifyContent: 'center' }}
    >
      <Text maxFontSizeMultiplier={1} style={{ fontSize: size * 0.5 }}>
        📡
      </Text>
    </LinearGradient>
  );
}
