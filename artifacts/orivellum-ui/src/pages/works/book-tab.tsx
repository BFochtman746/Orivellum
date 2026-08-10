import React, { useState } from "react";
import { Link } from "wouter";
import { useQuery, useQueryClient, useMutation } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BookOpen,
  Crown,
  FileText,
  Loader2,
  AlertTriangle,
  Compass,
  CheckCircle2,
  CircleDashed,
  CircleAlert,
  ChevronRight,
  ChevronDown,
  Play,
  ShieldAlert,
  Sparkles,
  X,
  Film,
  Download,
  RefreshCw,
  Clock,
  CheckCircle,
  XCircle,
  AlertCircle,
} from "lucide-react";
import { toast } from "sonner";
import { BrainstormB3Panel } from "./brainstorm-tab";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Book stage metadata ──────────────────────────────────────────────────────

// Stages are sourced from the backend BOOK_SM (state_machine.py). B17 is terminal.
const BOOK_STAGES: { state: string; label: string; desc: string }[] = [
  { state: "B0",  label: "Intake",              desc: "Manuscripts and source material collected" },
  { state: "B1",  label: "Outline",             desc: "Chapter structure and scope defined" },
  { state: "B2",  label: "Research",            desc: "Supporting research and sources complete" },
  { state: "B3",  label: "Architecture",        desc: "Work structure and plan approved" },
  { state: "B4",  label: "Chapter Extraction",  desc: "Chapters extracted and segmented from source" },
  { state: "B5",  label: "Chapter Drafting",    desc: "All chapters drafted" },
  { state: "B6",  label: "Continuity Review",   desc: "Narrative and factual continuity verified" },
  { state: "B7",  label: "Fact Check",          desc: "Claims verified against evidence" },
  { state: "B8",  label: "Style Pass",          desc: "Style and voice consistent throughout" },
  { state: "B9",  label: "Editorial Review",    desc: "Editor feedback received and applied" },
  { state: "B10", label: "Beta Read",           desc: "Beta reader feedback collected" },
  { state: "B11", label: "Revision",            desc: "Revisions complete, manuscript stable" },
  { state: "B12", label: "Final Polish",        desc: "Final language and formatting pass done" },
  { state: "B13", label: "Proof",               desc: "Typeset proof checked" },
  { state: "B14", label: "Layout",              desc: "Final layout and design complete" },
  { state: "B15", label: "Index & TOC",         desc: "Table of contents and index complete" },
  { state: "B16", label: "Quality Gate",        desc: "All quality checks passed" },
  { state: "B17", label: "Published",           desc: "Released to readers" },
];

const STAGE_MAP = Object.fromEntries(BOOK_STAGES.map((s, i) => [s.state, { ...s, index: i }]));
const TERMINAL_STATES = new Set(["B17"]);

// ─── Types (endpoint is not in the generated client) ─────────────────────────

interface BookVersion {
  id: string;
  title: string | null;
  kind: string | null;
  readiness: string;
  created_at: string;
  lifecycle: string;
  word_count: number;
  is_canonical: boolean;
}

interface OutlineChapter {
  id: string;
  seq: number;
  level: number;
  title: string | null;
  word_count: number;
  knowledge_count: number;
  chapter_status: "present" | "incomplete" | "missing";
}

interface BookGap {
  kind: string;
  severity: "high" | "medium" | "low";
  title: string;
  description: string;
}

interface BookIntelligence {
  canonical: (BookVersion & { canonical_source: "declared" | "auto" }) | null;
  versions: BookVersion[];
  outline: OutlineChapter[];
  expected_chapters: number;
  completeness: {
    structural_pct: number;
    content_pct: number;
    research_pct: number;
    editorial_pct: number;
  };
  knowledge_total: number;
  knowledge_reviewed: number;
  gaps: BookGap[];
  next_action: string;
}

// ─── Small pieces ─────────────────────────────────────────────────────────────

const GAUGES: { key: keyof BookIntelligence["completeness"]; label: string; hint: string }[] = [
  { key: "structural_pct", label: "Structure", hint: "chapters present vs expected" },
  { key: "content_pct", label: "Content", hint: "words vs full-length draft" },
  { key: "research_pct", label: "Research", hint: "chapters with ≥3 knowledge items" },
  { key: "editorial_pct", label: "Editorial", hint: "knowledge items reviewed" },
];

function gaugeColor(pct: number): React.CSSProperties {
  if (pct >= 75) return { background: "var(--green-2)", opacity: 0.75 };
  if (pct >= 40) return { background: "var(--gilt)", opacity: 0.75 };
  return { background: "var(--rust)", opacity: 0.65 };
}

