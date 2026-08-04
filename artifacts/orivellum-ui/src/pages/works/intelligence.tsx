/**
 * Book Intelligence page — /works/:workId/intelligence
 *
 * The MONARCH "single-view" dashboard for a Work: completeness, gap analysis,
 * chapter structure, key knowledge items, and research suggestions — all
 * without navigating through individual files.
 *
 * Every section now carries actionable CTAs:
 *  • Gap cards       → "Find sources" deep-links to the Search tab pre-filled
 *  • Missing chapters → "Go to document" links to Library for that doc
 *  • Low research     → "Import more sources" opens Library import dialog
 *  • Knowledge header → "Rescore evidence" runs the evidence-rescore endpoint
 *  • Pipeline banner  → shows current stage + advance readiness
 */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowLeft, BarChart2, AlertTriangle, Lightbulb, CheckCircle2,
  RefreshCw, ChevronDown, ChevronRight, Layers, Brain,
  BookOpen, FileText, Loader2, Zap, ArrowRight, TrendingUp,
  Search, UploadCloud, RotateCw, ExternalLink, CheckSquare, Plus,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
const LIB  = `${import.meta.env.BASE_URL}library`.replace(/\/+/g, "/").replace(/\/$/, "");
const WORKS_BASE = `${import.meta.env.BASE_URL}works`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ──────────────────────────────────────────────────────────────────────

interface ComplDimension {
  name: string; label: string; score: number;
  current: number | string; target: number | string; unit: string; rule: string;
  evidence?: string[];
}
interface ComplReport {
  overall: number; readiness: string; summary: string; evaluated_at?: string;
  dimensions: ComplDimension[];
}
interface GapItem {
  kind: string; title: string; description: string; severity: string;
  metadata?: Record<string, string>;
}
interface GapReport {
  coverage_pct: number; total_chapters: number;
  gaps: GapItem[]; suggested_queries: string[];
}
interface Chapter {
  id: string; seq: number; level: number; title: string;
  word_count: number; status: string; extraction_method: string; source_doc_id: string;
}
interface ChapterDoc { doc_title: string; doc_id: string; chapters: Chapter[]; }
interface ChaptersResponse { work_id: string; total_chapters: number; documents: ChapterDoc[]; }
interface WorkStats {
  documents_by_kind: Record<string, number>;
  documents_by_readiness: Record<string, number>;
  knowledge_by_kind: Record<string, number>;
  tasks_by_status: Record<string, number>;
  pending_task_count: number;
  conversation_count: number;
  avg_mastery_pct: number;
  concept_count: number;
}
interface PipelineData {
  id: string; status: string; stage_label?: string; next_status?: string | null;
  chapter_count: number;
  stage_artifact?: { status: string; artifact_type?: string } | null;
  open_findings?: Array<{ id: string; severity: string; description: string }>;
}

// ── Colour helpers ─────────────────────────────────────────────────────────────

function scoreColor(score: number) {
  if (score >= 80) return "text-emerald-700";
  if (score >= 50) return "text-amber-700";
  return "text-red-700";
}

const DIM_BAR: Record<string, string> = {
  structural: "bg-violet-500", content: "bg-blue-500",
  research:   "bg-emerald-500", editorial: "bg-amber-500", source: "bg-orange-400",
};
const GAP_ROW: Record<string, string> = {
  high:   "border-red-200   bg-red-50/40",
  medium: "border-amber-200 bg-amber-50/40",
  low:    "border-blue-200  bg-blue-50/40",
};
const GAP_DOT: Record<string, string> = {
  high: "bg-red-500", medium: "bg-amber-400", low: "bg-blue-400",
};

// ── Stages with AI workers ────────────────────────────────────────────────────

const WORKER_STAGES = new Set(["B0","B1","B2","B3","B4","B5"]);

// ── Main page ──────────────────────────────────────────────────────────────────

