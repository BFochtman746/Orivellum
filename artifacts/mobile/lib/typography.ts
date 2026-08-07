/**
 * Orivellum mobile typography — Apple HIG–aligned type scale.
 *
 * font(weight) returns fontFamily + fontWeight for the current platform:
 *   iOS  → undefined fontFamily so React Native resolves to SF Pro (system font),
 *           plus the matching fontWeight for the desired weight.
 *   Other → explicit Inter fontFamily so the pre-loaded custom font is used.
 *
 * Spread into any style object or StyleSheet.create entry:
 *   cardTitle: { fontSize: 15, lineHeight: 20, ...font('semibold') }
 */

import { Platform } from 'react-native';

type Weight = 'regular' | 'medium' | 'semibold' | 'bold';

// ── Fraunces editorial serif ───────────────────────────────────────────────────

type SerifWeight = 'semibold' | 'bold';

const SERIF_FAMILIES: Record<SerifWeight, string> = {
  semibold: 'Fraunces_600SemiBold',
  bold:     'Fraunces_700Bold',
};

const SERIF_WEIGHTS: Record<SerifWeight, '600' | '700'> = {
  semibold: '600',
  bold:     '700',
};

/**
 * Returns Fraunces serif font style for editorial screen titles and hero headings.
 * Always specifies fontFamily explicitly — fonts must be loaded via useFonts().
 * Use on large display text only (≥20px); body copy stays Inter.
 */
export function fontSerif(weight: SerifWeight = 'bold'): {
  fontFamily: string;
  fontWeight: '600' | '700';
} {
  return { fontFamily: SERIF_FAMILIES[weight], fontWeight: SERIF_WEIGHTS[weight] };
}

const FAMILIES: Record<Weight, string> = {
  regular: 'Inter_400Regular',
  medium: 'Inter_500Medium',
  semibold: 'Inter_600SemiBold',
  bold: 'Inter_700Bold',
};

const WEIGHTS = {
  regular: '400',
  medium: '500',
  semibold: '600',
  bold: '700',
} as const;

type FontStyle = {
  fontFamily?: string;
  fontWeight: '400' | '500' | '600' | '700';
};

/**
 * Returns the font style for the given weight.
 * On iOS, fontFamily is omitted so the OS resolves to SF Pro via fontWeight.
 * On other platforms, the loaded Inter font is specified explicitly.
 */
export function font(weight: Weight): FontStyle {
  if (Platform.OS === 'ios') {
    return { fontWeight: WEIGHTS[weight] };
  }
  return { fontFamily: FAMILIES[weight], fontWeight: WEIGHTS[weight] };
}

/**
 * Apple Human Interface Guidelines — Dynamic Type scale.
 * Sizes match SF Pro at the default (medium) accessibility text size.
 * Use these constants for all text elements to support Dynamic Type in future.
 */
export const TS = {
  largeTitle:  { fontSize: 34, lineHeight: 41,  ...font('bold')    },
  title1:      { fontSize: 28, lineHeight: 34,  ...font('bold')    },
  title2:      { fontSize: 22, lineHeight: 28,  ...font('bold')    },
  title3:      { fontSize: 20, lineHeight: 25,  ...font('semibold')},
  headline:    { fontSize: 17, lineHeight: 22,  ...font('semibold')},
  body:        { fontSize: 17, lineHeight: 22,  ...font('regular') },
  callout:     { fontSize: 16, lineHeight: 21,  ...font('regular') },
  calloutSemi: { fontSize: 16, lineHeight: 21,  ...font('semibold')},
  subhead:     { fontSize: 15, lineHeight: 20,  ...font('regular') },
  subheadSemi: { fontSize: 15, lineHeight: 20,  ...font('semibold')},
  footnote:    { fontSize: 13, lineHeight: 18,  ...font('regular') },
  footnoteSemi:{ fontSize: 13, lineHeight: 18,  ...font('semibold')},
  caption1:    { fontSize: 12, lineHeight: 16,  ...font('regular') },
  caption1Med: { fontSize: 12, lineHeight: 16,  ...font('medium')  },
  caption2:    { fontSize: 11, lineHeight: 14,  ...font('regular') },
  caption2Med: { fontSize: 11, lineHeight: 14,  ...font('medium')  },
} as const;

/** Minimum touch target dimension required by Apple HIG (44 × 44 pt). */
export const MIN_TOUCH = 44 as const;
