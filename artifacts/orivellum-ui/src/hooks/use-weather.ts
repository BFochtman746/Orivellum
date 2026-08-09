/**
 * useWeather — browser version of the mobile weather hook, upgraded to a
 * "top of the line" forecast function.
 *
 * Resolves a location in this order:
 *   1. A manually saved location (localStorage) — survives HTTP-only origins
 *      (Tailscale/LAN) where the browser Geolocation API is blocked.
 *   2. The browser Geolocation API (permission prompt on first use), with
 *      reverse-geocoding via BigDataCloud's free client endpoint.
 *
 * Weather comes from Open-Meteo (free, keyless, CORS-enabled):
 *   - current conditions incl. gusts, wind direction, pressure, cloud cover
 *   - 15-minute precipitation nowcast for the next 2 hours (radar-blended
 *     where available) → "Rain starting in ~30 min" style headlines
 *   - next-24h hourly (temp, condition, precip prob, UV, visibility,
 *     dew point, pressure trend)
 *   - 7-day daily forecast with sunrise/sunset, UV max, precip probability
 *   - Air Quality API (US AQI) fetched in parallel, non-fatal
 *
 * A small insight engine derives one human headline from all of the above.
 *
 * Caching: stale-while-revalidate. The last result persists to localStorage
 * so the card paints instantly on reload; fresh data (<15 min) is trusted,
 * older data is shown immediately while a background refetch replaces it.
 */

import { useEffect, useRef, useState } from "react";

// ── Types ─────────────────────────────────────────────────────────────────────

export type WeatherStatus = "idle" | "loading" | "ok" | "no_location" | "error";

export interface DayForecast {
  label: string; // "Today" | "Tomorrow" | "Mon" …
  tempMaxF: number;
  tempMinF: number;
  code: number;
  precipProb: number; // daily max probability, 0–100
  uvMax: number;
}

export interface HourlyPoint {
  hour: number;       // 0–23
  label: string;      // "Now", "2 PM", "11 PM"
  tempF: number;
  code: number;
  precipProb: number; // 0–100
}

export interface NowcastInfo {
  kind: "starts" | "ends" | "ongoing";
  minutes: number;    // minutes until the transition (0 for "ongoing" w/o end in window)
  message: string;    // "Rain starting in ~30 min"
  bars: number[];     // next-2h 15-min precipitation intensities (inches)
}

export interface AirQuality {
  aqi: number;        // US AQI
  label: string;      // "Good" | "Moderate" | …
  pm25: number;
}

export type PressureTrend = "rising" | "falling" | "steady" | "unknown";