export default function WorkIntelligence() {
  const { workId } = useParams<{ workId: string }>();
  const [, navigate] = useLocation();
  const queryClient  = useQueryClient();
  const [open, setOpen] = useState<Set<string>>(new Set(["completeness", "gaps"]));
  const [lastRescored, setLastRescored] = useState<string | null>(null);

  const toggle = (s: string) =>
    setOpen((prev) => { const n = new Set(prev); n.has(s) ? n.delete(s) : n.add(s); return n; });

  const { data: work } = useQuery<{ work: { id: string; title: string } }>({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}`).then((r) => r.json()),
    enabled: !!workId, staleTime: 60_000,
  });

  const { data: compl, isLoading: complLoading, refetch: refetchCompl } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/completeness`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: gaps, isLoading: gapsLoading, refetch: refetchGaps } = useQuery<GapReport>({
    queryKey: ["work-gaps", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/gaps`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: knData } = useQuery<{ knowledge: Array<{ id: string; kind: string; text: string; confidence?: number }> }>({
    queryKey: ["work-knowledge-top", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/knowledge`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: statsData } = useQuery<WorkStats>({
    queryKey: ["work-stats", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/stats`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: chaptersData } = useQuery<ChaptersResponse>({
    queryKey: ["work-chapters", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/chapters`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: pipelineResp } = useQuery<{ pipeline: PipelineData | null }>({
    queryKey: ["pipeline", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/pipeline`).then((r) => r.json()),
    enabled: !!workId, staleTime: 30_000,
  });

  // "Track as task" state — tracks gap keys (severity+index) already converted
  const [trackedGaps, setTrackedGaps] = useState<Set<string>>(new Set());

  const createTaskMutation = useMutation({
    mutationFn: async (text: string) => {
      const r = await apiFetch(`${BASE}/works/${workId}/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text }),
      });
      if (!r.ok) throw new Error("Could not create task");
      return r.json();
    },
    onSuccess: (_data, text) => {
      setTrackedGaps((prev) => new Set(prev).add(text));
      queryClient.invalidateQueries({ queryKey: ["work-stats", workId] });
      toast.success("Task created", { description: text });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // Evidence rescore mutation
  const rescoreMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/evidence/rescore`, { method: "POST" });
      if (!r.ok) throw new Error("Rescore failed");
      return r.json();
    },
    onSuccess: (data: any) => {
      const ts = new Date().toLocaleTimeString();
      setLastRescored(ts);
      queryClient.invalidateQueries({ queryKey: ["work-knowledge-top", workId] });
      toast.success(`Evidence rescored — ${data.rescored_count ?? 0} items updated`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const title = (work?.work as any)?.title ?? "Work Intelligence";

  // Derived counts
  const totalDocs = Object.values(statsData?.documents_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const totalKn   = Object.values(statsData?.knowledge_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const readyDocs = statsData?.documents_by_readiness?.["ready"] ?? 0;

  // Gap groups
  const highGaps  = gaps?.gaps.filter(g => g.severity === "high")   ?? [];
  const medGaps   = gaps?.gaps.filter(g => g.severity === "medium")  ?? [];
  const lowGaps   = gaps?.gaps.filter(g => g.severity === "low")     ?? [];
  const totalGaps = gaps?.gaps.length ?? 0;

  // Research dimension
  const researchDim = compl?.dimensions.find(d => d.name === "research");
  const researchLow = researchDim != null && researchDim.score < 40;

  // Pipeline state
  const pipeline = pipelineResp?.pipeline ?? null;
  const pipelineStage = pipeline?.status ?? null;
  const pipelineArtifact = pipeline?.stage_artifact ?? null;
  const pipelineFindings = pipeline?.open_findings ?? [];
  const artifactDone = pipelineArtifact?.status === "done";
  const needsArtifact = pipelineStage && WORKER_STAGES.has(pipelineStage) && !artifactDone;
  const hasBlockers = pipelineFindings.length > 0;
  const readyToAdvance = pipeline && !WORKER_STAGES.has(pipelineStage ?? "") && !hasBlockers;
  // For B0-B5: ready if artifact done and no blockers
  const readyToAdvanceWorker = pipeline && WORKER_STAGES.has(pipelineStage ?? "") && artifactDone && !hasBlockers;

  const allReady = !complLoading && !gapsLoading;

  // Navigation helpers
  const goSearch = (q: string) =>
    navigate(`${WORKS_BASE}/${workId}?tab=search&q=${encodeURIComponent(q)}`);
  const goBrainstorm = (q: string) =>
    navigate(`${WORKS_BASE}/${workId}?tab=brainstorm&q=${encodeURIComponent(q)}`);
  const goBook = () =>
    navigate(`${WORKS_BASE}/${workId}?tab=book`);

  return (
    <div className="space-y-6 animate-in fade-in duration-300 max-w-4xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate(`${WORKS_BASE}/${workId}`)} className="-ml-2">
          <ArrowLeft className="w-4 h-4 mr-1.5" /> {title}
        </Button>
        <Button variant="outline" size="sm"
          onClick={() => { refetchCompl(); refetchGaps(); }}
          disabled={!allReady}>
          <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Refresh all
        </Button>
      </div>

      <div className="border-b border-border/50 pb-3">
        <div className="flex items-center gap-3">
          <Brain className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-serif font-semibold tracking-tight">{title}</h1>
            <p className="text-muted-foreground text-sm font-serif mt-0.5">
              Knowledge Intelligence — what you have, what it means, what's missing, what's next.
            </p>
          </div>
        </div>
      </div>

      {/* ── Completeness + gaps metrics ───────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Overall"
          value={compl ? `${compl.overall}%` : "—"}
          sub={compl?.readiness ?? "loading…"}
          loading={complLoading}
          color={compl ? scoreColor(compl.overall) : "text-muted-foreground"}
        />
        <MetricCard
          label="Coverage"
          value={gaps ? `${gaps.coverage_pct}%` : "—"}
          sub="research coverage"
          loading={gapsLoading}
          color={gaps ? scoreColor(gaps.coverage_pct) : "text-muted-foreground"}
        />
        <MetricCard
          label="Gaps"
          value={totalGaps ? String(totalGaps) : (gaps ? "0" : "—")}
          sub={totalGaps > 0 ? `${highGaps.length} high · ${medGaps.length} med` : "none detected"}
          loading={gapsLoading}
          color={totalGaps > 0 ? "text-red-600" : (gaps ? "text-emerald-700" : "text-muted-foreground")}
        />
        <MetricCard
          label="Chapters"
          value={gaps ? String(gaps.total_chapters) : "—"}
          sub="sections extracted"
          loading={gapsLoading}
        />
      </div>

      {/* ── Work stats strip ─────────────────────────────────────────────────── */}
      {statsData && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Documents"
            value={String(totalDocs)}
            sub={`${readyDocs} ready`}
          />
          {/* Knowledge card with inline rescore action */}
          <Card className="border-border/50">
            <CardContent className="p-4 space-y-1">
              <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">Knowledge</p>
              <p className="text-2xl font-mono font-bold text-foreground">{totalKn}</p>
              <div className="flex items-center justify-between gap-1">
                <p className="text-[11px] font-mono text-muted-foreground">
                  {lastRescored ? `rescored ${lastRescored}` : `${Object.keys(statsData.knowledge_by_kind).length} kind${Object.keys(statsData.knowledge_by_kind).length !== 1 ? "s" : ""}`}
                </p>
                <button
                  className="flex items-center gap-0.5 text-[10px] font-mono text-primary/70 hover:text-primary transition-colors disabled:opacity-40"
                  onClick={() => rescoreMutation.mutate()}
                  disabled={rescoreMutation.isPending}
                  title="Re-score confidence and detect contradictions across all knowledge items"
                >
                  {rescoreMutation.isPending
                    ? <Loader2 className="w-3 h-3 animate-spin" />
                    : <Zap className="w-3 h-3" />}
                  Rescore
                </button>
              </div>
            </CardContent>
          </Card>
          <MetricCard
            label="Tasks"
            value={String(statsData.pending_task_count)}
            sub="pending"
            color={statsData.pending_task_count > 0 ? "text-amber-600" : "text-muted-foreground"}
          />
          <MetricCard
            label="Chats"
            value={String(statsData.conversation_count)}
            sub="conversations"
          />
        </div>
      )}

      {/* ── Pipeline advance banner ───────────────────────────────────────────── */}
      {pipeline ? (
        <PipelineBanner
          pipeline={pipeline}
          needsArtifact={!!needsArtifact}
          readyToAdvance={!!(readyToAdvance || readyToAdvanceWorker)}
          hasBlockers={hasBlockers}
          onGoBook={goBook}
        />
      ) : pipelineResp !== undefined ? (
        /* Pipeline not started */
        <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg border border-dashed border-border/60 bg-muted/10">
          <p className="text-xs text-muted-foreground">No production pipeline started for this Work yet.</p>
          <Button size="sm" variant="outline" className="gap-1.5 h-7 text-xs shrink-0" onClick={goBook}>
            Start pipeline <ArrowRight className="w-3 h-3" />
          </Button>
        </div>
      ) : null}

      {/* ── Low research coverage CTA ─────────────────────────────────────────── */}
      {researchLow && (
        <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg border border-amber-200 bg-amber-50/50">
          <div className="flex items-center gap-2 text-amber-800 text-sm">
            <TrendingUp className="w-4 h-4 shrink-0" />
            <span>Research coverage is low ({researchDim!.score}%). Import more primary sources to strengthen this Work.</span>
          </div>
          <Button size="sm" variant="outline"
            className="gap-1.5 h-7 text-xs shrink-0 border-amber-300 text-amber-800 hover:bg-amber-100"
            onClick={() => navigate(`${LIB}?import=1`)}>
            <UploadCloud className="w-3 h-3" />
            Import sources
          </Button>
        </div>
      )}

      {/* ── Completeness ─────────────────────────────────────────────────────── */}
      <Section id="completeness" label="Completeness" icon={BarChart2}
        open={open.has("completeness")} onToggle={() => toggle("completeness")}
        badge={compl ? `${compl.overall}%` : undefined}>
        {complLoading ? (
          <div className="space-y-3">{[1,2,3,4,5].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : compl ? (
          <div className="space-y-4">
            {compl.summary && (
              <p className="text-sm text-muted-foreground border-l-2 border-primary/30 pl-3 italic leading-relaxed">
                {compl.summary}
              </p>
            )}
            {compl.dimensions.map((d) => (
              <div key={d.name} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <div>
                    <span className="font-medium">{d.label}</span>
                    <span className="ml-2 text-[11px] font-mono text-muted-foreground">
                      {Number(d.current).toLocaleString()} / {Number(d.target).toLocaleString()} {d.unit}
                    </span>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className={`font-mono font-semibold text-sm ${scoreColor(d.score)}`}>{d.score}%</span>
                    {/* Import CTA on the research bar */}
                    {d.name === "research" && d.score < 40 && (
                      <button
                        className="flex items-center gap-1 text-[10px] font-mono text-amber-700 hover:text-amber-900 transition-colors"
                        onClick={() => navigate(`${LIB}?import=1`)}
                      >
                        <UploadCloud className="w-3 h-3" />
                        Import more sources
                      </button>
                    )}
                  </div>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${DIM_BAR[d.name] ?? "bg-primary"}`}
                    style={{ width: `${d.score}%` }}
                  />
                </div>
                <p className="text-[10px] font-mono text-muted-foreground/70">{d.rule}</p>
                {d.evidence && d.evidence.length > 0 && (
                  <ul className="space-y-0.5 mt-0.5">
                    {d.evidence.map((ev, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-[10px] font-mono text-muted-foreground/60">
                        <span className="shrink-0">·</span>
                        <span>{ev}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {compl.evaluated_at && (
              <p className="text-[10px] font-mono text-muted-foreground/40 text-right pt-1">
                evaluated {new Date(compl.evaluated_at).toLocaleString()}
              </p>
            )}
          </div>
        ) : (
          <Empty text="No completeness data yet — extract documents first." />
        )}
      </Section>

      {/* ── Research Gaps ────────────────────────────────────────────────────── */}
      <Section id="gaps" label="Research Gaps" icon={AlertTriangle}
        open={open.has("gaps")} onToggle={() => toggle("gaps")}
        badge={totalGaps ? String(totalGaps) : undefined}
        badgeVariant="destructive">
        {gapsLoading ? (
          <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : totalGaps > 0 ? (
          <div className="space-y-5">
            {[
              { severity: "high",   label: "High priority",   items: highGaps },
              { severity: "medium", label: "Medium priority", items: medGaps  },
              { severity: "low",    label: "Low priority",    items: lowGaps  },
            ].filter(g => g.items.length > 0).map(({ severity, label, items }) => (
              <div key={severity} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${GAP_DOT[severity]}`} />
                  <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-muted-foreground">
                    {label} ({items.length})
                  </span>
                </div>
                {items.map((g, i) => (
                  <div key={i} className={`flex items-start gap-3 p-3 rounded-lg border text-sm ${GAP_ROW[g.severity] ?? ""}`}>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium leading-snug">{g.title}</p>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{g.description}</p>
                      {g.metadata?.chapter_title && (
                        <p className="text-[10px] font-mono text-muted-foreground/60 mt-1">
                          chapter: {g.metadata.chapter_title}
                        </p>
                      )}
                    </div>
                    {/* Actions for high/medium gaps */}
                    {(severity === "high" || severity === "medium") && (
                      <div className="shrink-0 flex flex-col items-end gap-1.5 mt-0.5">
                        <button
                          className="flex items-center gap-1 text-[10px] font-mono text-primary/70 hover:text-primary transition-colors whitespace-nowrap"
                          onClick={() => goSearch(g.title)}
                          title={`Search this Work for: ${g.title}`}
                        >
                          <Search className="w-3 h-3" />
                          Find sources
                        </button>
                        <button
                          className="flex items-center gap-1 text-[10px] font-mono text-amber-600/80 hover:text-amber-700 transition-colors whitespace-nowrap"
                          onClick={() => goBrainstorm(g.title)}
                          title={`Brainstorm approaches for: ${g.title}`}
                        >
                          <Lightbulb className="w-3 h-3" />
                          Brainstorm approaches
                        </button>
                        {trackedGaps.has(g.title) ? (
                          <span className="flex items-center gap-1 text-[10px] font-mono text-emerald-700 whitespace-nowrap">
                            <CheckSquare className="w-3 h-3" />
                            Task created
                          </span>
                        ) : (
                          <button
                            className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors whitespace-nowrap disabled:opacity-40"
                            onClick={() => createTaskMutation.mutate(g.title)}
                            disabled={createTaskMutation.isPending}
                            title={`Track "${g.title}" as a work task`}
                          >
                            {createTaskMutation.isPending
                              ? <Loader2 className="w-3 h-3 animate-spin" />
                              : <Plus className="w-3 h-3" />}
                            Track as task
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ))}
          </div>
        ) : gaps ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 py-4">
            <CheckCircle2 className="w-4 h-4" />
            No research gaps detected — all chapters have sufficient coverage.
          </div>
        ) : (
          <Empty text="No gap analysis yet — extract documents first." />
        )}
      </Section>

      {/* ── Chapter Structure ────────────────────────────────────────────────── */}
      {chaptersData && chaptersData.total_chapters > 0 && (
        <Section id="chapters" label="Chapter Structure" icon={BookOpen}
          open={open.has("chapters")} onToggle={() => toggle("chapters")}
          badge={String(chaptersData.total_chapters)}>
          <div className="space-y-4">
            {chaptersData.documents.map((docGroup) => {
              const missingChapters = docGroup.chapters.filter(ch => ch.status === "missing");
              return (
                <div key={docGroup.doc_id}>
                  <div className="flex items-center gap-2 py-1.5 mb-1.5 border-b border-border/30">
                    <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    <span className="text-xs font-mono font-semibold text-muted-foreground truncate flex-1">{docGroup.doc_title}</span>
                    <Badge variant="outline" className="text-[9px] font-mono shrink-0">
                      {docGroup.chapters.length} ch
                    </Badge>
                    {/* Reprocess CTA when there are missing chapters */}
                    {missingChapters.length > 0 && (
                      <button
                        className="flex items-center gap-1 text-[10px] font-mono text-amber-700 hover:text-amber-900 transition-colors shrink-0"
                        onClick={() => navigate(`${LIB}/${docGroup.doc_id}`)}
                        title={`${missingChapters.length} missing chapter${missingChapters.length !== 1 ? "s" : ""} — view document in Library`}
                      >
                        <RotateCw className="w-3 h-3" />
                        Reprocess ({missingChapters.length} missing)
                      </button>
                    )}
                  </div>
                  <div className="pl-5 space-y-1">
                    {docGroup.chapters.map((ch) => (
                      <div key={ch.id}
                        className={`flex items-center gap-2 text-xs py-0.5 ${ch.status === "missing" ? "text-red-600/80" : "text-muted-foreground"}`}>
                        <span className="font-mono w-5 text-right shrink-0 opacity-50">{ch.seq}.</span>
                        <span className={`truncate flex-1 ${ch.level > 1 ? "pl-" + ((ch.level - 1) * 2) : ""}`}>
                          {ch.title || "(untitled)"}
                        </span>
                        {ch.status === "missing" && (
                          <span className="text-[9px] font-mono uppercase bg-red-100 text-red-600 px-1 rounded shrink-0">missing</span>
                        )}
                        {ch.word_count > 0 && (
                          <span className="font-mono opacity-40 shrink-0">{ch.word_count.toLocaleString()}w</span>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              );
            })}
          </div>
        </Section>
      )}

      {/* ── Suggested Research ───────────────────────────────────────────────── */}
      {gaps && gaps.suggested_queries.length > 0 && (
        <Section id="suggestions" label="Suggested Research" icon={Lightbulb}
          open={open.has("suggestions")} onToggle={() => toggle("suggestions")}>
          <div className="flex flex-wrap gap-2">
            {gaps.suggested_queries.map((q, i) => (
              <button
                key={i}
                className="flex items-center gap-1.5 px-2.5 py-1 rounded-md border border-border/60 bg-muted/20 font-mono text-xs text-muted-foreground hover:bg-primary/5 hover:border-primary/30 hover:text-primary transition-colors"
                onClick={() => goSearch(q)}
                title={`Search this Work for: ${q}`}
              >
                <Search className="w-3 h-3" />
                {q}
              </button>
            ))}
          </div>
          <p className="text-[10px] font-mono text-muted-foreground/50 mt-3">
            Click a query to search this Work's knowledge and documents.
          </p>
        </Section>
      )}

      {/* ── Knowledge Highlights ─────────────────────────────────────────────── */}
      {knData && knData.knowledge.length > 0 && (
        <Section id="knowledge" label="Knowledge Highlights" icon={Layers}
          open={open.has("knowledge")} onToggle={() => toggle("knowledge")}
          badge={String(knData.knowledge.length)}
          headerAction={
            <button
              className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground/60 hover:text-primary transition-colors disabled:opacity-40"
              onClick={(e) => { e.stopPropagation(); rescoreMutation.mutate(); }}
              disabled={rescoreMutation.isPending}
              title="Re-score confidence and detect contradictions"
            >
              {rescoreMutation.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <Zap className="w-3 h-3" />}
              {lastRescored ? `Rescored ${lastRescored}` : "Rescore evidence"}
            </button>
          }>
          <div className="space-y-2">
            {knData.knowledge.slice(0, 10).map((item) => (
              <div key={item.id} className="flex items-start gap-3 p-3 rounded-lg border border-border/40 bg-muted/10">
                <Badge variant="outline"
                  className="text-[10px] uppercase font-mono border-primary/30 text-primary shrink-0 mt-0.5">
                  {item.kind}
                </Badge>
                <p className="text-sm leading-snug flex-1">{item.text}</p>
                {item.confidence != null && (() => {
                  const pct = item.confidence * 100;
                  const tier = pct >= 80
                    ? { label: 'High', cls: 'text-emerald-700 bg-emerald-50 border-emerald-200' }
                    : pct >= 50
                    ? { label: 'Med', cls: 'text-amber-700 bg-amber-50 border-amber-200' }
                    : { label: 'Low', cls: 'text-red-700 bg-red-50 border-red-200' };
                  return (
                    <span
                      className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border shrink-0 mt-0.5 ${tier.cls}`}
                      title={`Confidence: ${pct.toFixed(1)}% (estimated)`}
                    >
                      {pct.toFixed(0)}% {tier.label}
                    </span>
                  );
                })()}
              </div>
            ))}
            {knData.knowledge.length > 10 && (
              <p className="text-xs font-mono text-muted-foreground text-center">
                {knData.knowledge.length - 10} more items — see the Knowledge tab.
              </p>
            )}
          </div>
        </Section>
      )}

      {/* Footer */}
      <div className="pt-2 flex items-center justify-between text-[11px] font-mono text-muted-foreground/50">
        <span>Orivellum Knowledge Intelligence</span>
        <Button variant="link" size="sm"
          className="text-[11px] font-mono text-muted-foreground/50 h-auto p-0"
          onClick={() => navigate(`${WORKS_BASE}/${workId}`)}>
          Full Work detail →
        </Button>
      </div>
    </div>
  );
}

