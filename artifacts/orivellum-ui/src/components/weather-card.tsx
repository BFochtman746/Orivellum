/**
 * WeatherCard — location-aware weather briefing for the web dashboard.
 *
 * Browser counterpart of the mobile app's weather card: current conditions,
 * a 4-day forecast strip, and an expandable next-24-hours view. Uses the
 * browser Geolocation API (permission prompt on first render) and the free
 * Open-Meteo service — no API keys.
 *
 * Renders nothing when geolocation is unsupported, and a small enable hint
 * when permission is denied (so the card never blocks the dashboard).
 */

import { useState } from "react";
import {
  Sun, Moon, Cloud, CloudRain, CloudSnow, CloudDrizzle, Zap, Wind,
  Droplets, MapPin, RefreshCw, ChevronDown, ChevronUp,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useWeather, wmoGroup, type ConditionGroup } from "@/hooks/use-weather";

// ── WMO code → lucide icon ────────────────────────────────────────────────────

function WeatherIcon({ code, isDay, className }: { code: number; isDay: boolean; className?: string }) {
  if (code <= 1) return isDay ? <Sun className={className} /> : <Moon className={className} />;
  if (code <= 3) return <Cloud className={className} />;
  if (code <= 48) return <Wind className={className} />;
  if (code <= 57) return <CloudDrizzle className={className} />;
  if (code <= 67) return <CloudRain className={className} />;
  if (code <= 77) return <CloudSnow className={className} />;
  if (code <= 82) return <CloudRain className={className} />;
  if (code <= 86) return <CloudSnow className={className} />;
  if (code >= 95) return <Zap className={className} />;
  return <Cloud className={className} />;
}

// Subtle background tint per condition group — stays inside the VELLUM
// palette (soft washes over the card background, not loud gradients).
const GROUP_TINT: Record<ConditionGroup, string> = {
  sunny:      "linear-gradient(135deg, rgba(212,175,55,0.10), transparent 60%)",
  clearNight: "linear-gradient(135deg, rgba(58,74,107,0.12), transparent 60%)",
  cloudy:     "linear-gradient(135deg, rgba(120,120,110,0.10), transparent 60%)",
  rain:       "linear-gradient(135deg, rgba(74,110,138,0.12), transparent 60%)",
  snow:       "linear-gradient(135deg, rgba(160,180,200,0.14), transparent 60%)",
  storm:      "linear-gradient(135deg, rgba(90,74,120,0.14), transparent 60%)",
};

export function WeatherCard() {
  const { status, data, reload } = useWeather();
  const [showHourly, setShowHourly] = useState(false);

  // Unsupported browsers: show nothing rather than a broken card.
  if (status === "unsupported") return null;

  if (status === "denied") {
    return (
      <Card>
        <CardContent className="py-3 px-4 flex items-center gap-2 text-xs text-muted-foreground font-mono">
          <MapPin className="w-3.5 h-3.5 shrink-0" />
          <span>Location access is off — allow it in your browser to see local weather here.</span>
        </CardContent>
      </Card>
    );
  }

  if (status === "error") {
    return (
      <Card>
        <CardContent className="py-3 px-4 flex items-center gap-2 text-xs text-muted-foreground font-mono">
          <Cloud className="w-3.5 h-3.5 shrink-0" />
          <span>Weather unavailable right now.</span>
          <button onClick={() => reload()} className="underline underline-offset-2 hover:text-foreground">
            Retry
          </button>
        </CardContent>
      </Card>
    );
  }

  if (!data) {
    return (
      <Card>
        <CardContent className="py-4 px-4 flex items-center gap-4">
          <Skeleton className="w-10 h-10 rounded-full" />
          <div className="space-y-2 flex-1">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-48" />
          </div>
        </CardContent>
      </Card>
    );
  }

  const group = wmoGroup(data.conditionCode, data.isDay);

  return (
    <Card className="overflow-hidden">
      <CardContent className="p-0" style={{ background: GROUP_TINT[group] }}>
        {/* Current conditions row */}
        <div className="flex items-center gap-4 px-4 py-3.5">
          <WeatherIcon code={data.conditionCode} isDay={data.isDay} className="w-9 h-9 shrink-0 opacity-80" />
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2 flex-wrap">
              <span className="text-2xl font-serif font-semibold leading-none">{data.tempF}°</span>
              <span className="text-sm text-muted-foreground">{data.conditionLabel}</span>
              {data.feelsLikeF !== data.tempF && (
                <span className="text-xs text-muted-foreground/70 font-mono">feels {data.feelsLikeF}°</span>
              )}
            </div>
            <div className="flex items-center gap-3 mt-1 text-xs text-muted-foreground font-mono">
              <span className="flex items-center gap-1 truncate">
                <MapPin className="w-3 h-3 shrink-0" />
                {data.city}{data.region ? `, ${data.region}` : ""}
              </span>
              <span className="flex items-center gap-1"><Droplets className="w-3 h-3" />{data.humidity}%</span>
              <span className="flex items-center gap-1"><Wind className="w-3 h-3" />{data.windMph} mph</span>
            </div>
          </div>

          {/* Forecast strip (hidden on very narrow screens) */}
          <div className="hidden sm:flex items-center gap-4 shrink-0">
            {data.forecast.slice(1, 4).map((d) => (
              <div key={d.label} className="flex flex-col items-center gap-0.5">
                <span className="text-[10px] font-mono uppercase tracking-wide text-muted-foreground">{d.label}</span>
                <WeatherIcon code={d.code} isDay className="w-4 h-4 opacity-70" />
                <span className="text-[11px] font-mono">
                  {d.tempMaxF}° <span className="text-muted-foreground/60">{d.tempMinF}°</span>
                </span>
              </div>
            ))}
          </div>

          <div className="flex flex-col gap-1 shrink-0">
            <button
              onClick={() => setShowHourly((v) => !v)}
              className="p-1 rounded hover:bg-foreground/5 text-muted-foreground"
              title={showHourly ? "Hide hourly" : "Next 24 hours"}
              aria-label={showHourly ? "Hide hourly forecast" : "Show hourly forecast"}
            >
              {showHourly ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
            </button>
            <button
              onClick={() => reload()}
              className="p-1 rounded hover:bg-foreground/5 text-muted-foreground"
              title="Refresh weather"
              aria-label="Refresh weather"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${status === "loading" ? "animate-spin" : ""}`} />
            </button>
          </div>
        </div>

        {/* Hourly strip — next 24 hours */}
        {showHourly && data.hourly.length > 0 && (
          <div className="border-t px-2 py-2 overflow-x-auto" style={{ borderColor: "var(--line)" }}>
            <div className="flex gap-1 min-w-max">
              {data.hourly.map((h, i) => (
                <div key={`${h.label}-${i}`} className="flex flex-col items-center gap-1 px-2 py-1 min-w-[52px]">
                  <span className="text-[10px] font-mono text-muted-foreground">{h.label}</span>
                  <WeatherIcon code={h.code} isDay={h.hour >= 6 && h.hour < 20} className="w-4 h-4 opacity-70" />
                  <span className="text-xs font-mono">{h.tempF}°</span>
                  {h.precipProb >= 20 ? (
                    <span className="text-[10px] font-mono text-blue-500/80 flex items-center gap-0.5">
                      <Droplets className="w-2.5 h-2.5" />{h.precipProb}%
                    </span>
                  ) : (
                    <span className="text-[10px]">&nbsp;</span>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
