/**
 * WeatherCard — GD-industrial weather tile for the Home Screen.
 *
 * Collapsed: current conditions, one derived insight headline ("Rain starting
 * in ~30 min", "Very high UV today", …) and a 3-day mini strip.
 * Tapping the card expands:
 *   - a 2-hour precipitation nowcast sparkline (when rain is in the window)
 *   - the next-24-hours strip (temperature, condition, precip probability)
 *   - a conditions grid (UV, wind + gusts, pressure trend, visibility,
 *     dew point, humidity, sunrise/sunset, air quality)
 *   - a 7-day forecast list
 *
 * Location comes from the browser when possible; on plain-HTTP origins
 * (Tailscale/LAN) where geolocation is blocked, a small city picker appears
 * once and the choice is remembered.
 *
 * Styled entirely with GD tokens so it sits naturally among the launch tiles.
 */

import { useState } from "react";
import {
  Sun, Moon, Cloud, CloudRain, CloudSnow, CloudDrizzle, Zap, Wind,
  Droplets, MapPin, RefreshCw, ChevronDown, ChevronUp, Search, LocateFixed,
  Sunrise, Sunset, Gauge, Eye, Thermometer, Leaf,
  Sparkles, Umbrella,
} from "lucide-react";
import {
  useWeather, searchCity, geolocationAvailable, uvLevel,
  type CityResult, type WeatherData,
} from "@/hooks/use-weather";

// ── WMO code → lucide icon ────────────────────────────────────────────────────

function WeatherIcon({ code, isDay, className, style }: {
  code: number; isDay: boolean; className?: string; style?: React.CSSProperties;
}) {
  const p = { className, style };
  if (code <= 1) return isDay ? <Sun {...p} /> : <Moon {...p} />;
  if (code <= 3) return <Cloud {...p} />;
  if (code <= 48) return <Wind {...p} />;
  if (code <= 57) return <CloudDrizzle {...p} />;
  if (code <= 67) return <CloudRain {...p} />;
  if (code <= 77) return <CloudSnow {...p} />;
  if (code <= 82) return <CloudRain {...p} />;
  if (code <= 86) return <CloudSnow {...p} />;
  if (code >= 95) return <Zap {...p} />;
  return <Cloud {...p} />;
}

// ── Shared shell so every state has the same footprint ───────────────────────

function Shell({ children, testid }: { children: React.ReactNode; testid?: string }) {
  return (
    <div
      data-testid={testid}
      style={{
        borderRadius: "var(--gd-r)",
        border: "1px solid var(--gd-line)",
        background: "var(--gd-card)",
        color: "var(--gd-text)",
        overflow: "hidden",
      }}
    >
      {children}
    </div>
  );
}

const monoXs: React.CSSProperties = {
  fontFamily: "var(--gd-data)",
  fontSize: 11,
  color: "var(--gd-muted)",
};

function aqiColor(aqi: number): string {
  if (aqi <= 50) return "var(--gd-ok, #4ade80)";
  if (aqi <= 100) return "var(--gd-warn, #facc15)";
  return "var(--gd-danger, #f87171)";
}

// ── City picker (no-geolocation fallback) ─────────────────────────────────────

