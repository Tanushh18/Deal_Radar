import React from 'react';
import Svg, { Circle, Path, Rect } from 'react-native-svg';

type Shape =
  | { d: string }
  | { circle: [number, number, number] }
  | { rect: [number, number, number, number, number] };

// Same 24×24 stroke icons as the web sprite in static/index.html.
const ICONS = {
  search: [{ d: 'M21 21l-4.35-4.35M19 11a8 8 0 11-16 0 8 8 0 0116 0z' }],
  close: [{ d: 'M18 6L6 18M6 6l12 12' }],
  sync: [{ d: 'M21 12a9 9 0 11-2.64-6.36M21 3v6h-6' }],
  bell: [{ d: 'M18 8A6 6 0 006 8c0 7-3 9-3 9h18s-3-2-3-9M13.73 21a2 2 0 01-3.46 0' }],
  filter: [{ d: 'M4 6h16M7 12h10M10 18h4' }],
  tag: [{ d: 'M20.6 13.4L12 22l-9-9V3h10l7.6 7.6a2 2 0 010 2.8z' }, { circle: [7.5, 7.5, 1.3] }],
  radio: [
    { d: 'M4.9 19.1a10 10 0 010-14.2M7.8 16.2a6 6 0 010-8.4M16.2 7.8a6 6 0 010 8.4M19.1 4.9a10 10 0 010 14.2' },
    { circle: [12, 12, 2] },
  ],
  user: [{ d: 'M20 21v-2a4 4 0 00-4-4H8a4 4 0 00-4 4v2' }, { circle: [12, 7, 4] }],
  check: [{ d: 'M20 6L9 17l-5-5' }],
  alert: [{ d: 'M12 9v4M12 17h.01M10.3 3.9L1.8 18a2 2 0 001.7 3h17a2 2 0 001.7-3L14.7 3.9a2 2 0 00-3.4 0z' }],
  info: [{ circle: [12, 12, 9] }, { d: 'M12 16v-4M12 8h.01' }],
  down: [{ d: 'M12 5v14M19 12l-7 7-7-7' }],
  trend: [{ d: 'M23 6l-9.5 9.5-5-5L1 18' }, { d: 'M17 6h6v6' }],
  clock: [{ circle: [12, 12, 9] }, { d: 'M12 7v5l3 2' }],
  moon: [{ d: 'M21 12.8A9 9 0 1111.2 3a7 7 0 009.8 9.8z' }],
  sun: [
    { circle: [12, 12, 4] },
    { d: 'M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4' },
  ],
  activity: [{ d: 'M22 12h-4l-3 9L9 3l-3 9H2' }],
  logout: [{ d: 'M9 21H5a2 2 0 01-2-2V5a2 2 0 012-2h4M16 17l5-5-5-5M21 12H9' }],
  plus: [{ d: 'M12 5v14M5 12h14' }],
  external: [{ d: 'M18 13v6a2 2 0 01-2 2H5a2 2 0 01-2-2V8a2 2 0 012-2h6M15 3h6v6M10 14L21 3' }],
  lock: [{ rect: [3, 11, 18, 11, 2] }, { d: 'M7 11V7a5 5 0 0110 0v4' }],
  arrowRight: [{ d: 'M5 12h14M12 5l7 7-7 7' }],
  sliders: [{ d: 'M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6' }],
  wifiOff: [{ d: 'M1 1l22 22M16.7 12.7A6 6 0 0112 21a6 6 0 01-4.7-8.3M5 12.6a10 10 0 013-2.1M8.8 5.3A14 14 0 0123 8.6' }],
  grid: [
    { rect: [3, 3, 7, 7, 1.5] },
    { rect: [14, 3, 7, 7, 1.5] },
    { rect: [3, 14, 7, 7, 1.5] },
    { rect: [14, 14, 7, 7, 1.5] },
  ],
  list: [{ rect: [3, 4, 6, 6, 1.5] }, { rect: [3, 14, 6, 6, 1.5] }, { d: 'M13 6h8M13 9h5M13 16h8M13 19h5' }],
  chevLeft: [{ d: 'M15 18l-6-6 6-6' }],
  chevRight: [{ d: 'M9 18l6-6-6-6' }],
  chevDown: [{ d: 'M6 9l6 6 6-6' }],
  up: [{ d: 'M12 19V5M5 12l7-7 7 7' }],
  share: [
    { circle: [18, 5, 3] },
    { circle: [6, 12, 3] },
    { circle: [18, 19, 3] },
    { d: 'M8.6 13.5l6.8 4M15.4 6.5l-6.8 4' },
  ],
  copy: [{ rect: [9, 9, 13, 13, 2] }, { d: 'M5 15H4a2 2 0 01-2-2V4a2 2 0 012-2h9a2 2 0 012 2v1' }],
  trash: [{ d: 'M3 6h18M8 6V4a2 2 0 012-2h4a2 2 0 012 2v2M19 6l-1 14a2 2 0 01-2 2H8a2 2 0 01-2-2L5 6' }],
  globe: [{ circle: [12, 12, 9] }, { d: 'M3 12h18M12 3a14 14 0 010 18M12 3a14 14 0 000 18' }],
  settings: [
    { circle: [12, 12, 3] },
    {
      d: 'M19.4 15a1.7 1.7 0 00.3 1.8l.1.1a2 2 0 11-2.8 2.8l-.1-.1a1.7 1.7 0 00-1.8-.3 1.7 1.7 0 00-1 1.5V21a2 2 0 11-4 0v-.1a1.7 1.7 0 00-1.1-1.5 1.7 1.7 0 00-1.8.3l-.1.1a2 2 0 11-2.8-2.8l.1-.1a1.7 1.7 0 00.3-1.8 1.7 1.7 0 00-1.5-1H3a2 2 0 110-4h.1a1.7 1.7 0 001.5-1.1 1.7 1.7 0 00-.3-1.8l-.1-.1a2 2 0 112.8-2.8l.1.1a1.7 1.7 0 001.8.3H9a1.7 1.7 0 001-1.5V3a2 2 0 114 0v.1a1.7 1.7 0 001 1.5 1.7 1.7 0 001.8-.3l.1-.1a2 2 0 112.8 2.8l-.1.1a1.7 1.7 0 00-.3 1.8V9a1.7 1.7 0 001.5 1H21a2 2 0 110 4h-.1a1.7 1.7 0 00-1.5 1z',
    },
  ],
  heart: [{ d: 'M20.8 4.6a5.5 5.5 0 00-7.8 0L12 5.7l-1-1.1a5.5 5.5 0 00-7.8 7.8l1 1.1L12 21l7.8-7.5 1-1.1a5.5 5.5 0 000-7.8z' }],
  link: [{ d: 'M10 13a5 5 0 007.5.5l3-3a5 5 0 00-7-7l-1.7 1.7M14 11a5 5 0 00-7.5-.5l-3 3a5 5 0 007 7l1.7-1.7' }],
  clipboard: [{ rect: [8, 2, 8, 4, 1] }, { d: 'M16 4h2a2 2 0 012 2v14a2 2 0 01-2 2H6a2 2 0 01-2-2V6a2 2 0 012-2h2' }],
  zap: [{ d: 'M13 2L3 14h9l-1 8 10-12h-9l1-8z' }],
  server: [
    { rect: [2, 3, 20, 8, 2] },
    { rect: [2, 13, 20, 8, 2] },
    { d: 'M6 7h.01M6 17h.01' },
  ],
} satisfies Record<string, Shape[]>;

export type IconName = keyof typeof ICONS;

export function Icon({
  name,
  size = 20,
  color,
  strokeWidth = 2,
}: {
  name: IconName;
  size?: number;
  color: string;
  strokeWidth?: number;
}) {
  const shapes: Shape[] = ICONS[name];
  return (
    <Svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke={color}
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      accessibilityElementsHidden
      importantForAccessibility="no-hide-descendants"
    >
      {shapes.map((s, i) => {
        if ('d' in s) return <Path key={i} d={s.d} />;
        if ('circle' in s) return <Circle key={i} cx={s.circle[0]} cy={s.circle[1]} r={s.circle[2]} />;
        const [x, y, w, h, rx] = s.rect;
        return <Rect key={i} x={x} y={y} width={w} height={h} rx={rx} />;
      })}
    </Svg>
  );
}
