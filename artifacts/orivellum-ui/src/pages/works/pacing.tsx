import { useState } from "react";
import { useParams, Link } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ArrowLeft,
  Play,
  Loader2,
  CheckCircle2,
  AlertTriangle,
  Wind,
  Activity,
  ZoomIn,
  TrendingDown,
  BookOpen,
  Eye,
  Layers,
  Settings,
  ChevronDown,
  ChevronRight,
} from "lucide-react";
import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  ReferenceLine,
} from "recharts";
import { toast } from "sonner";

const API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ────────────────────────────────────────────────────────────────────

type Scene = {
  id: string;
  chapter_id: string;
  seq: number;
  title: string | null;
  purpose: string | null;
  pov: string | null;
  setting: string | null;
  word_count: number;
  status: "proposed" | "confirmed" | "dismissed";
  /** Latest persisted metrics, null if no analysis run yet. */
  latest_metrics: SceneMetrics | null;
};

type SceneMetrics = {
  id: string;
  scene_id: string;
  tension_before: number | null;
  tension_after: number | null;
  emotional_intensity: number | null;
  revelation_density: number | null;
  action_ratio: number | null;
  reflection_ratio: number | null;
  sensory_grounding: number | null;
  has_aftermath: number;
  has_orientation: number;
  irreversible_turns: number;
  reader_questions_created: number;
  reader_questions_answered: number;
  consequence_present: number;
  purpose_clear: number;
  evidence: { field: string; quote: string; reasoning: string }[];
};

type PacingRun = {
  id: string;
  work_id: string;
  profile_name: string;
  status: "pending" | "running" | "done" | "failed";
  coverage: {
    total_scenes?: number;
    analyzed_scenes?: number;
    profile?: string;
    partial?: boolean;
    note?: string;
  };
  error: string | null;
  created_at: string;
};

type PacingFinding = {
  id: string;
  detector: string;
  finding_type: string;
  severity: "low" | "medium" | "high" | "critical";
  subject: string;
  explanation: string;
  evidence: unknown[];
  recommendation: {
    recommendation_type: string;
    explanation: string;
    placement?: string;
    what_changes?: string;
    distinct_question_test?: string;
    alternatives?: { option: string; tradeoff: string }[];
  };
  status: "open" | "accepted" | "intentional" | "dismissed";
  resolution_note: string;
};

type PacingProfile = {
  work_id: string;
  profile_name: string;
  thresholds: Record<string, unknown>;
  available_profiles: Record<string, string>;
};

// ── Constants ────────────────────────────────────────────────────────────────

const DETECTOR_META: Record<string, { label: string; icon: typeof Activity; color: string }> = {
  pacing_map:          { label: "Pacing Map",          icon: Activity,     color: "text-blue-500" },
  breath_map:          { label: "Breath Map",           icon: Wind,         color: "text-teal-500" },
  compression:         { label: "Compression",          icon: ZoomIn,       color: "text-orange-500" },
  drift:               { label: "Drift",                icon: TrendingDown, color: "text-slate-500" },
  book_boundary:       { label: "Book Boundary",        icon: BookOpen,     color: "text-purple-500" },
  immersion_integrity: { label: "Immersion Integrity",  icon: Eye,          color: "text-red-500" },
  series_rhythm:       { label: "Series Rhythm",        icon: Layers,       color: "text-indigo-500" },
};

const RECOMMENDATION_TYPE_LABELS: Record<string, string> = {
  more_scenes:         "→ Add scenes/chapters",
  another_book:        "→ Evaluate as another book",
  no_expansion:        "→ No expansion (revise in-place)",
  intentional_restraint: "→ Consider intentional restraint",
};

const SEVERITY_STYLES: Record<string, string> = {
  critical: "border-red-500 text-red-600 bg-red-50 dark:bg-red-950/30",
  high:     "border-orange-400 text-orange-600 bg-orange-50 dark:bg-orange-950/30",
  medium:   "border-yellow-400 text-yellow-600 bg-yellow-50 dark:bg-yellow-950/30",
  low:      "border-slate-300 text-slate-500",
};

