/**
 * WeatherCard — polished, Apple-Weather-inspired ambient card for the dashboard.
 *
 * Design principles:
 *   • Condition-matched LinearGradient background (sunny/cloudy/rain/snow/storm/night)
 *   • Fraunces editorial serif for the large temperature — matches VELLUM display style
 *   • Centered hero layout so the temperature is the undeniable focal point
 *   • Detail pills (feels-like, humidity, wind) + 4-day forecast strip
 *   • Gracefully disappears if permission denied — no nagging UI
 *   • Skeleton while loading; stale-cache indicator on refresh error
 */

import React, { useCallback } from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  TouchableOpacity,
  useColorScheme,
  View,
} from 'react-native';
import { LinearGradient } from 'expo-linear-gradient';
import { Feather } from '@expo/vector-icons';

import { font, fontSerif, TS } from '@/lib/typography';
import {
  useWeather,
  wmoIcon,
  wmoLabel,
  wmoGroup,
  type ConditionGroup,
  type DayForecast,
} from '@/hooks/useWeather';

// ── Gradient palette ──────────────────────────────────────────────────────────
// Each group → [topColor, bottomColor] for light and dark schemes.

const GRADIENT: Record<ConditionGroup, { light: readonly [string, string]; dark: readonly [string, string] }> = {
  sunny:      { light: ['#FFFCF0', '#F5E8A8'], dark: ['#231E08', '#2E2610'] },
  clearNight: { light: ['#1C2C44', '#111827'], dark: ['#070D1A', '#0D1422'] },
  cloudy:     { light: ['#EFF2F7', '#DAE2EF'], dark: ['#141B2A', '#1C2435'] },
  rain:       { light: ['#E4EDF7', '#C5D8EE'], dark: ['#0F1A2A', '#131E32'] },
  snow:       { light: ['#EEF2FF', '#D8E2FF'], dark: ['#10142C', '#181C44'] },
  storm:      { light: ['#2A2838', '#1C1A2A'], dark: ['#0A0A14', '#14121E'] },
};

// Text colors that contrast against each gradient
const TEXT_COLOR: Record<ConditionGroup, { light: string; dark: string }> = {
  sunny:      { light: '#2A2000', dark: '#F5E8B0' },
  clearNight: { light: '#E8F0FF', dark: '#E0EAFF' },
  cloudy:     { light: '#1A2233', dark: '#D8E2F0' },
  rain:       { light: '#0E1E34', dark: '#C8D8F0' },
  snow:       { light: '#10183A', dark: '#C8D4FF' },
  storm:      { light: '#E8E0F0', dark: '#D8D0EE' },
};

