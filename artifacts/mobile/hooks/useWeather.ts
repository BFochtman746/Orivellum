/**
 * useWeather — location-aware weather hook for the Orivellum dashboard.
 *
 * Requests foreground-only location permission once, fetches the user's
 * current position (last-known for instant response, then a fresh fix),
 * reverse-geocodes to city/region, and calls Open-Meteo for current
 * conditions + a 4-day forecast.
 *
 * Results are cached in memory for 15 minutes so repeated renders and
 * app-foreground restores don't trigger unnecessary network calls.
 *
 * No API key required — Open-Meteo is free and anonymous.
 */

import { useEffect, useRef, useState } from 'react';
import { AppState } from 'react-native';
import * as Location from 'expo-location';

// ── Types ──────────────────────────────────────────────────────────────────────

export type WeatherStatus = 'idle' | 'loading' | 'ok' | 'denied' | 'error';

export interface DayForecast {
  label: string;       // "Today" | "Tomorrow" | "Mon" …
  tempMaxF: number;
  tempMinF: number;
  code: number;
}

export interface HourlyPoint {
  hour: number;        // 0–23
  label: string;       // "Now", "2 PM", "11 PM"
  tempF: number;
  code: number;
  precipProb: number;  // 0–100 percent
}

export interface WeatherData {
  city: string;
  region: string;
  tempF: number;
  feelsLikeF: number;
  conditionCode: number;
  conditionLabel: string;
  humidity: number;
  windMph: number;
  isDay: boolean;
  forecast: DayForecast[];
  hourly: HourlyPoint[];   // next 24 hours starting from current hour
  fetchedAt: number;       // Date.now() — used for cache expiry
}

// ── WMO weather code → human label ───────────────────────────────────────────

export function wmoLabel(code: number): string {
  if (code === 0)                   return 'Clear sky';
  if (code === 1)                   return 'Mainly clear';
  if (code === 2)                   return 'Partly cloudy';
  if (code === 3)                   return 'Overcast';
  if (code >= 45 && code <= 48)     return 'Foggy';
  if (code >= 51 && code <= 55)     return 'Drizzle';
  if (code >= 56 && code <= 57)     return 'Freezing drizzle';
  if (code >= 61 && code <= 63)     return 'Rain';
  if (code >= 64 && code <= 67)     return 'Heavy rain';
  if (code >= 71 && code <= 73)     return 'Snow';
  if (code >= 74 && code <= 77)     return 'Heavy snow';
  if (code >= 80 && code <= 82)     return 'Rain showers';
  if (code >= 85 && code <= 86)     return 'Snow showers';
  if (code === 95)                  return 'Thunderstorm';
  if (code >= 96 && code <= 99)     return 'Thunderstorm & hail';
  return 'Unknown';
}

// ── WMO code → Feather icon name ─────────────────────────────────────────────

export function wmoIcon(code: number, isDay = true): string {
  if (code === 0 || code === 1)         return isDay ? 'sun' : 'moon';
  if (code === 2)                       return 'cloud';
  if (code === 3)                       return 'cloud';
  if (code >= 45 && code <= 48)         return 'wind';
  if (code >= 51 && code <= 57)         return 'cloud-drizzle';
  if (code >= 61 && code <= 67)         return 'cloud-rain';
  if (code >= 71 && code <= 77)         return 'cloud-snow';
  if (code >= 80 && code <= 82)         return 'cloud-rain';
  if (code >= 85 && code <= 86)         return 'cloud-snow';
  if (code >= 95)                       return 'zap';
  return 'cloud';
}

// ── Condition group (for gradient selection) ──────────────────────────────────

export type ConditionGroup =
  | 'sunny'       // 0-1, isDay
  | 'clearNight'  // 0-1, night
  | 'cloudy'      // 2-3, fog 45-48
  | 'rain'        // drizzle, rain, showers 51-82
  | 'snow'        // 71-86
  | 'storm';      // 95-99

export function wmoGroup(code: number, isDay: boolean): ConditionGroup {
  if (code <= 1)                          return isDay ? 'sunny' : 'clearNight';
  if (code <= 48)                         return 'cloudy';
  if (code >= 71 && code <= 77)           return 'snow';
  if (code >= 85 && code <= 86)           return 'snow';
  if (code >= 95)                         return 'storm';
  return 'rain';
}

// ── Open-Meteo fetch ──────────────────────────────────────────────────────────

const OPEN_METEO =
  'https://api.open-meteo.com/v1/forecast';

async function fetchWeather(lat: number, lon: number): Promise<any> {
  const params = new URLSearchParams({
    latitude:          lat.toFixed(5),
    longitude:         lon.toFixed(5),
    current:           [
      'temperature_2m',
      'apparent_temperature',
      'weathercode',
      'windspeed_10m',
      'relative_humidity_2m',
      'is_day',
    ].join(','),
    hourly:            [
      'temperature_2m',
      'weathercode',
      'precipitation_probability',
    ].join(','),
    daily:             [
      'temperature_2m_max',
      'temperature_2m_min',
      'weathercode',
    ].join(','),
    temperature_unit:  'fahrenheit',
    wind_speed_unit:   'mph',
    timezone:          'auto',
    forecast_days:     '2',   // 2 days → 48 hourly entries; enough for next-24h view
  });
  const r = await fetch(`${OPEN_METEO}?${params}`);
  if (!r.ok) throw new Error(`Open-Meteo HTTP ${r.status}`);
  return r.json();
}

