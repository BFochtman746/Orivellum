/**
 * useWeather — browser version of the mobile weather hook.
 *
 * Resolves a location in this order:
 *   1. A manually saved location (localStorage) — survives HTTP-only origins
 *      (Tailscale/LAN) where the browser Geolocation API is blocked.
 *   2. The browser Geolocation API (permission prompt on first use), with
 *      reverse-geocoding via BigDataCloud's free client endpoint.
 *
 * Weather comes from Open-Meteo: current conditions + 4-day forecast +
 * next-24h hourly (temperature_2m, weathercode, precipitation_probability).
 * Results cached in module memory for 15 minutes so navigating away and
 * back doesn't refetch.
 *
 * No API keys required — all services are free and anonymous.
 */

import { useEffect, useRef, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

export type WeatherStatus = "idle" | "loading" | "ok" | "no_location" | "error";

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

export interface SavedLocation {
  lat: number;
  lon: number;
  city: string;
  region: string;
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

// ── Saved manual location (localStorage) ──────────────────────────────────────

const LOC_KEY = "orivellum.weather.location";

export function getSavedLocation(): SavedLocation | null {
  try {
    const raw = localStorage.getItem(LOC_KEY);
    if (!raw) return null;
    const j = JSON.parse(raw);
    if (typeof j?.lat === "number" && typeof j?.lon === "number") return j as SavedLocation;
  } catch {
    /* corrupt entry — ignore */
  }
  return null;
}

function saveLocation(loc: SavedLocation) {
  try {
    localStorage.setItem(LOC_KEY, JSON.stringify(loc));
  } catch {
    /* storage full/blocked — weather still works this session */
  }
}

export function clearSavedLocation() {
  try {
    localStorage.removeItem(LOC_KEY);
  } catch {
    /* ignore */
  }
}

// ── City search (Open-Meteo geocoding, CORS-enabled, no key) ─────────────────

export interface CityResult {
  name: string;
  region: string;
  country: string;
  lat: number;
  lon: number;
}

export async function searchCity(query: string): Promise<CityResult[]> {
  const params = new URLSearchParams({ name: query, count: "5", language: "en", format: "json" });
  const r = await fetch(`https://geocoding-api.open-meteo.com/v1/search?${params}`);
  if (!r.ok) throw new Error(`geocoding HTTP ${r.status}`);
  const j = await r.json();
  return ((j.results ?? []) as any[]).map((c) => ({
    name: c.name,
    region: c.admin1 ?? "",
    country: c.country ?? "",
    lat: c.latitude,
    lon: c.longitude,
  }));
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

/** Whether asking the browser for a GPS fix can possibly work here. */
export function geolocationAvailable(): boolean {
  // Chrome/Safari refuse geolocation on insecure origins (plain-HTTP LAN /
  // Tailscale access); don't offer a button that can never succeed.
  return "geolocation" in navigator && (window.isSecureContext || location.hostname === "localhost");
}

// ── Cache (module-level so it survives route changes) ────────────────────────

const CACHE_MS = 15 * 60 * 1_000;
let _cache: WeatherData | null = null;

const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

function toWeatherData(wx: any, city: string, region: string): WeatherData {
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
    // The API is called with timezone=auto, so hourly timestamps are in the
    // SELECTED CITY's local time — which may differ from the browser's zone
    // for a manually chosen city. Shift "now" by the response's UTC offset
    // and read it with UTC getters to get the city-local hour and date.
    const offsetSec: number = typeof wx.utc_offset_seconds === "number" ? wx.utc_offset_seconds : 0;
    const cityNow = new Date(Date.now() + offsetSec * 1_000);
    const cityDateStr = [
      cityNow.getUTCFullYear(),
      String(cityNow.getUTCMonth() + 1).padStart(2, "0"),
      String(cityNow.getUTCDate()).padStart(2, "0"),
    ].join("-");
    hourly = buildHourlyPoints(
      wx.hourly.time,
      wx.hourly.temperature_2m,
      wx.hourly.weathercode,
      wx.hourly.precipitation_probability,
      cityNow.getUTCHours(),
      cityDateStr,
    );
  }

  return {
    city,
    region,
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
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useWeather() {
  const [status, setStatus] = useState<WeatherStatus>("idle");
  const [data, setData] = useState<WeatherData | null>(null);
  const loading = useRef(false);

  const load = async (force = false) => {
    if (!force && _cache && Date.now() - _cache.fetchedAt < CACHE_MS) {
      setData(_cache);
      setStatus("ok");
      return;
    }
    if (loading.current) return;
    loading.current = true;
    setStatus("loading");

    try {
      const saved = getSavedLocation();
      let result: WeatherData;

      if (saved) {
        const wx = await fetchWeather(saved.lat, saved.lon);
        result = toWeatherData(wx, saved.city, saved.region);
      } else if (geolocationAvailable()) {
        const pos = await getPosition();
        const { latitude: lat, longitude: lon } = pos.coords;
        const [place, wx] = await Promise.all([reverseGeocode(lat, lon), fetchWeather(lat, lon)]);
        result = toWeatherData(wx, place.city, place.region);
      } else {
        // Insecure origin or no geolocation API, and nothing saved yet:
        // the card offers a manual city picker instead.
        setStatus("no_location");
        return;
      }

      _cache = result;
      setData(result);
      setStatus("ok");
    } catch (e: any) {
      // Any GeolocationPositionError (denied / unavailable / timeout) with no
      // saved city → fall back to the manual picker rather than a dead error.
      if (e && typeof e.code === "number" && !getSavedLocation()) {
        setStatus("no_location");
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

  /** Save a manually chosen city and refetch for it. */
  const setLocation = (loc: SavedLocation) => {
    saveLocation(loc);
    _cache = null;
    void load(true);
  };

  /** Forget the manual city and try the browser's location again. */
  const useMyLocation = () => {
    clearSavedLocation();
    _cache = null;
    void load(true);
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, data, reload: () => load(true), setLocation, useMyLocation };
}
