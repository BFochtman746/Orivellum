/**
 * Orivellum mobile design tokens — derived from the sibling web app's index.css.
 * Light palette: earthy ivory/parchment base with forest green primary.
 * Dark palette: deep navy with lighter forest green.
 */

const colors = {
  light: {
    // Legacy aliases
    text: '#1A2233',
    tint: '#24432E',

    // Surfaces
    background: '#F5F0E8',
    foreground: '#1A2233',

    card: '#FDFAF5',
    cardForeground: '#1A2233',

    // Primary — forest green
    primary: '#24432E',
    primaryForeground: '#FDFAF5',

    // Secondary — warm beige
    secondary: '#EDD8C4',
    secondaryForeground: '#472B0F',

    // Muted — light parchment
    muted: '#E8E4DC',
    mutedForeground: '#546070',

    // Accent — same tone as muted
    accent: '#E8E4DC',
    accentForeground: '#1A2233',

    // Destructive — vermillion
    destructive: '#C43015',
    destructiveForeground: '#FFFFFF',

    // Structure
    border: '#DDD8CE',
    input: '#DDD8CE',
  },

  dark: {
    text: '#F5F0E8',
    tint: '#4D8C65',

    background: '#111827',
    foreground: '#F5F0E8',

    card: '#151E30',
    cardForeground: '#F5F0E8',

    primary: '#4D8C65',
    primaryForeground: '#111827',

    secondary: '#1E2C40',
    secondaryForeground: '#EBE7DF',

    muted: '#1C2233',
    mutedForeground: '#8D9CB0',

    accent: '#1E2C40',
    accentForeground: '#F5F0E8',

    destructive: '#CC3D22',
    destructiveForeground: '#FFFFFF',

    border: '#253040',
    input: '#253040',
  },

  // 0.25rem = 4px — matches --radius in web app (sharp/serious feel)
  radius: 4,
};

export default colors;