// ── Helpers ──────────────────────────────────────────────────────────────────

function pct(v: number | null): string {
  if (v == null) return "—";
  return `${Math.round(v * 100)}%`;
}

function bar(v: number | null, color = "bg-primary"): React.ReactElement {
  const w = v == null ? 0 : Math.round(v * 100);
  return (
    <div className="h-1.5 w-full rounded-full bg-muted overflow-hidden">
      <div className={`h-full rounded-full ${color}`} style={{ width: `${w}%` }} />
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────

export default function PacingPage() {
  const { workId } = useParams<{ workId: string }>();
  const [tab, setTab] = useState<"overview" | "breath" | "findings" | "profile">("overview");

  const workQ = useQuery({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${API}/works/${workId}`).then((r) => r.json()),
  });
  const work = workQ.data?.work ?? workQ.data;

  return (
    <div className="container mx-auto max-w-5xl px-4 py-6 space-y-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href={`/works/${workId}`}>
          <Button variant="ghost" size="icon" className="h-8 w-8">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div>
          <h1 className="text-xl font-semibold">Pacing & Immersion</h1>
          {work && <p className="text-sm text-muted-foreground">{work.title}</p>}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex gap-1 border-b">
        {(["overview", "breath", "findings", "profile"] as const).map((t) => (
          <button
            key={t}
            onClick={() => setTab(t)}
            className={`px-4 py-2 text-sm font-medium border-b-2 transition-colors ${
              tab === t
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground"
            }`}
          >
            {t === "overview" ? "Tension Map" :
             t === "breath"   ? "Breath Map" :
             t === "findings" ? "Findings" : "Profile"}
          </button>
        ))}
      </div>

      {workId && tab === "overview"  && <OverviewPanel workId={workId} />}
      {workId && tab === "breath"    && <BreathPanel workId={workId} />}
      {workId && tab === "findings"  && <FindingsPanel workId={workId} />}
      {workId && tab === "profile"   && <ProfilePanel workId={workId} />}
    </div>
  );
}

// ── Overview / Tension Map ────────────────────────────────────────────────────

function useScenes(workId: string) {
  return useQuery({
    queryKey: ["pacing-scenes", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/scenes`).then((r) => r.json()),
  });
}

function OverviewPanel({ workId }: { workId: string }) {
  const qc = useQueryClient();
  const scenesQ = useScenes(workId);
  const scenes: Scene[] = scenesQ.data?.scenes ?? [];

  const extractMut = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/scenes/extract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then((r) => r.json()),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["pacing-scenes", workId] });
      toast.success(`Extracted ${data.count} scenes`);
    },
    onError: () => toast.error("Scene extraction failed"),
  });

  const analyzeMut = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/analyze-all`, { method: "POST" }).then((r) => r.json()),
    onSuccess: (data) => {
      // Analysis runs in the background; poll the scene list until metrics arrive.
      let polls = 0;
      const poll = () => {
        polls++;
        qc.invalidateQueries({ queryKey: ["pacing-scenes", workId] });
        if (polls < 15) setTimeout(poll, 4000);
      };
      setTimeout(poll, 4000);
      toast.success(`Analyzing ${data.queued ?? "all"} scenes — results will appear as they complete`);
    },
    onError: () => toast.error("Analysis failed"),
  });

  // Build chart data from scenes that have persisted metrics (server-sourced).
  const analyzedCount = scenes.filter((sc) => sc.latest_metrics).length;
  const chartData = scenes
    .filter((sc) => sc.latest_metrics)
    .map((sc, i) => {
      const m = sc.latest_metrics!;
      return {
        name: sc.title || `S${i + 1}`,
        tension: m.tension_after ?? 0,
        intensity: m.emotional_intensity ?? 0,
        revelation: m.revelation_density ?? 0,
        grounding: m.sensory_grounding ?? 0,
      };
    });

  return (
    <div className="space-y-4">
      {/* Actions bar */}
      <div className="flex items-center gap-2 flex-wrap">
        <Button size="sm" onClick={() => extractMut.mutate()} disabled={extractMut.isPending}>
          {extractMut.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
          Extract Scenes
        </Button>
        {scenes.length > 0 && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => analyzeMut.mutate()}
            disabled={analyzeMut.isPending}
          >
            {analyzeMut.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : null}
            Analyze All Scenes
          </Button>
        )}
        <span className="text-xs text-muted-foreground">
          {scenes.length} scene{scenes.length !== 1 ? "s" : ""}
          {analyzedCount > 0 && ` · ${analyzedCount} analyzed`}
        </span>
      </div>

      {/* Tension chart */}
      {chartData.length > 0 && (
        <Card>
          <CardHeader className="py-3">
            <CardTitle className="text-sm font-medium flex items-center gap-2">
              <Activity className="h-4 w-4 text-blue-500" />
              Tension & Intensity Arc
            </CardTitle>
          </CardHeader>
          <CardContent className="py-2">
            <ResponsiveContainer width="100%" height={180}>
              <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: -16 }}>
                <XAxis dataKey="name" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
                <YAxis domain={[0, 1]} tick={{ fontSize: 10 }} tickCount={5} />
                <Tooltip
                  formatter={(v: number, name: string) => [
                    `${Math.round(v * 100)}%`,
                    name === "tension" ? "Tension" :
                    name === "intensity" ? "Emotional intensity" :
                    name === "revelation" ? "Revelation density" : "Grounding",
                  ]}
                />
                <ReferenceLine y={0.7} stroke="hsl(var(--destructive))" strokeDasharray="3 3" strokeOpacity={0.4} />
                <Line type="monotone" dataKey="tension"    stroke="hsl(var(--destructive))" dot={false} strokeWidth={2} />
                <Line type="monotone" dataKey="intensity"  stroke="hsl(var(--chart-2, #f59e0b))" dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
                <Line type="monotone" dataKey="revelation" stroke="hsl(var(--chart-3, #8b5cf6))" dot={false} strokeWidth={1.5} strokeDasharray="2 3" />
                <Line type="monotone" dataKey="grounding"  stroke="hsl(var(--chart-4, #10b981))" dot={false} strokeWidth={1} strokeOpacity={0.7} />
              </LineChart>
            </ResponsiveContainer>
            <div className="flex gap-4 text-xs text-muted-foreground mt-1 flex-wrap">
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-red-500 inline-block" />Tension</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-amber-500 inline-block" />Emotional intensity</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-purple-500 inline-block" />Revelation</span>
              <span className="flex items-center gap-1"><span className="w-3 h-0.5 bg-emerald-500 inline-block" />Grounding</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Scene list */}
      {scenes.length === 0 && !scenesQ.isLoading && (
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground text-sm">
            No scenes yet. Extract them from the chapters above.
          </CardContent>
        </Card>
      )}
      {scenesQ.isLoading && <Skeleton className="h-32 w-full" />}
      <div className="space-y-2">
        {scenes.map((sc) => (
          <SceneCard
            key={sc.id}
            scene={sc}
            metrics={sc.latest_metrics}
          />
        ))}
      </div>
    </div>
  );
}

function SceneCard({ scene, metrics }: { scene: Scene; metrics: SceneMetrics | null }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <Card
      className={`cursor-pointer ${scene.status === "dismissed" ? "opacity-40" : ""}`}
      onClick={() => setExpanded(!expanded)}
    >
      <CardContent className="py-3">
        <div className="flex items-start gap-2">
          <span className="text-xs text-muted-foreground w-5 shrink-0 mt-0.5">{scene.seq + 1}</span>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium">{scene.title || "Untitled"}</span>
              {scene.purpose && (
                <span className="text-[10px] text-muted-foreground">{scene.purpose}</span>
              )}
              <span className="text-[10px] text-muted-foreground ml-auto">
                {scene.word_count}w
              </span>
            </div>
            {metrics && (
              <div className="mt-1.5 grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
                <div>
                  <span className="text-muted-foreground">Tension: </span>
                  {pct(metrics.tension_before)} → {pct(metrics.tension_after)}
                </div>
                <div>
                  <span className="text-muted-foreground">Grounding: </span>
                  {pct(metrics.sensory_grounding)}
                </div>
                <div>
                  <span className="text-muted-foreground">Intensity: </span>
                  {pct(metrics.emotional_intensity)}
                </div>
                <div>
                  <span className="text-muted-foreground">Irreversible turns: </span>
                  {metrics.irreversible_turns}
                </div>
              </div>
            )}
            {!metrics && (
              <p className="text-[10px] text-muted-foreground mt-0.5">Not yet analyzed</p>
            )}
          </div>
          {expanded ? <ChevronDown className="h-3.5 w-3.5 mt-1 shrink-0" /> : <ChevronRight className="h-3.5 w-3.5 mt-1 shrink-0" />}
        </div>

        {expanded && metrics && (
          <div className="mt-3 space-y-2 border-t pt-3 text-xs" onClick={(e) => e.stopPropagation()}>
            <div className="grid grid-cols-2 gap-2">
              <MetricBar label="Tension before" value={metrics.tension_before} />
              <MetricBar label="Tension after" value={metrics.tension_after} />
              <MetricBar label="Emotional intensity" value={metrics.emotional_intensity} />
              <MetricBar label="Revelation density" value={metrics.revelation_density} />
              <MetricBar label="Action ratio" value={metrics.action_ratio} />
              <MetricBar label="Reflection ratio" value={metrics.reflection_ratio} />
              <MetricBar label="Sensory grounding" value={metrics.sensory_grounding} />
            </div>
            <div className="flex gap-3 flex-wrap text-[10px]">
              <BoolChip label="Aftermath" v={!!metrics.has_aftermath} />
              <BoolChip label="Orientation" v={!!metrics.has_orientation} />
              <BoolChip label="Consequence" v={!!metrics.consequence_present} />
              <BoolChip label="Purpose clear" v={!!metrics.purpose_clear} />
            </div>
            {metrics.evidence.length > 0 && (
              <div className="space-y-1">
                <p className="text-[10px] text-muted-foreground font-medium">Evidence</p>
                {metrics.evidence.slice(0, 3).map((ev, i) => (
                  <div key={i} className="rounded bg-muted/50 p-1.5 text-[10px]">
                    <span className="font-medium text-foreground">{ev.field}: </span>
                    <span className="italic">"{ev.quote}"</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function MetricBar({ label, value }: { label: string; value: number | null }) {
  const v = value ?? 0;
  const color = v >= 0.7 ? "bg-red-400" : v >= 0.4 ? "bg-amber-400" : "bg-emerald-400";
  return (
    <div>
      <div className="flex justify-between text-[10px] mb-0.5">
        <span className="text-muted-foreground">{label}</span>
        <span>{pct(value)}</span>
      </div>
      {bar(value, color)}
    </div>
  );
}

function BoolChip({ label, v }: { label: string; v: boolean }) {
  return (
    <span className={`flex items-center gap-0.5 ${v ? "text-emerald-600" : "text-slate-400"}`}>
      {v ? <CheckCircle2 className="h-3 w-3" /> : <span className="h-3 w-3 border rounded-full inline-block" />}
      {label}
    </span>
  );
}

// ── Breath Map ────────────────────────────────────────────────────────────────

function BreathPanel({ workId }: { workId: string }) {
  const scenesQ = useScenes(workId);
  const scenes: Scene[] = (scenesQ.data?.scenes ?? []).filter((s: Scene) => s.status !== "dismissed");

  const runsQ = useQuery({
    queryKey: ["pacing-runs", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/runs`).then((r) => r.json()),
  });
  const runs: PacingRun[] = runsQ.data?.runs ?? [];
  const latestRun = runs[0] ?? null;

  const findingsQ = useQuery({
    queryKey: ["pacing-findings", latestRun?.id],
    queryFn: () =>
      latestRun
        ? apiFetch(`${API}/pacing/runs/${latestRun.id}/findings`).then((r) => r.json())
        : Promise.resolve({ findings: [] }),
    enabled: !!latestRun,
  });
  const allFindings: PacingFinding[] = findingsQ.data?.findings ?? [];
  const breathFindings = allFindings.filter(
    (f) => f.detector === "breath_map" && f.status === "open"
  );

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        The Breath Map identifies where aftermath, orientation, sensory embodiment, or deliberate
        quiet is missing before or after major events.
      </p>

      {scenes.length === 0 && (
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground text-sm">
            No scenes yet — extract scenes from the Tension Map tab first.
          </CardContent>
        </Card>
      )}

      {/* Breath band */}
      {scenes.length > 0 && (
        <div className="space-y-1.5">
          {scenes.map((sc) => {
            const sceneFindings = breathFindings.filter((f) =>
              f.subject === sc.title || f.evidence.some((e: any) => e.scene_id === sc.id)
            );
            const hasMissing = sceneFindings.length > 0;
            return (
              <div key={sc.id} className="flex items-center gap-2 text-xs">
                <span className="w-4 text-muted-foreground text-right">{sc.seq + 1}</span>
                <div
                  className={`flex-1 h-6 rounded px-2 flex items-center text-[10px] font-medium ${
                    hasMissing
                      ? "bg-amber-100 dark:bg-amber-900/30 text-amber-700 border border-amber-300"
                      : "bg-muted/40 text-muted-foreground"
                  }`}
                >
                  {sc.title || "Untitled"}
                  {hasMissing && (
                    <AlertTriangle className="h-3 w-3 ml-auto shrink-0 text-amber-500" />
                  )}
                </div>
                {hasMissing && (
                  <span className="text-amber-600 text-[10px]">
                    {sceneFindings[0].finding_type.replace(/_/g, " ")}
                  </span>
                )}
              </div>
            );
          })}
        </div>
      )}

      {breathFindings.length === 0 && scenes.length > 0 && latestRun && (
        <p className="text-sm text-emerald-600 flex items-center gap-1">
          <CheckCircle2 className="h-4 w-4" />
          No breath-map issues found in the latest diagnostic run.
        </p>
      )}
      {!latestRun && scenes.length > 0 && (
        <p className="text-sm text-muted-foreground">
          Run diagnostics from the Findings tab to populate the breath map.
        </p>
      )}
    </div>
  );
}

