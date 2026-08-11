/**
 * Voice catalog card + dimension bar — shared presentation pieces for the
 * Voice Studio (Browse grid, detail panel, recommendation cards).
 */
import { Loader2, Pause, Play, CheckCircle2 } from "lucide-react";

// ── Types ──────────────────────────────────────────────────────────────────────

export interface VoiceDimensions {
  warmth: number;
  authority: number;
  gravitas: number;
  pace: number;
  brightness: number;
  age: number;
}

export interface VoiceEntry {
  id: string;
  name: string;
  accent?: string;
  gender?: string;
  description?: string;
  dimensions?: VoiceDimensions;
  tags?: string[];
  builtin?: boolean;
  engine?: string;
  custom?: boolean;
  /** Engine used to generate this voice's cached sample, if one exists.
   *  Always neural ("kokoro") — the robotic fallback was removed by policy.
   *  null = not yet generated. */
  sample_engine?: string | null;
}

// ── Dimension bar ─────────────────────────────────────────────────────────────

export function DimensionBar({ label, value, color }: { label: string; value: number; color: string }) {
  return (
    <div className="flex items-center gap-2">
      <span className="text-[10px] font-mono text-muted-foreground w-16 shrink-0 capitalize">{label}</span>
      <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${(value / 10) * 100}%`, background: color || "var(--gilt)" }}
        />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground w-4 text-right">{value}</span>
    </div>
  );
}

export const DIMENSION_COLORS: Record<string, string> = {
  warmth:    "var(--gilt)",
  authority: "var(--green-2)",
  gravitas:  "var(--rust)",
  pace:      "var(--green-2)",
  brightness:"var(--gilt)",
  age:       "#8A7A6A",
};

// ── Voice Card ────────────────────────────────────────────────────────────────

export function VoiceCard({
  voice,
  selected,
  onSelect,
  playingId,
  loadingId,
  onPlay,
}: {
  voice: VoiceEntry;
  selected: boolean;
  onSelect: (v: VoiceEntry) => void;
  playingId: string | null;
  loadingId: string | null;
  onPlay: (id: string) => void;
}) {
  const isPlaying = playingId === voice.id;
  const isLoading = loadingId === voice.id;
  const dims = voice.dimensions;

  const accentColor = voice.accent === "british"
    ? "border-border/60 bg-muted/20"
    : "";

  const genderIcon = voice.gender === "feminine" ? "♀" : voice.gender === "masculine" ? "♂" : "◆";

  return (
    <div
      onClick={() => onSelect(voice)}
      className={`
        relative group cursor-pointer rounded-xl border-2 p-4 transition-all
        hover:shadow-md hover:-translate-y-0.5
        ${selected
          ? "border-primary bg-primary/5 shadow-md ring-1 ring-primary/20"
          : "border-border/50 hover:border-primary/40 bg-card"
        }
      `}
    >
      {/* Header row */}
      <div className="flex items-start justify-between gap-2 mb-3">
        <div>
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-sm leading-tight">{voice.name}</span>
            <span className="text-[11px] text-muted-foreground">{genderIcon}</span>
            {voice.builtin && (
              <span className="text-[9px] font-mono px-1 py-0.5 rounded" style={{ background: "var(--green-soft)", color: "var(--green-2)" }}>
                ✓
              </span>
            )}
          </div>
          <div className="flex items-center gap-1 mt-0.5">
            {voice.accent && (
              <span className={`text-[10px] font-mono px-1.5 py-0.5 rounded-full border capitalize ${accentColor}`}>
                {voice.accent}
              </span>
            )}
          </div>
        </div>

        {/* Play button */}
        <button
          onClick={e => { e.stopPropagation(); onPlay(voice.id); }}
          disabled={isLoading}
          className={`
            w-9 h-9 rounded-full flex items-center justify-center shrink-0 transition-all
            ${isPlaying
              ? "bg-primary text-primary-foreground shadow-sm"
              : "bg-muted hover:bg-primary/10 hover:text-primary text-muted-foreground"
            }
            disabled:opacity-50
          `}
          title={isPlaying ? "Stop" : "Preview voice sample"}
        >
          {isLoading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : isPlaying ? (
            <Pause className="w-3.5 h-3.5" />
          ) : (
            <Play className="w-3.5 h-3.5 ml-0.5" />
          )}
        </button>
      </div>

      {/* Description */}
      {voice.description && (
        <p className="text-xs text-muted-foreground line-clamp-2 mb-3 leading-relaxed">
          {voice.description}
        </p>
      )}

      {/* Key dimensions — top 3 */}
      {dims && (
        <div className="space-y-1.5 mb-3">
          {(["warmth", "authority", "gravitas"] as const).map(key => (
            <DimensionBar
              key={key}
              label={key}
              value={dims[key]}
              color={DIMENSION_COLORS[key]}
            />
          ))}
        </div>
      )}

      {/* Genre tags */}
      {voice.tags && voice.tags.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {voice.tags.slice(0, 3).map(tag => (
            <span key={tag}
              className="text-[9px] font-mono px-1.5 py-0.5 rounded-full bg-muted text-muted-foreground">
              {tag}
            </span>
          ))}
        </div>
      )}

      {selected && (
        <div className="absolute top-2 right-2 w-4 h-4 rounded-full bg-primary flex items-center justify-center">
          <CheckCircle2 className="w-3 h-3 text-primary-foreground" />
        </div>
      )}
    </div>
  );
}
