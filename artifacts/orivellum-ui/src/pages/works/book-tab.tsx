import { useState } from "react";
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
  Play,
  ShieldAlert,
} from "lucide-react";
import { toast } from "sonner";

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

function gaugeColor(pct: number) {
  if (pct >= 75) return "bg-emerald-500/70";
  if (pct >= 40) return "bg-amber-500/70";
  return "bg-red-500/60";
}

const STATUS_CHIP: Record<OutlineChapter["chapter_status"], { label: string; cls: string; Icon: typeof CheckCircle2 }> = {
  present: { label: "Present", cls: "bg-emerald-50 text-emerald-700 border-emerald-200", Icon: CheckCircle2 },
  incomplete: { label: "Incomplete", cls: "bg-amber-50 text-amber-700 border-amber-200", Icon: CircleDashed },
  missing: { label: "Missing", cls: "bg-red-50 text-red-700 border-red-200", Icon: CircleAlert },
};

const SEV_CLS: Record<BookGap["severity"], string> = {
  high: "border-red-200 bg-red-50/60 text-red-800",
  medium: "border-amber-200 bg-amber-50/60 text-amber-800",
  low: "border-border/60 bg-muted/30 text-muted-foreground",
};

// ─── Pipeline panel ───────────────────────────────────────────────────────────

interface Pipeline {
  id: string; work_id: string; title: string; status: string;
  chapter_count: number; chapters_extracted: number;
  chapters_drafted: number; chapters_approved: number;
  created_at: string; updated_at: string;
}

function PipelinePanel({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [blockerMsg, setBlockerMsg] = useState<string | null>(null);

  const { data, isLoading } = useQuery({
    queryKey: ["pipeline", workId],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ pipeline: Pipeline | null }>;
    },
    staleTime: 30_000,
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/pipeline`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) });
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
        const detail = (json as any).detail ?? "Transition blocked by open findings";
        setBlockerMsg(detail);
        throw new Error(detail);
      }
      if (!r.ok) throw new Error((json as any).detail ?? "Advance failed");
      return json;
    },
    onSuccess: (json: any) => {
      setBlockerMsg(null);
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      queryClient.invalidateQueries({ queryKey: ["book-intelligence", workId] });
      // Use the mutation response (not stale cached data) to get the new stage label
      const newStatus = json?.pipeline?.status;
      const next = newStatus ? STAGE_MAP[newStatus] : null;
      if (next) toast.success(`Advanced to ${next.state} — ${next.label}`);
      else toast.success("Pipeline advanced");
    },
    onError: (e: Error) => {
      if (!blockerMsg) toast.error(e.message);
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
              <Badge className="bg-emerald-500/10 text-emerald-700 border-emerald-300">Complete</Badge>
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

        {/* Blocker warning */}
        {blockerMsg && (
          <div className="flex items-start gap-2 rounded-lg px-3 py-2 bg-destructive/10 border border-destructive/30 text-destructive text-xs">
            <ShieldAlert className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{blockerMsg}</span>
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
                  <div className={`h-full rounded-full transition-all duration-700 ${gaugeColor(pct)}`} style={{ width: `${pct}%` }} />
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
                    <chip.Icon className={`w-4 h-4 shrink-0 ${c.chapter_status === "present" ? "text-emerald-500" : c.chapter_status === "incomplete" ? "text-amber-500" : "text-red-400"}`} />
                    <span className="font-serif text-sm truncate flex-1" title={c.title ?? undefined}>
                      {c.title || "Untitled section"}
                    </span>
                    <span className="text-[10px] font-mono text-muted-foreground shrink-0" title="Word count">
                      {c.word_count.toLocaleString()} w
                    </span>
                    <span
                      className={`text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border ${c.knowledge_count === 0 ? "bg-red-50 text-red-600 border-red-200" : c.knowledge_count < 3 ? "bg-amber-50 text-amber-700 border-amber-200" : "bg-muted/40 text-muted-foreground border-border/50"}`}
                      title="Knowledge items supporting this chapter"
                    >
                      {c.knowledge_count} research
                    </span>
                    <span className={`text-[10px] font-mono shrink-0 px-1.5 py-0.5 rounded border ${chip.cls}`}>{chip.label}</span>
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
                  <div key={i} className={`py-2 px-3 rounded-lg border text-sm ${SEV_CLS[g.severity]}`}>
                    <div className="font-medium font-serif leading-snug">{g.title}</div>
                    <div className="text-xs opacity-80 mt-0.5 leading-snug">{g.description}</div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
