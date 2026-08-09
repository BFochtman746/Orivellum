/**
 * useWeather — browser version of the mobile weather hook.
 *
 * Uses the browser Geolocation API (permission prompt on first use),
 * reverse-geocodes via BigDataCloud's free client endpoint (no key, CORS
 * enabled), and calls Open-Meteo for current conditions + 4-day forecast +
 * next-24h hourly. Results cached in module memory for 15 minutes so
 * navigating away and back doesn't refetch.
 *
 * No API keys required — both services are free and anonymous.
 */

import { useEffect, useRef, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

export type WeatherStatus = "idle" | "loading" | "ok" | "denied" | "error" | "unsupported";

export interface DayForecast {
  label: string; // "Today" | "Tomorrow" | "Mon" …
  tempMaxF: number;
  tempMinF: number;
  code: number;
}

export interface HourlyPoint {
  hour: number;       // 0–23
  label: string;      // "Now", "2 PM", "11 PM"
  tempF: number;
  code: number;
  precipProb: number; // 0–100
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
  hourly: HourlyPoint[];
  fetchedAt: number;
}

// ── WMO weather code helpers (mirrors the mobile app) ────────────────────────

export function wmoLabel(code: number): string {
  if (code === 0) return "Clear sky";
  if (code === 1) return "Mainly clear";
  if (code === 2) return "Partly cloudy";
  if (code === 3) return "Overcast";
  if (code >= 45 && code <= 48) return "Foggy";
  if (code >= 51 && code <= 55) return "Drizzle";
  if (code >= 56 && code <= 57) return "Freezing drizzle";
  if (code >= 61 && code <= 63) return "Rain";
  if (code >= 64 && code <= 67) return "Heavy rain";
  if (code >= 71 && code <= 73) return "Snow";
  if (code >= 74 && code <= 77) return "Heavy snow";
  if (code >= 80 && code <= 82) return "Rain showers";
  if (code >= 85 && code <= 86) return "Snow showers";
  if (code === 95) return "Thunderstorm";
  if (code >= 96 && code <= 99) return "Thunderstorm & hail";
  return "Unknown";
}

export type ConditionGroup = "sunny" | "clearNight" | "cloudy" | "rain" | "snow" | "storm";

export function wmoGroup(code: number, isDay: boolean): ConditionGroup {
  if (code <= 1) return isDay ? "sunny" : "clearNight";
  if (code <= 48) return "cloudy";
  if (code >= 71 && code <= 77) return "snow";
  if (code >= 85 && code <= 86) return "snow";
  if (code >= 95) return "storm";
  return "rain";
}

// ── Hourly parser (pure — same logic as mobile buildHourlyPoints) ────────────

export function buildHourlyPoints(
  times: string[],
  temps: number[],
  codes: number[],
  precs: number[],
  nowLocalHour: number,
  localDateStr: string,
): HourlyPoint[] {
  const startIdx = times.findIndex(
    (t) => t.startsWith(localDateStr) && parseInt(t.slice(11, 13), 10) >= nowLocalHour,
  );
  const baseIdx = startIdx >= 0 ? startIdx : 0;

  const result: HourlyPoint[] = [];
  for (let i = 0; i < 24 && baseIdx + i < times.length; i++) {
    const idx = baseIdx + i;
    const hour = parseInt(times[idx].slice(11, 13), 10);
    const isPM = hour >= 12;
    const h12 = hour === 0 ? 12 : hour > 12 ? hour - 12 : hour;
    const label = i === 0 ? "Now" : `${h12} ${isPM ? "PM" : "AM"}`;
    result.push({
      hour,
      label,
      tempF: Math.round(temps[idx]),
      code: codes[idx],
      precipProb: precs[idx] ?? 0,
    });
  }
  return result;
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

const OPEN_METEO = "https://api.open-meteo.com/v1/forecast";
// Free, anonymous, CORS-enabled reverse geocoder intended for client-side use.
const REVERSE_GEO = "https://api.bigdatacloud.net/data/reverse-geocode-client";

async function fetchWeather(lat: number, lon: number): Promise<any> {
  const params = new URLSearchParams({
    latitude: lat.toFixed(5),
    longitude: lon.toFixed(5),
    current: [
      "temperature_2m",
      "apparent_temperature",
      "weathercode",
      "windspeed_10m",
      "relative_humidity_2m",
      "is_day",
    ].join(","),
    hourly: ["temperature_2m", "weathercode", "precipitation_probability"].join(","),
    daily: ["temperature_2m_max", "temperature_2m_min", "weathercode"].join(","),
    temperature_unit: "fahrenheit",
    wind_speed_unit: "mph",
    timezone: "auto",
    // 4 days feeds the Today+3-day forecast strip; hourly only needs the
    // first 48 entries (next-24h view) and buildHourlyPoints caps at 24.
    forecast_days: "4",
  });
  const r = await fetch(`${OPEN_METEO}?${params}`);
  if (!r.ok) throw new Error(`Open-Meteo HTTP ${r.status}`);
  return r.json();
}

async function reverseGeocode(lat: number, lon: number): Promise<{ city: string; region: string }> {
  try {
    const r = await fetch(
      `${REVERSE_GEO}?latitude=${lat.toFixed(5)}&longitude=${lon.toFixed(5)}&localityLanguage=en`,
    );
    if (!r.ok) throw new Error(`reverse-geocode HTTP ${r.status}`);
    const j = await r.json();
    return {
      city: j.city || j.locality || "Your location",
      region: j.principalSubdivision || j.countryName || "",
    };
  } catch {
    // Non-fatal — weather still shows without a place name.
    return { city: "Your location", region: "" };
  }
}

function getPosition(): Promise<GeolocationPosition> {
  return new Promise((resolve, reject) => {
    navigator.geolocation.getCurrentPosition(resolve, reject, {
      enableHighAccuracy: false,
      timeout: 15_000,
      maximumAge: 5 * 60 * 1_000, // accept a cached fix up to 5 min old
    });
  });
}

// ── Cache (module-level so it survives route changes) ────────────────────────

const CACHE_MS = 15 * 60 * 1_000;
let _cache: WeatherData | null = null;

const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useWeather() {
  const [status, setStatus] = useState<WeatherStatus>("idle");
  const [data, setData] = useState<WeatherData | null>(null);
  const loading = useRef(false);

  const load = async (force = false) => {
    if (!("geolocation" in navigator)) {
      setStatus("unsupported");
      return;
    }
    if (!force && _cache && Date.now() - _cache.fetchedAt < CACHE_MS) {
      setData(_cache);
      setStatus("ok");
      return;
    }
    if (loading.current) return;
    loading.current = true;
    setStatus("loading");

    try {
      const pos = await getPosition();
      const { latitude: lat, longitude: lon } = pos.coords;

      const [place, wx] = await Promise.all([reverseGeocode(lat, lon), fetchWeather(lat, lon)]);

      const cur = wx.current;
      const daily = wx.daily;

      const forecast: DayForecast[] = (daily.time as string[]).slice(0, 4).map((dateStr, i) => {
        const d = new Date(dateStr + "T12:00:00");
        const label = i === 0 ? "Today" : i === 1 ? "Tomorrow" : DAY_ABBR[d.getDay()];
        return {
          label,
          tempMaxF: Math.round(daily.temperature_2m_max[i]),
          tempMinF: Math.round(daily.temperature_2m_min[i]),
          code: daily.weathercode[i],
        };
      });

      let hourly: HourlyPoint[] = [];
      if (wx.hourly) {
        const now = new Date();
        // Local date components — toISOString() would give the UTC date.
        const localDateStr = [
          now.getFullYear(),
          String(now.getMonth() + 1).padStart(2, "0"),
          String(now.getDate()).padStart(2, "0"),
        ].join("-");
        hourly = buildHourlyPoints(
          wx.hourly.time,
          wx.hourly.temperature_2m,
          wx.hourly.weathercode,
          wx.hourly.precipitation_probability,
          now.getHours(),
          localDateStr,
        );
      }

      const result: WeatherData = {
        city: place.city,
        region: place.region,
        tempF: Math.round(cur.temperature_2m),
        feelsLikeF: Math.round(cur.apparent_temperature),
        conditionCode: cur.weathercode,
        conditionLabel: wmoLabel(cur.weathercode),
        humidity: Math.round(cur.relative_humidity_2m),
        windMph: Math.round(cur.windspeed_10m),
        isDay: cur.is_day === 1,
        forecast,
        hourly,
        fetchedAt: Date.now(),
      };

      _cache = result;
      setData(result);
      setStatus("ok");
    } catch (e: any) {
      // GeolocationPositionError code 1 = permission denied
      if (e && typeof e.code === "number" && e.code === 1) {
        setStatus("denied");
      } else if (_cache) {
        setData(_cache);
        setStatus("ok");
      } else {
        console.warn("[useWeather]", e);
        setStatus("error");
      }
    } finally {
      loading.current = false;
    }
  };

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, data, reload: () => load(true) };
}
