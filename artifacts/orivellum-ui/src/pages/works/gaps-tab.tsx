import { useState, useEffect, useRef, useMemo } from "react";
import { useParams, Link, useLocation, useSearch } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import {
  useGetWork,
  useGetWorkStats,
  useUpdateWork,
  useDeleteWork,
  useDeleteKnowledgeItem,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useGetWorkConversations,
  useCreateWorkTask,
  useUpdateWorkTask,
  useCreateConversation,
  useListLibrary,
  getGetWorkQueryKey,
  getGetWorkStatsQueryKey,
  getListWorksQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkDocumentsQueryKey,
  getGetWorkKnowledgeQueryKey,
  getGetWorkConversationsQueryKey,
  getListConversationsQueryKey,
  useGetEmbeddingsStatus,
  getGetEmbeddingsStatusQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  ArrowLeft,
  FileText,
  Network,
  CheckSquare,
  MessageSquare,
  Plus,
  Clock,
  Loader2,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  Check,
  X,
  Trash2,
  GraduationCap,
  RefreshCw,
  ChevronRight,
  MessageSquarePlus,
  Unlink,
  Search,
  BookOpen,
  ChevronDown,
  Trophy,
  BarChart2,
  AlertTriangle,
  TrendingUp,
  Lightbulb,
  Brain,
  Star,
  GitBranch,
  Share2,
  FileSpreadsheet,
  FileType,
  Presentation,
  Package,
  Download,
  Zap,
  Film,
  Scroll,
} from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Textarea } from "@/components/ui/textarea";
import { BookTab }       from "./book-tab";
import { BrainstormTab } from "./brainstorm-tab";
import { TrailerTab }    from "./trailer-tab";
import { GenesisTab }    from "./genesis-tab";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";
import { KnowledgeGraph, GNode } from "@/components/knowledge-graph";
import { LearnTab } from "@/pages/learning/learn-tab";


const WORK_API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface GapItem {
  kind: string; title: string; description: string; severity: string;
  metadata: Record<string, unknown>;
}
interface GapReport {
  coverage_pct: number; total_chapters: number;
  gaps: GapItem[]; suggested_queries: string[]; evaluated_at: string;
}

