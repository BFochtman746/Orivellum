/**
 * VELLUM design-system semantic color tokens for React Native.
 *
 * Mirrors the CSS custom properties defined in the web app's index.css:
 *   --green-2, --green-soft, --gilt, --gilt-soft, --gilt-line,
 *   --rust, --rust-soft, --ink-soft
 *
 * Use `useVellumTokens()` inside any React Native component — it
 * automatically returns the light or dark palette based on the device's
 * appearance setting, matching the web app's dark-mode switch.
 *
 * For static contexts (StyleSheet.create, module-level constants) use
 * `VELLUM_LIGHT` or `VELLUM_DARK` directly and accept that they won't
 * switch at runtime.
 */
import { useColorScheme } from 'react-native';

export const VELLUM_LIGHT = {
  /** Success / ready / confirmed — forest green. */
  green:     '#3C6A4B',
  greenSoft: 'rgba(39, 70, 51, 0.10)',
  /** Warning / processing / gilt-amber. */
  gilt:      '#9A7B2E',
  giltSoft:  'rgba(154, 123, 46, 0.12)',
  giltLine:  'rgba(154, 123, 46, 0.32)',
  /** Error / destructive — terracotta rust. */
  rust:      '#B2431E',
  rustSoft:  'rgba(178, 67, 30, 0.10)',
  /** Muted label / secondary text. */
  inkSoft:   '#5C5443',
} as const;

export const VELLUM_DARK = {
  green:     '#8FC2A1',
  greenSoft: 'rgba(111, 169, 130, 0.14)',
  gilt:      '#C9A25A',
  giltSoft:  'rgba(201, 162, 90, 0.14)',
  giltLine:  'rgba(201, 162, 90, 0.36)',
  rust:      '#D46A43',
  rustSoft:  'rgba(212, 106, 67, 0.14)',
  inkSoft:   '#B9B09B',
} as const;

export interface VellumTokens {
  green:     string;
  greenSoft: string;
  gilt:      string;
  giltSoft:  string;
  giltLine:  string;
  rust:      string;
  rustSoft:  string;
  inkSoft:   string;
}

/**
 * Returns VELLUM semantic tokens for the current color scheme.
 * Call this alongside `useColors()` in every component that renders
 * status-aware UI (readiness badges, review buttons, gap severity, etc.).
 */
export function useVellumTokens(): VellumTokens {
  const scheme = useColorScheme();
  return scheme === 'dark' ? VELLUM_DARK : VELLUM_LIGHT;
}

/**
 * Appends an alpha byte (00–FF) to a six-digit hex color string.
 * Useful for derived tints: alpha(T.green, 0.12) → '#3C6A4B1F'
 */
export function alpha(hex: string, opacity: number): string {
  const a = Math.round(Math.max(0, Math.min(1, opacity)) * 255)
    .toString(16)
    .padStart(2, '0');
  return hex + a;
}
