export type ColorTokens = {
  bg: string;
  bgSunk: string;
  surface: string;
  surface2: string;
  surface3: string;
  border: string;
  borderStrong: string;
  text: string;
  text2: string;
  text3: string;
  mediaBg: string;
  accent: string;
  accentHover: string;
  accentSoft: string;
  accentLine: string;
  accentText: string;
  cyan: string;
  cyanSoft: string;
  good: string;
  goodSoft: string;
  goodLine: string;
  hot: string;
  hotSoft: string;
  warn: string;
  warnSoft: string;
  gold: string;
  badgeLowBg: string;
  badgeLowText: string;
  badgeHotBg: string;
  badgeNewBg: string;
  badgeText: string;
  overlay: string;
  skeleton: string;
};

export const lightColors: ColorTokens = {
  bg: '#f7f8fa',
  bgSunk: '#eef1f6',
  surface: '#ffffff',
  surface2: '#f3f5f9',
  surface3: '#e8ecf3',
  border: '#e4e8ef',
  borderStrong: '#cbd3e1',
  text: '#111827',
  text2: '#6b7280',
  text3: '#9aa3b2',
  mediaBg: '#ffffff',
  accent: '#2563eb',
  accentHover: '#1d4ed8',
  accentSoft: 'rgba(37, 99, 235, 0.10)',
  accentLine: 'rgba(37, 99, 235, 0.28)',
  accentText: '#ffffff',
  cyan: '#0891b2',
  cyanSoft: 'rgba(6, 182, 212, 0.12)',
  good: '#15803d',
  goodSoft: 'rgba(22, 163, 74, 0.12)',
  goodLine: 'rgba(22, 163, 74, 0.30)',
  hot: '#dc2626',
  hotSoft: 'rgba(239, 68, 68, 0.10)',
  warn: '#b45309',
  warnSoft: 'rgba(245, 158, 11, 0.14)',
  gold: '#b45309',
  badgeLowBg: '#fbbf24',
  badgeLowText: '#33240a',
  badgeHotBg: '#16a34a',
  badgeNewBg: '#2563eb',
  badgeText: '#ffffff',
  overlay: 'rgba(15, 23, 42, 0.45)',
  skeleton: '#e8ecf3',
};

export const darkColors: ColorTokens = {
  bg: '#080b12',
  bgSunk: '#05070c',
  surface: '#111827',
  surface2: '#172033',
  surface3: '#202b40',
  border: '#1f2a3c',
  borderStrong: '#33405a',
  text: '#e9eef7',
  text2: '#9ba7bc',
  text3: '#6b7789',
  mediaBg: '#eef1f5',
  accent: '#5b93f7',
  accentHover: '#7aabff',
  accentSoft: 'rgba(91, 147, 247, 0.14)',
  accentLine: 'rgba(91, 147, 247, 0.34)',
  accentText: '#06101f',
  cyan: '#22d3ee',
  cyanSoft: 'rgba(34, 211, 238, 0.14)',
  good: '#34d399',
  goodSoft: 'rgba(52, 211, 153, 0.14)',
  goodLine: 'rgba(52, 211, 153, 0.32)',
  hot: '#f87171',
  hotSoft: 'rgba(248, 113, 113, 0.14)',
  warn: '#fbbf24',
  warnSoft: 'rgba(251, 191, 36, 0.14)',
  gold: '#fbbf24',
  badgeLowBg: '#fbbf24',
  badgeLowText: '#33240a',
  badgeHotBg: '#16a34a',
  badgeNewBg: '#2563eb',
  badgeText: '#ffffff',
  overlay: 'rgba(0, 0, 0, 0.6)',
  skeleton: '#1a2336',
};

export const radii = { xs: 8, sm: 12, md: 16, lg: 20, xl: 26, full: 999 } as const;

export const spacing = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, page: 14 } as const;

export const fontSize = {
  xxs: 10.5,
  xs: 11.5,
  sm: 13,
  md: 14.5,
  base: 16,
  lg: 18,
  xl: 21,
  xxl: 26,
} as const;

export const MIN_TOUCH = 44;

export type Theme = {
  dark: boolean;
  c: ColorTokens;
  r: typeof radii;
  s: typeof spacing;
  f: typeof fontSize;
};

export const lightTheme: Theme = { dark: false, c: lightColors, r: radii, s: spacing, f: fontSize };
export const darkTheme: Theme = { dark: true, c: darkColors, r: radii, s: spacing, f: fontSize };