function CityPicker({ onPick }: { onPick: (c: CityResult) => void }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<CityResult[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);

  const run = async () => {
    const q = query.trim();
    if (q.length < 2 || busy) return;
    setBusy(true);
    setFailed(false);
    try {
      setResults(await searchCity(q));
    } catch {
      setFailed(true);
      setResults(null);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="p-3 space-y-2">
      <div className="flex items-center gap-2" style={monoXs}>
        <MapPin className="w-3.5 h-3.5 shrink-0" />
        <span>Set a city to see local weather</span>
      </div>
      <div className="flex gap-2">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run()}
          placeholder="City name…"
          aria-label="City name"
          data-testid="input-weather-city"
          className="flex-1 min-w-0 px-3"
          style={{
            height: "var(--gd-tap)",
            borderRadius: "var(--gd-r-sm)",
            border: "1px solid var(--gd-line)",
            background: "var(--gd-surface)",
            color: "var(--gd-text)",
            fontFamily: "var(--gd-body)",
            fontSize: 14,
          }}
        />
        <button
          onClick={run}
          disabled={busy || query.trim().length < 2}
          aria-label="Search city"
          data-testid="button-weather-city-search"
          className="inline-flex items-center justify-center disabled:opacity-40"
          style={{
            width: "var(--gd-tap)",
            height: "var(--gd-tap)",
            borderRadius: "var(--gd-r-sm)",
            border: "1px solid var(--gd-line)",
            background: "var(--gd-accent-soft)",
            color: "var(--gd-accent)",
          }}
        >
          <Search className={`w-4 h-4 ${busy ? "animate-pulse" : ""}`} />
        </button>
      </div>
      {failed && <p style={monoXs}>Search failed — check the connection and try again.</p>}
      {results !== null && results.length === 0 && <p style={monoXs}>No matches found.</p>}
      {results && results.length > 0 && (
        <div className="space-y-1">
          {results.map((c, i) => (
            <button
              key={`${c.lat},${c.lon}`}
              onClick={() => onPick(c)}
              data-testid={`button-weather-city-${i}`}
              className="w-full text-left px-3 flex items-center gap-2"
              style={{
                minHeight: "var(--gd-tap)",
                borderRadius: "var(--gd-r-sm)",
                border: "1px solid var(--gd-line)",
                background: "var(--gd-surface)",
                color: "var(--gd-text)",
                fontSize: 13,
              }}
            >
              <MapPin className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-muted)" }} />
              <span className="truncate">
                {c.name}
                <span style={{ color: "var(--gd-muted)" }}>
                  {c.region ? ` · ${c.region}` : ""}{c.country ? ` · ${c.country}` : ""}
                </span>
              </span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Nowcast sparkline (next 2 h, 15-min bars) ─────────────────────────────────

function NowcastBars({ bars }: { bars: number[] }) {
  const max = Math.max(...bars, 0.02);
  return (
    <div className="flex items-end gap-1" style={{ height: 22 }} aria-hidden data-testid="spark-weather-nowcast">
      {bars.map((v, i) => (
        <div
          key={i}
          style={{
            width: 10,
            height: Math.max(2, Math.round((v / max) * 22)),
            borderRadius: 2,
            background: v > 0.004 ? "var(--gd-accent)" : "var(--gd-line)",
          }}
        />
      ))}
    </div>
  );
}

// ── Conditions grid (expanded view) ───────────────────────────────────────────

function StatCell({ icon, label, value, valueColor, testid }: {
  icon: React.ReactNode; label: string; value: string; valueColor?: string; testid?: string;
}) {
  return (
    <div className="flex items-center gap-2 min-w-0" data-testid={testid}>
      <span className="shrink-0 inline-flex" style={{ color: "var(--gd-muted)" }}>{icon}</span>
      <div className="min-w-0">
        <div style={{ ...monoXs, fontSize: 9, textTransform: "uppercase", letterSpacing: "0.08em" }}>{label}</div>
        <div className="truncate" style={{ ...monoXs, fontSize: 12, color: valueColor ?? "var(--gd-text)" }}>{value}</div>
      </div>
    </div>
  );
}

function ConditionsGrid({ d }: { d: WeatherData }) {
  const ic = "w-3.5 h-3.5";
  return (
    <div
      className="grid grid-cols-2 sm:grid-cols-4 gap-x-4 gap-y-3 px-3 py-3"
      style={{ borderTop: "1px solid var(--gd-line)" }}
      data-testid="grid-weather-conditions"
    >
      <StatCell icon={<Sun className={ic} />} label="UV index"
        value={`${Math.round(d.uvIndex)} · ${uvLevel(d.uvIndex)}${d.uvMaxToday > d.uvIndex + 0.5 ? ` (max ${Math.round(d.uvMaxToday)})` : ""}`} />
      <StatCell icon={<Wind className={ic} />} label="Wind"
        value={`${d.windMph} mph ${d.windDir}${d.windGustMph > d.windMph + 3 ? ` · gusts ${d.windGustMph}` : ""}`} />
      <StatCell
        icon={<Gauge className={ic} />} label="Pressure"
        value={`${d.pressureInHg.toFixed(2)} in${d.pressureTrend !== "unknown" ? ` · ${d.pressureTrend}` : ""}`}
        testid="cell-weather-pressure"
      />
      <StatCell icon={<Eye className={ic} />} label="Visibility"
        value={d.visibilityMi >= 9.9 ? "10+ mi" : `${d.visibilityMi.toFixed(1)} mi`} />
      <StatCell icon={<Thermometer className={ic} />} label="Dew point" value={`${d.dewPointF}°`} />
      <StatCell icon={<Droplets className={ic} />} label="Humidity" value={`${d.humidity}% · clouds ${d.cloudCover}%`} />
      <StatCell icon={d.isDay ? <Sunset className={ic} /> : <Sunrise className={ic} />}
        label={d.isDay ? "Sunset" : "Sunrise"} value={d.isDay ? d.sunset : d.sunrise} />
      <StatCell icon={<Leaf className={ic} />} label="Air quality"
        value={d.air ? `AQI ${d.air.aqi} · ${d.air.label}` : "Unavailable"}
        valueColor={d.air ? aqiColor(d.air.aqi) : undefined}
        testid="cell-weather-aqi" />
    </div>
  );
}

// ── 7-day forecast list (expanded view) ───────────────────────────────────────

function WeekList({ d }: { d: WeatherData }) {
  const weekMin = Math.min(...d.forecast.map((f) => f.tempMinF));
  const weekMax = Math.max(...d.forecast.map((f) => f.tempMaxF));
  const span = Math.max(1, weekMax - weekMin);
  return (
    <div className="px-3 pb-2 space-y-0.5" style={{ borderTop: "1px solid var(--gd-line)", paddingTop: 8 }} data-testid="list-weather-week">
      {d.forecast.map((f) => (
        <div key={f.label} className="flex items-center gap-2" style={{ minHeight: 26 }}>
          <span className="w-16 shrink-0" style={{ ...monoXs, fontSize: 11 }}>{f.label}</span>
          <WeatherIcon code={f.code} isDay className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-muted)" }} />
          <span className="w-9 shrink-0 text-right" style={{ ...monoXs, fontSize: 10, color: f.precipProb >= 30 ? "var(--gd-accent)" : "transparent" }}>
            {f.precipProb >= 30 ? `${f.precipProb}%` : "0"}
          </span>
          <span className="w-7 text-right shrink-0" style={{ ...monoXs, fontSize: 11 }}>{f.tempMinF}°</span>
          <div className="flex-1 relative" style={{ height: 4, borderRadius: 2, background: "var(--gd-line)" }}>
            <div
              className="absolute top-0 bottom-0"
              style={{
                borderRadius: 2,
                background: "var(--gd-accent)",
                opacity: 0.75,
                left: `${((f.tempMinF - weekMin) / span) * 100}%`,
                right: `${((weekMax - f.tempMaxF) / span) * 100}%`,
              }}
            />
          </div>
          <span className="w-7 text-right shrink-0" style={{ ...monoXs, fontSize: 11, color: "var(--gd-text)" }}>{f.tempMaxF}°</span>
        </div>
      ))}
    </div>
  );
}

// ── Card ──────────────────────────────────────────────────────────────────────

export function WeatherCard() {
  const { status, data, reload, setLocation, useMyLocation } = useWeather();
  const [showDetail, setShowDetail] = useState(false);
  const [picking, setPicking] = useState(false);

  if (picking) {
    return (
      <Shell testid="card-weather">
        <CityPicker
          onPick={(c) => {
            setLocation({ lat: c.lat, lon: c.lon, city: c.name, region: c.region });
            setPicking(false);
          }}
        />
        <div className="px-3 pb-3 flex items-center gap-4">
          {geolocationAvailable() && (
            <button
              onClick={() => { setPicking(false); useMyLocation(); }}
              data-testid="button-weather-use-my-location"
              className="inline-flex items-center gap-1.5"
              style={{ ...monoXs, color: "var(--gd-accent)" }}
            >
              <LocateFixed className="w-3 h-3" /> Use my location
            </button>
          )}
          <button
            onClick={() => setPicking(false)}
            data-testid="button-weather-picker-cancel"
            style={monoXs}
          >
            Cancel
          </button>
        </div>
      </Shell>
    );
  }

  if (status === "no_location") {
    return (
      <Shell testid="card-weather">
        <CityPicker
          onPick={(c) => setLocation({ lat: c.lat, lon: c.lon, city: c.name, region: c.region })}
        />
        {geolocationAvailable() && (
          <div className="px-3 pb-3">
            <button
              onClick={useMyLocation}
              data-testid="button-weather-use-my-location"
              className="inline-flex items-center gap-1.5"
              style={{ ...monoXs, color: "var(--gd-accent)" }}
            >
              <LocateFixed className="w-3 h-3" /> Use my location
            </button>
          </div>
        )}
      </Shell>
    );
  }

  if (status === "error") {
    return (
      <Shell testid="card-weather">
        <div className="p-3 flex items-center gap-2" style={monoXs}>
          <Cloud className="w-3.5 h-3.5 shrink-0" />
          <span>Weather unavailable right now.</span>
          <button
            onClick={() => reload()}
            data-testid="button-weather-retry"
            className="underline underline-offset-2"
            style={{ color: "var(--gd-accent)" }}
          >
            Retry
          </button>
        </div>
      </Shell>
    );
  }

  if (!data) {
    return (
      <Shell testid="card-weather">
        <div className="p-3 flex items-center gap-3" style={monoXs}>
          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
          <span>Checking the sky…</span>
        </div>
      </Shell>
    );
  }

  return (
    <Shell testid="card-weather">
      {/* Current conditions — the whole row is the detail toggle */}
      <button
        onClick={() => setShowDetail((v) => !v)}
        aria-expanded={showDetail}
        aria-label={showDetail ? "Hide forecast details" : "Show forecast details"}
        data-testid="button-weather-toggle"
        className="w-full text-left flex items-center gap-3 px-4"
        style={{ minHeight: "var(--gd-tap)", paddingTop: 12, paddingBottom: 12 }}
      >
        <WeatherIcon
          code={data.conditionCode}
          isDay={data.isDay}
          className="w-8 h-8 shrink-0"
          style={{ color: "var(--gd-accent)" }}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2 flex-wrap">
            <span
              data-testid="text-weather-temp"
              style={{ fontFamily: "var(--gd-display)", fontSize: 26, fontWeight: 600, lineHeight: 1 }}
            >
              {data.tempF}°
            </span>
            <span style={{ fontSize: 13 }}>{data.conditionLabel}</span>
            {data.feelsLikeF !== data.tempF && (
              <span style={monoXs}>feels {data.feelsLikeF}°</span>
            )}
            {data.air && data.air.aqi > 100 && (
              <span
                className="inline-flex items-center gap-1"
                style={{ ...monoXs, color: aqiColor(data.air.aqi) }}
                data-testid="chip-weather-aqi"
              >
                <Leaf className="w-3 h-3" /> AQI {data.air.aqi}
              </span>
            )}
          </div>
          <div className="flex items-center gap-3 mt-1 flex-wrap" style={monoXs}>
            <span className="flex items-center gap-1 truncate">
              <MapPin className="w-3 h-3 shrink-0" />
              {data.city}{data.region ? `, ${data.region}` : ""}
            </span>
            <span className="flex items-center gap-1"><Droplets className="w-3 h-3" />{data.humidity}%</span>
            <span className="flex items-center gap-1"><Wind className="w-3 h-3" />{data.windMph} mph {data.windDir}</span>
          </div>
          {/* Derived insight headline — nowcast, UV, pressure, AQI, sun times */}
          <div
            className="flex items-center gap-1.5 mt-1"
            style={{ ...monoXs, color: data.nowcast ? "var(--gd-accent)" : "var(--gd-muted)" }}
            data-testid="text-weather-insight"
          >
            {data.nowcast ? <Umbrella className="w-3 h-3 shrink-0" /> : <Sparkles className="w-3 h-3 shrink-0" />}
            <span className="truncate">{data.insight}</span>
          </div>
        </div>

        {/* 3-day mini strip (hidden on very narrow screens) */}
        <div className="hidden sm:flex items-center gap-4 shrink-0">
          {data.forecast.slice(1, 4).map((d) => (
            <div key={d.label} className="flex flex-col items-center gap-0.5">
              <span style={{ ...monoXs, fontSize: 10, textTransform: "uppercase", letterSpacing: "0.08em" }}>
                {d.label}
              </span>
              <WeatherIcon code={d.code} isDay className="w-4 h-4" style={{ color: "var(--gd-muted)" }} />
              <span style={{ ...monoXs, color: "var(--gd-text)" }}>
                {d.tempMaxF}° <span style={{ color: "var(--gd-muted)" }}>{d.tempMinF}°</span>
              </span>
            </div>
          ))}
        </div>

        {showDetail
          ? <ChevronUp className="w-4 h-4 shrink-0" style={{ color: "var(--gd-muted)" }} />
          : <ChevronDown className="w-4 h-4 shrink-0" style={{ color: "var(--gd-muted)" }} />}
      </button>

      {showDetail && (
        <>
          {/* 2-hour precipitation nowcast (only when there is rain in the window) */}
          {data.nowcast && (
            <div
              className="flex items-center gap-3 px-4 py-2"
              style={{ borderTop: "1px solid var(--gd-line)" }}
              data-testid="row-weather-nowcast"
            >
              <Umbrella className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-accent)" }} />
              <span style={{ ...monoXs, color: "var(--gd-text)" }}>{data.nowcast.message}</span>
              <div className="ml-auto flex items-center gap-2">
                <span style={{ ...monoXs, fontSize: 9 }}>next 2 h</span>
                <NowcastBars bars={data.nowcast.bars} />
              </div>
            </div>
          )}

          {/* Hourly strip — next 24 hours */}
          <div
            className="overflow-x-auto"
            style={{ borderTop: "1px solid var(--gd-line)", padding: "8px 8px" }}
            data-testid="strip-weather-hourly"
          >
            <div className="flex gap-1 min-w-max">
              {data.hourly.map((h, i) => (
                <div key={`${h.label}-${i}`} className="flex flex-col items-center gap-1 px-2 py-1 min-w-[52px]">
                  <span style={{ ...monoXs, fontSize: 10 }}>{h.label}</span>
                  <WeatherIcon
                    code={h.code}
                    isDay={h.hour >= 6 && h.hour < 20}
                    className="w-4 h-4"
                    style={{ color: "var(--gd-muted)" }}
                  />
                  <span style={{ ...monoXs, color: "var(--gd-text)", fontSize: 12 }}>{h.tempF}°</span>
                  {h.precipProb >= 20 ? (
                    <span className="flex items-center gap-0.5" style={{ ...monoXs, fontSize: 10, color: "var(--gd-accent)" }}>
                      <Droplets className="w-2.5 h-2.5" />{h.precipProb}%
                    </span>
                  ) : (
                    <span style={{ fontSize: 10 }}>&nbsp;</span>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Conditions grid: UV, wind, pressure, visibility, dew point, sun, AQI */}
          <ConditionsGrid d={data} />

          {/* 7-day outlook */}
          <WeekList d={data} />

          <div className="flex justify-end gap-4 pt-1 pb-2 pr-3">
            <button
              onClick={() => setPicking(true)}
              data-testid="button-weather-change-city"
              className="inline-flex items-center gap-1"
              style={{ ...monoXs, fontSize: 10 }}
              aria-label="Change city"
            >
              <MapPin className="w-3 h-3" /> change city
            </button>
            <button
              onClick={() => reload()}
              data-testid="button-weather-refresh"
              className="inline-flex items-center gap-1"
              style={{ ...monoXs, fontSize: 10 }}
              aria-label="Refresh weather"
            >
              <RefreshCw className={`w-3 h-3 ${status === "loading" ? "animate-spin" : ""}`} /> refresh
            </button>
          </div>
        </>
      )}
    </Shell>
  );
}
