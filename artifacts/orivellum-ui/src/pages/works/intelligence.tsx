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
import { useState, useRef, useEffect } from "react";
import { useParams, useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Page, EmptyState, ErrorState, LoadingState } from "@/components/primitives";
import {
  ArrowLeft, BarChart2, AlertTriangle, Lightbulb, CheckCircle2,
  RefreshCw, ChevronDown, ChevronRight, Layers, Brain,
  BookOpen, FileText, Loader2, Zap, ArrowRight, TrendingUp,
  Search, UploadCloud, RotateCw, ExternalLink, CheckSquare, Plus,
  ScanSearch, Check, EyeOff, Undo2,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
const LIB  = `${import.meta.env.BASE_URL}library`.replace(/\/+/g, "/").replace(/\/$/, "");
const WORKS_BASE = `${import.meta.env.BASE_URL}works`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ──────────────────────────────────────────────────────────────────────

// Honest completeness — predicates (true/false facts), observed counts, raw
// progress numbers (targets only when author-set). No overall score, no
// assumed denominators.
interface ComplPredicate {
  name: string; label: string; value: boolean; detail: string;
}
interface ComplCount {
  name: string; label: string; detail: string;
  value?: number; current?: number; total?: number;
}
interface ComplProgress {
  words: number; word_target: number | null;
  chapters: number; chapter_target: number | null;
  documents: number; note: string | null;
}
interface ComplReport {
  work_id: string; work_title: string; evaluated_at?: string;
  predicates: ComplPredicate[]; counts: ComplCount[]; progress: ComplProgress;
  coverage?: { overall?: CoverageOverall } | null;
}
interface GapItem {
  kind: string; title: string; description: string; severity: string;
  metadata?: Record<string, string>;
}
// Chao1 + Good–Turing coverage — an UPPER bound ("at most") with unseen
// counts. Replaces the removed self-referential coverage_pct.
interface CoverageOverall {
  completeness: number | null;
  unseen_est: number | null;
  band: string;
  summary: string;
}
interface CoverageReport { overall: CoverageOverall }
interface GapReport {
  coverage: CoverageReport | null; total_chapters: number | null;
  gaps: GapItem[]; suggested_queries: string[];
}
interface Chapter {
  id: string; seq: number; level: number; title: string;
  word_count: number; status: string; extraction_method: string; source_doc_id: string;
  knowledge_count: number;
}
interface ChapterKnowledgeItem {
  id: string; kind: string; text: string;
  confidence?: number; review_status?: string;
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

function scoreColor(score: number): string {
  if (score >= 80) return "var(--gd-success)";
  if (score >= 50) return "var(--gd-caution)";
  return "var(--gd-danger)";
}

const GAP_ROW: Record<string, React.CSSProperties> = {
  high:   { borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", background: "var(--gd-danger-soft)" },
  medium: { borderColor: "var(--gd-caution)", background: "var(--gd-caution-soft)" },
  low:    { borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)" },
};
const GAP_DOT: Record<string, string> = {
  high: "var(--gd-danger)", medium: "var(--gd-caution)", low: "var(--gd-success)",
};

// ── Stages with AI workers ────────────────────────────────────────────────────

// Stages with an AI worker: B0–B3 (planning) + B6/B7 (continuity + fact check).
// B4 (Chapter Extraction) and B5 (Chapter Drafting) have no LLM worker.
// Must match _STAGE_CFG in src/orivellum/capabilities/pipeline_workers.py.
const WORKER_STAGES = new Set(["B0", "B1", "B2", "B3", "B6", "B7"]);

// ── Main page ──────────────────────────────────────────────────────────────────

export default function WorkIntelligence() {
  const { workId } = useParams<{ workId: string }>();
  const [, navigate] = useLocation();
  const queryClient  = useQueryClient();

  // Read ?chapter=<id> from the URL (set by the Book tab knowledge badge link).
  // Parsed once on mount — we intentionally do NOT react to URL changes here
  // so the expanded/scroll state is stable after the user interacts with the page.
  const targetChapterId = new URLSearchParams(window.location.search).get("chapter") ?? undefined;
  // Auto-open the "chapters" accordion when arriving from a badge link so the
  // user doesn't have to manually expand it before the scroll lands.
  const [open, setOpen] = useState<Set<string>>(
    () => new Set(targetChapterId ? ["completeness", "gaps", "chapters"] : ["completeness", "gaps"]),
  );
  const [lastRescored, setLastRescored] = useState<string | null>(null);

  const toggle = (s: string) =>
    setOpen((prev) => { const n = new Set(prev); n.has(s) ? n.delete(s) : n.add(s); return n; });

  const { data: work } = useQuery<{ work: { id: string; title: string } }>({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}`).then((r) => r.json()),
    enabled: !!workId, staleTime: 60_000,
  });

  const { data: compl, isLoading: complLoading, isError: complError, refetch: refetchCompl } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/completeness`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: gaps, isLoading: gapsLoading, isError: gapsError, refetch: refetchGaps } = useQuery<GapReport>({
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

  // Readiness predicates
  const predicatesTotal = compl?.predicates?.length ?? 0;
  const predicatesMet = compl?.predicates?.filter(p => p.value).length ?? 0;
  const reviewedCount = compl?.counts?.find(c => c.name === "knowledge_reviewed");
  // Nudge toward importing sources while knowledge is thin.
  const researchLow = compl != null && (reviewedCount?.total ?? 0) < 5;

  // Pipeline state
  const pipeline = pipelineResp?.pipeline ?? null;
  const pipelineStage = pipeline?.status ?? null;
  const pipelineArtifact = pipeline?.stage_artifact ?? null;
  const pipelineFindings = pipeline?.open_findings ?? [];
  const artifactDone = pipelineArtifact?.status === "done";
  const needsArtifact = pipelineStage && WORKER_STAGES.has(pipelineStage) && !artifactDone;
  const hasBlockers = pipelineFindings.length > 0;
  const readyToAdvance = pipeline && !WORKER_STAGES.has(pipelineStage ?? "") && !hasBlockers;
  // For worker stages: ready if artifact done and no blockers
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
    <Page
      wide
      eyebrow="Knowledge Intelligence"
      title={title}
      actions={
        <>
          <Button variant="ghost" size="sm" className="min-h-11" onClick={() => navigate(`${WORKS_BASE}/${workId}`)}>
            <ArrowLeft className="w-4 h-4 mr-1.5" /> Work
          </Button>
          <Button variant="outline" size="sm" className="min-h-11"
            onClick={() => { refetchCompl(); refetchGaps(); }}
            disabled={!allReady}>
            <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Refresh all
          </Button>
        </>
      }
    >
      <div className="space-y-6 animate-in fade-in duration-300">
      <p className="flex items-center gap-2 text-muted-foreground text-sm">
        <Brain className="w-4 h-4 text-primary shrink-0" />
        What you have, what it means, what's missing, what's next.
      </p>

      {/* ── Completeness + gaps metrics ───────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Readiness"
          value={compl ? `${predicatesMet}/${predicatesTotal}` : "—"}
          sub={compl ? "checks passed" : "loading…"}
          loading={complLoading}
          color={
            compl
              ? predicatesMet === predicatesTotal
                ? "var(--gd-success)"
                : predicatesMet > 0
                  ? "var(--gd-caution)"
                  : "var(--gd-danger)"
              : "text-muted-foreground"
          }
        />
        <MetricCard
          label="Coverage"
          value={
            gaps?.coverage?.overall?.completeness != null
              ? `≤${Math.round(gaps.coverage.overall.completeness * 100)}%`
              : "—"
          }
          sub={
            gaps?.coverage?.overall?.unseen_est != null
              ? `~${Math.round(gaps.coverage.overall.unseen_est)} entities unseen (upper bound)`
              : "entity coverage, upper bound"
          }
          loading={gapsLoading}
          color={
            gaps?.coverage?.overall?.completeness != null
              ? scoreColor(gaps.coverage.overall.completeness * 100)
              : "text-muted-foreground"
          }
        />
        <MetricCard
          label="Hygiene"
          value={totalGaps ? String(totalGaps) : (gaps ? "0" : "—")}
          sub={totalGaps > 0 ? `${highGaps.length} high · ${medGaps.length} med` : "none detected"}
          loading={gapsLoading}
          color={totalGaps > 0 ? "var(--gd-danger)" : (gaps ? "var(--gd-success)" : "text-muted-foreground")}
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
            color={statsData.pending_task_count > 0 ? "var(--gd-caution)" : "text-muted-foreground"}
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
        <div className="flex items-center justify-between gap-3 px-4 py-3 rounded-lg border" style={{ borderColor: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}>
          <div className="flex items-center gap-2 text-sm" style={{ color: "var(--gd-caution)" }}>
            <TrendingUp className="w-4 h-4 shrink-0" />
            <span>
              Only {reviewedCount?.total ?? 0} knowledge item{(reviewedCount?.total ?? 0) === 1 ? "" : "s"} extracted so far.
              Import more primary sources to strengthen this Work.
            </span>
          </div>
          <Button size="sm" variant="outline"
            className="gap-1.5 h-7 text-xs shrink-0 hover:opacity-80"
            style={{ borderColor: "var(--gd-caution)", color: "var(--gd-caution)" }}
            onClick={() => navigate(`${LIB}?import=1`)}>
            <UploadCloud className="w-3 h-3" />
            Import sources
          </Button>
        </div>
      )}

      {/* ── Completeness ─────────────────────────────────────────────────────── */}
      <Section id="completeness" label="Completeness" icon={BarChart2}
        open={open.has("completeness")} onToggle={() => toggle("completeness")}
        badge={compl ? `${predicatesMet}/${predicatesTotal}` : undefined}>
        {complLoading ? (
          <LoadingState rows={5} label="Loading completeness" />
        ) : complError ? (
          <ErrorState
            title="Couldn't load completeness"
            detail="The completeness report failed to load."
            onRetry={() => refetchCompl()}
          />
        ) : compl ? (
          <div className="space-y-4">
            {/* Predicates — true/false facts, never percentages */}
            <div className="space-y-2">
              {compl.predicates.map((p) => (
                <div key={p.name} className="flex items-start gap-2.5 text-sm">
                  {p.value ? (
                    <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--gd-success)" }} />
                  ) : (
                    <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--gd-danger)" }} />
                  )}
                  <div className="min-w-0">
                    <span className="font-medium">{p.label}</span>
                    <span className="ml-2 text-[11px] font-mono" style={{ color: p.value ? "var(--gd-success)" : "var(--gd-danger)" }}>
                      {p.value ? "yes" : "no"}
                    </span>
                    <p className="text-[10px] font-mono text-muted-foreground/70">{p.detail}</p>
                  </div>
                </div>
              ))}
            </div>
            {/* Observed counts */}
            <div className="space-y-1.5 pt-1 border-t border-border/40">
              {compl.counts.map((c) => (
                <div key={c.name} className="flex items-center justify-between text-sm gap-3">
                  <span className="font-medium">{c.label}</span>
                  <span className="font-mono text-[12px] text-muted-foreground">
                    {c.total != null ? `${c.current ?? 0} of ${c.total}` : String(c.value ?? 0)}
                  </span>
                </div>
              ))}
              {/* Import CTA while knowledge is thin */}
              {researchLow && (
                <button
                  className="flex items-center gap-1 text-[10px] font-mono transition-opacity hover:opacity-80"
                  style={{ color: "var(--gd-caution)" }}
                  onClick={() => navigate(`${LIB}?import=1`)}
                >
                  <UploadCloud className="w-3 h-3" />
                  Import more sources
                </button>
              )}
            </div>
            {/* Raw progress — targets only when the author set them */}
            <div className="space-y-1 pt-1 border-t border-border/40 text-[12px] font-mono text-muted-foreground">
              <p>
                {compl.progress.words.toLocaleString()} words
                {compl.progress.word_target != null && ` of ${compl.progress.word_target.toLocaleString()} target`}
                {" · "}
                {compl.progress.chapters} chapter{compl.progress.chapters === 1 ? "" : "s"}
                {compl.progress.chapter_target != null && ` of ${compl.progress.chapter_target} target`}
                {" · "}
                {compl.progress.documents} document{compl.progress.documents === 1 ? "" : "s"}
              </p>
              {compl.progress.note && (
                <p className="text-[10px] text-muted-foreground/60">{compl.progress.note}</p>
              )}
            </div>
            {compl.evaluated_at && (
              <p className="text-[10px] font-mono text-muted-foreground/40 text-right pt-1">
                evaluated {new Date(compl.evaluated_at).toLocaleString()}
              </p>
            )}
          </div>
        ) : (
          <EmptyState
            icon={<BarChart2 />}
            title="No completeness data yet"
            description="Extract documents first to evaluate readiness."
          />
        )}
      </Section>

      {/* ── Research Gaps ────────────────────────────────────────────────────── */}
      <Section id="gaps" label="Research Gaps" icon={AlertTriangle}
        open={open.has("gaps")} onToggle={() => toggle("gaps")}
        badge={totalGaps ? String(totalGaps) : undefined}
        badgeVariant="destructive">
        {gapsLoading ? (
          <LoadingState rows={3} label="Loading research gaps" />
        ) : gapsError ? (
          <ErrorState
            title="Couldn't load gap analysis"
            detail="The gap report failed to load."
            onRetry={() => refetchGaps()}
          />
        ) : totalGaps > 0 ? (
          <div className="space-y-5">
            {[
              { severity: "high",   label: "High priority",   items: highGaps },
              { severity: "medium", label: "Medium priority", items: medGaps  },
              { severity: "low",    label: "Low priority",    items: lowGaps  },
            ].filter(g => g.items.length > 0).map(({ severity, label, items }) => (
              <div key={severity} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full shrink-0" style={{ background: GAP_DOT[severity] }} />
                  <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-muted-foreground">
                    {label} ({items.length})
                  </span>
                </div>
                {items.map((g, i) => (
                  <div key={i} className="flex items-start gap-3 p-3 rounded-lg border text-sm" style={GAP_ROW[g.severity]}>
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
                          className="flex items-center gap-1 text-[10px] font-mono transition-opacity hover:opacity-80 whitespace-nowrap"
                          style={{ color: "var(--gd-caution)" }}
                          onClick={() => goBrainstorm(g.title)}
                          title={`Brainstorm approaches for: ${g.title}`}
                        >
                          <Lightbulb className="w-3 h-3" />
                          Brainstorm approaches
                        </button>
                        {trackedGaps.has(g.title) ? (
                          <span className="flex items-center gap-1 text-[10px] font-mono whitespace-nowrap" style={{ color: "var(--gd-success)" }}>
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
          <div className="flex items-center gap-2 text-sm py-4" style={{ color: "var(--gd-success)" }}>
            <CheckCircle2 className="w-4 h-4" />
            No research gaps detected — all chapters have sufficient coverage.
          </div>
        ) : (
          <EmptyState
            icon={<AlertTriangle />}
            title="No gap analysis yet"
            description="Extract documents first to analyze research gaps."
          />
        )}
      </Section>

      {/* ── Continuity Findings (ConStory) ───────────────────────────────────── */}
      <FindingsSection
        workId={workId!}
        open={open.has("findings")}
        onToggle={() => toggle("findings")}
      />

      {/* ── Chapter Structure ────────────────────────────────────────────────── */}
      {chaptersData && chaptersData.total_chapters > 0 && (
        <Section id="chapters" label="Chapter Structure" icon={BookOpen}
          open={open.has("chapters")} onToggle={() => toggle("chapters")}
          badge={String(chaptersData.total_chapters)}>
          <ChapterList
            workId={workId!}
            chaptersData={chaptersData}
            baseApiUrl={BASE}
            onNavigate={navigate}
            libBase={LIB}
            targetChapterId={targetChapterId}
          />
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
                  const tier: { label: string; style: React.CSSProperties } = pct >= 80
                    ? { label: 'High', style: { color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" } }
                    : pct >= 50
                    ? { label: 'Med', style: { color: "var(--gd-caution)", background: "var(--gd-caution-soft)", borderColor: "var(--gd-caution)" } }
                    : { label: 'Low', style: { color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" } };
                  return (
                    <span
                      className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border shrink-0 mt-0.5"
                      style={tier.style}
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
    </Page>
  );
}

// ── Chapter list with expandable knowledge panels ──────────────────────────────

// Six distinct entity-kind badge styles mapped onto the VELLUM palette. Each
// pairs a token colour with a matching soft background + border so every kind
// stays visually distinct even though VELLUM has fewer hues than the original.
// Six distinct entity-kind badge styles mapped onto the VELLUM palette. Each
// pairs a token colour with a matching soft background + border so every kind
// stays visually distinct even though VELLUM has fewer hues than the original.
const KIND_COLOR: Record<string, React.CSSProperties> = {
  character:     { color: "var(--gd-bronze)", borderColor: "color-mix(in srgb, var(--gd-bronze) 28%, transparent)", background: "var(--gd-bronze-soft)" },
  event:         { color: "var(--gd-caution)", borderColor: "color-mix(in srgb, var(--gd-caution) 28%, transparent)", background: "var(--gd-caution-soft)" },
  setting:       { color: "var(--gd-sonar)", borderColor: "color-mix(in srgb, var(--gd-sonar) 28%, transparent)", background: "var(--gd-sonar-soft)" },
  relationship:  { color: "var(--gd-olive)", borderColor: "color-mix(in srgb, var(--gd-olive) 28%, transparent)", background: "var(--gd-olive-soft)" },
  theme:         { color: "var(--gd-danger)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", background: "var(--gd-danger-soft)" },
  foreshadowing: { color: "var(--gd-violet)", borderColor: "color-mix(in srgb, var(--gd-violet) 28%, transparent)", background: "color-mix(in srgb, var(--gd-violet) 12%, transparent)" },
};

function ChapterKnowledgePanel({ workId, chapterId, baseApiUrl }: {
  workId: string; chapterId: string; baseApiUrl: string;
}) {
  const { data, isLoading, isError } = useQuery<{
    knowledge: ChapterKnowledgeItem[]; count: number; chapter_title: string;
  }>({
    queryKey: ["chapter-knowledge", workId, chapterId],
    queryFn: () =>
      apiFetch(`${baseApiUrl}/works/${workId}/chapters/${chapterId}/knowledge`)
        .then((r) => r.json()),
    staleTime: 60_000,
  });

  if (isLoading) {
    return (
      <div className="pl-7 pt-1.5 pb-2 space-y-1">
        {[1, 2, 3].map(i => <Skeleton key={i} className="h-5 w-full" />)}
      </div>
    );
  }
  if (isError || !data) {
    return (
      <p className="pl-7 pt-1 text-[11px] font-mono" style={{ color: "var(--gd-danger)" }}>
        Could not load chapter knowledge.
      </p>
    );
  }
  if (data.count === 0) {
    return (
      <p className="pl-7 pt-1 pb-2 text-[11px] font-mono text-muted-foreground/60 italic">
        No knowledge items extracted for this chapter yet.
      </p>
    );
  }

  // Group by kind
  const grouped: Record<string, ChapterKnowledgeItem[]> = {};
  for (const item of data.knowledge) {
    (grouped[item.kind] ??= []).push(item);
  }

  return (
    <div className="pl-7 pt-1 pb-2 space-y-2">
      {Object.entries(grouped).map(([kind, items]) => (
        <div key={kind}>
          <p className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground/50 mb-0.5">
            {kind} ({items.length})
          </p>
          <div className="space-y-0.5">
            {items.map((item) => (
              <div key={item.id}
                className="flex items-start gap-2 text-[11px] leading-snug text-muted-foreground">
                <span
                  className={`shrink-0 mt-0.5 text-[8px] font-mono font-semibold uppercase px-1 py-px rounded border ${KIND_COLOR[item.kind] ? "" : "text-muted-foreground border-border"}`}
                  style={KIND_COLOR[item.kind]}
                >
                  {kind.slice(0, 4)}
                </span>
                <span className="flex-1 min-w-0">{item.text}</span>
                {item.confidence != null && (
                  <span className="shrink-0 text-[9px] font-mono opacity-40">
                    {(item.confidence * 100).toFixed(0)}%
                  </span>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function ChapterList({
  workId, chaptersData, baseApiUrl, onNavigate, libBase, targetChapterId,
}: {
  workId: string;
  chaptersData: ChaptersResponse;
  baseApiUrl: string;
  onNavigate: (path: string) => void;
  libBase: string;
  targetChapterId?: string;
}) {
  // Pre-expand the target chapter so its knowledge panel is visible on arrival.
  const [expandedChapters, setExpandedChapters] = useState<Set<string>>(
    () => (targetChapterId ? new Set([targetChapterId]) : new Set()),
  );

  // Ref map from chapter id → its wrapper div so we can scroll it into view.
  const chapterRefs = useRef<Record<string, HTMLDivElement | null>>({});

  // Tracks which row is currently playing the highlight animation.
  // Set to targetChapterId immediately after scrolling, cleared after 1.5 s.
  const [highlightedId, setHighlightedId] = useState<string | null>(null);

  // Scroll the target chapter into view and flash-highlight it once on mount.
  useEffect(() => {
    if (!targetChapterId) return;
    const el = chapterRefs.current[targetChapterId];
    if (!el) return;
    // A short delay lets the browser finish laying out the expanded panel.
    const timer = setTimeout(() => {
      el.scrollIntoView({ behavior: "smooth", block: "start" });
      // Start the highlight animation right as the scroll fires.
      setHighlightedId(targetChapterId);
      // Clear after the animation duration (1 500 ms) + a small buffer.
      setTimeout(() => setHighlightedId(null), 1700);
    }, 120);
    return () => clearTimeout(timer);
  }, [targetChapterId]);

  const toggleChapter = (id: string) =>
    setExpandedChapters(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  return (
    <div className="space-y-4">
      {chaptersData.documents.map((docGroup) => {
        const missingChapters = docGroup.chapters.filter(ch => ch.status === "missing");
        return (
          <div key={docGroup.doc_id}>
            {/* Document header row */}
            <div className="flex items-center gap-2 py-1.5 mb-1 border-b border-border/30">
              <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
              <span className="text-xs font-mono font-semibold text-muted-foreground truncate flex-1">
                {docGroup.doc_title}
              </span>
              <Badge variant="outline" className="text-[9px] font-mono shrink-0">
                {docGroup.chapters.length} ch
              </Badge>
              {missingChapters.length > 0 && (
                <button
                  className="flex items-center gap-1 text-[10px] font-mono transition-opacity hover:opacity-80 shrink-0"
                  style={{ color: "var(--gd-caution)" }}
                  onClick={() => onNavigate(`${libBase}/${docGroup.doc_id}`)}
                  title={`${missingChapters.length} missing chapter${missingChapters.length !== 1 ? "s" : ""} — view document in Library`}
                >
                  <RotateCw className="w-3 h-3" />
                  Reprocess ({missingChapters.length} missing)
                </button>
              )}
            </div>

            {/* Chapter rows */}
            <div className="space-y-0.5">
              {docGroup.chapters.map((ch) => {
                const isExpanded = expandedChapters.has(ch.id);
                const hasKnowledge = (ch.knowledge_count ?? 0) > 0;
                const isMissing = ch.status === "missing";
                return (
                  <div
                    key={ch.id}
                    ref={(el) => { chapterRefs.current[ch.id] = el; }}
                  >
                    {/* Chapter row — gets .chapter-highlight for ~1.5 s when
                        arrived from a badge deep-link (targetChapterId). */}
                    <div
                      className={`flex items-center gap-2 text-xs py-0.5 rounded px-1 -mx-1 group
                        ${isMissing ? "" : "text-muted-foreground"}
                        ${hasKnowledge ? "hover:bg-muted/30 cursor-pointer" : ""}
                        ${ch.id === highlightedId ? "chapter-highlight" : ""}`}
                      style={isMissing ? { color: "color-mix(in srgb, var(--gd-danger) 80%, transparent)" } : undefined}
                      onClick={hasKnowledge ? () => toggleChapter(ch.id) : undefined}
                      title={hasKnowledge ? (isExpanded ? "Collapse knowledge" : `Show ${ch.knowledge_count} knowledge items`) : undefined}
                    >
                      {/* Expand chevron */}
                      <span className={`w-3.5 h-3.5 shrink-0 flex items-center justify-center transition-transform
                        ${hasKnowledge ? "opacity-40 group-hover:opacity-70" : "opacity-0"}`}>
                        {isExpanded
                          ? <ChevronDown className="w-3 h-3" />
                          : <ChevronRight className="w-3 h-3" />}
                      </span>

                      <span className="font-mono w-5 text-right shrink-0 opacity-40">
                        {ch.seq + 1}.
                      </span>

                      <span className={`truncate flex-1 ${ch.level > 1 ? `pl-${(ch.level - 1) * 3}` : ""}`}>
                        {ch.title || "(untitled)"}
                      </span>

                      {isMissing && (
                        <span className="text-[9px] font-mono uppercase px-1 rounded shrink-0" style={{ color: "var(--gd-danger)", background: "var(--gd-danger-soft)" }}>
                          missing
                        </span>
                      )}

                      {hasKnowledge && (
                        <Badge variant="outline"
                          className="text-[8px] font-mono border-primary/20 text-primary/60 shrink-0 py-0 h-4">
                          {ch.knowledge_count} items
                        </Badge>
                      )}

                      {ch.word_count > 0 && (
                        <span className="font-mono opacity-30 shrink-0 text-[10px]">
                          {ch.word_count.toLocaleString()}w
                        </span>
                      )}
                    </div>

                    {/* Expanded knowledge panel */}
                    {isExpanded && hasKnowledge && (
                      <ChapterKnowledgePanel
                        workId={workId}
                        chapterId={ch.id}
                        baseApiUrl={baseApiUrl}
                      />
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        );
      })}
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
      <div className="flex items-center gap-2 px-4 py-2.5 rounded-lg border text-xs" style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", color: "var(--gd-success)" }}>
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
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border" style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)" }}>
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--gd-success)" }}>
          <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
          <span>
            <span className="font-medium">Ready to advance</span>
            {" — "}current stage <span className="font-mono">{pipeline.status}</span> ({stageLabel}) is complete.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 hover:opacity-80"
          style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", color: "var(--gd-success)" }}
          onClick={onGoBook}>
          Advance to {nextLabel} <ArrowRight className="w-3 h-3" />
        </Button>
      </div>
    );
  }

  if (hasBlockers) {
    const high = (pipeline.open_findings ?? []).filter(f => f.severity === "high" || f.severity === "critical");
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border" style={{ borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", background: "var(--gd-danger-soft)" }}>
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--gd-danger)" }}>
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
          <span>
            <span className="font-mono">{pipeline.status}</span> {stageLabel} has{" "}
            <span className="font-medium">{high.length} open finding{high.length !== 1 ? "s" : ""}</span>
            {" "}blocking advance.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 hover:opacity-80"
          style={{ borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", color: "var(--gd-danger)" }}
          onClick={onGoBook}>
          Resolve in Book tab <ExternalLink className="w-3 h-3" />
        </Button>
      </div>
    );
  }

  if (needsArtifact) {
    return (
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 rounded-lg border" style={{ borderColor: "var(--gd-caution)", background: "var(--gd-caution-soft)" }}>
        <div className="flex items-center gap-2 text-xs" style={{ color: "var(--gd-caution)" }}>
          <Zap className="w-3.5 h-3.5 shrink-0" />
          <span>
            Stage <span className="font-mono">{pipeline.status}</span> ({stageLabel}) needs its AI work run before you can advance.
          </span>
        </div>
        <Button size="sm" variant="outline"
          className="gap-1.5 h-7 text-xs shrink-0 hover:opacity-80"
          style={{ borderColor: "var(--gd-caution)", color: "var(--gd-caution)" }}
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

function MetricCard({ label, value, sub, loading, color }: {
  label: string; value: string; sub: string; loading?: boolean; color?: string;
}) {
  // `color` may be a Tailwind neutral class (e.g. text-muted-foreground) or a
  // VELLUM CSS colour token (var(...) / color-mix(...)). Route tokens through
  // an inline style so they adapt to dark mode automatically.
  const isToken = !!color && (color.startsWith("var(") || color.startsWith("color-mix("));
  const colorClass = isToken ? "" : (color ?? "text-foreground");
  return (
    <Card className="border-border/50">
      <CardContent className="p-4 space-y-1">
        <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
        {loading ? <Skeleton className="h-8 w-20" /> : (
          <p
            className={`text-2xl font-mono font-bold ${colorClass}`}
            style={isToken ? { color } : undefined}
          >{value}</p>
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

// ── Continuity Findings (ConStory) ────────────────────────────────────────────

interface NarrativeFinding {
  id: string; category: string; subtype: string; severity: string;
  canon_class: string | null;
  fact_quote: string; fact_chapter: number; fact_offset: number;
  contradiction_quote: string; contradiction_chapter: number; contradiction_offset: number;
  reasoning: string; disposition: string; disposition_note: string;
  chapter_seq?: number; chapter_title?: string; created_at: string;
}
interface FindingMetrics {
  book: { words: number; findings: number; ced: number };
  chapters: Array<{ chapter_id: string; seq: number; title: string; words: number; findings: number; ced: number }>;
  counts: { total: number; by_severity: Record<string, number>; by_disposition: Record<string, number> };
}
interface ConstoryRun {
  state: "running" | "done" | "error";
  chapters_done: number; chapters_total: number;
  findings_created: number; error?: string | null;
}

const SEV_STYLE: Record<string, React.CSSProperties> = {
  critical: { color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 45%, transparent)" },
  high:     { color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" },
  medium:   { color: "var(--gd-caution)", background: "var(--gd-caution-soft)", borderColor: "var(--gd-caution)" },
  low:      { color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" },
};
const DISPOSITION_LABEL: Record<string, string> = {
  open: "Open", fixed: "Fixed", intentional: "Intentional", wontfix: "Won't fix",
};

function FindingsSection({ workId, open, onToggle }: {
  workId: string; open: boolean; onToggle: () => void;
}) {
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<string>("open");
  const [noteFor, setNoteFor] = useState<string | null>(null);
  const [noteText, setNoteText] = useState("");

  const { data: statusData } = useQuery<{ run: ConstoryRun | null }>({
    queryKey: ["constory-status", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/constory/status`).then((r) => r.json()),
    refetchInterval: (q) => (q.state.data?.run?.state === "running" ? 3_000 : false),
  });
  const run = statusData?.run ?? null;
  const running = run?.state === "running";

  // Refresh findings + metrics when a run finishes.
  const prevRunning = useRef(false);
  useEffect(() => {
    if (prevRunning.current && !running) {
      queryClient.invalidateQueries({ queryKey: ["narrative-findings", workId] });
      queryClient.invalidateQueries({ queryKey: ["finding-metrics", workId] });
      if (run?.state === "done") toast.success(`Contradiction check finished — ${run.findings_created} new finding${run.findings_created === 1 ? "" : "s"}`);
      if (run?.state === "error") toast.error(`Contradiction check failed: ${run.error ?? "unknown error"}`);
    }
    prevRunning.current = running;
  }, [running, run, queryClient, workId]);

  const { data: findingsData, isLoading, isError, refetch: refetchFindings } = useQuery<{ findings: NarrativeFinding[] }>({
    queryKey: ["narrative-findings", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/findings`).then((r) => r.json()),
    staleTime: 60_000,
  });
  const { data: metrics } = useQuery<FindingMetrics>({
    queryKey: ["finding-metrics", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/findings/metrics`).then((r) => r.json()),
    staleTime: 60_000,
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/constory/run`, { method: "POST" });
      if (r.status === 409) throw new Error("A check is already running");
      if (!r.ok) throw new Error("Could not start the contradiction check");
      return r.json();
    },
    onSuccess: () => {
      toast.success("Contradiction check started");
      queryClient.invalidateQueries({ queryKey: ["constory-status", workId] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const dispositionMutation = useMutation({
    mutationFn: async ({ id, disposition, note }: { id: string; disposition: string; note?: string }) => {
      const r = await apiFetch(`${BASE}/works/${workId}/findings/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ disposition, note: note ?? "" }),
      });
      if (!r.ok) {
        const detail = (await r.json().catch(() => null))?.detail;
        throw new Error(detail ?? "Could not update the finding");
      }
      return r.json();
    },
    onSuccess: () => {
      setNoteFor(null); setNoteText("");
      queryClient.invalidateQueries({ queryKey: ["narrative-findings", workId] });
      queryClient.invalidateQueries({ queryKey: ["finding-metrics", workId] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const all = findingsData?.findings ?? [];
  const shown = filter === "all" ? all : all.filter((f) => f.disposition === filter);
  const openCount = all.filter((f) => f.disposition === "open").length;

  return (
    <Section id="findings" label="Continuity Findings" icon={ScanSearch}
      open={open} onToggle={onToggle}
      badge={openCount ? String(openCount) : undefined}
      badgeVariant="destructive"
      headerAction={
        <button
          className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground/60 hover:text-primary transition-colors disabled:opacity-40"
          onClick={() => runMutation.mutate()}
          disabled={running || runMutation.isPending}
          title="Check every chapter against all earlier chapters and canon"
        >
          {running || runMutation.isPending
            ? <Loader2 className="w-3 h-3 animate-spin" />
            : <RotateCw className="w-3 h-3" />}
          {running
            ? `Checking ${run?.chapters_done ?? 0}/${run?.chapters_total ?? "?"}…`
            : "Run check"}
        </button>
      }>
      {/* CED summary */}
      {metrics && metrics.book.words > 0 && (
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 mb-4 text-[11px] font-mono text-muted-foreground">
          <span>
            Book CED <span className="font-semibold text-foreground">{metrics.book.ced}</span>
            <span className="text-muted-foreground/50"> / 10k words</span>
          </span>
          <span>{metrics.book.findings} error finding{metrics.book.findings === 1 ? "" : "s"} in {metrics.book.words.toLocaleString()} words</span>
          {Object.entries(metrics.counts.by_severity).map(([sev, n]) => (
            <span key={sev} className="px-1.5 py-0.5 rounded border text-[10px] font-semibold" style={SEV_STYLE[sev]}>
              {n} {sev}
            </span>
          ))}
        </div>
      )}

      {/* Disposition filter */}
      <div className="flex flex-wrap gap-1.5 mb-4">
        {["open", "fixed", "intentional", "wontfix", "all"].map((d) => (
          <button key={d}
            className={`px-2 py-0.5 rounded-md border font-mono text-[10px] uppercase tracking-wider transition-colors ${
              filter === d ? "border-primary/50 text-primary bg-primary/5" : "border-border/60 text-muted-foreground hover:text-foreground"
            }`}
            onClick={() => setFilter(d)}>
            {d === "all" ? "All" : DISPOSITION_LABEL[d]}
            {d !== "all" && (
              <span className="ml-1 opacity-60">{all.filter((f) => f.disposition === d).length}</span>
            )}
          </button>
        ))}
      </div>

      {isLoading ? (
        <LoadingState rows={2} label="Loading continuity findings" />
      ) : isError ? (
        <ErrorState
          title="Couldn't load findings"
          detail="The continuity findings failed to load."
          onRetry={() => refetchFindings()}
        />
      ) : shown.length === 0 ? (
        all.length === 0 ? (
          <EmptyState
            icon={<ScanSearch />}
            title="No contradictions recorded yet"
            description='Press "Run check" to scan every chapter against all earlier chapters and canon.'
          />
        ) : (
          <EmptyState
            icon={<ScanSearch />}
            title={`No ${filter === "all" ? "" : DISPOSITION_LABEL[filter].toLowerCase() + " "}findings.`}
          />
        )
      ) : (
        <div className="space-y-3">
          {shown.map((f) => (
            <div key={f.id}
              className={`p-3 rounded-lg border border-border/40 bg-muted/10 ${f.disposition !== "open" ? "opacity-60" : ""}`}>
              <div className="flex flex-wrap items-center gap-1.5 mb-2">
                <span className="px-1.5 py-0.5 rounded border text-[10px] font-mono font-semibold uppercase" style={SEV_STYLE[f.severity]}>
                  {f.severity}
                </span>
                <Badge variant="outline" className="text-[10px] font-mono">
                  {f.category.replace(/_/g, " ")} · {f.subtype.replace(/_/g, " ")}
                </Badge>
                {f.canon_class && (
                  <Badge variant="outline" className="text-[10px] font-mono border-primary/40 text-primary">
                    canon {f.canon_class}
                  </Badge>
                )}
                {f.disposition !== "open" && (
                  <Badge variant="secondary" className="text-[10px] font-mono">
                    {DISPOSITION_LABEL[f.disposition]}
                  </Badge>
                )}
              </div>

              {/* Dual evidence */}
              <div className="space-y-1.5 text-sm">
                <p className="leading-snug">
                  <span className="text-[10px] font-mono text-muted-foreground/70 mr-1.5">
                    {f.fact_chapter > 0 ? `ch ${f.fact_chapter} @${f.fact_offset}` : "canon"}
                  </span>
                  <span className="italic">“{f.fact_quote}”</span>
                </p>
                <p className="leading-snug">
                  <span className="text-[10px] font-mono mr-1.5" style={{ color: "var(--gd-danger)" }}>
                    ch {f.contradiction_chapter} @{f.contradiction_offset}
                  </span>
                  <span className="italic">“{f.contradiction_quote}”</span>
                </p>
              </div>
              {f.reasoning && (
                <p className="text-[11px] text-muted-foreground mt-1.5">{f.reasoning}</p>
              )}
              {f.disposition === "intentional" && f.disposition_note && (
                <p className="text-[11px] font-mono mt-1.5" style={{ color: "var(--gd-caution)" }}>
                  note: {f.disposition_note}
                </p>
              )}

              {/* Disposition actions */}
              <div className="flex flex-wrap items-center gap-2.5 mt-2.5">
                {f.disposition === "open" ? (
                  <>
                    <button
                      className="flex items-center gap-1 text-[10px] font-mono transition-opacity hover:opacity-80"
                      style={{ color: "var(--gd-success)" }}
                      disabled={dispositionMutation.isPending}
                      onClick={() => dispositionMutation.mutate({ id: f.id, disposition: "fixed" })}>
                      <Check className="w-3 h-3" /> Fixed
                    </button>
                    <button
                      className="flex items-center gap-1 text-[10px] font-mono transition-opacity hover:opacity-80"
                      style={{ color: "var(--gd-caution)" }}
                      disabled={dispositionMutation.isPending}
                      onClick={() => { setNoteFor(noteFor === f.id ? null : f.id); setNoteText(""); }}>
                      <Lightbulb className="w-3 h-3" /> Intentional…
                    </button>
                    <button
                      className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                      disabled={dispositionMutation.isPending}
                      onClick={() => dispositionMutation.mutate({ id: f.id, disposition: "wontfix" })}>
                      <EyeOff className="w-3 h-3" /> Won't fix
                    </button>
                  </>
                ) : (
                  <button
                    className="flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
                    disabled={dispositionMutation.isPending}
                    onClick={() => dispositionMutation.mutate({ id: f.id, disposition: "open" })}>
                    <Undo2 className="w-3 h-3" /> Reopen
                  </button>
                )}
              </div>

              {/* Intentional note input (required) */}
              {noteFor === f.id && (
                <div className="flex items-center gap-2 mt-2">
                  <input
                    autoFocus
                    className="flex-1 px-2 py-1 rounded-md border border-border/60 bg-background text-xs font-mono focus:outline-none focus:border-primary/50"
                    placeholder="Why is this deliberate? (required — e.g. delayed revelation)"
                    value={noteText}
                    onChange={(e) => setNoteText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter" && noteText.trim()) {
                        dispositionMutation.mutate({ id: f.id, disposition: "intentional", note: noteText.trim() });
                      }
                      if (e.key === "Escape") setNoteFor(null);
                    }}
                  />
                  <button
                    className="text-[10px] font-mono text-primary disabled:opacity-40"
                    disabled={!noteText.trim() || dispositionMutation.isPending}
                    onClick={() => dispositionMutation.mutate({ id: f.id, disposition: "intentional", note: noteText.trim() })}>
                    Save
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
      <p className="text-[10px] font-mono text-muted-foreground/50 mt-4">
        Every finding quotes both passages at their real character offsets. Severity is computed
        from the contradiction type and canon class — findings marked Intentional or Won't fix are
        excluded from CED.
      </p>
    </Section>
  );
}