const STATUS_CHIP: Record<OutlineChapter["chapter_status"], { label: string; style: React.CSSProperties; Icon: typeof CheckCircle2 }> = {
  present:    { label: "Present",    style: { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" }, Icon: CheckCircle2 },
  incomplete: { label: "Incomplete", style: { color: "var(--gilt)",   background: "var(--gilt-soft)",  borderColor: "var(--gilt-line)" }, Icon: CircleDashed },
  missing:    { label: "Missing",    style: { color: "var(--rust)",   background: "var(--rust-soft)",  borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" }, Icon: CircleAlert },
};

const SEV_STYLE: Record<BookGap["severity"], React.CSSProperties> = {
  high:   { color: "var(--rust)", background: "var(--rust-soft)",  borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" },
  medium: { color: "var(--gilt)", background: "var(--gilt-soft)",  borderColor: "var(--gilt-line)" },
  low:    {},
};

/** Human-readable labels for gate metric keys returned by the backend. */
const METRIC_LABELS: Record<string, string> = {
  doc_count:      "Documents",
  structural_pct: "Chapter extraction",
  research_pct:   "Research coverage",
  content_pct:    "Content coverage",
  editorial_pct:  "Editorial review",
  high_gaps:      "High-severity gaps",
  stage_artifact: "Stage AI work",
};

/** Button / heading labels for each stage's AI worker. */
const STAGE_WORKER_LABELS: Record<string, string> = {
  B0: "Generate Project Brief",
  B1: "Generate Chapter Outline",
  B2: "Build Research Agenda",
  B3: "Design Architecture",
  B4: "Run Continuity Check",
  B5: "Run Fact Check",
};

/** Human-readable artifact type labels for the display panel. */
const ARTIFACT_TYPE_LABELS: Record<string, string> = {
  project_brief:     "Project Brief",
  chapter_outline:   "Chapter Outline",
  research_agenda:   "Research Agenda",
  architecture:      "Architecture",
  continuity_report: "Continuity Report",
  fact_check_report: "Fact-Check Report",
};

// ─── Pipeline panel supporting types ─────────────────────────────────────────

interface PipelineArtifact {
  id: string;
  stage: string;
  artifact_type: string;
  content: Record<string, unknown>;
  status: "pending" | "running" | "done" | "failed";
  error?: string | null;
}

interface PipelineFinding {
  id: string;
  kind: string;
  severity: string;
  description: string;
  state: string;
}

interface Pipeline {
  id: string; work_id: string; title: string; status: string;
  chapter_count: number; chapters_extracted: number;
  chapters_drafted: number; chapters_approved: number;
  created_at: string; updated_at: string;
  stage_artifact?: PipelineArtifact | null;
  open_findings?: PipelineFinding[];
}

// ─── Artifact display components ─────────────────────────────────────────────

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function ArtifactSummary({ type, content }: { type: string; content: Record<string, any> }) {
  if (type === "project_brief") {
    const goals = (content.goals as string[] | undefined) ?? [];
    return (
      <div className="space-y-1.5 pt-2">
        {content.title && <div><span className="opacity-60">Title: </span>{String(content.title)}</div>}
        {content.premise && <div className="normal-case font-sans opacity-80">{String(content.premise).slice(0, 200)}</div>}
        {goals.slice(0, 3).map((g, i) => (
          <div key={i}><span className="opacity-60">Goal {i + 1}: </span>{String(g).slice(0, 120)}</div>
        ))}
      </div>
    );
  }
  if (type === "chapter_outline") {
    const chapters = (content.chapters as Array<Record<string, unknown>> | undefined) ?? [];
    return (
      <div className="space-y-1.5 pt-2">
        <div><span className="opacity-60">Chapters: </span>{chapters.length}</div>
        {chapters.slice(0, 4).map((c, i) => (
          <div key={i}>{String(c.seq ?? i + 1)}. {String(c.title ?? "—").slice(0, 80)}</div>
        ))}
        {chapters.length > 4 && <div className="opacity-50">…{chapters.length - 4} more</div>}
      </div>
    );
  }
  if (type === "research_agenda") {
    const qs = (content.open_questions as unknown[] | undefined) ?? [];
    const gaps = (content.knowledge_gaps as unknown[] | undefined) ?? [];
    return (
      <div className="space-y-1.5 pt-2">
        <div>{qs.length} open question{qs.length !== 1 ? "s" : ""}</div>
        <div>{gaps.length} knowledge gap{gaps.length !== 1 ? "s" : ""}</div>
        {content.coverage_assessment && (
          <div className="normal-case font-sans opacity-80">{String(content.coverage_assessment).slice(0, 200)}</div>
        )}
      </div>
    );
  }
  if (type === "architecture") {
    return (
      <div className="space-y-1.5 pt-2">
        {content.arc_type && <div><span className="opacity-60">Arc: </span>{String(content.arc_type)}</div>}
        {content.rationale && <div className="normal-case font-sans opacity-80">{String(content.rationale).slice(0, 200)}</div>}
      </div>
    );
  }
  if (type === "continuity_report") {
    const issues = (content.issues as unknown[] | undefined) ?? [];
    return (
      <div className="space-y-1.5 pt-2">
        <div className={issues.length > 0 ? "text-destructive" : ""} style={issues.length === 0 ? { color: "var(--green-2)" } : undefined}>
          {issues.length === 0 ? "✓ No continuity issues found" : `${issues.length} issue${issues.length !== 1 ? "s" : ""} detected`}
        </div>
        {content.summary && <div className="normal-case font-sans opacity-80">{String(content.summary)}</div>}
      </div>
    );
  }
  if (type === "fact_check_report") {
    const claims = (content.unverified_claims as unknown[] | undefined) ?? [];
    return (
      <div className="space-y-1.5 pt-2">
        {content.overall_confidence && (
          <div><span className="opacity-60">Confidence: </span>{String(content.overall_confidence)}</div>
        )}
        {claims.length > 0 && (
          <div style={{ color: "var(--gilt)" }}>{claims.length} unverified claim{claims.length !== 1 ? "s" : ""}</div>
        )}
        {content.summary && <div className="normal-case font-sans opacity-80">{String(content.summary)}</div>}
      </div>
    );
  }
  return (
    <pre className="whitespace-pre-wrap text-[10px] normal-case">
      {JSON.stringify(content, null, 2).slice(0, 400)}
    </pre>
  );
}

function ArtifactDisplay({ artifact }: { artifact: PipelineArtifact }) {
  const [open, setOpen] = useState(false);

  if (artifact.status === "pending") return null;

  if (artifact.status === "running") {
    return (
      <div className="flex items-center gap-2 text-xs text-muted-foreground py-1">
        <Loader2 className="w-3 h-3 animate-spin" />
        AI is working on this stage…
      </div>
    );
  }

  if (artifact.status === "failed") {
    return (
      <div className="flex items-start gap-1.5 text-xs text-destructive">
        <CircleAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
        <span>Stage work failed: {artifact.error ?? "unknown error"}</span>
      </div>
    );
  }

  // status === "done"
  const typeLabel = ARTIFACT_TYPE_LABELS[artifact.artifact_type] ?? artifact.artifact_type;
  return (
    <div className="rounded-lg border border-border/50 bg-muted/20 overflow-hidden text-xs font-mono">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-left hover:bg-muted/30 transition-colors"
        onClick={() => setOpen(!open)}
      >
        <span className="flex items-center gap-1.5 uppercase tracking-widest text-[10px]" style={{ color: "var(--green-2)" }}>
          <CheckCircle2 className="w-3 h-3" />
          {typeLabel}
        </span>
        {open
          ? <ChevronDown className="w-3 h-3 text-muted-foreground" />
          : <ChevronRight className="w-3 h-3 text-muted-foreground" />}
      </button>
      {open && (
        <div className="px-3 pb-3 text-[11px] border-t border-border/30">
          <ArtifactSummary type={artifact.artifact_type} content={artifact.content} />
        </div>
      )}
    </div>
  );
}

// ─── Findings list (B4/B5 pipeline governance findings) ──────────────────────

function FindingsList({ findings, workId }: { findings: PipelineFinding[]; workId: string }) {
  const queryClient = useQueryClient();
  const [dismissing, setDismissing] = useState<string | null>(null);

  if (findings.length === 0) return null;

  const handleDismiss = async (id: string) => {
    setDismissing(id);
    try {
      const r = await apiFetch(`${BASE}/governance/findings/${id}/resolve`, { method: "PATCH" });
      if (!r.ok) throw new Error("Failed to resolve finding");
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      toast.success("Finding resolved");
    } catch {
      toast.error("Could not resolve finding");
    } finally {
      setDismissing(null);
    }
  };

  const SEV: Record<string, React.CSSProperties> = {
    critical: { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" },
    high:     { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 22%, transparent)" },
    medium:   { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" },
    low:      {},
  };

  return (
    <div className="space-y-1.5">
      <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
        <AlertTriangle className="w-3 h-3" />
        {findings.length} open finding{findings.length !== 1 ? "s" : ""} — resolve to advance
      </div>
      {findings.map((f) => (
        <div
          key={f.id}
          className="flex items-start gap-2 px-2.5 py-2 rounded-lg border text-xs"
          style={SEV[f.severity] ?? SEV.medium}
        >
          <span className="flex-1 leading-snug">{f.description}</span>
          <button
            className="shrink-0 opacity-60 hover:opacity-100 transition-opacity mt-0.5"
            title="Resolve finding"
            onClick={() => handleDismiss(f.id)}
            disabled={dismissing === f.id}
          >
            {dismissing === f.id
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <X className="w-3 h-3" />}
          </button>
        </div>
      ))}
    </div>
  );
}

// ─── Pipeline panel ───────────────────────────────────────────────────────────

/** Download through apiFetch so the Bearer-token fallback works (plain
 *  window.open only carries the session cookie, which the PWA can lose). */
async function downloadViaApi(url: string, fallbackName: string) {
  const r = await apiFetch(url);
  if (!r.ok) {
    let detail = "";
    try { detail = (await r.json())?.detail ?? ""; } catch { /* not json */ }
    throw new Error(detail || `Download failed (${r.status})`);
  }
  const disposition = r.headers.get("content-disposition") ?? "";
  const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
  const blobUrl = URL.createObjectURL(await r.blob());
  const a = document.createElement("a");
  a.href = blobUrl;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(blobUrl);
}

function PackageExportRow({ workId }: { workId: string }) {
  const { data } = useQuery({
    queryKey: ["pipeline-package", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline/package`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{
        ready: boolean;
        chapters_with_text?: number;
        chapters_total?: number;
        reasons?: string[];
      }>;
    },
    staleTime: 15_000,
  });

  if (!data) return null;

  if (!data.ready) {
    return (
      <div className="flex items-start gap-2 text-[11px] text-muted-foreground">
        <Download className="w-3.5 h-3.5 mt-0.5 shrink-0 opacity-50" />
        <span>
          <span className="font-mono uppercase tracking-widest text-[10px] mr-2">Package</span>
          {data.reasons?.[0] ?? "Not ready to package yet."}
        </span>
      </div>
    );
  }

  return (
    <div className="flex items-center justify-between gap-3 flex-wrap">
      <div className="text-[11px] text-muted-foreground">
        <span className="font-mono uppercase tracking-widest text-[10px] mr-2">Package</span>
        {data.chapters_with_text} chapter{data.chapters_with_text === 1 ? "" : "s"} ready to export
        (EPUB + Markdown)
      </div>
      <Button
        size="sm"
        variant="outline"
        className="gap-1.5 h-7 text-xs shrink-0"
        onClick={() =>
          downloadViaApi(`${BASE}/works/${workId}/pipeline/package/download`, "book-package.zip")
            .catch((e) => toast.error(e.message))
        }
      >
        <Download className="w-3 h-3" />
        Download book package
      </Button>
    </div>
  );
}

function PipelinePanel({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [blockerMsg, setBlockerMsg] = useState<string | null>(null);
  const [gateDetail, setGateDetail] = useState<{ gate: string; metric: string; threshold: number; actual: number } | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["pipeline", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ pipeline: Pipeline | null }>;
    },
    staleTime: 15_000,
    // Poll every 3 s while the artifact worker is running so the UI picks up
    // the completed result even if the mutation call returns before the DB write.
    refetchInterval: (query) => {
      const pipeline = (query.state.data as { pipeline: Pipeline | null } | undefined)?.pipeline;
      return pipeline?.stage_artifact?.status === "running" ? 3000 : false;
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!r.ok) throw new Error("Could not initialise pipeline");
      return r.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      toast.success("Pipeline initialised at B0 — Intake");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const advanceMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline/advance`, { method: "POST" });
      const json = await r.json().catch(() => ({}));
      if (r.status === 409) {
        const body = json as { detail?: string; gate?: string; metric?: string; threshold?: number; actual?: number };
        const detail: string = body.detail ?? "Transition blocked by open findings";
        if (body.gate && body.metric !== undefined) {
          setGateDetail({ gate: body.gate, metric: body.metric, threshold: body.threshold ?? 0, actual: body.actual ?? 0 });
        } else {
          setGateDetail(null);
        }
        setBlockerMsg(detail);
        throw new Error(detail);
      }
      if (!r.ok) throw new Error((json as { detail?: string }).detail ?? "Advance failed");
      return json;
    },
    onSuccess: (json: { pipeline?: { status?: string } }) => {
      setBlockerMsg(null);
      setGateDetail(null);
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      queryClient.invalidateQueries({ queryKey: ["book-intelligence", workId] });
      const newStatus = json?.pipeline?.status;
      const next = newStatus ? STAGE_MAP[newStatus] : null;
      if (next) toast.success(`Advanced to ${next.state} — ${next.label}`);
      else toast.success("Pipeline advanced");
    },
    onError: (e: Error) => {
      if (!blockerMsg) toast.error(e.message);
    },
  });

  const runStageMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline/run-stage`, { method: "POST" });
      const json = await r.json().catch(() => ({}));
      if (!r.ok) {
        const detail = (json as { detail?: string }).detail ?? "Stage worker failed";
        throw new Error(detail);
      }
      return json;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      toast.success("Stage work completed");
    },
    onError: (e: Error) => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      toast.error(e.message);
    },
  });

  if (isLoading) return <Skeleton className="h-16 w-full" />;

  const pipeline = data?.pipeline ?? null;

  if (!pipeline) {
    return (
      <Card className="border-dashed border-primary/30">
        <CardContent className="p-4 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Play className="w-4 h-4 text-primary/60 shrink-0" />
            <div>
              <div className="text-xs font-mono uppercase tracking-widest text-muted-foreground">Production Pipeline</div>
              <p className="text-sm text-muted-foreground mt-0.5">No pipeline started yet. Initialise to begin tracking this book through the B0–B17 lifecycle.</p>
            </div>
          </div>
          <Button size="sm" variant="outline" className="shrink-0 gap-1.5"
            onClick={() => createMutation.mutate()} disabled={createMutation.isPending}>
            {createMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
            Start Pipeline
          </Button>
        </CardContent>
      </Card>
    );
  }

  const current = STAGE_MAP[pipeline.status];
  const nextIdx  = (current?.index ?? -1) + 1;
  const next     = BOOK_STAGES[nextIdx] ?? null;
  const isTerminal = TERMINAL_STATES.has(pipeline.status);
  const progressPct = current ? Math.round(((current.index + 1) / BOOK_STAGES.length) * 100) : 0;

  const hasWorker = pipeline.status in STAGE_WORKER_LABELS;
  const artifact = pipeline.stage_artifact ?? null;
  const artifactDone = artifact?.status === "done";
  const artifactRunning = artifact?.status === "running";
  const workerLabel = STAGE_WORKER_LABELS[pipeline.status];
  const openFindings = (pipeline.open_findings ?? []).filter(f => f.state === "open");

  return (
    <Card className="border-primary/20 bg-primary/[0.02]">
      <CardContent className="p-4 space-y-3">
        {/* Header row */}
        <div className="flex items-start justify-between gap-3 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="flex items-center justify-center w-8 h-8 rounded-full bg-primary/10 border border-primary/20 shrink-0">
              <span className="text-[10px] font-mono font-bold text-primary">{pipeline.status}</span>
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-medium text-sm">{current?.label ?? pipeline.status}</span>
                <Badge variant="outline" className="text-[9px] font-mono h-4 px-1">
                  {pipeline.chapter_count} ch
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground leading-snug">{current?.desc}</p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {!isTerminal && next && (
              <Button size="sm" variant="default" className="gap-1.5 h-7 text-xs"
                onClick={() => advanceMutation.mutate()} disabled={advanceMutation.isPending}>
                {advanceMutation.isPending
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : <ChevronRight className="w-3 h-3" />}
                Advance to {next.label}
              </Button>
            )}
            {isTerminal && (
              <Badge
                className="border"
                style={{ color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" }}
              >Complete</Badge>
            )}
          </div>
        </div>

        {/* Progress bar */}
        <div className="h-1 bg-muted rounded-full overflow-hidden">
          <div className="h-full bg-primary/50 rounded-full transition-all duration-700"
               style={{ width: `${progressPct}%` }} />
        </div>
        <div className="flex justify-between text-[10px] font-mono text-muted-foreground">
          <span>B0 Intake</span>
          <span>{progressPct}% through lifecycle</span>
          <span>B17 Published</span>
        </div>

        {/* Brainstorm panel — shown at Architecture (B3) to encourage exploration before advancing */}
        {pipeline.status === "B3" && (
          <div className="pt-1 border-t border-border/30">
            <BrainstormB3Panel workId={workId} />
          </div>
        )}

        {/* AI stage worker section — shown for B0–B5 */}
        {hasWorker && (
          <div className="space-y-2 pt-1 border-t border-border/30">
            {/* Run button — shown when no artifact, failed, or to retry */}
            {(!artifact || artifact.status === "failed") && (
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1.5 h-7 text-xs border-primary/30 text-primary hover:bg-primary/5"
                  onClick={() => runStageMutation.mutate()}
                  disabled={runStageMutation.isPending || artifactRunning}
                >
                  {runStageMutation.isPending
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Sparkles className="w-3 h-3" />}
                  {artifact?.status === "failed" ? `Retry: ${workerLabel}` : workerLabel}
                </Button>
                <span className="text-[10px] text-muted-foreground">
                  Required before advancing
                </span>
              </div>
            )}

            {/* Artifact display */}
            {artifact && artifact.status !== "pending" && (
              <ArtifactDisplay artifact={artifact} />
            )}

            {/* Rerun button when done */}
            {artifactDone && (
              <button
                className="text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors underline underline-offset-2"
                onClick={() => runStageMutation.mutate()}
                disabled={runStageMutation.isPending}
              >
                {runStageMutation.isPending ? "Regenerating…" : "↺ Regenerate"}
              </button>
            )}
          </div>
        )}

        {/* Open findings (B4 / B5 continuity & fact-check issues) */}
        {openFindings.length > 0 && (
          <div className="pt-1 border-t border-border/30">
            <FindingsList findings={openFindings} workId={workId} />
          </div>
        )}

        {/* Package & export */}
        <div className="pt-1 border-t border-border/30">
          <PackageExportRow workId={workId} />
        </div>

        {/* Blocker warning */}
        {blockerMsg && (
          <div className="rounded-lg border border-destructive/30 bg-destructive/10 text-destructive text-xs overflow-hidden">
            <div className="flex items-start gap-2 px-3 py-2">
              <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span className="flex-1">{blockerMsg}</span>
            </div>
            {gateDetail && gateDetail.metric !== "stage_artifact" && (
              <div className="flex items-center gap-3 px-3 py-1.5 border-t border-destructive/20 bg-destructive/5">
                <div className="flex-1 min-w-0">
                  <div className="flex justify-between text-[10px] font-mono mb-0.5 opacity-80">
                    <span>{METRIC_LABELS[gateDetail.metric] ?? gateDetail.metric}</span>
                    <span>
                      {gateDetail.actual}
                      {gateDetail.metric !== "doc_count" && gateDetail.metric !== "high_gaps" ? "%" : ""}
                      {" / need "}
                      {gateDetail.threshold}
                      {gateDetail.metric !== "doc_count" && gateDetail.metric !== "high_gaps" ? "%" : ""}
                    </span>
                  </div>
                  <div className="h-1 rounded-full bg-destructive/20 overflow-hidden">
                    <div
                      className="h-full rounded-full bg-destructive/50 transition-all"
                      style={{ width: `${Math.min(100, gateDetail.threshold > 0 ? (gateDetail.actual / gateDetail.threshold) * 100 : 0)}%` }}
                    />
                  </div>
                </div>
                <Link
                  href={`/works/${workId}`}
                  className="text-[10px] font-mono underline underline-offset-2 opacity-70 hover:opacity-100 shrink-0"
                >
                  Intelligence ↗
                </Link>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Trailer Architect panel ──────────────────────────────────────────────────

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

interface TrailerFinding {
  code: string;
  severity: string;
  msg: string;
}

interface TrailerPkg {
  brief: Record<string, unknown>;
  concept: Record<string, unknown>;
  method: Record<string, unknown>;
  plan: Record<string, unknown>;
  validation: { status: string; critical: number; findings: TrailerFinding[] };
  status: string;
  status_badge: string;
  generated: string;
  docs: Record<string, string>;
  shot_prompts: Record<string, string>;
}

interface TrailerPackage {
  id: string;
  work_id: string;
  status: string;
  phase: string;
  error?: string | null;
  created_at: string;
  updated_at: string;
  package: TrailerPkg | null;
}

const PHASE_LABELS: Record<string, string> = {
  loading: "Loading book content…",
  analyze:  "Analyzing book…",
  concept:  "Generating concepts…",
  method:   "Selecting production method…",
  plan:     "Building shotlist + narration…",
  validate: "Validating package…",
  package:  "Assembling package…",
  done:     "Complete",
  error:    "Failed",
};

function TrailerStatusBadge({ status, phase }: { status: string; phase: string }) {
  if (status === "running") {
    return (
      <span className="flex items-center gap-1 text-[10px] font-mono text-primary">
        <Loader2 className="w-3 h-3 animate-spin" />
        {PHASE_LABELS[phase] ?? phase}
      </span>
    );
  }
  if (status === "ready") {
    return (
      <span className="flex items-center gap-1 text-[10px] font-mono text-emerald-600">
        <CheckCircle className="w-3 h-3" /> READY
      </span>
    );
  }
  if (status === "blocked") {
    return (
      <span className="flex items-center gap-1 text-[10px] font-mono text-amber-600">
        <AlertCircle className="w-3 h-3" /> BLOCKED
      </span>
    );
  }
  return (
    <span className="flex items-center gap-1 text-[10px] font-mono text-destructive">
      <XCircle className="w-3 h-3" /> FAILED
    </span>
  );
}

function TrailerPackageView({ trailer }: { trailer: TrailerPackage }) {
  const [activeDoc, setActiveDoc] = useState<string | null>(null);
  const pkg = trailer.package;

  if (!pkg) {
    return (
      <div className="text-sm text-muted-foreground italic py-4 text-center">
        {trailer.status === "running"
          ? `Still generating — ${PHASE_LABELS[trailer.phase] ?? trailer.phase}…`
          : trailer.status === "failed"
            ? `Generation failed${trailer.error ? `: ${trailer.error}` : " — start a new trailer."}`
            : "No package was produced for this trailer — start a new one."}
      </div>
    );
  }

  // ── Pre-extract all typed data so JSX never sees `unknown` ──────────────
  const statusReady    = pkg.status === "READY";
  const statusBadge    = pkg.status_badge;
  const generated      = pkg.generated;
  const criticalFindings = (pkg.validation.findings as TrailerFinding[])
    .filter(f => f.severity === "critical");
  const docKeys        = Object.keys(pkg.docs ?? {});
  const activeDocText  = activeDoc ? (pkg.docs[activeDoc] ?? "") : "";

  // Brief fields
  const logline  = typeof pkg.brief.logline  === "string" ? pkg.brief.logline  : "";
  const genre    = typeof pkg.brief.genre    === "string" ? pkg.brief.genre    : "";
  const tone     = Array.isArray(pkg.brief.tone) ? (pkg.brief.tone as string[]).slice(0, 3) : [];

  // Concept fields
  const conceptName  = typeof pkg.concept.name  === "string" ? pkg.concept.name  : "";
  const conceptAngle = typeof pkg.concept.angle === "string" ? pkg.concept.angle : "";
  const conceptBeats = Array.isArray(pkg.concept.beats) ? (pkg.concept.beats as string[]) : [];

  // Plan fields
  const planRaw    = pkg.plan as Record<string, unknown>;
  const shotCount  = Array.isArray(planRaw?.shots) ? (planRaw.shots as unknown[]).length : 0;
  const duration   = typeof planRaw?.duration === "number" ? String(planRaw.duration) : "?";

  return (
    <div className="space-y-4 pt-2">
      {/* Status */}
      <div className={`flex items-center gap-2 px-3 py-2 rounded-lg border text-xs font-mono ${
        statusReady
          ? "border-emerald-200 bg-emerald-50/60 text-emerald-800"
          : "border-amber-200 bg-amber-50/60 text-amber-800"
      }`}>
        {statusReady
          ? <CheckCircle className="w-3.5 h-3.5 shrink-0" />
          : <AlertCircle className="w-3.5 h-3.5 shrink-0" />}
        <span className="font-semibold">{statusBadge}</span>
        <span className="opacity-70 ml-auto">Generated {generated}</span>
      </div>

      {/* Blocking findings */}
      {criticalFindings.map((f, i) => (
        <div key={i} className="flex items-start gap-2 px-3 py-2 rounded border border-destructive/30 bg-destructive/5 text-xs text-destructive">
          <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          <span><strong>{f.code}</strong> — {f.msg}</span>
        </div>
      ))}

      {/* Brief summary */}
      <div className="rounded-lg border border-border/50 bg-muted/20 p-3 space-y-1.5 text-xs">
        <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground mb-2">Book Brief</div>
        {logline && <p className="font-serif text-sm leading-snug">"{logline}"</p>}
        <div className="flex flex-wrap gap-2 pt-1">
          {genre && <Badge variant="secondary" className="font-mono text-[9px]">{genre}</Badge>}
          {tone.map((t, i) => (
            <Badge key={i} variant="outline" className="font-mono text-[9px]">{t}</Badge>
          ))}
        </div>
      </div>

      {/* Chosen concept */}
      {conceptName && (
        <div className="rounded-lg border border-primary/20 bg-primary/[0.03] p-3 text-xs space-y-1">
          <div className="text-[10px] font-mono uppercase tracking-widest text-primary/70">Chosen Concept</div>
          <div className="font-semibold font-serif">{conceptName}</div>
          <div className="text-muted-foreground">{conceptAngle}</div>
          <div className="flex flex-wrap gap-1 pt-1">
            {conceptBeats.map((b, i) => (
              <span key={i} className="px-2 py-0.5 rounded-full bg-muted/50 border border-border/50 text-[10px] font-mono">
                {b}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Shot count */}
      <div className="flex items-center gap-2 text-xs text-muted-foreground font-mono">
        <Film className="w-3.5 h-3.5" />
        {shotCount} shot{shotCount !== 1 ? "s" : ""} planned
        <span className="opacity-50">·</span>
        {duration}s runtime
      </div>

      {/* Human-readable doc tabs */}
      {docKeys.length > 0 && (
        <div className="space-y-2">
          <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">Production Documents</div>
          <div className="flex flex-wrap gap-1.5">
            {docKeys.map((key) => (
              <button
                key={key}
                onClick={() => setActiveDoc(activeDoc === key ? null : key)}
                className={`px-2 py-0.5 rounded border text-[10px] font-mono transition-colors ${
                  activeDoc === key
                    ? "bg-primary text-primary-foreground border-primary"
                    : "bg-muted/30 border-border/50 hover:bg-muted/60 text-muted-foreground"
                }`}
              >
                {key.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase())}
              </button>
            ))}
          </div>
          {activeDoc && activeDocText && (
            <div className="rounded-lg border border-border/50 bg-muted/10 p-3 max-h-80 overflow-y-auto">
              <pre className="text-[11px] font-mono whitespace-pre-wrap leading-relaxed">
                {activeDocText}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TrailerItem({ trailer, workId }: { trailer: TrailerListItem; workId: string }) {
  const [expanded, setExpanded] = useState(false);

  // Poll while running
  const { data: fullTrailer } = useQuery<TrailerPackage>({
    queryKey: ["trailer", workId, trailer.id],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/trailers/${trailer.id}`);
      if (!r.ok) throw new Error("Failed to load trailer");
      return r.json();
    },
    enabled: expanded || trailer.status === "running",
    refetchInterval: trailer.status === "running" ? 3000 : false,
    staleTime: trailer.status === "running" ? 0 : 60_000,
  });

  const liveStatus = fullTrailer?.status ?? trailer.status;
  const livePhase  = fullTrailer?.phase  ?? trailer.phase;

  return (
    <div className="rounded-lg border border-border/50 bg-card/50 overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-muted/20 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <Film className="w-4 h-4 text-muted-foreground shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="text-xs font-mono text-muted-foreground/70 truncate">
            {new Date(trailer.created_at).toLocaleString()}
          </div>
        </div>
        <TrailerStatusBadge status={liveStatus} phase={livePhase} />
        {expanded
          ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </button>

      {expanded && (
        <div className="border-t border-border/40 px-3 pb-3">
          {liveStatus === "running" && (
            <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
              <Loader2 className="w-4 h-4 animate-spin text-primary" />
              <span className="font-mono text-xs">{PHASE_LABELS[livePhase] ?? livePhase}</span>
            </div>
          )}
          {liveStatus === "failed" && (
            <div className="py-3 text-xs text-destructive font-mono">
              {fullTrailer?.error ?? "Pipeline failed — check server logs."}
            </div>
          )}
          {(liveStatus === "ready" || liveStatus === "blocked") && fullTrailer && (
            <>
              {fullTrailer.package && (
                <div className="flex justify-end pt-2">
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 h-7 text-xs"
                    onClick={() =>
                      downloadViaApi(
                        `${BASE}/works/${workId}/trailers/${trailer.id}/export`,
                        "trailer-package.zip",
                      ).catch((e) => toast.error(e.message))
                    }
                  >
                    <Download className="w-3 h-3" />
                    Download package
                  </Button>
                </div>
              )}
              <TrailerPackageView trailer={fullTrailer} />
            </>
          )}
        </div>
      )}
    </div>
  );
}

function TrailerPanel({ workId, lifecycle }: { workId: string; lifecycle: string }) {
  const queryClient = useQueryClient();

  const { data, isLoading } = useQuery<{ trailers: TrailerListItem[]; count: number }>({
    queryKey: ["trailers", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/trailers`);
      if (!r.ok) throw new Error("Failed to load trailers");
      return r.json();
    },
    enabled: !!workId,
    staleTime: 30_000,
    // Poll while any trailer is running
    refetchInterval: 5000,
  });

  const generateMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/trailer`, { method: "POST" });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body?.detail ?? "Failed to start trailer generation");
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Trailer Architect pipeline started");
      queryClient.invalidateQueries({ queryKey: ["trailers", workId] });
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  const hasRunning = data?.trailers.some(t => t.status === "running") ?? false;

  return (
    <Card>
      <CardContent className="p-4 space-y-4">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Film className="w-4 h-4 text-muted-foreground" />
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
              Trailer Architect
            </h3>
            {data && data.count > 0 && (
              <span className="px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-muted/60 text-muted-foreground">
                {data.count}
              </span>
            )}
          </div>

          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 h-7 text-xs border-primary/30 text-primary hover:bg-primary/5 disabled:opacity-50"
            onClick={() => generateMutation.mutate()}
            disabled={generateMutation.isPending || hasRunning}
          >
            {generateMutation.isPending || hasRunning
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <Sparkles className="w-3 h-3" />}
            {hasRunning ? "Generating…" : "Generate Trailer"}
          </Button>
        </div>

        {/* Trailer list */}
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : !data || data.count === 0 ? (
          <div className="text-sm text-muted-foreground italic font-serif py-6 text-center border border-dashed border-border/60 rounded-lg">
            No trailers generated yet. Add at least one processed document, then click &ldquo;Generate Trailer&rdquo;.
          </div>
        ) : (
          <div className="space-y-2">
            {data.trailers.map((t) => (
              <TrailerItem key={t.id} trailer={t} workId={workId} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Book tab ─────────────────────────────────────────────────────────────────

export function BookTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [settingCanonical, setSettingCanonical] = useState<string | null>(null);

  // Fetch work lifecycle for the Trailer Architect canon guard
  const { data: workData } = useQuery<{ lifecycle?: string }>({
    queryKey: ["work-lifecycle", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}`);
      if (!r.ok) return {};
      const d = await r.json();
      return { lifecycle: d.work?.lifecycle ?? d.lifecycle ?? "" };
    },
    enabled: !!workId,
    staleTime: 60_000,
  });
  const workLifecycle = workData?.lifecycle ?? "";

  const { data, isLoading, isError } = useQuery({
    queryKey: ["book-intelligence", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/book-intelligence`);
      if (!r.ok) throw new Error("Failed to load book intelligence");
      return r.json() as Promise<BookIntelligence>;
    },
    enabled: !!workId,
    staleTime: 30_000,
  });

  const handleSetCanonical = async (docId: string) => {
    setSettingCanonical(docId);
    try {
      const r = await apiFetch(`${BASE}/library/${docId}/lifecycle`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lifecycle: "canonical" }),
      });
      if (!r.ok) throw new Error("Failed");
      await queryClient.invalidateQueries({ queryKey: ["book-intelligence", workId] });
      toast.success("Canonical manuscript set");
    } catch {
      toast.error("Could not set canonical manuscript");
    } finally {
      setSettingCanonical(null);
    }
  };

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-40 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return (
      <div className="text-center py-16 text-muted-foreground font-mono text-sm">
        Could not load the book intelligence view. Is the server running?
      </div>
    );
  }

  const { canonical, versions, outline, completeness, gaps, next_action } = data;

  return (
    <div className="space-y-8">
      {/* Production pipeline lifecycle tracker */}
      <PipelinePanel workId={workId} />

      {/* Next action */}
      <Card className="border-primary/30 bg-primary/[0.03]">
        <CardContent className="p-4 flex items-start gap-3">
          <Compass className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div>
            <div className="text-[10px] font-mono uppercase tracking-widest text-primary/70 mb-1">
              Next recommended action
            </div>
            <p className="font-serif text-base leading-snug">{next_action}</p>
          </div>
        </CardContent>
      </Card>

      {/* Completeness gauges */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        {GAUGES.map(({ key, label, hint }) => {
          const pct = completeness[key] ?? 0;
          return (
            <Card key={key}>
              <CardContent className="p-4">
                <div className="flex items-baseline justify-between">
                  <span className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">{label}</span>
                  <span className="text-lg font-semibold font-mono">{pct}%</span>
                </div>
                <div className="mt-2 h-1.5 bg-muted rounded-full overflow-hidden">
                  <div className="h-full rounded-full transition-all duration-700" style={{ width: `${pct}%`, ...gaugeColor(pct) }} />
                </div>
                <div className="mt-1.5 text-[10px] text-muted-foreground/70 leading-tight">{hint}</div>
              </CardContent>
            </Card>
          );
        })}
      </div>

      <div className="grid md:grid-cols-5 gap-6">
        {/* Outline */}
        <div className="md:col-span-3 space-y-3">
          <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
            <BookOpen className="w-3.5 h-3.5" /> Outline
            <span className="text-muted-foreground/50 normal-case tracking-normal">
              {outline.length} chapter{outline.length !== 1 ? "s" : ""} · {data.expected_chapters} expected
            </span>
          </h3>
          {outline.length === 0 ? (
            <div className="text-sm text-muted-foreground italic font-serif py-6 text-center border border-dashed border-border/60 rounded-lg">
              No chapter structure detected yet — link a manuscript with headings, or reprocess an existing one.
            </div>
          ) : (
            <div className="space-y-1">
              {outline.map((c) => {
                const chip = STATUS_CHIP[c.chapter_status];
                return (
                  <div
                    key={c.id}
                    className="flex items-center gap-3 py-2 px-3 rounded-lg border border-border/40 bg-card/50"
                    style={{ marginLeft: `${Math.min(c.level - 1, 2) * 16}px` }}
                  >
                    <chip.Icon className="w-4 h-4 shrink-0" style={{ color: chip.style.color as string }} />
                    <span className="font-serif text-sm truncate flex-1" title={c.title ?? undefined}>
                      {c.title || "Untitled section"}
                    </span>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0" title="Word count">
                      {c.word_count.toLocaleString()} w
                    </span>
                    <Link
                      href={`/works/${workId}/intelligence?chapter=${c.id}`}
                      className="text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border hover:opacity-75 transition-opacity"
                      style={c.knowledge_count === 0
                        ? { color: "var(--rust)", background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" }
                        : c.knowledge_count < 3
                        ? { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }
                        : {}}
                      title={`${c.knowledge_count} knowledge item${c.knowledge_count !== 1 ? "s" : ""} — view on Intelligence page`}
                    >
                      {c.knowledge_count} research
                    </Link>
                    <span className="text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border" style={chip.style}>{chip.label}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* Right column: versions + gaps */}
        <div className="md:col-span-2 space-y-6">
          {/* Versions */}
          <div className="space-y-3">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <FileText className="w-3.5 h-3.5" /> Manuscript versions
            </h3>
            {versions.length === 0 ? (
              <div className="text-sm text-muted-foreground italic font-serif py-4 text-center border border-dashed border-border/60 rounded-lg">
                No documents linked to this Work yet.
              </div>
            ) : (
              <div className="space-y-1.5">
                {versions.map((v) => (
                  <div
                    key={v.id}
                    className={`group flex items-center gap-2 py-2 px-3 rounded-lg border ${v.is_canonical ? "border-primary/40 bg-primary/[0.04]" : "border-border/40 bg-card/50"}`}
                  >
                    {v.is_canonical && <Crown className="w-3.5 h-3.5 text-primary shrink-0" />}
                    <Link href={`/library/${v.id}`} className="font-serif text-sm truncate flex-1 hover:underline" title={v.title ?? undefined}>
                      {v.title || "Untitled"}
                    </Link>
                    <Badge variant="secondary" className="font-mono text-[9px] uppercase shrink-0">{v.kind ?? "?"}</Badge>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0">{v.word_count.toLocaleString()} w</span>
                    {v.is_canonical ? (
                      <span className="text-[9px] font-mono uppercase text-primary shrink-0">
                        Canonical{canonical?.canonical_source === "auto" ? " (auto)" : ""}
                      </span>
                    ) : (
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-6 px-2 text-[10px] font-mono opacity-0 group-hover:opacity-100 transition-opacity shrink-0"
                        disabled={settingCanonical === v.id}
                        onClick={() => handleSetCanonical(v.id)}
                      >
                        {settingCanonical === v.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "Make canonical"}
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            )}
          </div>

          {/* Gaps */}
          <div className="space-y-3">
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
              <AlertTriangle className="w-3.5 h-3.5" /> Gaps
              {gaps.length > 0 && (
                <span className="px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-amber-100 text-amber-700 leading-none">{gaps.length}</span>
              )}
            </h3>
            {gaps.length === 0 ? (
              <div className="text-sm text-emerald-700 font-serif py-4 text-center border border-emerald-200 bg-emerald-50/50 rounded-lg">
                No gaps detected — this book looks well covered.
              </div>
            ) : (
              <div className="space-y-1.5">
                {gaps.map((g, i) => (
                  <div key={i} className="py-2 px-3 rounded-lg border text-sm" style={SEV_STYLE[g.severity]}>
                    <div className="font-medium font-serif leading-snug">{g.title}</div>
                    <div className="text-xs opacity-80 mt-0.5 leading-snug">{g.description}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Trailer Architect */}
      <TrailerPanel workId={workId} lifecycle={workLifecycle} />
    </div>
  );
}
