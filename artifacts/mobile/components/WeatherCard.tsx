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

import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Animated,
  Modal,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TouchableOpacity,
  useColorScheme,
  useWindowDimensions,
  View,
} from 'react-native';
import Svg, { Path, Polyline, Rect } from 'react-native-svg';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
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
  type HourlyPoint,
  type WeatherData,
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

// ── HourlyItem ────────────────────────────────────────────────────────────────

function HourlyItem({
  point,
  textColor,
}: {
  point: HourlyPoint;
  textColor: string;
}) {
  const isDaytime = point.hour >= 6 && point.hour < 20;
  const icon = wmoIcon(point.code, isDaytime);
  return (
    <View style={hourlyStyles.item}>
      <Text style={[hourlyStyles.hourLabel, { color: textColor }]}>{point.label}</Text>
      <Feather
        name={icon as any}
        size={22}
        color={textColor}
        style={{ opacity: 0.88, marginVertical: 8 }}
      />
      <Text style={[hourlyStyles.hourTemp, { color: textColor }]}>{point.tempF}°</Text>
      {point.precipProb > 10 && (
        <Text style={[hourlyStyles.hourPrecip, { color: textColor }]}>
          {point.precipProb}%
        </Text>
      )}
    </View>
  );
}

// ── TemperatureSparkline ──────────────────────────────────────────────────────
// Ambient temperature arc that sits above the hourly scroll. SVG polyline for
// the curve + translucent area fill + small precip bars at the bottom edge.

const _SPARK_H     = 60;
const _SPARK_PAD_T = 10;  // top padding (curve doesn't kiss the top edge)
const _SPARK_PAD_B = 22;  // bottom padding — lower 10 px reserved for precip bars

function TemperatureSparkline({
  hourly,
  color,
}: {
  hourly: HourlyPoint[];
  color: string;
}) {
  const { width } = useWindowDimensions();
  if (hourly.length < 2) return null;

  const n     = hourly.length;
  const temps = hourly.map(p => p.tempF);
  const tMin  = Math.min(...temps);
  const tMax  = Math.max(...temps);
  const tRange = tMax - tMin || 1;

  // Pixel coords
  const yUsable = _SPARK_H - _SPARK_PAD_T - _SPARK_PAD_B;
  const px = (i: number) => (i / (n - 1)) * width;
  const py = (t: number) => _SPARK_PAD_T + (1 - (t - tMin) / tRange) * yUsable;

  // SVG points string for the polyline
  const ptStr = hourly
    .map((p, i) => `${px(i).toFixed(1)},${py(p.tempF).toFixed(1)}`)
    .join(' ');

  // Filled area under the curve → closes at bottom
  const curveBot = _SPARK_H - _SPARK_PAD_B;   // top of the precip-bar zone
  const areaD =
    `M ${px(0).toFixed(1)},${py(hourly[0].tempF).toFixed(1)} ` +
    hourly
      .slice(1)
      .map((p, i) => `L ${px(i + 1).toFixed(1)},${py(p.tempF).toFixed(1)}`)
      .join(' ') +
    ` L ${px(n - 1).toFixed(1)},${curveBot} L ${px(0).toFixed(1)},${curveBot} Z`;

  // Precip bars: occupy the bottom 10 px, height scaled to precipProb
  const BAR_ZONE_H = 10;
  const precipTopY  = _SPARK_H - _SPARK_PAD_B;  // baseline of bar zone
  const barW        = Math.max(width / n - 1.5, 2);

  return (
    <Svg width={width} height={_SPARK_H} style={sparkStyles.svg}>
      {/* Filled area under the temperature curve */}
      <Path d={areaD} fill={color} fillOpacity={0.07} />

      {/* Temperature polyline */}
      <Polyline
        points={ptStr}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeOpacity={0.6}
        strokeLinejoin="round"
        strokeLinecap="round"
      />

      {/* Precipitation bars */}
      {hourly.map((p, i) => {
        if (p.precipProb <= 10) return null;
        const h = Math.round((p.precipProb / 100) * BAR_ZONE_H);
        return (
          <Rect
            key={i}
            x={(px(i) - barW / 2).toFixed(1)}
            y={(precipTopY + BAR_ZONE_H - h).toFixed(1)}
            width={barW.toFixed(1)}
            height={h}
            fill="#6aaeff"
            fillOpacity={0.50}
            rx={1}
          />
        );
      })}
    </Svg>
  );
}