// Three distinct severity tiers — high (rust), medium (gilt), low (green-2).
const GAP_SEVERITY_STYLE: Record<string, React.CSSProperties> = {
  high:   { borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)", color: "var(--rust)" },
  medium: { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" },
  low:    { borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", background: "var(--green-soft)", color: "var(--green-2)" },
};
const GAP_DOT: Record<string, string> = {
  high: "var(--rust)", medium: "var(--gilt)", low: "var(--green-2)",
};

export function GapsTab({ workId, onBrainstorm }: { workId: string; onBrainstorm?: (seed: string) => void }) {
  const [, navigate] = useLocation();
  const [actionPending, setActionPending] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const createTask = useCreateWorkTask();

  // Subscribe to the pipeline cache that WorkDetail already keeps alive so we
  // can derive whether polling is needed — no extra network request.
  const { data: pipelineData } = useQuery<{ pipeline: any | null }>({
    queryKey: ["pipeline", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/pipeline`).then((r) => r.json()),
    enabled: !!workId,
    staleTime: 30_000,
  });
  // A pipeline is "active" when it exists and hasn't reached the terminal B17 gate.
  const pipelineActive =
    !!pipelineData?.pipeline && pipelineData.pipeline.status !== "B17";

  const [forceRefresh, setForceRefresh] = useState(false);
  const { data, isLoading, error, refetch, isFetching } = useQuery<GapReport>({
    queryKey: ["work-gaps", workId, forceRefresh],
    queryFn: () =>
      apiFetch(
        `${WORK_API_BASE}/works/${workId}/gaps${forceRefresh ? "?refresh=true" : ""}`
      ).then((r) => {
        if (!r.ok) throw new Error("gaps fetch failed");
        return r.json();
      }),
    staleTime: forceRefresh ? 0 : 120_000,
    // Poll every 15 s while the pipeline is advancing so new gaps surface
    // automatically. 15 s (vs 10 s for Completeness) because gap recomputation
    // is heavier. Stops when the pipeline reaches B17 or when none exists.
    refetchInterval: pipelineActive && !forceRefresh ? 15_000 : false,
  });

  /** Turn a gap into a Work task so it shows up in the Tasks tab. */
  const createTaskFromGap = (gapTitle: string) => {
    createTask.mutate(
      { workId, data: { text: `Research gap: ${gapTitle}` } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          toast.success("Task added");
        },
        onError: () => toast.error("Could not add task"),
      }
    );
  };

  /** Create a work-linked conversation pre-set to research a chapter topic. */
  const createResearchChat = async (chapterTitle: string) => {
    setActionPending(chapterTitle);
    try {
      const r = await apiFetch(`${WORK_API_BASE}/conversations`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: `Research: ${chapterTitle}`, work_id: workId }),
      });
      if (r.ok) {
        const d = await r.json();
        if (d.conversation?.id) navigate(`/chat?id=${d.conversation.id}`);
      } else {
        toast.error("Could not create research conversation");
      }
    } catch { toast.error("Network error"); }
    finally { setActionPending(null); }
  };

  /** Force re-extraction of a document that has no structural headings.
   *  Uses force=true so ready documents are re-queued (not skipped). */
  const reextractDoc = async (docId: string) => {
    if (!docId) return;
    setActionPending(docId);
    try {
      const r = await apiFetch(`${WORK_API_BASE}/library/${docId}/reprocess?force=true`, { method: "POST" });
      if (r.ok) {
        const d = await r.json();
        if (d.message?.includes("already ready") && !d.ok) {
          // Should not happen with force=true, but guard anyway
          toast.error("Re-extraction could not be queued");
        } else {
          toast.success("Re-extraction queued — the gap will clear once complete");
          setTimeout(() => refetch(), 4000);
        }
      } else { toast.error("Could not queue re-extraction"); }
    } catch { toast.error("Network error"); }
    finally { setActionPending(null); }
  };

  if (isLoading) return (
    <div className="space-y-3">{[1,2,3].map(i => <Skeleton key={i} className="h-20 w-full" />)}</div>
  );
  if (error || !data) return (
    <div className="text-center py-16 text-muted-foreground border border-dashed rounded-lg">
      <AlertTriangle className="w-8 h-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">Could not load gap analysis.</p>
    </div>
  );

  const byKind = data.gaps.reduce<Record<string, GapItem[]>>((acc, g) => {
    (acc[g.severity] ??= []).push(g);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      {/* Coverage bar */}
      <div className="p-4 rounded-xl border border-border/50 bg-muted/10 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-medium text-sm">Research coverage</span>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-lg font-mono font-bold">{data.coverage_pct}%</span>
            <button
              onClick={() => { setForceRefresh(true); refetch(); }}
              disabled={isFetching}
              className="text-[10px] font-mono text-muted-foreground hover:text-foreground"
            >
              {isFetching ? "…" : "refresh"}
            </button>
          </div>
        </div>
        <div className="h-2.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full rounded-full transition-all duration-500"
            style={{
              width: `${data.coverage_pct}%`,
              background:
                data.coverage_pct >= 80 ? "var(--green-2)" :
                data.coverage_pct >= 50 ? "var(--gilt)" : "var(--rust)",
            }}
          />
        </div>
        <p className="text-xs font-mono text-muted-foreground">
          {data.total_chapters} chapter{data.total_chapters !== 1 ? "s" : ""} analysed
        </p>
      </div>

      {/* Gaps list */}
      {data.gaps.length === 0 ? (
        <div className="text-center py-10 border border-dashed rounded-lg text-muted-foreground text-sm">
          No gaps detected — all chapters have sufficient research coverage.
        </div>
      ) : (
        <div className="space-y-4">
          {(["high", "medium", "low"] as const).map((sev) => {
            const items = byKind[sev] ?? [];
            if (items.length === 0) return null;
            return (
              <div key={sev} className="space-y-2">
                <h4 className="text-[11px] font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
                  <span className="w-2 h-2 rounded-full" style={{ background: GAP_DOT[sev] }} />
                  {sev} priority ({items.length})
                </h4>
                {items.map((g, i) => {
                  const chapTitle = (g.metadata.chapter_title as string | undefined) ?? g.title;
                  const docId     = g.metadata.doc_id as string | undefined;
                  const isResearchPending = actionPending === chapTitle;
                  const isExtractPending  = actionPending === docId;
                  return (
                    <div key={i} className="p-3.5 rounded-lg border" style={GAP_SEVERITY_STYLE[sev]}>
                      <div className="flex items-start justify-between gap-2 mb-1">
                        <p className="font-medium text-sm">{g.title}</p>
                        <div className="flex items-center gap-1.5 shrink-0">
                          <span className="text-[9px] font-mono uppercase tracking-wide opacity-50 border border-current/20 rounded px-1 py-0.5">{g.kind.replace(/_/g, " ")}</span>
                          {!!g.metadata.chapter_title && String(g.metadata.chapter_title) !== g.title && (
                            <span className="text-[9px] font-mono opacity-40 max-w-[120px] truncate" title={String(g.metadata.chapter_title)}>
                              {String(g.metadata.chapter_title)}
                            </span>
                          )}
                        </div>
                      </div>
                      <p className="text-[12px] leading-relaxed opacity-80">{g.description}</p>
                      {/* One-click actions — Add task is available on all gap kinds */}
                      <div className="flex items-center justify-end gap-3 mt-2 pt-2 border-t border-current/10">
                        <button
                          disabled={createTask.isPending}
                          onClick={() => createTaskFromGap(chapTitle)}
                          className="flex items-center gap-1.5 text-[11px] font-mono opacity-70 hover:opacity-100 disabled:opacity-30 transition-opacity"
                        >
                          <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M12 5v14M5 12h14"/></svg>
                          Add task
                        </button>
                        {onBrainstorm && (
                          <button
                            onClick={() => onBrainstorm(g.title)}
                            className="flex items-center gap-1.5 text-[11px] font-mono opacity-80 hover:opacity-100 transition-opacity"
                            style={{ color: "var(--gilt)" }}
                          >
                            <Lightbulb className="w-3 h-3" />
                            Brainstorm this →
                          </button>
                        )}
                        {(g.kind === "uncovered_chapter" || g.kind === "weak_coverage") && (
                          <button
                            disabled={!!actionPending}
                            onClick={() => createResearchChat(chapTitle)}
                            className="flex items-center gap-1.5 text-[11px] font-mono opacity-70 hover:opacity-100 disabled:opacity-30 transition-opacity"
                          >
                            {isResearchPending
                              ? <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                              : <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.35-4.35"/></svg>}
                            Research this chapter →
                          </button>
                        )}
                      </div>
                      {g.kind === "undocumented_doc" && docId && (
                        <div className="flex justify-end mt-2 pt-2 border-t border-current/10">
                          <button
                            disabled={!!actionPending}
                            onClick={() => reextractDoc(docId)}
                            className="flex items-center gap-1.5 text-[11px] font-mono opacity-70 hover:opacity-100 disabled:opacity-30 transition-opacity"
                          >
                            {isExtractPending
                              ? <svg className="w-3 h-3 animate-spin" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
                              : <svg className="w-3 h-3" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/></svg>}
                            Re-extract document →
                          </button>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      )}

      {/* Suggested queries */}
      {data.suggested_queries.length > 0 && (
        <div className="space-y-2">
          <h4 className="text-[11px] font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
            <Lightbulb className="w-3.5 h-3.5" /> Suggested research queries
          </h4>
          <div className="flex flex-col gap-2">
            {data.suggested_queries.map((q, i) => (
              <div
                key={i}
                className="flex items-center gap-2 px-3 py-2 rounded-lg border border-border/60 bg-muted/10 text-xs font-mono"
              >
                <Lightbulb className="w-3 h-3 text-muted-foreground/50 shrink-0" />
                <span className="flex-1 text-muted-foreground leading-snug">{q}</span>
                {/* Brainstorm → opens Ideas tab with this query as seed */}
                {onBrainstorm && (
                  <button
                    onClick={() => onBrainstorm(q)}
                    className="shrink-0 text-[10px] font-mono text-primary/80 hover:text-primary border border-primary/25 rounded px-2 py-0.5 hover:bg-primary/5 transition-colors whitespace-nowrap"
                    title="Brainstorm this query in the Ideas tab"
                  >
                    Brainstorm →
                  </button>
                )}
                {/* Discuss → creates a work-linked chat conversation */}
                <button
                  onClick={() => createResearchChat(q)}
                  disabled={actionPending === q}
                  className="shrink-0 text-[10px] font-mono text-muted-foreground hover:text-foreground border border-border/50 rounded px-2 py-0.5 hover:bg-muted/50 transition-colors whitespace-nowrap disabled:opacity-40"
                  title="Discuss this query in Chat"
                >
                  {actionPending === q ? '…' : 'Discuss →'}
                </button>
              </div>
            ))}
          </div>
          <p className="text-[10px] text-muted-foreground/60 font-mono">
            Brainstorm opens the Ideas tab · Discuss opens a work-linked chat
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Completeness tab ─────────────────────────────────────────────────────────