const SUB_OPACITY: Record<ConditionGroup, number> = {
  sunny:      0.65,
  clearNight: 0.70,
  cloudy:     0.65,
  rain:       0.68,
  snow:       0.68,
  storm:      0.72,
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function updatedLabel(fetchedAt: number): string {
  const sec = Math.round((Date.now() - fetchedAt) / 1_000);
  if (sec < 90)          return 'Just now';
  const min = Math.round(sec / 60);
  if (min < 60)          return `${min}m ago`;
  return `${Math.round(min / 60)}h ago`;
}

// ── Sub-components ────────────────────────────────────────────────────────────

function DetailPill({
  icon,
  label,
  textColor,
  pillBg,
}: {
  icon: string;
  label: string;
  textColor: string;
  pillBg: string;
}) {
  return (
    <View style={[pillStyles.pill, { backgroundColor: pillBg }]}>
      <Feather name={icon as any} size={12} color={textColor} style={{ opacity: 0.85 }} />
      <Text style={[pillStyles.text, { color: textColor }]}>{label}</Text>
    </View>
  );
}

function ForecastDay({
  day,
  textColor,
  isDay,
}: {
  day: DayForecast;
  textColor: string;
  isDay: boolean;
}) {
  const icon = wmoIcon(day.code, isDay);
  return (
    <View style={forecastStyles.col}>
      <Text style={[forecastStyles.label, { color: textColor, opacity: 0.65 }]}>
        {day.label}
      </Text>
      <Feather
        name={icon as any}
        size={18}
        color={textColor}
        style={{ opacity: 0.9, marginVertical: 4 }}
      />
      <Text style={[forecastStyles.high, { color: textColor }]}>
        {day.tempMaxF}°
      </Text>
      <Text style={[forecastStyles.low, { color: textColor, opacity: 0.55 }]}>
        {day.tempMinF}°
      </Text>
    </View>
  );
}

// ── Skeleton ──────────────────────────────────────────────────────────────────

function WeatherSkeleton({ isDark }: { isDark: boolean }) {
  const bg    = isDark ? '#1C2233' : '#EFF2F5';
  const pulse = isDark ? '#253040' : '#DDE4EE';
  return (
    <View style={[skStyles.card, { backgroundColor: bg }]}>
      <View style={[skStyles.bar, { width: 160, backgroundColor: pulse }]} />
      <View style={[skStyles.bar, { width: 80, height: 64, marginTop: 16, alignSelf: 'center', backgroundColor: pulse }]} />
      <View style={[skStyles.bar, { width: 120, alignSelf: 'center', backgroundColor: pulse }]} />
      <View style={{ flexDirection: 'row', gap: 8, justifyContent: 'center', marginTop: 14 }}>
        {[60, 72, 56].map(w => (
          <View key={w} style={[skStyles.pill, { width: w, backgroundColor: pulse }]} />
        ))}
      </View>
    </View>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function WeatherCard() {
  const scheme  = useColorScheme();
  const isDark  = scheme === 'dark';
  const { status, data, reload } = useWeather();

  const handleReload = useCallback(() => reload(), [reload]);

  // Loading skeleton
  if (status === 'loading' || status === 'idle') {
    return <WeatherSkeleton isDark={isDark} />;
  }

  // Silently hide if permission denied or persistent error with no cached data
  if (status === 'denied' || (status === 'error' && !data)) {
    return null;
  }

  if (!data) return null;

  const group      = wmoGroup(data.conditionCode, data.isDay);
  const gradColors = GRADIENT[group][isDark ? 'dark' : 'light'];
  const textColor  = TEXT_COLOR[group][isDark ? 'dark' : 'light'];
  const subOpacity = SUB_OPACITY[group];
  const condIcon   = wmoIcon(data.conditionCode, data.isDay);
  const pillBg     = isDark
    ? 'rgba(255,255,255,0.10)'
    : 'rgba(0,0,0,0.07)';

  return (
    <View style={cardStyles.shadow}>
      <LinearGradient
        colors={gradColors}
        start={{ x: 0, y: 0 }}
        end={{ x: 0.3, y: 1 }}
        style={cardStyles.card}
      >
        {/* ── Location row ────────────────────────────────────────────────── */}
        <View style={cardStyles.locationRow}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 5, flex: 1 }}>
            <Feather name="map-pin" size={11} color={textColor} style={{ opacity: subOpacity }} />
            <Text
              style={[cardStyles.locationText, { color: textColor, opacity: subOpacity }]}
              numberOfLines={1}
            >
              {data.city}{data.region ? `, ${data.region}` : ''}
            </Text>
          </View>
          <TouchableOpacity
            onPress={handleReload}
            hitSlop={{ top: 12, bottom: 12, left: 12, right: 12 }}
            activeOpacity={0.6}
            style={{ flexDirection: 'row', alignItems: 'center', gap: 4 }}
          >
            <Text style={[cardStyles.updatedText, { color: textColor, opacity: subOpacity }]}>
              {updatedLabel(data.fetchedAt)}
            </Text>
            <Feather name="refresh-cw" size={11} color={textColor} style={{ opacity: subOpacity }} />
          </TouchableOpacity>
        </View>

        {/* ── Hero: icon + temperature + condition ────────────────────────── */}
        <View style={cardStyles.heroSection}>
          <Feather
            name={condIcon as any}
            size={42}
            color={textColor}
            style={{ opacity: 0.92, marginBottom: 6 }}
          />
          <Text style={[cardStyles.temperature, { color: textColor }]}>
            {data.tempF}°
          </Text>
          <Text style={[cardStyles.condition, { color: textColor, opacity: subOpacity }]}>
            {data.conditionLabel}
          </Text>
          <Text style={[cardStyles.feelsLike, { color: textColor, opacity: subOpacity - 0.08 }]}>
            Feels like {data.feelsLikeF}°
          </Text>
        </View>

        {/* ── Detail pills ────────────────────────────────────────────────── */}
        <View style={cardStyles.pillRow}>
          <DetailPill icon="droplet" label={`${data.humidity}%`} textColor={textColor} pillBg={pillBg} />
          <DetailPill icon="wind"    label={`${data.windMph} mph`} textColor={textColor} pillBg={pillBg} />
        </View>

        {/* ── Divider ─────────────────────────────────────────────────────── */}
        <View style={[cardStyles.divider, { backgroundColor: textColor, opacity: 0.12 }]} />

        {/* ── 4-day forecast strip ────────────────────────────────────────── */}
        <View style={cardStyles.forecastRow}>
          {data.forecast.map(day => (
            <ForecastDay
              key={day.label}
              day={day}
              textColor={textColor}
              isDay={data.isDay}
            />
          ))}
        </View>
      </LinearGradient>
    </View>
  );
}

// ── Styles ────────────────────────────────────────────────────────────────────

const cardStyles = StyleSheet.create({
  shadow: {
    borderRadius: 16,
    marginBottom: 20,
    // iOS shadow
    shadowColor: '#000',
    shadowOpacity: 0.12,
    shadowRadius: 12,
    shadowOffset: { width: 0, height: 4 },
    // Android elevation
    elevation: 4,
  },
  card: {
    borderRadius: 16,
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 18,
    overflow: 'hidden',
  },
  locationRow: {
    flexDirection: 'row',
    alignItems: 'center',
    marginBottom: 16,
  },
  locationText: {
    fontSize: 13,
    ...font('medium'),
    letterSpacing: 0.1,
    flex: 1,
  },
  updatedText: {
    fontSize: 11,
    ...font('regular'),
  },
  heroSection: {
    alignItems: 'center',
    paddingVertical: 4,
    marginBottom: 16,
  },
  temperature: {
    fontSize: 80,
    lineHeight: 88,
    ...fontSerif('bold'),
    letterSpacing: -3,
    includeFontPadding: false,
  },
  condition: {
    fontSize: 17,
    ...font('medium'),
    marginTop: 2,
    letterSpacing: 0.1,
  },
  feelsLike: {
    fontSize: 13,
    ...font('regular'),
    marginTop: 4,
  },
  pillRow: {
    flexDirection: 'row',
    justifyContent: 'center',
    gap: 10,
    marginBottom: 18,
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    marginBottom: 14,
  },
  forecastRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
  },
});

const pillStyles = StyleSheet.create({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 5,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 20,
  },
  text: {
    fontSize: 12,
    ...font('medium'),
    letterSpacing: 0.1,
  },
});

const forecastStyles = StyleSheet.create({
  col: {
    flex: 1,
    alignItems: 'center',
    gap: 1,
  },
  label: {
    fontSize: 11,
    ...font('medium'),
    letterSpacing: 0.3,
    textTransform: 'uppercase',
  },
  high: {
    fontSize: 14,
    ...font('semibold'),
    letterSpacing: -0.3,
  },
  low: {
    fontSize: 12,
    ...font('regular'),
  },
});

const skStyles = StyleSheet.create({
  card: {
    borderRadius: 16,
    paddingHorizontal: 18,
    paddingTop: 16,
    paddingBottom: 18,
    marginBottom: 20,
  },
  bar: {
    height: 14,
    borderRadius: 7,
    marginBottom: 6,
  },
  pill: {
    height: 30,
    borderRadius: 15,
  },
});
