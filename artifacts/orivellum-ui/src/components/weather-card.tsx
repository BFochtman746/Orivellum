/**
 * WeatherCard — GD-industrial weather tile for the Home Screen.
 *
 * Collapsed: current conditions + place + a 3-day mini strip.
 * Tapping the card expands the next-24-hours strip (temperature, condition
 * icon, precipitation probability) — the same view the retired mobile app had.
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
} from "lucide-react";
import {
  useWeather, searchCity, geolocationAvailable,
  type CityResult,
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

// ── Card ──────────────────────────────────────────────────────────────────────

export function WeatherCard() {
  const { status, data, reload, setLocation, useMyLocation } = useWeather();
  const [showHourly, setShowHourly] = useState(false);
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
      {/* Current conditions — the whole row is the hourly toggle */}
      <button
        onClick={() => setShowHourly((v) => !v)}
        aria-expanded={showHourly}
        aria-label={showHourly ? "Hide hourly forecast" : "Show next 24 hours"}
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
          </div>
          <div className="flex items-center gap-3 mt-1 flex-wrap" style={monoXs}>
            <span className="flex items-center gap-1 truncate">
              <MapPin className="w-3 h-3 shrink-0" />
              {data.city}{data.region ? `, ${data.region}` : ""}
            </span>
            <span className="flex items-center gap-1"><Droplets className="w-3 h-3" />{data.humidity}%</span>
            <span className="flex items-center gap-1"><Wind className="w-3 h-3" />{data.windMph} mph</span>
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

        {showHourly
          ? <ChevronUp className="w-4 h-4 shrink-0" style={{ color: "var(--gd-muted)" }} />
          : <ChevronDown className="w-4 h-4 shrink-0" style={{ color: "var(--gd-muted)" }} />}
      </button>

      {/* Hourly strip — next 24 hours */}
      {showHourly && (
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
          <div className="flex justify-end gap-4 pt-1 pr-1">
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
        </div>
      )}
    </Shell>
  );
}