// ── Findings ──────────────────────────────────────────────────────────────────

function FindingsPanel({ workId }: { workId: string }) {
  const qc = useQueryClient();

  const runsQ = useQuery({
    queryKey: ["pacing-runs", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/runs`).then((r) => r.json()),
  });
  const runs: PacingRun[] = runsQ.data?.runs ?? [];
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const activeRun = runs.find((r) => r.id === selectedRunId) ?? runs[0] ?? null;

  const runMut = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      }).then((r) => r.json()),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["pacing-runs", workId] });
      setSelectedRunId(data.run?.id ?? null);
      toast.success("Diagnostics complete");
    },
    onError: () => toast.error("Diagnostics failed"),
  });

  const findingsQ = useQuery({
    queryKey: ["pacing-findings", activeRun?.id],
    queryFn: () =>
      activeRun
        ? apiFetch(`${API}/pacing/runs/${activeRun.id}/findings`).then((r) => r.json())
        : Promise.resolve({ findings: [] }),
    enabled: !!activeRun,
  });
  const findings: PacingFinding[] = findingsQ.data?.findings ?? [];
  const open = findings.filter((f) => f.status === "open");

  const resolveMut = useMutation({
    mutationFn: ({ id, status, note }: { id: string; status: string; note: string }) =>
      apiFetch(`${API}/pacing/findings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, resolution_note: note }),
      }).then((r) => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pacing-findings", activeRun?.id] });
      toast.success("Finding updated");
    },
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <Button size="sm" onClick={() => runMut.mutate()} disabled={runMut.isPending}>
          {runMut.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Play className="h-4 w-4 mr-1" />}
          Run Diagnostics
        </Button>
        {runs.length > 1 && (
          <Select value={activeRun?.id ?? ""} onValueChange={setSelectedRunId}>
            <SelectTrigger className="w-52 h-8 text-xs">
              <SelectValue placeholder="Select run" />
            </SelectTrigger>
            <SelectContent>
              {runs.map((r) => (
                <SelectItem key={r.id} value={r.id}>
                  {new Date(r.created_at).toLocaleString()} · {r.profile_name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
        {activeRun && (
          <span className="text-xs text-muted-foreground">
            {activeRun.coverage.analyzed_scenes ?? 0} scenes analyzed ·{" "}
            {open.length} open finding{open.length !== 1 ? "s" : ""}
            {activeRun.coverage.partial && " · ⚠ partial"}
          </span>
        )}
      </div>

      {!activeRun && !runsQ.isLoading && (
        <Card>
          <CardContent className="py-8 text-center text-muted-foreground text-sm">
            No diagnostic runs yet. Run diagnostics to find pacing issues.
          </CardContent>
        </Card>
      )}

      {activeRun?.coverage.note && (
        <Card className="border-amber-300">
          <CardContent className="py-3 text-sm text-amber-700">
            ⚠ {activeRun.coverage.note}
          </CardContent>
        </Card>
      )}

      <div className="space-y-2">
        {findings.map((f) => (
          <FindingCard
            key={f.id}
            finding={f}
            onResolve={(status, note) => resolveMut.mutate({ id: f.id, status, note })}
          />
        ))}
      </div>

      {findings.length > 0 && open.length === 0 && (
        <p className="text-sm text-emerald-600 flex items-center gap-1">
          <CheckCircle2 className="h-4 w-4" />
          All findings resolved.
        </p>
      )}
    </div>
  );
}

function FindingCard({
  finding,
  onResolve,
}: {
  finding: PacingFinding;
  onResolve: (status: string, note: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const [note, setNote] = useState(finding.resolution_note);
  const [status, setStatus] = useState<PacingFinding["status"]>(finding.status);
  const isResolved = finding.status !== "open";

  const dm = DETECTOR_META[finding.detector] ?? { label: finding.detector, icon: Activity, color: "text-slate-500" };
  const Icon = dm.icon;
  const recType = finding.recommendation?.recommendation_type ?? "";

  return (
    <Card
      className={`cursor-pointer transition-opacity ${isResolved ? "opacity-60" : ""}`}
      onClick={() => setExpanded(!expanded)}
    >
      <CardContent className="py-3">
        <div className="flex items-start gap-2">
          <Badge
            variant="outline"
            className={`text-[10px] shrink-0 ${SEVERITY_STYLES[finding.severity] ?? ""}`}
          >
            {finding.severity.toUpperCase()}
          </Badge>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`flex items-center gap-1 text-[10px] ${dm.color}`}>
                <Icon className="h-3 w-3" />{dm.label}
              </span>
              <span className="text-sm font-medium">{finding.subject}</span>
            </div>
            <p className="text-sm text-muted-foreground mt-0.5">{finding.explanation}</p>
            {recType && (
              <p className="text-[10px] text-primary mt-1">
                {RECOMMENDATION_TYPE_LABELS[recType] ?? recType}
              </p>
            )}
          </div>
        </div>

        {expanded && (
          <div className="mt-3 space-y-2 border-t pt-3" onClick={(e) => e.stopPropagation()}>
            {/* Full recommendation */}
            {finding.recommendation?.explanation && (
              <div className="rounded-md bg-muted/50 p-2 text-xs">
                <p className="font-medium mb-1">Recommendation</p>
                <p>{finding.recommendation.explanation}</p>
                {finding.recommendation.placement && (
                  <p className="mt-1 text-muted-foreground">
                    <span className="font-medium">Placement: </span>
                    {finding.recommendation.placement}
                  </p>
                )}
                {finding.recommendation.distinct_question_test && (
                  <p className="mt-1 text-muted-foreground">
                    <span className="font-medium">Test: </span>
                    {finding.recommendation.distinct_question_test}
                  </p>
                )}
              </div>
            )}

            {/* Alternatives */}
            {(finding.recommendation?.alternatives ?? []).length > 0 && (
              <div className="text-xs space-y-1">
                <p className="text-muted-foreground font-medium">Alternatives</p>
                {finding.recommendation.alternatives!.map((a, i) => (
                  <div key={i} className="rounded bg-muted/30 px-2 py-1">
                    <span className="font-medium">{a.option.replace(/_/g, " ")}: </span>
                    {a.tradeoff}
                  </div>
                ))}
              </div>
            )}

            {/* Resolution controls */}
            {!isResolved ? (
              <div className="space-y-2 pt-1">
                <Select value={status} onValueChange={(v) => setStatus(v as PacingFinding["status"])}>
                  <SelectTrigger className="h-8 text-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="open">Open</SelectItem>
                    <SelectItem value="accepted">Accept (fix planned)</SelectItem>
                    <SelectItem value="intentional">Intentional</SelectItem>
                    <SelectItem value="dismissed">Dismiss</SelectItem>
                  </SelectContent>
                </Select>
                <Textarea
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Resolution rationale (required for dismiss/intentional)"
                  rows={2}
                  className="text-xs"
                />
                <Button
                  size="sm"
                  onClick={() => onResolve(status, note)}
                  disabled={status === "open"}
                >
                  Save resolution
                </Button>
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">
                <span className="font-medium capitalize">{finding.status}</span>
                {finding.resolution_note && <span> — {finding.resolution_note}</span>}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Profile ───────────────────────────────────────────────────────────────────

function ProfilePanel({ workId }: { workId: string }) {
  const qc = useQueryClient();

  const profileQ = useQuery({
    queryKey: ["pacing-profile", workId],
    queryFn: () =>
      apiFetch(`${API}/works/${workId}/pacing/profile`).then((r) => r.json()),
  });
  const profile: PacingProfile | undefined = profileQ.data?.profile;

  const [selected, setSelected] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: (name: string) =>
      apiFetch(`${API}/works/${workId}/pacing/profile`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ profile_name: name }),
      }).then((r) => r.json()),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pacing-profile", workId] });
      toast.success("Profile updated — re-run diagnostics to apply");
    },
    onError: () => toast.error("Failed to save profile"),
  });

  const profileName = selected ?? profile?.profile_name ?? "deep_immersive";

  return (
    <div className="space-y-4">
      <p className="text-sm text-muted-foreground">
        Pacing profiles guide thresholds for all seven detectors. They are advisory —
        they never override your judgement. Select the profile that best matches your
        genre and intention.
      </p>

      {profileQ.isLoading && <Skeleton className="h-32 w-full" />}

      {profile && (
        <div className="space-y-3">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
            {Object.entries(profile.available_profiles).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setSelected(key)}
                className={`rounded-lg border px-4 py-3 text-left transition-colors ${
                  profileName === key
                    ? "border-primary bg-primary/5"
                    : "border-border hover:border-primary/50"
                }`}
              >
                <p className="text-sm font-medium">{label as string}</p>
                <p className="text-[10px] text-muted-foreground mt-0.5">{key}</p>
              </button>
            ))}
          </div>

          <Button
            onClick={() => saveMut.mutate(profileName)}
            disabled={saveMut.isPending || profileName === profile.profile_name}
            size="sm"
          >
            {saveMut.isPending ? <Loader2 className="h-4 w-4 animate-spin mr-1" /> : <Settings className="h-4 w-4 mr-1" />}
            Save profile
          </Button>

          {profile.profile_name && (
            <Card>
              <CardContent className="py-3 text-xs text-muted-foreground">
                <span className="font-medium text-foreground">Current: </span>
                {profile.available_profiles[profile.profile_name] ?? profile.profile_name}
              </CardContent>
            </Card>
          )}
        </div>
      )}
    </div>
  );
}