const sparkStyles = StyleSheet.create({
  svg: {
    // Negative left/right margin so the SVG bleeds to the sheet edges
    // (sheet has paddingHorizontal: 0, so no adjustment needed here)
    alignSelf: 'stretch',
    marginBottom: 8,
  },
});

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

// ── HourlySheet ───────────────────────────────────────────────────────────────
// Matches the DiagnosticsSheet spring pattern: animationType="none", spring slide
// in, timing slide out, backdrop fades in parallel.

const _HOURLY_SHEET_H = 380;

function HourlySheet({
  visible,
  onClose,
  data,
  sheetBg,
  sheetText,
}: {
  visible: boolean;
  onClose: () => void;
  data: WeatherData;
  sheetBg: string;
  sheetText: string;
}) {
  const insets = useSafeAreaInsets();
  const [rendered, setRendered] = useState(false);

  const slideAnim = useRef(new Animated.Value(_HOURLY_SHEET_H + 60)).current;
  const fadeAnim  = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (visible) {
      setRendered(true);
      Animated.parallel([
        Animated.spring(slideAnim, { toValue: 0, useNativeDriver: true, tension: 85, friction: 13 }),
        Animated.timing(fadeAnim,  { toValue: 1, duration: 180, useNativeDriver: true }),
      ]).start();
    } else {
      Animated.parallel([
        Animated.timing(slideAnim, { toValue: _HOURLY_SHEET_H + 60, duration: 220, useNativeDriver: true }),
        Animated.timing(fadeAnim,  { toValue: 0, duration: 180, useNativeDriver: true }),
      ]).start(() => setRendered(false));
    }
  }, [visible]);

  if (!rendered) return null;

  return (
    <Modal transparent visible={rendered} animationType="none" onRequestClose={onClose} statusBarTranslucent>
      {/* Backdrop */}
      <Animated.View
        style={[StyleSheet.absoluteFill, { backgroundColor: 'rgba(0,0,0,0.45)', opacity: fadeAnim }]}
        pointerEvents={visible ? 'auto' : 'none'}
      >
        <Pressable style={StyleSheet.absoluteFill} onPress={onClose} />
      </Animated.View>

      {/* Sheet */}
      <Animated.View
        style={[
          hourlyStyles.sheet,
          {
            backgroundColor: sheetBg,
            paddingBottom: insets.bottom + 20,
            transform: [{ translateY: slideAnim }],
          },
        ]}
        pointerEvents="box-none"
      >
        {/* Drag handle */}
        <View style={[hourlyStyles.handle, { backgroundColor: sheetText }]} />

        {/* Header */}
        <View style={hourlyStyles.sheetHeader}>
          <Text style={[hourlyStyles.sheetTitle, { color: sheetText }]}>
            Hourly Forecast
          </Text>
          <TouchableOpacity onPress={onClose} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
            <Feather name="x" size={18} color={sheetText} style={{ opacity: 0.5 }} />
          </TouchableOpacity>
        </View>

        {/* Location sub-label */}
        <Text style={[hourlyStyles.sheetSub, { color: sheetText }]}>
          {data.city}{data.region ? `, ${data.region}` : ''}
        </Text>

        {/* Temperature sparkline — ambient arc across all 24 hours */}
        <TemperatureSparkline hourly={data.hourly} color={sheetText} />

        {/* Horizontal hourly scroll */}
        <ScrollView
          horizontal
          showsHorizontalScrollIndicator={false}
          contentContainerStyle={hourlyStyles.hourlyScroll}
        >
          {data.hourly.map((pt, i) => (
            <HourlyItem key={i} point={pt} textColor={sheetText} />
          ))}
        </ScrollView>
      </Animated.View>
    </Modal>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

export function WeatherCard() {
  const scheme  = useColorScheme();
  const isDark  = scheme === 'dark';
  const { status, data, reload } = useWeather();
  const [showHourly, setShowHourly] = useState(false);

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

  // Sheet background mirrors the card's gradient top colour, slightly darkened
  const sheetBg      = isDark ? '#0D111A' : '#F4F7FC';
  const sheetText    = isDark ? '#D0DCEE' : '#0E1E34';

  return (
    <>
      {/* ── Tap the card to open hourly forecast ──────────────────────────── */}
      <TouchableOpacity
        activeOpacity={0.96}
        onPress={() => data.hourly.length > 0 && setShowHourly(true)}
        style={{ marginBottom: 0 }}
      >
        <View style={cardStyles.shadow}>
          <LinearGradient
            colors={gradColors}
            start={{ x: 0, y: 0 }}
            end={{ x: 0.3, y: 1 }}
            style={cardStyles.card}
          >
            {/* ── Location row ──────────────────────────────────────────────── */}
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

            {/* ── Hero: icon + temperature + condition ──────────────────────── */}
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

            {/* ── Detail pills ──────────────────────────────────────────────── */}
            <View style={cardStyles.pillRow}>
              <DetailPill icon="droplet" label={`${data.humidity}%`} textColor={textColor} pillBg={pillBg} />
              <DetailPill icon="wind"    label={`${data.windMph} mph`} textColor={textColor} pillBg={pillBg} />
            </View>

            {/* ── Divider ───────────────────────────────────────────────────── */}
            <View style={[cardStyles.divider, { backgroundColor: textColor, opacity: 0.12 }]} />

            {/* ── 4-day forecast strip ──────────────────────────────────────── */}
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

            {/* Tap hint */}
            {data.hourly.length > 0 && (
              <View style={{ alignItems: 'center', marginTop: 10 }}>
                <Feather name="chevron-up" size={14} color={textColor} style={{ opacity: 0.3 }} />
              </View>
            )}
          </LinearGradient>
        </View>
      </TouchableOpacity>

      {/* ── Hourly forecast bottom sheet ──────────────────────────────────── */}
      <HourlySheet
        visible={showHourly}
        onClose={() => setShowHourly(false)}
        data={data}
        sheetBg={sheetBg}
        sheetText={sheetText}
      />
    </>
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

const hourlyStyles = StyleSheet.create({
  sheet: {
    position: 'absolute',
    bottom: 0,
    left: 0,
    right: 0,
    borderTopLeftRadius: 20,
    borderTopRightRadius: 20,
    paddingTop: 12,
    paddingHorizontal: 0,
  },
  handle: {
    width: 36,
    height: 4,
    borderRadius: 2,
    opacity: 0.2,
    alignSelf: 'center',
    marginBottom: 16,
  },
  sheetHeader: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 20,
    marginBottom: 2,
  },
  sheetTitle: {
    fontSize: 17,
    fontWeight: '600',
    letterSpacing: -0.3,
  },
  sheetSub: {
    fontSize: 12,
    opacity: 0.45,
    paddingHorizontal: 20,
    marginBottom: 16,
  },
  hourlyScroll: {
    paddingHorizontal: 16,
    gap: 4,
  },
  item: {
    alignItems: 'center',
    width: 62,
    paddingVertical: 12,
    paddingHorizontal: 6,
    borderRadius: 14,
    backgroundColor: 'rgba(128,128,128,0.07)',
    marginHorizontal: 4,
  },
  hourLabel: {
    fontSize: 11,
    fontWeight: '600',
    letterSpacing: 0.2,
    opacity: 0.65,
  },
  hourTemp: {
    fontSize: 16,
    fontWeight: '700',
    letterSpacing: -0.5,
  },
  hourPrecip: {
    fontSize: 10,
    opacity: 0.55,
    marginTop: 3,
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