export interface WeatherData {
  city: string;
  region: string;
  tempF: number;
  feelsLikeF: number;
  conditionCode: number;
  conditionLabel: string;
  humidity: number;
  windMph: number;
  windGustMph: number;
  windDir: string;      // compass: "NW"
  isDay: boolean;
  uvIndex: number;      // current hour
  uvMaxToday: number;
  visibilityMi: number;
  dewPointF: number;
  cloudCover: number;   // 0–100
  pressureInHg: number;
  pressureTrend: PressureTrend;
  sunrise: string;      // "6:00 AM" (city-local)
  sunset: string;
  air: AirQuality | null;
  nowcast: NowcastInfo | null;
  insight: string;      // one derived headline
  forecast: DayForecast[]; // 7 days
  hourly: HourlyPoint[];
  fetchedAt: number;
  /** Which location this snapshot belongs to: "lat,lon" for a saved city,
   * "geo" for browser geolocation. Guards the persisted cache. */
  locKey: string;
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

const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"] as const;

export function compassDir(deg: number): string {
  if (!Number.isFinite(deg)) return "";
  return COMPASS[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
}

export function uvLevel(uv: number): string {
  if (uv < 3) return "Low";
  if (uv < 6) return "Moderate";
  if (uv < 8) return "High";
  if (uv < 11) return "Very high";
  return "Extreme";
}

export function aqiLabel(aqi: number): string {
  if (aqi <= 50) return "Good";
  if (aqi <= 100) return "Moderate";
  if (aqi <= 150) return "Unhealthy (sensitive)";
  if (aqi <= 200) return "Unhealthy";
  if (aqi <= 300) return "Very unhealthy";
  return "Hazardous";
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

// ── 15-minute precipitation nowcast (pure) ────────────────────────────────────

const PRECIP_EPS = 0.004; // inches per 15 min ≈ trace

/** "YYYY-MM-DDTHH:MM" city-local → minutes on a shared linear scale.
 * Both operands are city-local, so interpreting them as UTC is safe — the
 * offset cancels out in differences. */
function localMin(t: string): number {
  return Date.UTC(
    +t.slice(0, 4), +t.slice(5, 7) - 1, +t.slice(8, 10),
    +t.slice(11, 13), +t.slice(14, 16),
  ) / 60_000;
}

export function buildNowcast(
  times: string[],
  precip: (number | null)[],
  cityNowStr: string, // "YYYY-MM-DDTHH:MM" city-local
): NowcastInfo | null {
  if (!times?.length || !precip?.length) return null;
  // Last slot whose start time is <= now (times are city-local strings), i.e.
  // the partially elapsed current slot.
  let base = -1;
  for (let i = 0; i < times.length; i++) {
    if (times[i] <= cityNowStr) base = i;
    else break;
  }
  if (base < 0) base = 0;
  const bars: number[] = [];
  for (let i = base; i < times.length && bars.length < 8; i++) bars.push(precip[i] ?? 0);
  if (!bars.length) return null;

  const nowMin = localMin(cityNowStr);
  // Minutes from the actual city-local instant to the start of bar slot `idx`
  // (never below 1 — the current slot already began).
  const minutesUntil = (idx: number) =>
    Math.max(1, Math.round(localMin(times[base + idx]) - nowMin));

  const wetNow = bars[0] > PRECIP_EPS;
  if (!wetNow) {
    const idx = bars.findIndex((v) => v > PRECIP_EPS);
    if (idx <= 0) return null; // dry for the whole window
    const minutes = minutesUntil(idx);
    return { kind: "starts", minutes, message: `Rain starting in ~${minutes} min`, bars };
  }
  const dryIdx = bars.findIndex((v) => v <= PRECIP_EPS);
  if (dryIdx > 0) {
    const minutes = minutesUntil(dryIdx);
    return { kind: "ends", minutes, message: `Rain stopping in ~${minutes} min`, bars };
  }
  return { kind: "ongoing", minutes: 0, message: "Rain continuing for the next 2 hours", bars };
}

// ── Insight engine (pure) — one headline, highest signal first ────────────────

export function deriveInsight(d: {
  nowcast: NowcastInfo | null;
  hourly: HourlyPoint[];
  uvMaxToday: number;
  isDay: boolean;
  pressureTrend: PressureTrend;
  air: AirQuality | null;
  forecast: DayForecast[];
  sunset: string;
  sunrise: string;
}): string {
  if (d.nowcast) return d.nowcast.message;

  // Next likely rain in the coming 12 h
  const rainy = d.hourly.slice(0, 12).find((h) => h.precipProb >= 55);
  if (rainy && rainy.label !== "Now") return `Rain likely around ${rainy.label}`;

  if (d.air && d.air.aqi > 150) return `Air quality ${d.air.label.toLowerCase()} — AQI ${d.air.aqi}`;

  if (d.isDay && d.uvMaxToday >= 8) return `${uvLevel(d.uvMaxToday)} UV today — peaks at ${Math.round(d.uvMaxToday)}`;

  if (d.pressureTrend === "falling") return "Pressure falling — conditions may turn";

  if (d.forecast.length >= 2) {
    const delta = d.forecast[1].tempMaxF - d.forecast[0].tempMaxF;
    if (delta >= 10) return `Much warmer tomorrow — up to ${d.forecast[1].tempMaxF}°`;
    if (delta <= -10) return `Much cooler tomorrow — high of ${d.forecast[1].tempMaxF}°`;
  }

  return d.isDay ? `Sunset at ${d.sunset}` : `Sunrise at ${d.sunrise}`;
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
const AIR_QUALITY = "https://air-quality-api.open-meteo.com/v1/air-quality";
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
      "wind_gusts_10m",
      "wind_direction_10m",
      "relative_humidity_2m",
      "is_day",
      "precipitation",
      "pressure_msl",
      "cloud_cover",
    ].join(","),
    // Radar-blended short-term precipitation where available; elsewhere
    // Open-Meteo interpolates from the hourly model — still useful signal.
    minutely_15: "precipitation",
    hourly: [
      "temperature_2m",
      "weathercode",
      "precipitation_probability",
      "uv_index",
      "visibility",
      "dew_point_2m",
      "pressure_msl",
    ].join(","),
    daily: [
      "temperature_2m_max",
      "temperature_2m_min",
      "weathercode",
      "sunrise",
      "sunset",
      "uv_index_max",
      "precipitation_probability_max",
      "precipitation_sum",
    ].join(","),
    temperature_unit: "fahrenheit",
    wind_speed_unit: "mph",
    precipitation_unit: "inch",
    timezone: "auto",
    forecast_days: "7",
    // 8 × 15 min = the 2-hour nowcast window (plus a little slack for the
    // partially elapsed current slot).
    forecast_minutely_15: "12",
  });
  const r = await fetch(`${OPEN_METEO}?${params}`);
  if (!r.ok) throw new Error(`Open-Meteo HTTP ${r.status}`);
  return r.json();
}

