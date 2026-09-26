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

// "Paper & ink": warm off-white / warm charcoal, ink-black actions, orange only
// for discounts, green only for price drops. No blues, no gradients.
export const lightColors: ColorTokens = {
  bg: '#f5f2ec',
  bgSunk: '#ece8e0',
  surface: '#ffffff',
  surface2: '#f0ece5',
  surface3: '#e6e1d8',
  border: '#e3ded4',
  borderStrong: '#d2ccc0',
  text: '#17150f',
  text2: '#5e5a52',
  text3: '#8f897e',
  mediaBg: '#ffffff',
  accent: '#17150f',
  accentHover: '#000000',
  accentSoft: 'rgba(23, 21, 15, 0.06)',
  accentLine: 'rgba(23, 21, 15, 0.18)',
  accentText: '#f5f2ec',
  cyan: '#2f6f8f',
  cyanSoft: 'rgba(47, 111, 143, 0.12)',
  good: '#1f7a4d',
  goodSoft: 'rgba(31, 122, 77, 0.12)',
  goodLine: 'rgba(31, 122, 77, 0.30)',
  hot: '#d9480f',
  hotSoft: 'rgba(217, 72, 15, 0.10)',
  warn: '#a15c07',
  warnSoft: 'rgba(196, 122, 20, 0.13)',
  gold: '#a15c07',
  badgeLowBg: '#1f7a4d',
  badgeLowText: '#ffffff',
  badgeHotBg: '#d9480f',
  badgeNewBg: '#17150f',
  badgeText: '#ffffff',
  overlay: 'rgba(23, 21, 15, 0.45)',
  skeleton: '#e8e3da',
};

export const darkColors: ColorTokens = {
  bg: '#14120e',
  bgSunk: '#0f0d0a',
  surface: '#1c1914',
  surface2: '#24201a',
  surface3: '#2d2922',
  border: '#2d2922',
  borderStrong: '#3b362d',
  text: '#f1ece2',
  text2: '#b4ad9f',
  text3: '#857f73',
  mediaBg: '#f4f1eb',
  accent: '#f1ece2',
  accentHover: '#ffffff',
  accentSoft: 'rgba(241, 236, 226, 0.08)',
  accentLine: 'rgba(241, 236, 226, 0.22)',
  accentText: '#14120e',
  cyan: '#6fb3cf',
  cyanSoft: 'rgba(111, 179, 207, 0.14)',
  good: '#52c48c',
  goodSoft: 'rgba(82, 196, 140, 0.14)',
  goodLine: 'rgba(82, 196, 140, 0.32)',
  hot: '#ff7a45',
  hotSoft: 'rgba(255, 122, 69, 0.14)',
  warn: '#e8a94a',
  warnSoft: 'rgba(232, 169, 74, 0.14)',
  gold: '#e8a94a',
  badgeLowBg: '#1f7a4d',
  badgeLowText: '#ffffff',
  badgeHotBg: '#c2410c',
  badgeNewBg: '#3b362d',
  badgeText: '#ffffff',
  overlay: 'rgba(0, 0, 0, 0.6)',
  skeleton: '#26221c',
};

export const radii = { xs: 8, sm: 12, md: 14, lg: 18, xl: 24, full: 999 } as const;

export const spacing = { 1: 4, 2: 8, 3: 12, 4: 16, 5: 20, 6: 24, 8: 32, page: 16 } as const;

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
