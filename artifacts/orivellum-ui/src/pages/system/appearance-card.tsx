/**
 * AppearanceCard — theme + calibration controls (WP2).
 *
 * Appearance: Daylight / Hull / System (resolved value applied instantly).
 * Calibration: text size (100/112/125%), editor measure (62/72/100ch),
 * reading face (system sans / system serif). Everything previews live,
 * persists to localStorage for the pre-paint boot script, and mirrors to
 * the personal settings record via theme.ts.
 */
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Sun, Moon, MonitorSmartphone, RotateCcw, Type, Ruler, BookOpen } from "lucide-react";
import {
  useThemePreference,
  useUiPreferences,
  setCalibration,
  resetUiPreferences,
  type ThemePreference,
  type TextSize,
  type EditorMeasure,
  type ReadingFace,
} from "@/lib/theme";

function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string; icon?: React.ReactNode }[];
  onChange: (v: T) => void;
}) {
  return (
    <div className="space-y-1.5">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div role="radiogroup" aria-label={label} className="flex gap-1 rounded-lg border border-border bg-muted/30 p-1">
        {options.map((o) => (
          <button
            key={o.value}
            role="radio"
            aria-checked={value === o.value}
            onClick={() => onChange(o.value)}
            className={`flex-1 min-h-11 rounded-md px-2 text-sm flex items-center justify-center gap-1.5 touch-manipulation transition-colors ${
              value === o.value
                ? "bg-primary text-primary-foreground font-medium"
                : "text-foreground hover:bg-muted"
            }`}
          >
            {o.icon}
            <span>{o.label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

export function AppearanceCard() {
  const { preference, resolved, setPreference } = useThemePreference();
  const prefs = useUiPreferences();

  return (
    <Card className="vellum-card">
      <CardContent className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {resolved === "hull" ? <Moon className="w-5 h-5 text-primary" /> : <Sun className="w-5 h-5 text-primary" />}
            <div>
              <div className="font-medium">Appearance</div>
              <div className="text-xs text-muted-foreground">
                Premium Daylight by default · applies everywhere instantly
              </div>
            </div>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="min-h-11 text-muted-foreground"
            onClick={() => resetUiPreferences()}
          >
            <RotateCcw className="w-4 h-4 mr-1.5" />
            Reset
          </Button>
        </div>

        <Segmented
          label="Theme"
          value={preference}
          onChange={setPreference}
          options={[
            { value: "daylight", label: "Daylight", icon: <Sun className="w-4 h-4" /> },
            { value: "hull", label: "Hull", icon: <Moon className="w-4 h-4" /> },
            { value: "system", label: "System", icon: <MonitorSmartphone className="w-4 h-4" /> },
          ]}
        />

        <Segmented
          label="Text size"
          value={prefs.textSize}
          onChange={(v) => setCalibration({ textSize: v })}
          options={[
            { value: "100", label: "100%" },
            { value: "112", label: "112%" },
            { value: "125", label: "125%" },
          ]}
        />

        <Segmented
          label="Editor measure"
          value={prefs.measure}
          onChange={(v) => setCalibration({ measure: v })}
          options={[
            { value: "focused", label: "Focused", icon: <Ruler className="w-4 h-4" /> },
            { value: "standard", label: "Standard" },
            { value: "wide", label: "Wide" },
          ]}
        />

        <Segmented
          label="Reading face"
          value={prefs.readingFace}
          onChange={(v) => setCalibration({ readingFace: v })}
          options={[
            { value: "sans", label: "System Sans", icon: <Type className="w-4 h-4" /> },
            { value: "serif", label: "System Serif", icon: <BookOpen className="w-4 h-4" /> },
          ]}
        />

        {/* live preview of the calibrated reading surface */}
        <div
          className="prose-measure rounded-lg border border-border bg-card p-4 text-sm leading-relaxed"
          style={{ fontFamily: "var(--reading-font)" }}
        >
          The quick brown fox jumps over the lazy dog — this paragraph previews
          your text size, measure, and reading face exactly as chat and reading
          surfaces will render them.
        </div>
      </CardContent>
    </Card>
  );
}
