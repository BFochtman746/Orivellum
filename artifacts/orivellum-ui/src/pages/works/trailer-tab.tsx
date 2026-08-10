/**
 * trailer-tab.tsx — Full Trailer Architect package viewer
 *
 * Five panels: Brief · Concepts (scored) · Shotlist (copy-prompt) ·
 * Narration & Music · Assembly.  Plus: generate trigger, live phase
 * progress, download production_package.json, and history list.
 */

import { useEffect, useRef, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter,
  DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { toast } from "sonner";
import { useReadAloud } from "@/lib/read-aloud";
import {
  Film, Sparkles, Loader2, CheckCircle, AlertCircle, XCircle,
  Copy, Check, ChevronDown, ChevronRight, Download, Music,
  Mic, Clapperboard, LayoutList, BookOpen, Star, Clock,
  BarChart3, Layers, Settings2, Smartphone, Monitor, Blend,
  Play, Wand2, ScrollText,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ───────────────────────────────────────────────────────────────────

interface TrailerListItem {
  id: string;
  work_id: string;
  status: "running" | "ready" | "blocked" | "failed";
  phase: string;
  has_package: boolean;
  error?: string | null;
  created_at: string;
  updated_at: string;
}

interface ConceptScore {
  fidelity: number;
  hook: number;
  feasibility: number;
  distinctiveness: number;
}

interface TrailerConcept {
  name: string;
  angle: string;
  rationale: string;
  duration: number;
  beats: string[];
  visual_style: string;
  voice_direction: string;
  music_direction: string;
  scores?: ConceptScore;
  score_total?: number;
}

interface TrailerShot {
  beat: string;
  beat_type?: "hook" | "peak" | "close";
  duration: number;
  description: string;
  image_prompt: string;
  motion_prompt: string;
  negative_prompt: string;
  on_screen_text?: string;
  vertical_framing_note?: string;
  image_model?: string;
  video_model?: string;
  resolution?: string;
  frames?: number;
  steps?: number;
  seed_policy?: string;
  upscale?: string;
}

// Combined package shape when format="both" or "all"
interface CombinedTrailerPackage {
  format: "both" | "all";
  full: TrailerPackage["package"];
  short: TrailerPackage["package"];
  square?: TrailerPackage["package"];
  brief: Record<string, unknown>;
  concept: TrailerConcept;
  method: Record<string, unknown>;
  generated: string;
  status: string;
  status_badge: string;
}

type TrailerFormat = "full" | "short" | "square";

interface TrailerNarrationLine {
  t_start: number;
  text: string;
  emotion?: string;
  pace?: string;
  pronunciation?: string;
}

interface TrailerMusic {
  prompt: string;
  tempo_bpm?: number;
  mood?: string;
  length_seconds?: number;
  structure?: string;
}

interface TrailerPackage {
  id: string;
  work_id: string;
  status: string;
  phase: string;
  error?: string | null;
  created_at: string;
  package: {
    brief: Record<string, unknown>;
    concept: TrailerConcept;
    plan: {
      shots: TrailerShot[];
      narration: TrailerNarrationLine[];
      music: TrailerMusic;
      titles: { text: string; for_shot: number; style: string }[];
      assembly: Record<string, unknown>;
      duration: number;
      _all_concepts?: TrailerConcept[];
    };
    method: Record<string, unknown>;
    validation: {
      status: string;
      critical: number;
      findings: { code: string; severity: string; msg: string }[];
    };
    docs: Record<string, string>;
    shot_prompts: Record<string, string>;
    status: string;
    status_badge: string;
    generated: string;
  } | null;
}

// ─── Phase labels ─────────────────────────────────────────────────────────────

const PHASE_LABELS: Record<string, string> = {
  loading: "Loading book content…",
  analyze: "Analyzing book…",
  concept: "Generating concepts…",
  method: "Selecting production method…",
  plan: "Building shotlist & narration…",
  validate: "Validating package…",
  package: "Assembling package…",
  done: "Complete",
  error: "Failed",
};

const PHASES = ["loading", "analyze", "concept", "method", "plan", "validate", "package"];

// ─── Copy button ──────────────────────────────────────────────────────────────

function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };
  return (
    <button
      onClick={copy}
      className="inline-flex items-center gap-1 text-[10px] font-mono px-2 py-0.5 rounded border border-border/50 bg-muted/30 text-muted-foreground hover:text-foreground hover:bg-muted/60 transition-colors"
    >
      {copied ? <Check className="w-3 h-3 text-emerald-600" /> : <Copy className="w-3 h-3" />}
      {copied ? "Copied!" : label}
    </button>
  );
}

// ─── Score bar ────────────────────────────────────────────────────────────────