// ── Pipeline banner ────────────────────────────────────────────────────────────

function PipelineBanner({
  pipeline, needsArtifact, readyToAdvance, hasBlockers, onGoBook,
}: {
  pipeline: PipelineData;
  needsArtifact: boolean;
  readyToAdvance: boolean;
  hasBlockers: boolean;
  onGoBook: () => void;
}) {
  const isTerminal = pipeline.status === "B17";

  if (isTerminal) {
    return (
      <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border border-emerald-200 bg-emerald-50/50 text-emerald-700 text-xs">
        <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
        <span className="font-medium">Pipeline complete</span>
        <span className="opacity-70">— this Work has reached B17 (Published).</span>
      </div>
    );
  }

  const stageLabel = pipeline.stage_label ?? pipeline.status;
  const nextLabel  = pipeline.next_status ? pipeline.next_status : null;

  if (readyToAdvance && nextLabel) {
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border border-emerald-200 bg-emerald-50/60">
        <div className="flex items-center gap-2 text-emerald-800 text-xs">
          <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
          <span>
            <span className="font-medium">Ready to advance</span>
            {" — "}current stage <span className="font-mono">{pipeline.status}</span> ({stageLabel}) is complete.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 border-emerald-300 text-emerald-800 hover:bg-emerald-100"
          onClick={onGoBook}>
          Advance to {nextLabel} <ArrowRight className="w-3 h-3" />
        </Button>
      </div>
    );
  }

  if (hasBlockers) {
    const high = (pipeline.open_findings ?? []).filter(f => f.severity === "high" || f.severity === "critical");
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border border-red-200 bg-red-50/40">
        <div className="flex items-center gap-2 text-red-800 text-xs">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>
            <span className="font-mono">{pipeline.status}</span> {stageLabel} has{" "}
            <span className="font-medium">{high.length} open finding{high.length !== 1 ? "s" : ""}</span>
            {" "}blocking advance.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 border-red-300 text-red-800 hover:bg-red-100"
          onClick={onGoBook}>
          Resolve in Book tab <ExternalLink className="w-3 h-3" />
        </Button>
      </div>
    );
  }

  if (needsArtifact) {
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border border-amber-200 bg-amber-50/40">
        <div className="flex items-center gap-2 text-amber-800 text-xs">
          <Zap className="w-3.5 h-3.5 shrink-0" />
          <span>
            Stage <span className="font-mono">{pipeline.status}</span> ({stageLabel}) needs its AI work run before you can advance.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 border-amber-300 text-amber-800 hover:bg-amber-100"
          onClick={onGoBook}>
          Run stage work <ArrowRight className="w-3 h-3" />
        </Button>
      </div>
    );
  }

  // Neutral info
  return (
    <div className="flex items-center justify-between gap-3 px-4 py-2 rounded-lg border border-border/40 bg-muted/10">
      <div className="flex items-center gap-2 text-muted-foreground text-xs">
        <span className="font-mono text-[10px] bg-muted/60 border border-border/50 px-1.5 py-0.5 rounded">{pipeline.status}</span>
        <span>{stageLabel}</span>
      </div>
      <button className="text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
        onClick={onGoBook}>
        Book tab →
      </button>
    </div>
  );
}

