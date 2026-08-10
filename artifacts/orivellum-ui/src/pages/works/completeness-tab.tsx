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

interface ComplDimension {
  name: string; label: string; score: number;
  current: number | string; target: number | string; unit: string;
  rule: string; evidence: string[];
}
interface ComplReport {
  overall: number; readiness: string; summary: string; evaluated_at: string;
  dimensions: ComplDimension[];
}

const READINESS_COLOR: Record<string, string> = {
  "Ready":          "text-emerald-700 bg-emerald-50 border-emerald-200",
  "Near-Complete":  "text-blue-700 bg-blue-50 border-blue-200",
  "Substantial":    "text-violet-700 bg-violet-50 border-violet-200",
  "Developing":     "text-amber-700 bg-amber-50 border-amber-200",
  "Draft":          "text-muted-foreground bg-muted border-border",
};

const DIM_BAR_COLOR: Record<string, string> = {
  structural: "bg-violet-500",
  content:    "bg-blue-500",
  research:   "bg-emerald-500",
  editorial:  "bg-amber-500",
  source:     "bg-orange-400",
};

export function CompletenessTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();

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

  const { data, isLoading, error, refetch, isFetching } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/completeness`).then((r) => {
        if (!r.ok) throw new Error("completeness fetch failed");
        return r.json();
      }),
    staleTime: 60_000,
    // Poll every 10 s while the pipeline is advancing so progress bars stay live.
    // Stops automatically when the pipeline reaches B17 or when no pipeline exists.
    refetchInterval: pipelineActive ? 10_000 : false,
  });

  // Fetch the work to read/write meta.completeness_targets
  const { data: workResp } = useGetWork(workId, {
    query: { queryKey: getGetWorkQueryKey(workId), enabled: !!workId },
  });
  const updateWork = useUpdateWork();

  // ── Target editing state ─────────────────────────────────────────────────
  const [editingTargets, setEditingTargets] = useState(false);
  const currentMeta = (workResp?.work as any)?.meta ?? {};
  const savedTargets = (currentMeta?.completeness_targets ?? {}) as {
    word_target?: number;
    chapter_target?: number;
  };

  const [wordInput, setWordInput]       = useState("");
  const [chapterInput, setChapterInput] = useState("");

  const openTargetEditor = () => {
    setWordInput(String(savedTargets.word_target ?? 50000));
    setChapterInput(String(savedTargets.chapter_target ?? 10));
    setEditingTargets(true);
  };

  const saveTargets = () => {
    const wt = parseInt(wordInput, 10);
    const ct = parseInt(chapterInput, 10);
    if (!wt || !ct || wt < 1 || ct < 1) {
      toast.error("Targets must be positive numbers");
      return;
    }
    const mergedMeta = { ...currentMeta, completeness_targets: { word_target: wt, chapter_target: ct } };
    updateWork.mutate(
      { workId, data: { meta: mergedMeta } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
          // Invalidate completeness so it reruns with new targets
          queryClient.invalidateQueries({ queryKey: ["work-completeness", workId] });
          setEditingTargets(false);
          toast.success("Targets saved — scores updated");
        },
        onError: () => toast.error("Could not save targets"),
      }
    );
  };

  // ── Render ───────────────────────────────────────────────────────────────

  if (isLoading) return (
    <div className="space-y-4">
      {[1,2,3,4,5].map(i => <Skeleton key={i} className="h-16 w-full" />)}
    </div>
  );

  if (error || !data) return (
    <div className="text-center py-16 text-muted-foreground border border-dashed rounded-lg">
      <BarChart2 className="w-8 h-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">Could not load completeness — re-extract documents first.</p>
    </div>
  );

  const readinessClass = READINESS_COLOR[data.readiness] ?? READINESS_COLOR["Draft"];

  return (
    <div className="space-y-6">
      {/* Overall banner */}
      <div className={`flex items-center justify-between p-4 rounded-xl border ${readinessClass}`}>
        <div>
          <p className="text-xs font-mono uppercase tracking-wider opacity-70 mb-0.5">Readiness</p>
          <p className="text-2xl font-serif font-semibold">{data.readiness}</p>
          <p className="text-xs font-mono mt-1 opacity-70">{data.summary}</p>
        </div>
        <div className="text-right shrink-0 ml-6">
          <p className="text-4xl font-mono font-bold">{data.overall}%</p>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="text-[10px] font-mono opacity-60 hover:opacity-100 transition-opacity mt-1"
          >
            {isFetching ? "updating…" : "refresh"}
          </button>
        </div>
      </div>

      {/* Completeness targets editor */}
      <div className="p-4 rounded-lg border border-border/50 bg-muted/10">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
              Completeness targets
            </h3>
            {!editingTargets && (
              <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                {savedTargets.word_target
                  ? `${Number(savedTargets.word_target).toLocaleString()} words · ${savedTargets.chapter_target ?? 10} chapters`
                  : "Using defaults (50,000 words · 10 chapters)"}
              </p>
            )}
          </div>
          {!editingTargets && (
            <button
              onClick={openTargetEditor}
              className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors"
            >
              <Pencil className="w-3 h-3" /> Edit
            </button>
          )}
        </div>

        {editingTargets && (
          <div className="space-y-3">
            <div className="flex items-center gap-3 flex-wrap">
              <label className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                Word target
                <Input
                  type="number"
                  min={1}
                  value={wordInput}
                  onChange={(e) => setWordInput(e.target.value)}
                  className="w-28 h-7 text-sm font-mono"
                  placeholder="50000"
                />
              </label>
              <label className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                Chapter target
                <Input
                  type="number"
                  min={1}
                  value={chapterInput}
                  onChange={(e) => setChapterInput(e.target.value)}
                  className="w-20 h-7 text-sm font-mono"
                  placeholder="10"
                />
              </label>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={saveTargets}
                disabled={updateWork.isPending}
                className="h-7 text-xs gap-1.5"
              >
                {updateWork.isPending
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : <Check className="w-3 h-3" />}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditingTargets(false)}
                disabled={updateWork.isPending}
                className="h-7 text-xs gap-1.5"
              >
                <X className="w-3 h-3" /> Cancel
              </Button>
            </div>
          </div>
        )}

        {/* ── Word-count + chapter progress bars ─────────────────────────── */}
        {!editingTargets && (() => {
          const contentDim = data.dimensions.find((d) => d.name === "content");
          const structDim  = data.dimensions.find((d) => d.name === "structure");
          if (!contentDim && !structDim) return null;

          const barColor = (pct: number): string =>
            pct >= 70 ? "var(--green-2)" : pct >= 30 ? "var(--gilt)" : "var(--rust)";

          return (
            <div className="mt-3 space-y-2.5">
              {contentDim && Number(contentDim.target) > 0 && (() => {
                const pct = Math.min(100, Math.round((Number(contentDim.current) / Number(contentDim.target)) * 100));
                return (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono text-muted-foreground">Words</span>
                      <span className="text-[11px] font-mono text-muted-foreground">
                        {Number(contentDim.current).toLocaleString()} / {Number(contentDim.target).toLocaleString()}
                        <span className="ml-1.5 opacity-60">({pct}%)</span>
                      </span>
                    </div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{ width: `${pct}%`, background: barColor(pct) }}
                      />
                    </div>
                  </div>
                );
              })()}
              {structDim && Number(structDim.target) > 0 && (() => {
                const pct = Math.min(100, Math.round((Number(structDim.current) / Number(structDim.target)) * 100));
                return (
                  <div>
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-[11px] font-mono text-muted-foreground">Chapters</span>
                      <span className="text-[11px] font-mono text-muted-foreground">
                        {structDim.current} / {structDim.target}
                        <span className="ml-1.5 opacity-60">({pct}%)</span>
                      </span>
                    </div>
                    <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                      <div
                        className="h-full rounded-full transition-all duration-700"
                        style={{ width: `${pct}%`, background: barColor(pct) }}
                      />
                    </div>
                  </div>
                );
              })()}
            </div>
          );
        })()}
      </div>

      {/* Dimension breakdown */}
      <div className="space-y-3">
        <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
          Five dimensions
        </h3>
        {data.dimensions.map((dim) => (
          <div key={dim.name} className="p-4 rounded-lg border border-border/50 bg-muted/10 space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <span className="font-medium text-sm">{dim.label}</span>
                <span className="ml-2 text-[11px] font-mono text-muted-foreground">
                  {Number(dim.current).toLocaleString()} / {Number(dim.target).toLocaleString()} {dim.unit}
                </span>
              </div>
              <span className="text-sm font-mono font-semibold">{dim.score}%</span>
            </div>
            {/* Progress bar */}
            <div className="h-2 bg-muted rounded-full overflow-hidden">
              <div
                className={`h-full rounded-full transition-all duration-500 ${DIM_BAR_COLOR[dim.name] ?? "bg-primary"}`}
                style={{ width: `${dim.score}%` }}
              />
            </div>
            {/* Rule + evidence */}
            <p className="text-[11px] font-mono text-muted-foreground">{dim.rule}</p>
            {dim.evidence.map((ev, i) => (
              <p key={i} className="text-[11px] text-muted-foreground/70 pl-2 border-l border-border/50">{ev}</p>
            ))}
          </div>
        ))}
      </div>

      <p className="text-[10px] font-mono text-muted-foreground/50 text-right">
        Evaluated {data.evaluated_at ? new Date(data.evaluated_at).toLocaleString() : "recently"}
      </p>
    </div>
  );
}

// ─── Graph tab ────────────────────────────────────────────────────────────────