async function fetchAirQuality(lat: number, lon: number): Promise<AirQuality | null> {
  try {
    const params = new URLSearchParams({
      latitude: lat.toFixed(5),
      longitude: lon.toFixed(5),
      current: "us_aqi,pm2_5",
      timezone: "auto",
    });
    const r = await fetch(`${AIR_QUALITY}?${params}`);
    if (!r.ok) return null;
    const j = await r.json();
    const aqi = j?.current?.us_aqi;
    if (typeof aqi !== "number") return null;
    return { aqi: Math.round(aqi), label: aqiLabel(aqi), pm25: j.current.pm2_5 ?? 0 };
  } catch {
    return null; // non-fatal — the card simply omits the AQI chip
  }
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

// ── Cache (module memory + localStorage, stale-while-revalidate) ─────────────

const CACHE_MS = 15 * 60 * 1_000;       // trusted-fresh window
const STALE_MAX_MS = 3 * 60 * 60 * 1_000; // beyond this, stale data isn't shown
const CACHE_KEY = "orivellum.weather.cache";

let _cache: WeatherData | null = null;

/** A cache entry is usable when its age is sane (0..STALE_MAX — rejects
 * future-dated clocks) and it was fetched for the location we'd fetch now. */
function cacheUsable(c: WeatherData | null, expectedLocKey: string): c is WeatherData {
  if (!c) return false;
  const age = Date.now() - c.fetchedAt;
  if (age < 0 || age > STALE_MAX_MS) return false;
  return c.locKey === expectedLocKey;
}

function currentLocKey(): string {
  const saved = getSavedLocation();
  return saved ? `${saved.lat.toFixed(5)},${saved.lon.toFixed(5)}` : "geo";
}

function readPersistedCache(): WeatherData | null {
  try {
    const raw = localStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const j = JSON.parse(raw) as WeatherData;
    if (typeof j?.fetchedAt !== "number" || !Array.isArray(j?.forecast)) return null;
    return j;
  } catch {
    return null;
  }
}

function persistCache(d: WeatherData) {
  try {
    localStorage.setItem(CACHE_KEY, JSON.stringify(d));
  } catch {
    /* storage full/blocked — memory cache still works this session */
  }
}

const DAY_ABBR = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"] as const;

function fmtClock(isoLocal: string): string {
  // "2026-08-09T06:04" → "6:04 AM" without timezone surprises.
  const hh = parseInt(isoLocal.slice(11, 13), 10);
  const mm = isoLocal.slice(14, 16);
  const isPM = hh >= 12;
  const h12 = hh === 0 ? 12 : hh > 12 ? hh - 12 : hh;
  return `${h12}:${mm} ${isPM ? "PM" : "AM"}`;
}

function toWeatherData(wx: any, air: AirQuality | null, city: string, region: string, locKey: string): WeatherData {
  const cur = wx.current;
  const daily = wx.daily;

  const forecast: DayForecast[] = (daily.time as string[]).slice(0, 7).map((dateStr, i) => {
    const d = new Date(dateStr + "T12:00:00");
    const label = i === 0 ? "Today" : i === 1 ? "Tomorrow" : DAY_ABBR[d.getDay()];
    return {
      label,
      tempMaxF: Math.round(daily.temperature_2m_max[i]),
      tempMinF: Math.round(daily.temperature_2m_min[i]),
      code: daily.weathercode[i],
      precipProb: Math.round(daily.precipitation_probability_max?.[i] ?? 0),
      uvMax: daily.uv_index_max?.[i] ?? 0,
    };
  });

  // The API is called with timezone=auto, so hourly/minutely timestamps are in
  // the SELECTED CITY's local time — which may differ from the browser's zone
  // for a manually chosen city. Shift "now" by the response's UTC offset and
  // read it with UTC getters to get the city-local hour and date.
  const offsetSec: number = typeof wx.utc_offset_seconds === "number" ? wx.utc_offset_seconds : 0;
  const cityNow = new Date(Date.now() + offsetSec * 1_000);
  const cityDateStr = [
    cityNow.getUTCFullYear(),
    String(cityNow.getUTCMonth() + 1).padStart(2, "0"),
    String(cityNow.getUTCDate()).padStart(2, "0"),
  ].join("-");
  const cityNowStr = `${cityDateStr}T${String(cityNow.getUTCHours()).padStart(2, "0")}:${String(cityNow.getUTCMinutes()).padStart(2, "0")}`;

  let hourly: HourlyPoint[] = [];
  let uvIndex = 0;
  let visibilityMi = 0;
  let dewPointF = 0;
  let pressureTrend: PressureTrend = "unknown";
  if (wx.hourly) {
    hourly = buildHourlyPoints(
      wx.hourly.time,
      wx.hourly.temperature_2m,
      wx.hourly.weathercode,
      wx.hourly.precipitation_probability,
      cityNow.getUTCHours(),
      cityDateStr,
    );
    const times: string[] = wx.hourly.time;
    let base = times.findIndex(
      (t: string) => t.startsWith(cityDateStr) && parseInt(t.slice(11, 13), 10) >= cityNow.getUTCHours(),
    );
    if (base < 0) base = 0;
    uvIndex = wx.hourly.uv_index?.[base] ?? 0;
    visibilityMi = (wx.hourly.visibility?.[base] ?? 0) / 1609.34;
    dewPointF = wx.hourly.dew_point_2m?.[base] ?? 0;
    // Pressure over the last ~3 hours; >1 hPa move counts as a trend. Hourly
    // data starts at today's city-local midnight, so shortly after midnight
    // there is no lookback — report "unknown" rather than a fake "steady".
    const pNow = wx.hourly.pressure_msl?.[base];
    const pPast = wx.hourly.pressure_msl?.[base - 3];
    if (typeof pNow === "number" && typeof pPast === "number") {
      const delta = pNow - pPast;
      pressureTrend = delta > 1 ? "rising" : delta < -1 ? "falling" : "steady";
    }
  }

  const nowcast = wx.minutely_15
    ? buildNowcast(wx.minutely_15.time, wx.minutely_15.precipitation, cityNowStr)
    : null;

  const sunrise = daily.sunrise?.[0] ? fmtClock(daily.sunrise[0]) : "";
  const sunset = daily.sunset?.[0] ? fmtClock(daily.sunset[0]) : "";
  const uvMaxToday = daily.uv_index_max?.[0] ?? 0;
  const isDay = cur.is_day === 1;

  const insight = deriveInsight({
    nowcast, hourly, uvMaxToday, isDay, pressureTrend, air, forecast, sunset, sunrise,
  });

  return {
    city,
    region,
    tempF: Math.round(cur.temperature_2m),
    feelsLikeF: Math.round(cur.apparent_temperature),
    conditionCode: cur.weathercode,
    conditionLabel: wmoLabel(cur.weathercode),
    humidity: Math.round(cur.relative_humidity_2m),
    windMph: Math.round(cur.windspeed_10m),
    windGustMph: Math.round(cur.wind_gusts_10m ?? 0),
    windDir: compassDir(cur.wind_direction_10m),
    isDay,
    uvIndex,
    uvMaxToday,
    visibilityMi,
    dewPointF: Math.round(dewPointF),
    cloudCover: Math.round(cur.cloud_cover ?? 0),
    pressureInHg: (cur.pressure_msl ?? 0) * 0.02953,
    pressureTrend,
    sunrise,
    sunset,
    air,
    nowcast,
    insight,
    forecast,
    hourly,
    fetchedAt: Date.now(),
    locKey,
  };
}

// ── Hook ──────────────────────────────────────────────────────────────────────

export function useWeather() {
  const [status, setStatus] = useState<WeatherStatus>("idle");
  const [data, setData] = useState<WeatherData | null>(null);
  const loading = useRef(false);
  // Request generation: a forced load (city change, refresh) supersedes any
  // fetch already in flight — the older result is discarded on arrival
  // instead of silently swallowing the new request.
  const gen = useRef(0);

  const load = async (force = false) => {
    if (!_cache) _cache = readPersistedCache();
    const locKey = currentLocKey();
    if (!cacheUsable(_cache, locKey)) _cache = null;
    const age = _cache ? Date.now() - _cache.fetchedAt : Infinity;

    if (!force && _cache && age < CACHE_MS) {
      setData(_cache);
      setStatus("ok");
      return;
    }
    if (!force && loading.current) return;
    const myGen = ++gen.current;
    loading.current = true;

    // Stale-while-revalidate: paint the stale data immediately, refresh behind.
    const haveStale = !force && _cache != null;
    if (haveStale) {
      setData(_cache);
      setStatus("ok");
    } else {
      setStatus("loading");
    }

    try {
      const saved = getSavedLocation();
      let result: WeatherData;

      if (saved) {
        const [wx, air] = await Promise.all([
          fetchWeather(saved.lat, saved.lon),
          fetchAirQuality(saved.lat, saved.lon),
        ]);
        result = toWeatherData(wx, air, saved.city, saved.region, locKey);
      } else if (geolocationAvailable()) {
        const pos = await getPosition();
        const { latitude: lat, longitude: lon } = pos.coords;
        const [place, wx, air] = await Promise.all([
          reverseGeocode(lat, lon),
          fetchWeather(lat, lon),
          fetchAirQuality(lat, lon),
        ]);
        result = toWeatherData(wx, air, place.city, place.region, locKey);
      } else {
        // Insecure origin or no geolocation API, and nothing saved yet:
        // the card offers a manual city picker instead.
        if (gen.current === myGen) setStatus(haveStale ? "ok" : "no_location");
        return;
      }

      if (gen.current !== myGen) return; // superseded by a newer request
      _cache = result;
      persistCache(result);
      setData(result);
      setStatus("ok");
    } catch (e: any) {
      if (gen.current !== myGen) return; // superseded — ignore stale failure
      // Any GeolocationPositionError (denied / unavailable / timeout) with no
      // saved city → fall back to the manual picker rather than a dead error.
      if (e && typeof e.code === "number" && !getSavedLocation() && !haveStale) {
        setStatus("no_location");
      } else if (cacheUsable(_cache, locKey)) {
        setData(_cache);
        setStatus("ok");
      } else {
        console.warn("[useWeather]", e);
        setStatus("error");
      }
    } finally {
      if (gen.current === myGen) loading.current = false;
    }
  };

  /** Save a manually chosen city and refetch for it. */
  const setLocation = (loc: SavedLocation) => {
    saveLocation(loc);
    _cache = null;
    try { localStorage.removeItem(CACHE_KEY); } catch { /* ignore */ }
    void load(true);
  };

  /** Forget the manual city and try the browser's location again. */
  const useMyLocation = () => {
    clearSavedLocation();
    _cache = null;
    try { localStorage.removeItem(CACHE_KEY); } catch { /* ignore */ }
    void load(true);
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { status, data, reload: () => load(true), setLocation, useMyLocation };
}