// ── Helper components ──────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, loading, color = "text-foreground" }: {
  label: string; value: string; sub: string; loading?: boolean; color?: string;
}) {
  return (
    <Card className="border-border/50">
      <CardContent className="p-4 space-y-1">
        <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
        {loading ? <Skeleton className="h-8 w-20" /> : (
          <p className={`text-2xl font-mono font-bold ${color}`}>{value}</p>
        )}
        <p className="text-[11px] font-mono text-muted-foreground">{sub}</p>
      </CardContent>
    </Card>
  );
}

function Section({
  id, label, icon: Icon, open, onToggle, badge, badgeVariant = "secondary",
  headerAction, children,
}: {
  id: string; label: string; icon: React.ElementType; open: boolean;
  onToggle: () => void; badge?: string; badgeVariant?: "secondary" | "destructive";
  headerAction?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="border border-border/50 rounded-xl overflow-hidden">
      <button onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-muted/10 hover:bg-muted/20 transition-colors">
        <div className="flex items-center gap-2.5">
          <Icon className="w-4 h-4 text-primary/70" />
          <span className="font-mono text-sm font-semibold uppercase tracking-wider">{label}</span>
          {badge && (
            <Badge variant={badgeVariant} className="text-[10px] font-mono">{badge}</Badge>
          )}
        </div>
        <div className="flex items-center gap-3">
          {headerAction && (
            <span onClick={e => e.stopPropagation()}>{headerAction}</span>
          )}
          {open ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
        </div>
      </button>
      {open && <div className="px-5 py-4">{children}</div>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-muted-foreground py-4 text-center">{text}</p>;
}