function ScoreBar({ label, value, weight }: { label: string; value: number; weight: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="space-y-0.5">
      <div className="flex items-center justify-between text-[10px] font-mono text-muted-foreground">
        <span>{label} <span className="opacity-50">×{weight}</span></span>
        <span className="font-semibold text-foreground">{pct}%</span>
      </div>
      <div className="h-1 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary/60 rounded-full transition-all duration-500"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ─── Brief panel ──────────────────────────────────────────────────────────────

function BriefPanel({ brief }: { brief: Record<string, unknown> }) {
  const logline  = typeof brief.logline  === "string" ? brief.logline  : "";
  const genre    = typeof brief.genre    === "string" ? brief.genre    : "";
  const tone     = Array.isArray(brief.tone)          ? (brief.tone    as string[]) : [];
  const themes   = Array.isArray(brief.themes)        ? (brief.themes  as string[]) : [];
  const motifs   = Array.isArray(brief.visual_motifs) ? (brief.visual_motifs as string[]) : [];
  const protagonist   = typeof brief.protagonist    === "string" ? brief.protagonist    : "";
  const stakes        = typeof brief.central_stakes  === "string" ? brief.central_stakes  : "";
  const arc           = typeof brief.emotional_arc   === "string" ? brief.emotional_arc   : "";
  const audience      = typeof brief.audience        === "string" ? brief.audience        : "";
  const comparables   = Array.isArray(brief.comparable_titles)
    ? (brief.comparable_titles as string[]) : [];

  return (
    <div className="space-y-5">
      {logline && (
        <blockquote className="border-l-2 border-primary/40 pl-4 font-serif text-base leading-relaxed italic text-foreground/90">
          "{logline}"
        </blockquote>
      )}

      {/* Genre / tone / themes */}
      <div className="flex flex-wrap gap-2">
        {genre && <Badge variant="secondary" className="font-mono text-[10px]">{genre}</Badge>}
        {tone.map((t, i) => (
          <Badge key={i} variant="outline" className="font-mono text-[10px]">{t}</Badge>
        ))}
        {themes.map((t, i) => (
          <Badge key={i} className="font-mono text-[10px] bg-primary/10 text-primary border-primary/20">{t}</Badge>
        ))}
      </div>

      {/* Visual motifs */}
      {motifs.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">Visual Motifs</div>
          <div className="flex flex-wrap gap-1.5">
            {motifs.map((m, i) => (
              <span key={i} className="text-xs font-serif italic text-muted-foreground px-2 py-0.5 rounded bg-muted/40 border border-border/40">
                {m}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Story pillars */}
      <div className="grid gap-3 sm:grid-cols-2">
        {protagonist && (
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Protagonist</div>
            <p className="text-sm font-serif">{protagonist}</p>
          </div>
        )}
        {stakes && (
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Central Stakes</div>
            <p className="text-sm font-serif">{stakes}</p>
          </div>
        )}
        {arc && (
          <div className="sm:col-span-2">
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Emotional Arc</div>
            <p className="text-sm font-serif">{arc}</p>
          </div>
        )}
        {audience && (
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Audience</div>
            <p className="text-sm">{audience}</p>
          </div>
        )}
        {comparables.length > 0 && (
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-1">Comps</div>
            <p className="text-sm font-serif italic">{comparables.join(", ")}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// ─── Concepts panel ───────────────────────────────────────────────────────────

function ConceptsPanel({
  chosen,
  allConcepts,
}: {
  chosen: TrailerConcept;
  allConcepts: TrailerConcept[];
}) {
  const concepts = allConcepts.length > 0 ? allConcepts : [chosen];
  const sortedConcepts = [...concepts].sort(
    (a, b) => (b.score_total ?? 0) - (a.score_total ?? 0)
  );

  return (
    <div className="space-y-4">
      <p className="text-xs text-muted-foreground font-mono">
        Rubric weights: Fidelity 35% · Hook 30% · Feasibility 20% · Distinctiveness 15%
      </p>
      {sortedConcepts.map((c, i) => {
        const isChosen = c.name === chosen.name;
        return (
          <div
            key={i}
            className={`rounded-lg border p-4 space-y-3 transition-colors ${
              isChosen
                ? "border-primary/40 bg-primary/[0.03]"
                : "border-border/50 bg-muted/10"
            }`}
          >
            {/* Header */}
            <div className="flex items-start justify-between gap-3">
              <div className="space-y-0.5">
                <div className="flex items-center gap-2">
                  <span className="font-semibold font-serif">{c.name}</span>
                  {isChosen && (
                    <span className="inline-flex items-center gap-1 text-[9px] font-mono px-1.5 py-0.5 rounded-full bg-primary/10 text-primary border border-primary/20">
                      <Star className="w-2.5 h-2.5" /> CHOSEN
                    </span>
                  )}
                </div>
                <p className="text-xs text-muted-foreground">{c.angle}</p>
              </div>
              {c.score_total !== undefined && (
                <div className="shrink-0 text-right">
                  <div className="text-lg font-mono font-semibold text-primary leading-none">
                    {Math.round(c.score_total * 100)}
                  </div>
                  <div className="text-[9px] font-mono text-muted-foreground">/100</div>
                </div>
              )}
            </div>

            {/* Score bars */}
            {c.scores && (
              <div className="grid grid-cols-2 gap-x-6 gap-y-1.5">
                <ScoreBar label="Fidelity" value={c.scores.fidelity} weight={0.35} />
                <ScoreBar label="Hook" value={c.scores.hook} weight={0.30} />
                <ScoreBar label="Feasibility" value={c.scores.feasibility} weight={0.20} />
                <ScoreBar label="Distinctiveness" value={c.scores.distinctiveness} weight={0.15} />
              </div>
            )}

            {/* Rationale */}
            <p className="text-xs text-muted-foreground leading-relaxed">{c.rationale}</p>

            {/* Beats */}
            {c.beats && c.beats.length > 0 && (
              <div className="flex flex-wrap gap-1 pt-0.5">
                {c.beats.map((b, j) => (
                  <span
                    key={j}
                    className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted/50 border border-border/40 text-muted-foreground"
                  >
                    {j + 1}. {b}
                  </span>
                ))}
              </div>
            )}

            {/* Style row */}
            <div className="grid grid-cols-3 gap-3 text-[10px]">
              {c.visual_style && (
                <div>
                  <span className="font-mono uppercase text-muted-foreground">Visual</span>
                  <p className="mt-0.5 font-serif text-xs italic">{c.visual_style}</p>
                </div>
              )}
              {c.voice_direction && (
                <div>
                  <span className="font-mono uppercase text-muted-foreground">Voice</span>
                  <p className="mt-0.5 font-serif text-xs italic">{c.voice_direction}</p>
                </div>
              )}
              {c.music_direction && (
                <div>
                  <span className="font-mono uppercase text-muted-foreground">Music</span>
                  <p className="mt-0.5 font-serif text-xs italic">{c.music_direction}</p>
                </div>
              )}
            </div>

            <div className="text-[10px] font-mono text-muted-foreground">
              ~{c.duration}s · {c.beats?.length ?? 0} beats
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Shotlist panel ───────────────────────────────────────────────────────────

function ShotlistPanel({
  shots,
  shotPrompts,
}: {
  shots: TrailerShot[];
  shotPrompts: Record<string, string>;
}) {
  const [expanded, setExpanded] = useState<Set<number>>(new Set([0]));
  const toggle = (i: number) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      next.has(i) ? next.delete(i) : next.add(i);
      return next;
    });

  if (shots.length === 0) {
    return (
      <p className="text-sm text-muted-foreground italic text-center py-8">
        No shots in this package.
      </p>
    );
  }

  return (
    <div className="space-y-2">
      {shots.map((shot, i) => {
        const open = expanded.has(i);
        const promptKey = `shot_${String(i).padStart(2, "0")}`;
        const promptText = shotPrompts[promptKey] ?? shot.image_prompt;
        return (
          <div
            key={i}
            className="rounded-lg border border-border/50 overflow-hidden"
          >
            {/* Header row */}
            <button
              className="w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-muted/30 transition-colors"
              onClick={() => toggle(i)}
            >
              <span className="font-mono text-xs text-muted-foreground shrink-0 w-12">
                {String(i).padStart(2, "0")}
              </span>
              {shot.beat_type && (
                <Badge
                  className={`text-[9px] font-mono shrink-0 ${
                    shot.beat_type === "hook"
                      ? "bg-orange-500/15 text-orange-600 border-orange-500/30"
                      : shot.beat_type === "peak"
                        ? "bg-primary/15 text-primary border-primary/30"
                        : "bg-muted text-muted-foreground border-border"
                  }`}
                  variant="outline"
                >
                  {shot.beat_type.toUpperCase()}
                </Badge>
              )}
              <span className="flex-1 text-xs font-semibold font-mono truncate">
                {shot.beat}
              </span>
              <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                {shot.duration}s
              </span>
              {shot.on_screen_text && (
                <Badge variant="outline" className="text-[9px] font-mono shrink-0">
                  {shot.on_screen_text.slice(0, 12)}
                </Badge>
              )}
              {open ? (
                <ChevronDown className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              ) : (
                <ChevronRight className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              )}
            </button>

            {/* Detail */}
            {open && (
              <div className="px-4 pb-4 pt-1 space-y-3 border-t border-border/30 bg-muted/[0.02]">
                {shot.description && (
                  <p className="text-xs text-muted-foreground font-serif italic">
                    {shot.description}
                  </p>
                )}

                {/* Image prompt */}
                <div className="space-y-1">
                  <div className="flex items-center justify-between">
                    <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                      Image Prompt
                      {shot.image_model && <span className="ml-1.5 text-primary/60">({shot.image_model})</span>}
                    </span>
                    <CopyButton text={shot.image_prompt} label="Copy image prompt" />
                  </div>
                  <div className="p-2.5 rounded bg-muted/40 border border-border/30 text-[11px] font-mono leading-relaxed text-foreground/80 break-words">
                    {shot.image_prompt}
                  </div>
                </div>

                {/* Motion prompt */}
                {shot.motion_prompt && (
                  <div className="space-y-1">
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                        Motion Prompt
                        {shot.video_model && <span className="ml-1.5 text-primary/60">({shot.video_model})</span>}
                      </span>
                      <CopyButton text={shot.motion_prompt} label="Copy motion prompt" />
                    </div>
                    <div className="p-2.5 rounded bg-muted/40 border border-border/30 text-[11px] font-mono leading-relaxed text-foreground/80">
                      {shot.motion_prompt}
                    </div>
                  </div>
                )}

                {/* Negative prompt */}
                {shot.negative_prompt && (
                  <div className="space-y-1">
                    <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
                      Negative
                    </span>
                    <div className="p-2 rounded bg-destructive/5 border border-destructive/10 text-[11px] font-mono text-muted-foreground">
                      {shot.negative_prompt}
                    </div>
                  </div>
                )}

                {/* Settings row */}
                {(shot.resolution || shot.frames || shot.steps) && (
                  <div className="flex flex-wrap gap-3 text-[10px] font-mono text-muted-foreground pt-1">
                    {shot.resolution && <span>📐 {shot.resolution}</span>}
                    {shot.frames && <span>🎞 {shot.frames} frames</span>}
                    {shot.steps && <span>🔢 {shot.steps} steps</span>}
                    {shot.seed_policy && <span>🌱 {shot.seed_policy}</span>}
                    {shot.upscale && <span>🔍 {shot.upscale}</span>}
                  </div>
                )}

                {/* Vertical framing note (short-form only) */}
                {shot.vertical_framing_note && (
                  <div className="flex items-start gap-2 pt-1 px-2.5 py-2 rounded-md bg-sky-500/5 border border-sky-500/20">
                    <Smartphone className="w-3 h-3 mt-0.5 text-sky-500 shrink-0" />
                    <span className="text-[11px] text-sky-700 dark:text-sky-400 font-mono">
                      {shot.vertical_framing_note}
                    </span>
                  </div>
                )}

                {/* Full ComfyUI prompt copy button */}
                {promptText && (
                  <div className="pt-1 border-t border-border/20">
                    <CopyButton text={promptText} label="Copy full ComfyUI prompt block" />
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

// ─── Music / SFX generation ───────────────────────────────────────────────────

interface MusicModelInfo {
  id: string;
  name: string;
  vendor: string;
  license: string;
  license_url: string;
  license_summary: string;
  commercial_use: string;
  max_duration_s: number;
  good_for: string[];
  license_acked: boolean;
  installed: boolean;
  loaded: boolean;
  load_error?: string | null;
}

interface MusicGenStatus {
  configured: boolean;
  reachable: boolean;
  device?: string | null;
  models: MusicModelInfo[];
}

function useMusicGenStatus() {
  return useQuery<MusicGenStatus>({
    queryKey: ["music-gen-status"],
    queryFn: () => apiFetch(`${BASE}/studio/music/status`).then(r => r.json()),
    staleTime: 30_000,
    retry: 1,
  });
}

const SFX_MAX_S = 15;

/** One-tap generation for a trailer music/SFX prompt.
 *  Renders nothing when the music sidecar is not configured (clean degradation). */
function MusicGenControls({
  prompt,
  kind,
  defaultDuration,
  workId,
}: {
  prompt: string;
  kind: "music" | "sfx";
  defaultDuration?: number | null;
  workId?: string;
}) {
  const { data: status } = useMusicGenStatus();
  const qc = useQueryClient();
  const readAloud = useReadAloud();

  const models = (status?.models ?? []).filter(m => m.good_for.includes(kind));
  const [modelId, setModelId] = useState<string>("");
  const model = models.find(m => m.id === modelId) ?? models[0];

  const cap = Math.min(model?.max_duration_s ?? 47, kind === "sfx" ? SFX_MAX_S : Infinity);
  const [duration, setDuration] = useState<number>(() =>
    Math.max(1, Math.min(defaultDuration ?? (kind === "sfx" ? 5 : 30), cap)));

  const [generating, setGenerating] = useState(false);
  const [outputPath, setOutputPath] = useState<string | null>(null);
  const [licenseOpen, setLicenseOpen] = useState(false);
  const [licenseChecked, setLicenseChecked] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Stop polling on unmount.
  useEffect(() => () => {
    if (pollRef.current) clearInterval(pollRef.current);
  }, []);

  if (!status?.configured || !model) return null;

  const unreachable = !status.reachable;
  const serveUrl = outputPath
    ? `${BASE}/studio/outputs/serve?path=${encodeURIComponent(outputPath)}`
    : null;

  async function startGeneration() {
    if (!model) return;
    setGenerating(true);
    setOutputPath(null);
    try {
      const dur = Math.max(1, Math.min(duration, cap));
      const resp = await apiFetch(`${BASE}/studio/music/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt, model: model.id, kind, duration_s: dur, work_id: workId ?? null,
        }),
      });
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const { job_id } = await resp.json();
      // Own exactly one interval: kill any stale poller before starting a new
      // one, and let this closure clear only the interval it created.
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
      const interval = setInterval(async () => {
        const stop = () => {
          clearInterval(interval);
          if (pollRef.current === interval) pollRef.current = null;
        };
        try {
          const r = await apiFetch(`${BASE}/studio/music/jobs/${job_id}`);
          if (!r.ok) throw new Error(`HTTP ${r.status}`);
          const job = await r.json();
          if (job.state === "done") {
            stop();
            setGenerating(false);
            setOutputPath(job.output_path);
            qc.invalidateQueries({ queryKey: ["listStudioOutputs"] });
            if (job.registered === false) {
              toast.warning(job.warning ?? "Audio saved, but library registration failed.", { duration: 9000 });
            } else {
              toast.success(kind === "sfx" ? "Sound effect ready — find it in Studio outputs" : "Music ready — find it in Studio outputs");
            }
          } else if (job.state === "error") {
            stop();
            setGenerating(false);
            toast.error(`Generation failed: ${job.error ?? "unknown error"}`, { duration: 9000 });
          }
        } catch {
          stop();
          setGenerating(false);
          toast.error("Lost track of the generation job — check Studio outputs in a minute.");
        }
      }, 3000);
      pollRef.current = interval;
    } catch (e: any) {
      setGenerating(false);
      toast.error(`Could not start generation: ${e.message}`, { duration: 8000 });
    }
  }

  function handleGenerateClick() {
    if (!prompt.trim() || !model) return;
    if (!model.license_acked) {
      setLicenseChecked(false);
      setLicenseOpen(true);
      return;
    }
    void startGeneration();
  }

  async function acceptLicense() {
    if (!model) return;
    try {
      const r = await apiFetch(`${BASE}/studio/music/licenses/${model.id}/ack`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ accepted: true }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      await qc.invalidateQueries({ queryKey: ["music-gen-status"] });
      setLicenseOpen(false);
      void startGeneration();
    } catch (e: any) {
      toast.error(`Could not record license acceptance: ${e.message}`);
    }
  }

  return (
    <div className="space-y-2 pt-2 border-t border-border/30">
      <div className="flex flex-wrap items-center gap-2">
        {models.length > 1 ? (
          <Select value={model.id} onValueChange={setModelId}>
            <SelectTrigger className="h-7 w-auto min-w-[160px] text-[11px] font-mono">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {models.map(m => (
                <SelectItem key={m.id} value={m.id} className="text-xs">
                  {m.name}{m.commercial_use === "no" ? " (non-commercial)" : ""}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        ) : (
          <span className="text-[10px] font-mono text-muted-foreground">{model.name}</span>
        )}
        <div className="flex items-center gap-1">
          <Input
            type="number"
            min={1}
            max={cap}
            value={duration}
            onChange={(e) => setDuration(Number(e.target.value))}
            className="h-7 w-16 text-[11px] font-mono"
            aria-label="Duration in seconds"
          />
          <span className="text-[10px] font-mono text-muted-foreground">s · max {cap}s</span>
        </div>
        <Button
          size="sm"
          className="h-7 gap-1.5 text-xs"
          disabled={generating || unreachable || !prompt.trim()}
          onClick={handleGenerateClick}
          title={unreachable ? "Music engine is configured but not reachable — start the sidecar" : undefined}
        >
          {generating
            ? <><Loader2 className="w-3 h-3 animate-spin" /> Generating…</>
            : <><Wand2 className="w-3 h-3" /> Generate {kind === "sfx" ? "effect" : "music"}</>}
        </Button>
        {serveUrl && (
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1.5 text-xs"
            onClick={() => readAloud.startUrl({
              title: kind === "sfx" ? "Sound effect" : "Trailer music",
              href: "/studio",
              url: serveUrl,
            })}
          >
            <Play className="w-3 h-3" /> Play
          </Button>
        )}
      </div>
      {unreachable && (
        <p className="text-[10px] font-mono" style={{ color: "var(--gilt)" }}>
          Music engine configured but not responding — start the sidecar (scripts\start-music-sidecar.ps1).
        </p>
      )}
      {model && !model.license_acked && (
        <p className="text-[10px] font-mono text-muted-foreground/70 flex items-center gap-1">
          <ScrollText className="w-3 h-3" /> {model.name} requires a one-time license acknowledgement before first use.
        </p>
      )}

      {/* License acknowledgement dialog */}
      <Dialog open={licenseOpen} onOpenChange={setLicenseOpen}>
        <DialogContent className="max-w-md">
          <DialogHeader>
            <DialogTitle className="font-serif text-lg">
              {model.name} — license terms
            </DialogTitle>
            <DialogDescription asChild>
              <div className="space-y-3 pt-1 text-left">
                <p className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
                  {model.license}
                </p>
                <p className="text-sm leading-relaxed">{model.license_summary}</p>
                <a
                  href={model.license_url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-xs underline text-primary"
                >
                  Read the full license terms
                </a>
              </div>
            </DialogDescription>
          </DialogHeader>
          <label className="flex items-start gap-2 text-sm cursor-pointer">
            <Checkbox
              checked={licenseChecked}
              onCheckedChange={(v) => setLicenseChecked(v === true)}
              className="mt-0.5"
            />
            <span>I have read and accept these license terms for my use case.</span>
          </label>
          <DialogFooter>
            <Button variant="outline" size="sm" onClick={() => setLicenseOpen(false)}>
              Cancel
            </Button>
            <Button size="sm" disabled={!licenseChecked} onClick={acceptLicense}>
              Accept & generate
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

/** Free-prompt sound-effect generator shown under the music brief.
 *  Hidden when the sidecar is not configured. */
function SfxGenerator({ workId }: { workId?: string }) {
  const { data: status } = useMusicGenStatus();
  const [prompt, setPrompt] = useState("");
  if (!status?.configured) return null;
  const sfxCapable = (status.models ?? []).some(m => m.good_for.includes("sfx"));
  if (!sfxCapable) return null;
  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
        <Wand2 className="w-3.5 h-3.5" />
        Sound Effects
      </div>
      <div className="rounded-lg border border-border/50 bg-muted/10 p-4 space-y-2">
        <Input
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe an effect — e.g. deep cinematic braam with metallic tail…"
          className="h-8 text-sm font-serif"
        />
        <MusicGenControls prompt={prompt} kind="sfx" defaultDuration={5} workId={workId} />
      </div>
    </div>
  );
}

// ─── Narration & Music panel ──────────────────────────────────────────────────

function NarrationMusicPanel({
  narration,
  music,
  workId,
}: {
  narration: TrailerNarrationLine[];
  music: TrailerMusic;
  workId?: string;
}) {
  return (
    <div className="space-y-6">
      {/* Narration table */}
      <div className="space-y-2">
        <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
          <Mic className="w-3.5 h-3.5" />
          Narration Script
        </div>
        {narration.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">No narration lines.</p>
        ) : (
          <div className="rounded-lg border border-border/50 overflow-hidden">
            <div className="grid grid-cols-[48px_1fr_72px_72px] gap-0 text-[10px] font-mono uppercase tracking-widest text-muted-foreground bg-muted/30 px-3 py-1.5 border-b border-border/30">
              <span>T(s)</span><span>Line</span><span>Emotion</span><span>Pace</span>
            </div>
            {narration.map((line, i) => (
              <div
                key={i}
                className="grid grid-cols-[48px_1fr_72px_72px] gap-0 items-start px-3 py-2 border-b border-border/20 last:border-0 hover:bg-muted/20 transition-colors"
              >
                <span className="text-[11px] font-mono text-muted-foreground pt-0.5">
                  {line.t_start}s
                </span>
                <span className="text-sm font-serif pr-3">{line.text}</span>
                <span className="text-[10px] font-mono text-muted-foreground pt-0.5">
                  {line.emotion}
                </span>
                <span className="text-[10px] font-mono text-muted-foreground pt-0.5">
                  {line.pace}
                </span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Music brief */}
      {music && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            <Music className="w-3.5 h-3.5" />
            Music Brief (MusicGen)
          </div>
          <div className="rounded-lg border border-border/50 bg-muted/10 p-4 space-y-3">
            {/* Prompt */}
            {music.prompt && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-mono text-muted-foreground uppercase tracking-widest">
                    Prompt (paste into MusicGen)
                  </span>
                  <CopyButton text={music.prompt} label="Copy music prompt" />
                </div>
                <div className="p-3 rounded bg-muted/40 border border-border/30 text-sm font-serif italic leading-relaxed">
                  {music.prompt}
                </div>
              </div>
            )}
            {/* Specs */}
            <div className="flex flex-wrap gap-4 text-xs font-mono">
              {music.mood && (
                <span className="text-muted-foreground">Mood: <strong className="text-foreground">{music.mood}</strong></span>
              )}
              {music.tempo_bpm && (
                <span className="text-muted-foreground">Tempo: <strong className="text-foreground">{music.tempo_bpm} bpm</strong></span>
              )}
              {music.length_seconds && (
                <span className="text-muted-foreground">Length: <strong className="text-foreground">{music.length_seconds}s</strong></span>
              )}
            </div>
            {music.structure && (
              <div className="text-xs text-muted-foreground font-serif italic">{music.structure}</div>
            )}
            <p className="text-[10px] font-mono text-muted-foreground/60">
              License note: MusicGen weights are CC-BY-NC (non-commercial only — its MIT license covers
              only the code). For work you may publish commercially, use Stable Audio Open and verify
              the Stability AI Community License terms for your situation.
            </p>
            {music.prompt && (
              <MusicGenControls
                prompt={music.prompt}
                kind="music"
                defaultDuration={music.length_seconds}
                workId={workId}
              />
            )}
          </div>
        </div>
      )}

      {/* Sound effects — free-prompt generation (hidden when engine not configured) */}
      <SfxGenerator workId={workId} />
    </div>
  );
}

// ─── Assembly panel ───────────────────────────────────────────────────────────

function AssemblyPanel({ assembly, duration }: {
  assembly: Record<string, unknown>;
  duration: number;
}) {
  const tl = (assembly.timeline ?? {}) as Record<string, unknown>;
  const v1 = Array.isArray(tl.V1_video) ? tl.V1_video as { shot: number; in: number; dur: number }[] : [];
  const a1 = Array.isArray(tl.A1_narration) ? tl.A1_narration as { t: number; line: string }[] : [];
  const a2 = Array.isArray(tl.A2_music) ? tl.A2_music as { t: number; duck_under_vo_db?: number }[] : [];
  const audioMix = (assembly.audio_mix ?? {}) as Record<string, unknown>;
  const masters = Array.isArray(assembly.masters) ? assembly.masters as { aspect: string; note?: string }[] : [];
  const exportSpec = (assembly.export ?? {}) as Record<string, unknown>;
  const transitions = typeof assembly.transitions === "string" ? assembly.transitions : "";

  return (
    <div className="space-y-6 text-xs">
      {/* Summary */}
      <div className="flex flex-wrap gap-4 font-mono text-muted-foreground">
        <span>Duration: <strong className="text-foreground">{duration}s</strong></span>
        {typeof exportSpec.codec === "string" && exportSpec.codec && (
          <span>Codec: <strong className="text-foreground">{exportSpec.codec}</strong></span>
        )}
        {(typeof exportSpec.fps === "string" || typeof exportSpec.fps === "number") && exportSpec.fps && (
          <span>FPS: <strong className="text-foreground">{String(exportSpec.fps)}</strong></span>
        )}
        {transitions && (
          <span>Transitions: <strong className="text-foreground">{transitions}</strong></span>
        )}
      </div>

      {/* Video track */}
      {v1.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            V1 — Video Track
          </div>
          <div className="space-y-0.5">
            {v1.map((c, i) => (
              <div key={i} className="flex items-center gap-2 font-mono text-[11px]">
                <span className="w-14 shrink-0 text-muted-foreground">@{c.in}s</span>
                <div className="flex-1 h-4 bg-primary/10 border border-primary/20 rounded-sm flex items-center px-1.5 text-primary/70 text-[9px]"
                  style={{ width: `${Math.max(40, (c.dur / duration) * 100)}%`, maxWidth: "100%" }}>
                  Shot {String(c.shot).padStart(2, "0")} · {c.dur}s
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Narration track */}
      {a1.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            A1 — Narration
          </div>
          <div className="space-y-0.5">
            {a1.map((c, i) => (
              <div key={i} className="flex items-start gap-2 text-[11px]">
                <span className="font-mono text-muted-foreground shrink-0 w-14">{c.t}s</span>
                <span className="font-serif text-foreground/80">{c.line}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Music track */}
      {a2.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            A2 — Music
          </div>
          <p className="text-[11px] font-mono text-muted-foreground">
            score.wav @ 0s · duck {a2[0]?.duck_under_vo_db ?? "?"}dB under VO
          </p>
        </div>
      )}

      {/* Audio mix */}
      {Object.keys(audioMix).length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            Audio Mix (LUFS targets)
          </div>
          <div className="flex flex-wrap gap-3 font-mono text-[11px]">
            {Object.entries(audioMix).map(([k, v]) => (
              <span key={k} className="text-muted-foreground">
                {k}: <strong className="text-foreground">{String(v)} LUFS</strong>
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Masters */}
      {masters.length > 0 && (
        <div>
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">
            Export Masters
          </div>
          <div className="space-y-1">
            {masters.map((m, i) => (
              <div key={i} className="flex items-center gap-3 text-[11px] font-mono">
                <Badge variant="outline" className="text-[9px]">{m.aspect}</Badge>
                {m.note && <span className="text-muted-foreground">{m.note}</span>}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Package detail viewer ────────────────────────────────────────────────────

function TrailerPackageDetail({ trailer }: { trailer: TrailerPackage }) {
  const [panel, setPanel] = useState("brief");
  const [activeFmt, setActiveFmt] = useState<TrailerFormat>("full");
  const rawPkg = trailer.package;

  if (!rawPkg) {
    return (
      <div className="text-sm text-muted-foreground italic text-center py-8">
        {trailer.status === "running"
          ? `Still generating — ${PHASE_LABELS[trailer.phase] ?? trailer.phase}…`
          : trailer.status === "failed"
            ? `Generation failed${trailer.error ? `: ${trailer.error}` : " — start a new trailer."}`
            : "No package was produced for this trailer — start a new one."}
      </div>
    );
  }

  // Support combined {format:"both"|"all", full:{...}, short:{...}, square?:{...}} packages
  const combinedFmt: string | undefined = (rawPkg as any).format;
  const isCombined = combinedFmt === "both" || combinedFmt === "all";
  const hasSquare  = combinedFmt === "all" && !!(rawPkg as any).square;
  const pkg: any = isCombined
    ? activeFmt === "short"
      ? (rawPkg as any).short
      : activeFmt === "square" && hasSquare
        ? (rawPkg as any).square
        : (rawPkg as any).full
    : rawPkg;

  // Shots and narration — resolved from the active sub-package
  const plan = pkg?.plan ?? ({} as any);
  const shots: TrailerShot[] = Array.isArray(plan.shots) ? plan.shots : [];
  const narration: TrailerNarrationLine[] = Array.isArray(plan.narration) ? plan.narration : [];
  const music: TrailerMusic = plan.music ?? {};
  const assembly = (plan.assembly ?? {}) as Record<string, unknown>;
  const duration: number = typeof plan.duration === "number" ? plan.duration : 0;
  // Concepts live in the full sub-package (shared across formats)
  const basePkg: any = isCombined ? (rawPkg as any).full : rawPkg;
  const allConcepts: TrailerConcept[] = Array.isArray(basePkg?.plan?._all_concepts)
    ? basePkg.plan._all_concepts
    : Array.isArray(plan._all_concepts)
      ? plan._all_concepts
      : [rawPkg.concept];
  const shotPrompts = pkg?.shot_prompts ?? {};

  // Validation badge
  const valOk = pkg.validation?.status === "READY";
  const criticalCount: number = pkg.validation?.critical ?? 0;
  const findings = (pkg.validation?.findings ?? []).filter(
    (f: { severity: string; code: string; msg: string }) => f.severity === "critical"
  );

  // Download handler
  function downloadPackage() {
    const json = JSON.stringify(pkg, null, 2);
    const blob = new Blob([json], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `trailer-package-${trailer.id.slice(0, 8)}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="space-y-4">
      {/* Format toggle — only shown for combined "both"/"all" packages */}
      {isCombined && (
        <div className="flex items-center gap-1 p-0.5 rounded-lg bg-muted/50 border border-border/50 w-fit">
          {(
            [
              { value: "full",   Icon: Monitor,    label: "Full trailer", sub: "75 s · 16:9" },
              { value: "short",  Icon: Smartphone, label: "Social clip",  sub: "30 s · 9:16" },
              ...(hasSquare
                ? [{ value: "square" as const, Icon: Layers, label: "Square",      sub: "30 s · 1:1" }]
                : []),
            ] as const
          ).map(({ value, Icon, label, sub }) => (
            <button
              key={value}
              onClick={() => setActiveFmt(value as TrailerFormat)}
              className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-mono transition-all ${
                activeFmt === value
                  ? "bg-background border border-border shadow-sm text-foreground"
                  : "text-muted-foreground hover:text-foreground"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{label}</span>
              <span className="text-[9px] text-muted-foreground">{sub}</span>
            </button>
          ))}
        </div>
      )}

      {/* Package header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          {valOk ? (
            <span className="flex items-center gap-1.5 text-xs font-mono text-emerald-700 bg-emerald-50/80 border border-emerald-200 rounded-full px-2.5 py-1">
              <CheckCircle className="w-3.5 h-3.5" /> READY
            </span>
          ) : (
            <span className="flex items-center gap-1.5 text-xs font-mono text-amber-700 bg-amber-50/80 border border-amber-200 rounded-full px-2.5 py-1">
              <AlertCircle className="w-3.5 h-3.5" /> BLOCKED · {criticalCount} critical
            </span>
          )}
          <span className="text-xs font-mono text-muted-foreground">
            Generated {pkg.generated}
          </span>
          <span className="text-xs font-mono text-muted-foreground">
            {shots.length} shots · ~{duration}s
            {activeFmt === "short"  && <span className="ml-1 text-sky-600">· 9:16</span>}
            {activeFmt === "square" && <span className="ml-1 text-violet-600">· 1:1</span>}
          </span>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 h-7 text-xs"
          onClick={downloadPackage}
        >
          <Download className="w-3 h-3" />
          Download JSON
        </Button>
      </div>

      {/* Blocking findings */}
      {findings.map((f: { severity: string; code: string; msg: string }, i: number) => (
        <div key={i} className="flex items-start gap-2 px-3 py-2 rounded border border-destructive/30 bg-destructive/5 text-xs text-destructive">
          <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span><strong>{f.code}</strong> — {f.msg}</span>
        </div>
      ))}

      {/* Five-panel tabs */}
      <Tabs value={panel} onValueChange={setPanel}>
        <TabsList className="flex w-full justify-start border-b border-border/50 rounded-none bg-transparent h-auto p-0 space-x-4 overflow-x-auto">
          {[
            { value: "brief",     icon: BookOpen,    label: "Brief" },
            { value: "concepts",  icon: BarChart3,   label: "Concepts" },
            { value: "shotlist",  icon: Clapperboard, label: `Shotlist (${shots.length})` },
            { value: "narration", icon: Mic,         label: "Narration & Music" },
            { value: "assembly",  icon: Layers,      label: "Assembly" },
          ].map(({ value, icon: Icon, label }) => (
            <TabsTrigger
              key={value}
              value={value}
              className="shrink-0 rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-2 px-1 font-mono text-[10px] uppercase tracking-wider"
            >
              <Icon className="w-3 h-3 mr-1.5" /> {label}
            </TabsTrigger>
          ))}
        </TabsList>

        <div className="mt-5">
          <TabsContent value="brief">
            <BriefPanel brief={pkg.brief} />
          </TabsContent>
          <TabsContent value="concepts">
            <ConceptsPanel chosen={pkg.concept} allConcepts={allConcepts} />
          </TabsContent>
          <TabsContent value="shotlist">
            <ShotlistPanel shots={shots} shotPrompts={shotPrompts} />
          </TabsContent>
          <TabsContent value="narration">
            <NarrationMusicPanel narration={narration} music={music} workId={trailer.work_id} />
          </TabsContent>
          <TabsContent value="assembly">
            <AssemblyPanel assembly={assembly} duration={duration} />
          </TabsContent>
        </div>
      </Tabs>
    </div>
  );
}

// ─── Progress bar ─────────────────────────────────────────────────────────────

function PhaseProgress({ phase }: { phase: string }) {
  const idx = PHASES.indexOf(phase);
  const pct = idx < 0 ? 15 : Math.round(((idx + 1) / PHASES.length) * 100);
  return (
    <div className="space-y-1.5 pt-2">
      <div className="flex items-center justify-between text-xs font-mono">
        <span className="flex items-center gap-1.5 text-primary">
          <Loader2 className="w-3 h-3 animate-spin" />
          {PHASE_LABELS[phase] ?? phase}
        </span>
        <span className="text-muted-foreground">{pct}%</span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full bg-primary/50 rounded-full transition-all duration-1000"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

// ─── Trailer history row ──────────────────────────────────────────────────────

function TrailerHistoryRow({ trailer, workId }: { trailer: TrailerListItem; workId: string }) {
  const [open, setOpen] = useState(false);

  const { data: full, isLoading } = useQuery<TrailerPackage>({
    queryKey: ["trailer-full", workId, trailer.id],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/trailers/${trailer.id}`);
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    enabled: open || trailer.status === "running",
    refetchInterval: trailer.status === "running" ? 3_000 : false,
    staleTime: trailer.status === "running" ? 0 : 60_000,
  });

  const liveStatus = full?.status ?? trailer.status;
  const livePhase  = full?.phase  ?? trailer.phase;
  const isRunning  = liveStatus === "running";

  return (
    <div className="rounded-lg border border-border/50 overflow-hidden">
      {/* Row */}
      <button
        className="w-full flex items-center gap-3 px-4 py-3 text-left hover:bg-muted/20 transition-colors"
        onClick={() => setOpen((p) => !p)}
      >
        <Film className="w-4 h-4 text-muted-foreground shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-mono text-muted-foreground truncate">
            {new Date(trailer.created_at).toLocaleString()}
          </div>
          {isRunning && <PhaseProgress phase={livePhase} />}
        </div>
        {liveStatus === "ready" && (
          <span className="flex items-center gap-1 text-[10px] font-mono text-emerald-600 shrink-0">
            <CheckCircle className="w-3 h-3" /> READY
          </span>
        )}
        {liveStatus === "blocked" && (
          <span className="flex items-center gap-1 text-[10px] font-mono text-amber-600 shrink-0">
            <AlertCircle className="w-3 h-3" /> BLOCKED
          </span>
        )}
        {liveStatus === "failed" && (
          <span className="flex items-center gap-1 text-[10px] font-mono text-destructive shrink-0">
            <XCircle className="w-3 h-3" /> FAILED
          </span>
        )}
        {!isRunning && (
          open
            ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
            : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        )}
      </button>

      {/* Error */}
      {full?.error && !isRunning && (
        <div className="px-4 py-2 text-xs text-destructive font-mono border-t border-destructive/20 bg-destructive/5">
          {full.error}
        </div>
      )}

      {/* Package detail */}
      {open && !isRunning && full && full.package && (
        <div className="px-4 pb-5 pt-2 border-t border-border/30">
          {isLoading ? (
            <Skeleton className="h-40 w-full" />
          ) : (
            <TrailerPackageDetail trailer={full} />
          )}
        </div>
      )}
    </div>
  );
}

// ─── Main tab ─────────────────────────────────────────────────────────────────

// Format picker labels / icons
const FORMAT_OPTIONS: { value: TrailerFormat | "both" | "all"; Icon: React.FC<{ className?: string }>; label: string; desc: string }[] = [
  { value: "all",    Icon: Blend,      label: "All three",      desc: "16:9 + 9:16 + 1:1 in one job" },
  { value: "both",   Icon: Blend,      label: "Full + Social",  desc: "75 s 16:9 & 30 s 9:16" },
  { value: "full",   Icon: Monitor,    label: "Full trailer",   desc: "75 s · 16:9" },
  { value: "short",  Icon: Smartphone, label: "Social 9:16",    desc: "30 s · 9:16 Reels/TikTok/Shorts" },
  { value: "square", Icon: Layers,     label: "Square 1:1",     desc: "30 s · 1:1 Instagram Feed/LinkedIn" },
];

export function TrailerTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [genFormat, setGenFormat] = useState<"full" | "short" | "square" | "both" | "all">("all");

  const { data, isLoading } = useQuery<{ trailers: TrailerListItem[]; count: number }>({
    queryKey: ["trailers", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/trailers`);
      if (!r.ok) throw new Error("Failed to load trailers");
      return r.json();
    },
    staleTime: 30_000,
    // Poll while any trailer is running
    refetchInterval: (q) => {
      const trailers = (q.state.data as any)?.trailers ?? [];
      return trailers.some((t: TrailerListItem) => t.status === "running") ? 3_000 : false;
    },
  });

  const generateMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(
        `${BASE}/works/${workId}/trailer?format=${genFormat}`,
        { method: "POST" },
      );
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as any)?.detail ?? "Failed to start");
      return body;
    },
    onSuccess: () => {
      const label = FORMAT_OPTIONS.find((o) => o.value === genFormat)?.label ?? genFormat;
      toast.success(`Trailer Architect started — ${label}`);
      queryClient.invalidateQueries({ queryKey: ["trailers", workId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const hasRunning = data?.trailers.some((t) => t.status === "running") ?? false;

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
            <Film className="w-5 h-5 text-muted-foreground" />
            Trailer Architect
          </h2>
          <p className="text-sm text-muted-foreground mt-0.5">
            Concept · shotlist · narration · music · assembly — full landscape and/or 30 s vertical social clip.
          </p>
        </div>
        <div className="flex flex-col items-end gap-2 shrink-0">
          {/* Format picker */}
          <div className="flex items-center gap-0.5 p-0.5 rounded-lg bg-muted/50 border border-border/50">
            {FORMAT_OPTIONS.map(({ value, Icon, label }) => (
              <button
                key={value}
                onClick={() => setGenFormat(value as any)}
                title={FORMAT_OPTIONS.find((o) => o.value === value)?.desc}
                className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-[11px] font-mono transition-all ${
                  genFormat === value
                    ? "bg-background border border-border shadow-sm text-foreground"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                <Icon className="w-3 h-3" />
                {label}
              </button>
            ))}
          </div>
          <Button
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending || hasRunning}
            className="gap-2"
          >
            {generateMutation.isPending || hasRunning ? (
              <><Loader2 className="w-4 h-4 animate-spin" /> {hasRunning ? "Generating…" : "Starting…"}</>
            ) : (
              <><Sparkles className="w-4 h-4" /> Generate</>
            )}
          </Button>
        </div>
      </div>

      {/* How it works — shown when empty */}
      {!isLoading && (!data || data.count === 0) && (
        <Card className="border-border/50 border-dashed bg-muted/10">
          <CardContent className="py-8 text-center space-y-3">
            <Film className="w-8 h-8 text-muted-foreground/40 mx-auto" />
            <p className="text-sm text-muted-foreground font-serif italic">
              No trailer packages yet.
            </p>
            <p className="text-xs text-muted-foreground max-w-md mx-auto">
              Click <strong>Generate Trailer</strong> to run the six-stage pipeline: book analysis → concept scoring → model selection → shotlist & narration → validation → production package.
            </p>
            <p className="text-xs text-muted-foreground/60">
              Requires at least one processed document with extracted text.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Loading skeleton */}
      {isLoading && (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      )}

      {/* Trailer history */}
      {data && data.count > 0 && (
        <div className="space-y-3">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
            {data.count} Package{data.count !== 1 ? "s" : ""} — Newest First
          </div>
          {data.trailers.map((t) => (
            <TrailerHistoryRow key={t.id} trailer={t} workId={workId} />
          ))}
        </div>
      )}
    </div>
  );
}