// ── Cache ─────────────────────────────────────────────────────────────────────

const CACHE_MS = 15 * 60 * 1_000; // 15 minutes

const DAY_ABBR = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'] as const;

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useWeather() {
  const [status, setStatus] = useState<WeatherStatus>('idle');
  const [data,   setData]   = useState<WeatherData | null>(null);
  const cache = useRef<WeatherData | null>(null);
  const loading = useRef(false);

  const load = async (force = false) => {
    // Serve from cache when still fresh
    if (
      !force &&
      cache.current &&
      Date.now() - cache.current.fetchedAt < CACHE_MS
    ) {
      setData(cache.current);
      setStatus('ok');
      return;
    }

    // Guard against concurrent loads
    if (loading.current) return;
    loading.current = true;
    setStatus('loading');

    try {
      // ── 1. Permission ──────────────────────────────────────────────────────
      const { status: perm } = await Location.requestForegroundPermissionsAsync();
      if (perm !== 'granted') {
        setStatus('denied');
        return;
      }

      // ── 2. Position: last-known first (instant), then a fresh fix ─────────
      let pos: Location.LocationObject | null =
        await Location.getLastKnownPositionAsync({ maxAge: 5 * 60 * 1_000 });
      if (!pos) {
        pos = await Location.getCurrentPositionAsync({
          accuracy: Location.Accuracy.Balanced,
        });
      }
      if (!pos) throw new Error('No position available');

      const { latitude: lat, longitude: lon } = pos.coords;

      // ── 3. Parallel: geocode + weather ────────────────────────────────────
      const [geocode, wx] = await Promise.all([
        Location.reverseGeocodeAsync({ latitude: lat, longitude: lon }),
        fetchWeather(lat, lon),
      ]);

      const place = geocode[0];
      const cur   = wx.current;
      const daily = wx.daily;

      const forecast: DayForecast[] = (daily.time as string[])
        .slice(0, 4)
        .map((dateStr: string, i: number) => {
          const d    = new Date(dateStr + 'T12:00:00');
          const label =
            i === 0 ? 'Today' : i === 1 ? 'Tomorrow' : DAY_ABBR[d.getDay()];
          return {
            label,
            tempMaxF: Math.round(daily.temperature_2m_max[i]),
            tempMinF: Math.round(daily.temperature_2m_min[i]),
            code:     daily.weathercode[i],
          };
        });

      // Build hourly array: next 24 hours starting from current hour
      const hourly: HourlyPoint[] = [];
      if (wx.hourly) {
        const nowHour = new Date().getHours();
        const times   = wx.hourly.time            as string[];
        const temps   = wx.hourly.temperature_2m  as number[];
        const codes   = wx.hourly.weathercode     as number[];
        const precs   = wx.hourly.precipitation_probability as number[];

        // Find first index whose hour >= current hour (within today or early hours of tomorrow)
        const todayPrefix = new Date().toISOString().slice(0, 10); // "2026-08-08"
        const startIdx = times.findIndex((t) => t.startsWith(todayPrefix) && parseInt(t.slice(11, 13), 10) >= nowHour);
        const baseIdx  = startIdx >= 0 ? startIdx : 0;

        for (let i = 0; i < 24 && baseIdx + i < times.length; i++) {
          const idx   = baseIdx + i;
          const hour  = parseInt(times[idx].slice(11, 13), 10);
          const isPM  = hour >= 12;
          const h12   = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
          const label = i === 0 ? 'Now' : `${h12} ${isPM ? 'PM' : 'AM'}`;
          hourly.push({
            hour,
            label,
            tempF:      Math.round(temps[idx]),
            code:       codes[idx],
            precipProb: precs[idx] ?? 0,
          });
        }
      }

      const result: WeatherData = {
        city:           place?.city ?? place?.district ?? place?.name ?? 'Your Location',
        region:         place?.region ?? place?.country ?? '',
        tempF:          Math.round(cur.temperature_2m),
        feelsLikeF:     Math.round(cur.apparent_temperature),
        conditionCode:  cur.weathercode,
        conditionLabel: wmoLabel(cur.weathercode),
        humidity:       Math.round(cur.relative_humidity_2m),
        windMph:        Math.round(cur.windspeed_10m),
        isDay:          cur.is_day === 1,
        forecast,
        hourly,
        fetchedAt:      Date.now(),
      };

      cache.current = result;
      setData(result);
      setStatus('ok');
    } catch (e) {
      console.warn('[useWeather]', e);
      // If we have stale cache, surface it and mark ok rather than error
      if (cache.current) {
        setData(cache.current);
        setStatus('ok');
      } else {
        setStatus('error');
      }
    } finally {
      loading.current = false;
    }
  };

  // Initial load on mount
  useEffect(() => { load(); }, []);

  // Refresh when the app returns to the foreground — but only when the
  // 15-minute cache has expired.  Mirrors the AppState pattern used in
  // useMailAttentionCount.ts so the approach is consistent across hooks.
  useEffect(() => {
    const sub = AppState.addEventListener('change', (nextState) => {
      if (nextState === 'active') {
        const cacheAge = cache.current ? Date.now() - cache.current.fetchedAt : Infinity;
        if (cacheAge >= CACHE_MS) {
          load();
        }
      }
    });
    return () => sub.remove();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return { status, data, reload: () => load(true) };
}
