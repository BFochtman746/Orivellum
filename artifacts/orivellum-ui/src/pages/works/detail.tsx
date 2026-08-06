import { useState, useEffect, useRef } from "react";
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
  RotateCcw,
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

// ─── Work detail shell ────────────────────────────────────────────────────────

export default function WorkDetail() {
  const { workId } = useParams();
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const { data: workResp, isLoading: loadingWork } = useGetWork(workId!, {
    query: { enabled: !!workId, queryKey: getGetWorkQueryKey(workId!) },
  });
  const work = workResp?.work;
  const { data: statsResp } = useGetWorkStats(workId!, {
    query: {
      queryKey: getGetWorkStatsQueryKey(workId!),
      enabled: !!workId,
      // Poll while any docs are still processing so the readiness strip stays current
      refetchInterval: (query) => {
        const byR = ((query.state.data as any)?.documents_by_readiness ?? {}) as Record<string, number>;
        return (byR.imported ?? 0) > 0 ? 4_000 : false;
      },
    },
  });
  const stats = statsResp as any;
  const pendingTaskCount: number = stats?.pending_task_count ?? 0;
  const updateWork = useUpdateWork();
  const deleteWork = useDeleteWork();

  // Pipeline status — shared cache key with BookTab so no duplicate fetches
  const PIPELINE_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
  const { data: pipelineData, refetch: refetchPipeline } = useQuery<{ pipeline: any | null }>({
    queryKey: ["pipeline", workId],
    queryFn: () => apiFetch(`${PIPELINE_BASE}/works/${workId}/pipeline`).then(r => r.json()),
    enabled: !!workId,
    staleTime: 30_000,
  });
  const hasPipeline = !!(pipelineData?.pipeline);
  const pipelineStatus = pipelineData?.pipeline?.status ?? null;

  const startPipeline = useMutation({
    mutationFn: () =>
      apiFetch(`${PIPELINE_BASE}/works/${workId}/pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: (work as any)?.title ?? "" }),
      }).then(r => { if (!r.ok) throw new Error("failed"); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      setActiveTab("book");
      toast.success("Book pipeline started");
    },
    onError: () => toast.error("Could not start book pipeline"),
  });

  const handleDelete = () => {
    if (!workId) return;
    if (!window.confirm("Delete this work? This cannot be undone.")) return;
    deleteWork.mutate(
      { workId },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
          navigate("/works");
        },
        onError: () => toast.error("Could not delete work"),
      }
    );
  };

  // Tab state — initialised from ?tab= URL param so deep-links from the
  // Intelligence page work (e.g. ?tab=search&q=gap+title).
  const _searchStr   = useSearch();
  const _urlParams   = new URLSearchParams(_searchStr);
  const [activeTab, setActiveTab] = useState(() => _urlParams.get("tab") ?? "book");
  const _initialSearchQuery = _urlParams.get("q") ?? "";
  // Brainstorm seed — set when user clicks "Brainstorm this" on a gap card
  const [brainstormSeed, setBrainstormSeed] = useState(_initialSearchQuery);
  const [brainstormContext, setBrainstormContext] = useState("general");
  const handleBrainstormGap = (seed: string) => {
    setBrainstormSeed(seed);
    setBrainstormContext("research_planning");
    setActiveTab("brainstorm");
  };

  // Inline editing state
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const startEdit = () => {
    setEditTitle((work as any)?.title ?? "");
    setEditDesc((work as any)?.description ?? "");
    setEditing(true);
  };

  const cancelEdit = () => setEditing(false);

  const saveEdit = () => {
    if (!workId || !editTitle.trim()) return;
    updateWork.mutate(
      { workId, data: { title: editTitle.trim(), description: editDesc.trim() || null } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
          toast.success("Work updated");
          setEditing(false);
        },
        onError: () => toast.error("Could not save changes"),
      }
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500 pb-20">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4 text-sm font-mono uppercase tracking-widest text-muted-foreground">
          <Link href="/works" className="hover:text-foreground transition-colors flex items-center gap-1">
            <ArrowLeft className="w-3 h-3" /> Works
          </Link>
          <span>/</span>
          <span className="text-foreground">
            {loadingWork ? <Skeleton className="w-20 h-4 inline-block align-middle" /> : work?.title}
          </span>
        </div>
        {work && (
          <div className="flex items-center gap-2">
            {/* Start Book Pipeline — only shown when no pipeline exists yet */}
            {!hasPipeline && pipelineData !== undefined && (
              <Button
                size="sm"
                variant="outline"
                disabled={startPipeline.isPending}
                onClick={() => startPipeline.mutate()}
                className="gap-1.5 text-xs border-amber-400/40 text-amber-700 hover:bg-amber-50 dark:text-amber-400 dark:hover:bg-amber-950/30"
              >
                {startPipeline.isPending
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Starting…</>
                  : <><BookOpen className="w-3.5 h-3.5" /> Start Book Pipeline</>}
              </Button>
            )}
            {/* Show current pipeline stage badge when pipeline already exists */}
            {hasPipeline && pipelineStatus && (
              <button
                onClick={() => setActiveTab("book")}
                className="flex items-center gap-1.5 text-[11px] font-mono px-2 py-1 rounded-full border border-primary/20 bg-primary/5 text-primary hover:bg-primary/10 transition-colors"
                title="View book pipeline"
              >
                <BookOpen className="w-3 h-3" />
                {pipelineStatus}
              </button>
            )}
            <Button
              size="sm"
              variant="outline"
              onClick={() => navigate(`/works/${workId}/intelligence`)}
              className="gap-1.5 text-xs border-primary/30 text-primary hover:bg-primary/5"
            >
              <Brain className="w-3.5 h-3.5" />
              Intelligence
            </Button>
            <GenerateMenu workId={workId!} />
            <QuickChatButton workId={workId!} />
            <button
              onClick={handleDelete}
              disabled={deleteWork.isPending}
              className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-destructive transition-colors px-2 py-1 rounded hover:bg-destructive/5"
            >
              {deleteWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              Delete
            </button>
          </div>
        )}
      </div>

      {/* Header */}
      {loadingWork ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : work ? (
        <div className="space-y-4">
          {editing ? (
            <div className="space-y-3">
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="text-2xl font-serif font-semibold h-auto py-2 px-3 border-primary/40"
                placeholder="Work title"
                autoFocus
              />
              <Textarea
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                className="font-serif text-base resize-none"
                placeholder="Description (optional)"
                rows={2}
              />
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={saveEdit} disabled={updateWork.isPending || !editTitle.trim()} className="gap-1.5">
                  {updateWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={updateWork.isPending} className="gap-1.5">
                  <X className="w-3.5 h-3.5" /> Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-start gap-3">
                <h1 className="vellum-h1">{work.title}</h1>
                <button
                  onClick={startEdit}
                  className="mt-3 p-1.5 rounded text-muted-foreground/50 hover:text-muted-foreground hover:bg-muted/50 transition-colors"
                  title="Edit title and description"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
              </div>
              <div className="gilt-rule w-40" />
              {work.description ? (
                <p className="text-[14px] mt-1.5 max-w-3xl leading-relaxed epigraph" style={{ fontSize: '14.5px', margin: '6px 0 0', borderLeft: 'none', paddingLeft: 0 }}>
                  {work.description}
                </p>
              ) : (
                <button
                  onClick={startEdit}
                  className="text-sm text-muted-foreground/40 italic mt-2 hover:text-muted-foreground transition-colors"
                >
                  Add a description…
                </button>
              )}
            </div>
          )}
          <div className="flex items-center gap-3 flex-wrap">
            <Select
              value={(work as any).status ?? "active"}
              onValueChange={(val) =>
                updateWork.mutate(
                  { workId: workId!, data: { status: val } },
                  {
                    onSuccess: () => {
                      queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId!) });
                      toast.success(val === "archived" ? "Work archived" : "Work set to active");
                    },
                    onError: () => toast.error("Could not update status"),
                  }
                )
              }
              disabled={updateWork.isPending}
            >
              <SelectTrigger className="h-6 text-[11px] font-mono uppercase px-2 py-0 w-auto border-primary/20 bg-primary/5 text-primary rounded-full focus:ring-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active" className="text-xs font-mono uppercase">Active</SelectItem>
                <SelectItem value="complete" className="text-xs font-mono uppercase">Complete</SelectItem>
                <SelectItem value="archived" className="text-xs font-mono uppercase">Archived</SelectItem>
              </SelectContent>
            </Select>
            <Select
              value={(work as any).work_type ?? "research"}
              onValueChange={(val) =>
                updateWork.mutate(
                  { workId: workId!, data: { work_type: val } },
                  {
                    onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId!) }),
                    onError: () => toast.error("Could not update type"),
                  }
                )
              }
              disabled={updateWork.isPending}
            >
              <SelectTrigger className="h-6 text-[11px] font-mono uppercase px-2 py-0 w-auto border-secondary/30 bg-secondary/40 text-secondary-foreground rounded-full focus:ring-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {["research", "writing", "reference", "study", "project", "personal"].map((t) => (
                  <SelectItem key={t} value={t} className="text-xs font-mono uppercase">{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <span className="text-sm font-mono text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" />
              Created {work.created_at ? format(new Date(work.created_at), "MMM d, yyyy") : "Unknown"}
            </span>
          </div>
          {stats && (
            <div className="flex flex-wrap items-center gap-4 pt-1">
              {[
                {
                  label: "Documents",
                  value: Object.values(stats.documents_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                },
                {
                  label: "Knowledge",
                  value: Object.values(stats.knowledge_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                },
                {
                  label: "Pending tasks",
                  value: (stats.tasks_by_status as Record<string, number> ?? {}).pending ?? 0,
                },
                {
                  label: "Conversations",
                  value: stats.conversation_count ?? 0,
                },
              ].map(({ label, value }) => {
                const tabMap: Record<string, string> = {
                  "Documents": "docs",
                  "Knowledge": "knowledge",
                  "Pending tasks": "tasks",
                  "Conversations": "conversations",
                };
                const target = tabMap[label];
                return (
                  <button
                    key={label}
                    className={`text-center group ${target ? "cursor-pointer hover:opacity-70 transition-opacity" : ""}`}
                    onClick={target ? () => setActiveTab(target as typeof activeTab) : undefined}
                    title={target ? `Go to ${label}` : undefined}
                  >
                    <div className={`text-lg font-semibold font-mono leading-none ${target ? "text-primary group-hover:underline" : ""}`}>{value}</div>
                    <div className="text-[10px] font-mono uppercase text-muted-foreground mt-0.5">{label}</div>
                  </button>
                );
              })}
              {/* Mastery bar — shown when concepts exist for this work */}
              {(stats as any).concept_count > 0 && (
                <div className="flex items-center gap-3 ml-2 pl-4 border-l border-border/50">
                  <div className="text-center">
                    <div className="text-lg font-semibold font-mono leading-none">{(stats as any).avg_mastery_pct ?? 0}%</div>
                    <div className="text-[10px] font-mono uppercase text-muted-foreground mt-0.5">Mastery</div>
                  </div>
                  <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
                    <div
                      className="h-full bg-emerald-500/70 rounded-full transition-all duration-700"
                      style={{ width: `${(stats as any).avg_mastery_pct ?? 0}%` }}
                    />
                  </div>
                </div>
              )}
              {/* Readiness strip — shown when any doc is still processing or has errors */}
              {(() => {
                const byR = stats.documents_by_readiness as Record<string, number> ?? {};
                const processing = byR.imported ?? 0;
                const errors = (byR.error ?? 0) + (byR.no_text ?? 0);
                if (processing === 0 && errors === 0) return null;
                return (
                  <div className="flex items-center gap-2 ml-2 pl-4 border-l border-border/50">
                    {processing > 0 && (
                      <span className="flex items-center gap-1 text-[10px] font-mono text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                        {processing} processing
                      </span>
                    )}
                    {errors > 0 && (
                      <span className="flex items-center gap-1 text-[10px] font-mono text-red-600 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                        {errors} error{errors !== 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      ) : null}

      {/* Tabs */}
      <div className="pt-8">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
          <TabsList className="w-full justify-start border-b border-border/50 rounded-none bg-transparent h-auto p-0 space-x-6">
            {[
              { value: "book",         icon: BookOpen,      label: "Book",         badge: null },
              { value: "documents",    icon: FileText,      label: "Documents",    badge: null },
              { value: "knowledge",    icon: Network,       label: "Knowledge",    badge: null },
              { value: "graph",        icon: Share2,        label: "Graph",        badge: null },
              { value: "completeness", icon: BarChart2,     label: "Completeness", badge: null },
              { value: "gaps",         icon: AlertTriangle, label: "Gaps",         badge: null },
              { value: "tasks",        icon: CheckSquare,   label: "Tasks",        badge: pendingTaskCount ?? null },
              { value: "conversations",icon: MessageSquare, label: "Conversations",badge: null },
              { value: "search",       icon: Search,        label: "Search",       badge: null },
              { value: "quiz",         icon: GraduationCap, label: "Quiz",         badge: null },
              { value: "learn",        icon: BookOpen,      label: "Learn",        badge: null },
              { value: "brainstorm",   icon: Lightbulb,     label: "Brainstorm",   badge: null },
              { value: "trailer",      icon: Film,          label: "Trailer",       badge: ((stats as any)?.trailer_count > 0 ? (stats as any)?.trailer_count : null) as number | null },
              { value: "genesis",      icon: Scroll,        label: "Genesis",       badge: null },
            ].map(({ value, icon: Icon, label, badge }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider"
              >
                <Icon className="w-4 h-4 mr-2" /> {label}
                {badge !== null && badge !== undefined && badge > 0 && (
                  <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-primary/10 text-primary leading-none">
                    {badge}
                  </span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="mt-8">
            <TabsContent value="book"><ErrorBoundary label="book tab"><BookTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="documents"><ErrorBoundary label="documents tab"><DocumentsTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="knowledge"><ErrorBoundary label="knowledge tab"><KnowledgeTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="graph"><ErrorBoundary label="graph tab"><GraphTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="completeness"><ErrorBoundary label="completeness tab"><CompletenessTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="gaps"><ErrorBoundary label="gaps tab"><GapsTab workId={workId!} onBrainstorm={handleBrainstormGap} /></ErrorBoundary></TabsContent>
            <TabsContent value="tasks"><ErrorBoundary label="tasks tab"><TasksTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="conversations"><ErrorBoundary label="conversations tab"><ConversationsTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="search"><ErrorBoundary label="search tab"><SearchTab workId={workId!} initialQuery={_initialSearchQuery} /></ErrorBoundary></TabsContent>
            <TabsContent value="quiz"><ErrorBoundary label="quiz tab"><QuizTab workId={workId!} workTitle={(work as any)?.title ?? "this Work"} /></ErrorBoundary></TabsContent>
            <TabsContent value="learn"><ErrorBoundary label="learn tab"><LearnTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="brainstorm"><ErrorBoundary label="brainstorm tab"><BrainstormTab key={brainstormSeed} workId={workId!} initialSeed={brainstormSeed} initialContext={brainstormContext} /></ErrorBoundary></TabsContent>
            <TabsContent value="trailer"><ErrorBoundary label="trailer tab"><TrailerTab workId={workId!} /></ErrorBoundary></TabsContent>
            <TabsContent value="genesis"><ErrorBoundary label="genesis tab"><GenesisTab workId={workId!} /></ErrorBoundary></TabsContent>
          </div>
        </Tabs>
      </div>
    </div>
  );
}

// ─── Documents tab ────────────────────────────────────────────────────────────

const DOC_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

async function reprocessWorkDoc(docId: string): Promise<void> {
  const resp = await apiFetch(`${DOC_BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

type ReadinessFilter = "all" | "ready" | "imported" | "error";
type DocSortKey = "name" | "date" | "kind" | "readiness";

function DocumentsTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const [open, setOpen] = useState(false);
  const [docFilter, setDocFilter] = useState("");
  const [readinessFilter, setReadinessFilter] = useState<ReadinessFilter>("all");
  const [docSort, setDocSort] = useState<DocSortKey>("date");
  const [retrying, setRetrying] = useState<string | null>(null);

  const { data: docsResp, isLoading } = useGetWorkDocuments(workId, {
    query: {
      enabled: !!workId,
      queryKey: getGetWorkDocumentsQueryKey(workId),
      // Poll every 4 s while any doc is still in "imported" state
      refetchInterval: (query) => {
        const docs = (query.state.data as any)?.documents ?? [];
        return docs.some((d: any) => d.readiness === "imported") ? 4_000 : false;
      },
    },
  });

  // Library documents not yet linked to this work — for the picker
  const { data: libraryResp } = useListLibrary();
  const unlinked = (libraryResp?.documents ?? []).filter(
    (d) => !d.work_id && d.id !== workId
  );

  const [linking, setLinking] = useState(false);
  const [dismissedDupes, setDismissedDupes] = useState<Set<string>>(new Set());
  const [resolvingDupe, setResolvingDupe] = useState<string | null>(null);

  // Fetch near-duplicate / version-relationship suggestions for this Work
  const { data: dupesResp, refetch: refetchDupes } = useQuery({
    queryKey: ["work-duplicates", workId],
    queryFn: async () => {
      const r = await apiFetch(`${DOC_BASE}/works/${workId}/duplicates`);
      if (!r.ok) return { pairs: [], count: 0 };
      return r.json() as Promise<{ pairs: any[]; count: number }>;
    },
    enabled: !!workId,
    staleTime: 60_000,
  });
  const dupePairs = (dupesResp?.pairs ?? []).filter(
    (p: any) => !dismissedDupes.has(p.id)
  );

  const handleDeclareCanonicaL = async (dupeId: string, canonicalDocId: string) => {
    setResolvingDupe(dupeId);
    try {
      const r = await apiFetch(
        `${DOC_BASE}/library/duplicates/${dupeId}/resolve`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "mark_superseded", canonical_doc_id: canonicalDocId }),
        }
      );
      if (!r.ok) throw new Error("Resolve failed");
      setDismissedDupes((prev) => new Set([...prev, dupeId]));
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["work-duplicates", workId] }),
        queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) }),
      ]);
      toast.success("Canonical document declared");
    } catch {
      toast.error("Could not resolve duplicate pair");
    } finally {
      setResolvingDupe(null);
    }
  };

  const handleLink = async (docId: string) => {
    setLinking(true);
    try {
      const r = await apiFetch(`${DOC_BASE}/library/${docId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_id: workId }),
      });
      if (!r.ok) throw new Error("Link failed");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) }),
        queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) }),
      ]);
      toast.success("Document linked");
      setOpen(false);
    } catch {
      toast.error("Could not link document");
    } finally {
      setLinking(false);
    }
  };

  const handleRetry = async (e: React.MouseEvent, docId: string) => {
    e.stopPropagation();
    setRetrying(docId);
    try {
      await reprocessWorkDoc(docId);
      toast.success("Reprocessing started");
      // Start polling — the refetchInterval will kick in automatically
      queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) });
    } catch (err: any) {
      toast.error(err?.message ?? "Could not reprocess document");
    } finally {
      setRetrying(null);
    }
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const docs = docsResp?.documents ?? [];

  // Compute readiness counts
  const readyCnt     = docs.filter((d) => d.readiness === "ready").length;
  const processingCnt = docs.filter((d) => d.readiness === "imported").length;
  const errorCnt     = docs.filter((d) => d.readiness === "error" || d.readiness === "no_text").length;
  const hasNonReady  = processingCnt > 0 || errorCnt > 0;

  // Apply readiness filter first, then text filter, then sort
  const byReadiness = readinessFilter === "all" ? docs
    : readinessFilter === "error" ? docs.filter((d) => d.readiness === "error" || d.readiness === "no_text")
    : docs.filter((d) => d.readiness === readinessFilter);

  const byText = docFilter.trim()
    ? byReadiness.filter((d) => {
        const hay = `${d.title ?? ""} ${(d as any).source ?? ""}`.toLowerCase();
        return hay.includes(docFilter.trim().toLowerCase());
      })
    : byReadiness;

  const filteredDocs = [...byText].sort((a, b) => {
    if (docSort === "name") {
      const na = (a.title ?? (a as any).source ?? "").toLowerCase();
      const nb = (b.title ?? (b as any).source ?? "").toLowerCase();
      return na.localeCompare(nb);
    }
    if (docSort === "kind") return (a.kind ?? "").localeCompare(b.kind ?? "");
    if (docSort === "readiness") return (a.readiness ?? "").localeCompare(b.readiness ?? "");
    // date (default) — newest first
    return ((b as any).created_at ?? "").localeCompare((a as any).created_at ?? "");
  });

  const READINESS_FILTERS: { key: ReadinessFilter; label: string; count: number }[] = [
    { key: "all",      label: "All",        count: docs.length },
    { key: "ready",    label: "Ready",      count: readyCnt },
    { key: "imported", label: "Processing", count: processingCnt },
    { key: "error",    label: "Error",      count: errorCnt },
  ];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <h3 className="text-xl font-serif font-medium">Source Material</h3>
        <div className="flex items-center gap-2">
          {docs.length > 5 && (
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <Input
                className="pl-8 h-8 text-xs w-48 font-mono"
                placeholder="Filter documents…"
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
              />
            </div>
          )}
          <Select value={docSort} onValueChange={(v) => setDocSort(v as DocSortKey)}>
            <SelectTrigger className="h-8 text-xs w-[110px] font-mono">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="date" className="text-xs font-mono">Newest</SelectItem>
              <SelectItem value="name" className="text-xs font-mono">Name A–Z</SelectItem>
              <SelectItem value="kind" className="text-xs font-mono">Kind</SelectItem>
              <SelectItem value="readiness" className="text-xs font-mono">Readiness</SelectItem>
            </SelectContent>
          </Select>
          <Button size="sm" variant="outline" className="gap-2" onClick={() => setOpen(true)}>
            <Plus className="w-4 h-4" /> Add Document
          </Button>
        </div>
      </div>

      {/* Readiness summary + filter pills — shown whenever there are docs */}
      {docs.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          {/* Summary line */}
          {hasNonReady ? (
            <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
              <span className="text-emerald-700 font-semibold">{readyCnt} ready</span>
              {processingCnt > 0 && (
                <>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="flex items-center gap-1 text-amber-600">
                    <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse inline-block" />
                    {processingCnt} processing
                  </span>
                </>
              )}
              {errorCnt > 0 && (
                <>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="text-red-600">{errorCnt} error{errorCnt !== 1 ? "s" : ""}</span>
                </>
              )}
            </div>
          ) : (
            <div className="text-[11px] font-mono text-emerald-700">
              {readyCnt} document{readyCnt !== 1 ? "s" : ""} ready
            </div>
          )}

          {/* Filter pills */}
          <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
            {READINESS_FILTERS.filter(({ count, key }) => key === "all" || count > 0).map(({ key, label, count }) => (
              <button
                key={key}
                onClick={() => setReadinessFilter(key)}
                className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                  readinessFilter === key
                    ? "bg-background text-foreground shadow-sm font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                } ${key === "error" && count > 0 ? "data-[active=false]:text-red-600" : ""}`}
              >
                {label}
                {count > 0 && <span className="ml-1 opacity-60">{count}</span>}
              </button>
            ))}
          </div>
        </div>
      )}

      {/* ── Version-relationship suggestions ──────────────────────────── */}
      {dupePairs.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium text-amber-700">
            <GitBranch className="w-4 h-4" />
            <span>Version relationships detected</span>
            <span className="text-[10px] font-mono text-amber-600/70 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
              {dupePairs.length} pair{dupePairs.length !== 1 ? "s" : ""}
            </span>
          </div>
          {dupePairs.map((pair: any) => (
            <div
              key={pair.id}
              className="bg-amber-50/60 border border-amber-200/80 rounded-lg p-3 space-y-2"
            >
              <p className="text-xs text-amber-800 leading-relaxed">
                <span className="font-semibold">{pair.doc_a_title || "Untitled"}</span>
                <span className="mx-1.5 text-amber-500">&amp;</span>
                <span className="font-semibold">{pair.doc_b_title || "Untitled"}</span>
                <span className="ml-1.5 text-amber-600/80">
                  ({pair.kind === "near_duplicate" ? "near duplicates" : "likely revisions"} · {Math.round(pair.similarity * 100)}% similar)
                </span>
              </p>
              <p className="text-[11px] text-amber-700/80">
                Declare one as canonical — the other will be marked as superseded.
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  disabled={resolvingDupe === pair.id}
                  onClick={() => handleDeclareCanonicaL(pair.id, pair.doc_a_id)}
                  className="text-[11px] px-2.5 py-1 rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 transition-colors disabled:opacity-50 font-mono"
                >
                  {pair.doc_a_title || "Doc A"} is canonical
                </button>
                <button
                  disabled={resolvingDupe === pair.id}
                  onClick={() => handleDeclareCanonicaL(pair.id, pair.doc_b_id)}
                  className="text-[11px] px-2.5 py-1 rounded border border-amber-300 bg-white text-amber-800 hover:bg-amber-100 transition-colors disabled:opacity-50 font-mono"
                >
                  {pair.doc_b_title || "Doc B"} is canonical
                </button>
                <button
                  onClick={() => setDismissedDupes((prev) => new Set([...prev, pair.id]))}
                  className="text-[11px] px-2 py-1 rounded text-amber-600/70 hover:text-amber-800 transition-colors font-mono"
                >
                  Dismiss
                </button>
                {resolvingDupe === pair.id && <Loader2 className="w-3.5 h-3.5 animate-spin text-amber-600" />}
              </div>
            </div>
          ))}
        </div>
      )}

      {filteredDocs.length > 0 ? (
        <div className="grid gap-3">
          {filteredDocs.map((doc) => {
            const isError = doc.readiness === "error" || doc.readiness === "no_text";
            const isProcessing = doc.readiness === "imported";
            return (
            <Card
              key={doc.id}
              className={`hover-elevate cursor-pointer group ${isError ? "border-red-200/60" : ""}`}
              onClick={() => navigate(`/library/${doc.id}`)}
            >
              <CardContent className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <FileText className={`w-5 h-5 ${isError ? "text-red-400" : "text-muted-foreground"}`} />
                  <div>
                    <h4 className="font-medium">{doc.title || doc.source || "Untitled"}</h4>
                    <div className="flex gap-2 mt-1 flex-wrap">
                      <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                      <Badge
                        variant="outline"
                        className={`text-[10px] uppercase font-mono ${
                          isError
                            ? "border-red-200 bg-red-50 text-red-700"
                            : isProcessing
                            ? "border-amber-200 bg-amber-50 text-amber-700"
                            : "border-emerald-200 bg-emerald-50 text-emerald-700"
                        }`}
                      >
                        {isProcessing && <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse mr-1 inline-block" />}
                        {doc.readiness}
                      </Badge>
                      {(doc as any).lifecycle === "canonical" && (
                        <span className="text-[10px] font-mono flex items-center gap-0.5 bg-amber-50 border border-amber-300 text-amber-800 rounded px-1.5 py-0.5">
                          <Star className="w-2.5 h-2.5" />canonical
                        </span>
                      )}
                      {(doc as any).lifecycle === "superseded" && (
                        <span className="text-[10px] font-mono bg-muted/50 border border-border text-muted-foreground rounded px-1.5 py-0.5 line-through">
                          superseded
                        </span>
                      )}
                      {(doc as any).lifecycle === "reference" && (
                        <span className="text-[10px] font-mono bg-blue-50 border border-blue-200 text-blue-700 rounded px-1.5 py-0.5">
                          reference
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <div className="text-xs font-mono text-muted-foreground">
                    {doc.created_at ? format(new Date(doc.created_at), "MMM d, yyyy") : ""}
                  </div>
                  {/* Retry button — visible on hover for error/no_text docs */}
                  {isError && (
                    <button
                      onClick={(e) => handleRetry(e, doc.id!)}
                      disabled={retrying === doc.id}
                      title="Retry extraction"
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded text-amber-600 hover:text-amber-700 hover:bg-amber-50 disabled:opacity-40"
                    >
                      {retrying === doc.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <RefreshCw className="w-3.5 h-3.5" />}
                    </button>
                  )}
                  <button
                    onClick={async (e) => {
                      e.stopPropagation();
                      const r = await apiFetch(`${DOC_BASE}/library/${doc.id}`, {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ work_id: null }),
                      });
                      if (r.ok) {
                        await Promise.all([
                          queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) }),
                          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) }),
                        ]);
                        toast.success("Document unlinked");
                      } else {
                        toast.error("Could not unlink document");
                      }
                    }}
                    title="Unlink from this work"
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded text-muted-foreground/50 hover:text-destructive hover:bg-destructive/5"
                  >
                    <Unlink className="w-3.5 h-3.5" />
                  </button>
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      ) : docFilter.trim() || readinessFilter !== "all" ? (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground text-sm">
            {docFilter.trim()
              ? `No documents match "${docFilter}".`
              : `No ${readinessFilter === "imported" ? "processing" : readinessFilter} documents.`}
          </p>
          <button
            onClick={() => { setDocFilter(""); setReadinessFilter("all"); }}
            className="text-xs text-primary underline mt-2"
          >
            Clear filter
          </button>
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No documents added to this work yet.</p>
          <Button size="sm" variant="outline" className="gap-2 mt-4" onClick={() => setOpen(true)}>
            <Plus className="w-4 h-4" /> Add from Library
          </Button>
        </div>
      )}

      {/* Document picker dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-serif">Link a Document</DialogTitle>
            <DialogDescription>
              Choose a document from your library to associate with this work.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-80 mt-2">
            {unlinked.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                No unlinked documents in your library.{" "}
                <Link href="/library" className="underline">Import one</Link> first.
              </p>
            ) : (
              <div className="space-y-2 pr-2">
                {unlinked.map((doc) => (
                  <button
                    key={doc.id}
                    disabled={linking}
                    onClick={() => handleLink(doc.id!)}
                    className="w-full text-left flex items-center gap-3 p-3 rounded-lg border border-border/50 hover:bg-muted/50 transition-colors disabled:opacity-50"
                  >
                    <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">{doc.title || doc.source || "Untitled"}</div>
                      <div className="flex gap-1.5 mt-0.5">
                        <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                        <Badge variant="outline" className="text-[10px] uppercase font-mono">{doc.readiness}</Badge>
                      </div>
                    </div>
                    {linking && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />}
                  </button>
                ))}
              </div>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Knowledge tab ────────────────────────────────────────────────────────────

const BASE_KN = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

async function setKnowledgeReview(itemId: string, status: string, force = false): Promise<void> {
  const resp = await apiFetch(`${BASE_KN}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    // force is required to deliberately flip an already-finalized decision;
    // without it the API rejects stale/concurrent overwrites with 409.
    body: JSON.stringify({ review_status: status, force }),
  });
  if (!resp.ok) throw new Error("Review update failed");
}

// ─── Rescore button ────────────────────────────────────────────────────────────

const WORKS_API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

function RescoreButton({ workId, onDone }: { workId: string; onDone: () => void }) {
  const [rescoring, setRescoring] = useState(false);

  const handleRescore = async () => {
    setRescoring(true);
    try {
      const r = await apiFetch(`${WORKS_API_BASE}/works/${workId}/evidence/rescore`, { method: "POST" });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `HTTP ${r.status}`);
      }
      const data = await r.json();
      const parts: string[] = [];
      if (data.rescored_count > 0) parts.push(`${data.rescored_count} score${data.rescored_count !== 1 ? "s" : ""} updated`);
      if (data.conflict_count > 0) parts.push(`${data.conflict_count} conflict${data.conflict_count !== 1 ? "s" : ""} found`);
      if (parts.length === 0) parts.push("No changes — scores are up to date");
      toast.success(parts.join(" · "), { duration: 4000 });
      onDone();
    } catch (e: any) {
      toast.error(e.message ?? "Could not rescore");
    } finally {
      setRescoring(false);
    }
  };

  return (
    <Button
      size="sm"
      variant="outline"
      className="gap-1.5 h-7 text-xs"
      onClick={handleRescore}
      disabled={rescoring}
      title="Re-score confidence on all knowledge items and detect contradictions"
    >
      {rescoring
        ? <><Loader2 className="w-3 h-3 animate-spin" /> Rescoring…</>
        : <><RefreshCw className="w-3 h-3" /> Rescore</>}
    </Button>
  );
}

type KnowledgeFilter = "all" | "pending" | "approved" | "rejected";
type KnowledgeKindFilter = "all" | "entity" | "claim" | "relationship" | "summary";
type KnowledgeConfFilter = "all" | "high" | "med" | "low";

function KnowledgeTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [filter, setFilter] = useState<KnowledgeFilter>("all");
  const [kindFilter, setKindFilter] = useState<KnowledgeKindFilter>("all");
  const [confFilter, setConfFilter] = useState<KnowledgeConfFilter>("all");
  const [searchText, setSearchText] = useState("");
  const [showAddForm, setShowAddForm] = useState(false);
  const [newKText, setNewKText] = useState("");
  const [newKKind, setNewKKind] = useState("claim");
  const [addingK, setAddingK] = useState(false);
  const WORK_API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
  // API search state — hooks must be unconditional, before any early return
  const [apiSearchResults, setApiSearchResults] = useState<any[]>([]);
  const [apiSearchLoading, setApiSearchLoading] = useState(false);
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const apiSeqRef = useRef(0); // monotonic counter to discard stale responses

  const deleteKnowledge = useDeleteKnowledgeItem();
  const { data: knowResp, isLoading } = useGetWorkKnowledge(workId, {}, {
    query: { enabled: !!workId, queryKey: getGetWorkKnowledgeQueryKey(workId, {}) },
  });
  const { data: docsResp } = useGetWorkDocuments(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkDocumentsQueryKey(workId) },
  });

  // Debounce the search input by 300 ms (matches library document knowledge tab behaviour)
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(searchText.trim()), 300);
    return () => clearTimeout(t);
  }, [searchText]);

  // Smart threshold search: when the Work has > KN_SEARCH_THRESHOLD items, send a
  // debounced API request to GET /api/knowledge/ask?work_id=... instead of filtering
  // in memory.  For ≤ threshold items, client-side filtering is fast enough.
  // Must be above any early return to satisfy React rules of hooks.
  const KN_SEARCH_THRESHOLD = 50;
  const allKnowledgeCount = knowResp?.knowledge?.length ?? 0;
  const useApiSearch = allKnowledgeCount > KN_SEARCH_THRESHOLD && debouncedSearch.length > 0;

  useEffect(() => {
    if (!useApiSearch) {
      setApiSearchResults([]);
      setApiSearchLoading(false);
      return;
    }
    // Claim a sequence slot so any in-flight older request cannot clobber us.
    const seq = ++apiSeqRef.current;
    const controller = new AbortController();
    setApiSearchLoading(true);
    const doSearch = async () => {
      try {
        const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
        const params = new URLSearchParams({ q: debouncedSearch, work_id: workId, limit: "50" });
        const r = await apiFetch(`${base}/knowledge/ask?${params}`, { signal: controller.signal });
        if (seq !== apiSeqRef.current) return; // a newer request has started — discard
        if (!r.ok) { setApiSearchLoading(false); return; }
        const d = await r.json();
        if (seq !== apiSeqRef.current) return;
        setApiSearchResults(d.knowledge ?? []);
      } catch {
        // aborted or network error — leave previous results visible
      } finally {
        if (seq === apiSeqRef.current) setApiSearchLoading(false);
      }
    };
    doSearch();
    return () => {
      controller.abort();
      if (seq === apiSeqRef.current) setApiSearchLoading(false);
    };
  }, [useApiSearch, debouncedSearch, workId]);

  // Build doc id → display name lookup
  const docNames: Record<string, string> = {};
  for (const d of docsResp?.documents ?? []) {
    if (d.id) {
      const src = (d as any).source ?? "";
      docNames[d.id] = d.title || src.split("/").pop() || d.id.slice(0, 8);
    }
  }

  const handleReview = async (itemId: string, status: "approved" | "rejected", force = false) => {
    setReviewing(itemId);
    try {
      await setKnowledgeReview(itemId, status, force);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing(null);
    }
  };

  const handleDeleteKnowledge = (itemId: string) => {
    if (!window.confirm("Delete this knowledge item?")) return;
    deleteKnowledge.mutate(
      { itemId },
      {
        onSuccess: () => {
          toast.success("Knowledge item deleted");
          queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
        },
        onError: () => toast.error("Could not delete item"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const allKnowledge = knowResp?.knowledge ?? [];
  const pendingCount = allKnowledge.filter((k) => k.review_status === "ai_auto").length;

  // Shared predicate functions so the same filters apply to both in-memory and API results
  const applyReviewFilter = (k: any) => {
    if (filter === "pending")  return k.review_status === "ai_auto";
    if (filter === "approved") return k.review_status === "approved";
    if (filter === "rejected") return k.review_status === "rejected";
    return true;
  };
  const applyKindFilter = (k: any) => {
    if (kindFilter === "all") return true;
    return (k.kind ?? "").toLowerCase() === kindFilter;
  };
  const applyConfFilter = (k: any) => {
    if (confFilter === "all") return true;
    // Compare against the unrounded confidence value (0–1) so the tier boundary
    // matches exactly what the badge and Search tab display: ≥0.80=High, ≥0.50=Med, <0.50=Low.
    // Using Math.round first would misclassify borderline values (e.g. 0.795 rounds
    // to 80 and enters the "High" bucket even though the badge shows "Med").
    const raw = k.confidence ?? 0;
    if (confFilter === "high") return raw >= 0.80;
    if (confFilter === "med")  return raw >= 0.50 && raw < 0.80;
    if (confFilter === "low")  return raw < 0.50;
    return true;
  };

  const reviewFiltered = allKnowledge
    .filter(applyReviewFilter)
    .filter(applyKindFilter)
    .filter(applyConfFilter);

  // Collect distinct kinds for the kind filter pills
  const availableKinds = Array.from(new Set(allKnowledge.map((k) => (k.kind ?? "").toLowerCase()))).filter(Boolean);

  // Apply the same review/kind/conf predicates to API results so active filters are respected
  const apiFiltered = apiSearchResults
    .filter(applyReviewFilter)
    .filter(applyKindFilter)
    .filter(applyConfFilter);

  const knowledge = searchText.trim()
    ? (useApiSearch
        ? apiFiltered           // server-side search, locally filtered
        : reviewFiltered.filter((k) => {
            const q = searchText.trim().toLowerCase();
            return (
              (k.text ?? "").toLowerCase().includes(q) ||
              ((k as any).subject ?? "").toLowerCase().includes(q) ||
              ((k as any).object ?? "").toLowerCase().includes(q) ||
              (k.kind ?? "").toLowerCase().includes(q)
            );
          }))
    : reviewFiltered;

  const FILTERS: { key: KnowledgeFilter; label: string }[] = [
    { key: "all",      label: `All (${allKnowledge.length})` },
    { key: "pending",  label: `AI Review${pendingCount > 0 ? ` (${pendingCount})` : ""}` },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Dismissed" },
  ];

  const KIND_LABELS: Record<string, string> = {
    entity: "Entity", claim: "Claim", relationship: "Relationship", summary: "Summary",
  };
  const KIND_FILTERS: { key: KnowledgeKindFilter; label: string }[] = [
    { key: "all", label: "All kinds" },
    ...availableKinds.map((k) => ({ key: k as KnowledgeKindFilter, label: KIND_LABELS[k] ?? k })),
  ];

  const CONF_FILTERS: { key: KnowledgeConfFilter; label: string }[] = [
    { key: "all",  label: "All" },
    { key: "high", label: "High ≥80%" },
    { key: "med",  label: "Med" },
    { key: "low",  label: "Low" },
  ];

  const handleAddKnowledge = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKText.trim() || addingK) return;
    setAddingK(true);
    try {
      const r = await apiFetch(`${WORK_API}/works/${workId}/knowledge`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newKText.trim(), kind: newKKind }),
      });
      if (!r.ok) throw new Error("Failed");
      setNewKText("");
      setShowAddForm(false);
      queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
      toast.success("Knowledge item added");
    } catch {
      toast.error("Could not add knowledge item");
    } finally {
      setAddingK(false);
    }
  };

  return (
    <div className="space-y-4">
      {showAddForm && (
        <form onSubmit={handleAddKnowledge} className="flex gap-2 p-3 rounded-lg border border-primary/20 bg-primary/[0.02]">
          <select
            value={newKKind}
            onChange={(e) => setNewKKind(e.target.value)}
            className="h-9 rounded-md border border-input bg-background px-2 text-xs font-mono text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring shrink-0"
          >
            {["claim", "entity", "relationship", "summary"].map((k) => (
              <option key={k} value={k}>{k}</option>
            ))}
          </select>
          <Input
            autoFocus
            placeholder="Enter knowledge statement…"
            value={newKText}
            onChange={(e) => setNewKText(e.target.value)}
            className="flex-1 bg-background/50 text-sm"
          />
          <Button type="submit" size="sm" disabled={!newKText.trim() || addingK}>
            {addingK ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Add"}
          </Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setShowAddForm(false)}>
            <X className="w-3.5 h-3.5" />
          </Button>
        </form>
      )}
      <div className="flex items-start justify-between flex-wrap gap-3">
        <h3 className="text-xl font-serif font-medium">Structured Knowledge</h3>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          <RescoreButton workId={workId} onDone={() => queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) })} />
          <Button size="sm" variant="outline" className="gap-1.5 h-7 text-xs" onClick={() => setShowAddForm((v) => !v)}>
            <Plus className="w-3 h-3" /> Add manually
          </Button>
          {allKnowledge.length > 10 && (
            <div className="relative flex items-center">
              {apiSearchLoading
                ? <Loader2 className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground animate-spin" />
                : <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />}
              <Input
                className="pl-8 pr-8 h-8 text-xs w-52 font-mono"
                placeholder={allKnowledge.length > KN_SEARCH_THRESHOLD ? "Search knowledge…" : "Filter knowledge…"}
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
              />
              {searchText && (
                <button
                  onClick={() => { setSearchText(""); setApiSearchResults([]); }}
                  className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground transition-colors"
                  title="Clear search"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
              {useApiSearch && !apiSearchLoading && (
                <span className="absolute -top-1.5 right-0 text-[9px] font-mono font-semibold text-primary/70 bg-primary/10 border border-primary/20 rounded px-1 leading-tight">
                  API
                </span>
              )}
            </div>
          )}
          {allKnowledge.length > 0 && (
            <div className="flex items-center gap-2 flex-wrap justify-end">
              {/* Kind filter */}
              {availableKinds.length > 1 && (
                <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
                  {KIND_FILTERS.map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => setKindFilter(key)}
                      className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                        kindFilter === key
                          ? "bg-background text-foreground shadow-sm font-semibold"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {/* Confidence filter */}
              {allKnowledge.some((k) => k.confidence !== null && k.confidence !== undefined) && (
                <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
                  {CONF_FILTERS.map(({ key, label }) => (
                    <button
                      key={key}
                      onClick={() => setConfFilter(key)}
                      className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                        confFilter === key
                          ? "bg-background text-foreground shadow-sm font-semibold"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              )}
              {/* Review status filter */}
              <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
                {FILTERS.map(({ key, label }) => (
                  <button
                    key={key}
                    onClick={() => setFilter(key)}
                    className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                      filter === key
                        ? "bg-background text-foreground shadow-sm font-semibold"
                        : "text-muted-foreground hover:text-foreground"
                    } ${key === "pending" && pendingCount > 0 ? "text-violet-700" : ""}`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {knowledge.length > 0 ? (
        <div className="grid gap-3">
          {knowledge.map((item) => {
            const isAI = item.review_status === "ai_auto";
            const isApproved = item.review_status === "approved";
            const isRejected = item.review_status === "rejected";
            const isReviewing = reviewing === item.id;
            return (
            <Card key={item.id} className={`transition-opacity ${isRejected ? "opacity-50" : ""}`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">
                        {item.kind}
                      </Badge>
                      {item.review_status === "ai_auto" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-violet-200 bg-violet-50 text-violet-700">
                          <Sparkles className="w-2.5 h-2.5" /> AI
                        </span>
                      ) : item.review_status === "approved" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-emerald-200 bg-emerald-50 text-emerald-700">
                          ✓ approved
                        </span>
                      ) : item.review_status === "rejected" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-red-200 bg-red-50 text-red-700">
                          ✕ rejected
                        </span>
                      ) : (
                        <Badge variant="secondary" className="text-[10px] uppercase font-mono">
                          {item.review_status}
                        </Badge>
                      )}
                    </div>
                    {item.subject && item.predicate && item.object ? (
                      <div className="font-mono text-sm bg-muted/30 p-2 rounded border border-border/50">
                        <span className="font-semibold text-primary">{item.subject}</span>{" "}
                        <span className="text-muted-foreground">{item.predicate}</span>{" "}
                        <span className="font-semibold">{item.object}</span>
                      </div>
                    ) : (
                      <p className="text-sm font-serif leading-relaxed">{item.text}</p>
                    )}
                    {(item as any).source_doc_id && (
                      <a
                        href={`/library/${(item as any).source_doc_id}`}
                        onClick={(e) => { e.stopPropagation(); navigate(`/library/${(item as any).source_doc_id}`); e.preventDefault(); }}
                        className="text-[10px] font-mono text-muted-foreground/70 hover:text-primary mt-1.5 inline-block transition-colors"
                      >
                        ↗ {docNames[(item as any).source_doc_id] ?? "source doc"}
                      </a>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {item.confidence !== undefined && item.confidence !== null && (() => {
                      const pct = item.confidence * 100;
                      const tier =
                        pct >= 80 ? { label: "High", color: "text-emerald-700 bg-emerald-50 border-emerald-200" }
                        : pct >= 50 ? { label: "Med", color: "text-amber-700 bg-amber-50 border-amber-200" }
                        : { label: "Low", color: "text-red-700 bg-red-50 border-red-200" };
                      return (
                        <div className="flex flex-col items-end gap-0.5" title={`Confidence: ${pct.toFixed(1)}% (estimated) — ${tier.label === "High" ? "Well-evidenced: corroborated by multiple sources, recent, reviewed" : tier.label === "Med" ? "Partially evidenced: some corroboration or review present" : "Thin evidence: single source, unreviewed, or old — verify before relying on this"}`}>
                          <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${tier.color}`}>
                            {pct.toFixed(0)}% {tier.label}
                          </span>
                          <div className="w-12 h-1 rounded-full bg-muted overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })()}
                    {(isAI || isApproved || isRejected) && (
                      <>
                        <button
                          disabled={isReviewing || isApproved}
                          onClick={() => handleReview(item.id!, "approved", isRejected)}
                          title="Approve"
                          className={`p-1.5 rounded transition-colors ${
                            isApproved
                              ? "text-emerald-600 bg-emerald-50"
                              : "text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50"
                          } disabled:opacity-40`}
                        >
                          <ThumbsUp className="w-3.5 h-3.5" />
                        </button>
                        <button
                          disabled={isReviewing || isRejected}
                          onClick={() => handleReview(item.id!, "rejected", isApproved)}
                          title="Dismiss"
                          className={`p-1.5 rounded transition-colors ${
                            isRejected
                              ? "text-red-600 bg-red-50"
                              : "text-muted-foreground hover:text-red-600 hover:bg-red-50"
                          } disabled:opacity-40`}
                        >
                          <ThumbsDown className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => handleDeleteKnowledge(item.id!)}
                      title="Delete item"
                      className="p-1.5 rounded text-muted-foreground/40 hover:text-destructive hover:bg-destructive/5 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      ) : searchText.trim() ? (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground text-sm">No knowledge items match "{searchText}".</p>
          <button onClick={() => setSearchText("")} className="text-xs text-primary underline mt-2">Clear filter</button>
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No knowledge extracted yet.</p>
          <p className="text-xs text-muted-foreground mt-1">
            Link a document and Orivellum will extract concepts, facts, and excerpts automatically.
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Tasks tab ────────────────────────────────────────────────────────────────

function TasksTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const { data: tasksResp, isLoading } = useGetWorkTasks(workId, {}, {
    query: { enabled: !!workId, queryKey: getGetWorkTasksQueryKey(workId) },
  });
  const createTask = useCreateWorkTask();
  const updateTask = useUpdateWorkTask();
  const [newTaskText, setNewTaskText] = useState("");
  const [newTaskPriority, setNewTaskPriority] = useState<number>(0);
  const [editingTaskId, setEditingTaskId] = useState<string | null>(null);
  const [editTaskText, setEditTaskText] = useState("");

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskText.trim()) return;
    createTask.mutate(
      { workId, data: { text: newTaskText, priority: newTaskPriority || undefined } },
      {
        onSuccess: () => {
          setNewTaskText("");
          setNewTaskPriority(0);
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          toast.success("Task added");
        },
        onError: () => toast.error("Could not add task"),
      }
    );
  };

  const handleStartEdit = (task: { id?: string; text?: string }) => {
    setEditingTaskId(task.id ?? null);
    setEditTaskText(task.text ?? "");
  };

  const handleSaveEdit = (taskId: string) => {
    const trimmed = editTaskText.trim();
    setEditingTaskId(null);
    if (!trimmed) return;
    updateTask.mutate(
      { workId, taskId, data: { text: trimmed } },
      {
        onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) }),
        onError: () => toast.error("Could not update task"),
      }
    );
  };

  const handleChangePriority = (taskId: string, current: number) => {
    const next = ((current ?? 0) + 1) % 4;
    updateTask.mutate(
      { workId, taskId, data: { priority: next } },
      {
        onSuccess: () => queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) }),
        onError: () => toast.error("Could not update priority"),
      }
    );
  };

  const handleToggle = (taskId: string, current: string) => {
    const next = current === "completed" ? "pending" : "completed";
    updateTask.mutate(
      { workId, taskId, data: { status: next } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
        },
        onError: () => toast.error("Could not update task"),
      }
    );
  };

  const [taskFilter, setTaskFilter] = useState<"all" | "pending" | "completed">("all");

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const allTasks = tasksResp?.tasks ?? [];
  const tasks = taskFilter === "all" ? allTasks : allTasks.filter((t) => t.status === taskFilter);

  return (
    <div className="space-y-6 max-w-3xl">
      {/* Filter chips */}
      <div className="flex items-center gap-2">
        {(["all", "pending", "completed"] as const).map((f) => (
          <button
            key={f}
            onClick={() => setTaskFilter(f)}
            className={`px-3 py-1 rounded-full text-xs font-mono uppercase tracking-wider border transition-colors ${
              taskFilter === f
                ? "bg-primary text-primary-foreground border-primary"
                : "bg-transparent text-muted-foreground border-border hover:border-primary/50"
            }`}
          >
            {f === "all" ? `All (${allTasks.length})` : f === "pending" ? `Pending (${allTasks.filter((t) => t.status !== "completed").length})` : `Done (${allTasks.filter((t) => t.status === "completed").length})`}
          </button>
        ))}
      </div>

      <form onSubmit={handleAdd} className="flex gap-2">
        <Input
          placeholder="Add a new task…"
          value={newTaskText}
          onChange={(e) => setNewTaskText(e.target.value)}
          className="bg-background/50 flex-1"
        />
        <select
          value={newTaskPriority}
          onChange={(e) => setNewTaskPriority(Number(e.target.value))}
          className="h-9 rounded-md border border-input bg-background px-2 text-xs font-mono text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring"
          title="Priority (0 = none, higher = more urgent)"
        >
          <option value={0}>P0</option>
          <option value={1}>P1</option>
          <option value={2}>P2</option>
          <option value={3}>P3</option>
        </select>
        <Button type="submit" disabled={!newTaskText.trim() || createTask.isPending}>
          {createTask.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add"}
        </Button>
      </form>

      <div className="space-y-2">
        {tasks.length > 0 ? (
          tasks.map((task) => (
            <div
              key={task.id}
              className="flex items-start gap-3 p-3 rounded-lg hover:bg-muted/30 transition-colors group border border-transparent hover:border-border/50"
            >
              <Checkbox
                id={task.id}
                className="mt-1"
                checked={task.status === "completed"}
                onCheckedChange={() => handleToggle(task.id!, task.status ?? "pending")}
                disabled={updateTask.isPending}
              />
              <div className="flex-1 space-y-1">
                {editingTaskId === task.id ? (
                  <input
                    autoFocus
                    className="w-full text-sm bg-transparent border-b border-primary outline-none pb-0.5"
                    value={editTaskText}
                    onChange={(e) => setEditTaskText(e.target.value)}
                    onBlur={() => handleSaveEdit(task.id!)}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") handleSaveEdit(task.id!);
                      if (e.key === "Escape") setEditingTaskId(null);
                    }}
                  />
                ) : (
                  <label
                    htmlFor={task.id}
                    className={`text-sm font-medium leading-none cursor-pointer ${
                      task.status === "completed" ? "line-through text-muted-foreground" : ""
                    }`}
                    onDoubleClick={() => handleStartEdit(task)}
                    title="Double-click to edit"
                  >
                    {task.text}
                  </label>
                )}
              </div>
              <button
                onClick={() => handleChangePriority(task.id!, task.priority ?? 0)}
                className="opacity-0 group-hover:opacity-100 transition-opacity"
                title="Click to cycle priority"
              >
                <Badge
                  variant="outline"
                  className={`text-[9px] uppercase font-mono cursor-pointer hover:bg-primary/10 ${
                    task.priority === 1 ? "border-red-400 text-red-600" :
                    task.priority === 2 ? "border-amber-400 text-amber-600" :
                    task.priority === 3 ? "border-blue-400 text-blue-600" : ""
                  }`}
                >
                  P{task.priority || 0}
                </Badge>
              </button>
              <button
                onClick={() => {
                  if (!task.id) return;
                  apiFetch(`${WORK_API_BASE}/works/${workId}/tasks/${task.id}`, { method: "DELETE" })
                    .then(() => {
                      queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
                      queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
                    })
                    .catch(() => toast.error("Could not delete task"));
                }}
                className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:bg-destructive/10 hover:text-destructive text-muted-foreground"
                title="Delete task"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground italic">No tasks yet for this work.</p>
        )}
      </div>
    </div>
  );
}

// ─── Generate menu ────────────────────────────────────────────────────────────

const GEN_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type GenFormat = "excel" | "report-pdf" | "report-docx" | "slides" | "bundle";

interface GenerationResult {
  ok: boolean;
  doc_id: string;
  filename: string;
  path: string;
  download_url: string;
}

function GenerateMenu({ workId }: { workId: string }) {
  const [busy, setBusy] = useState<GenFormat | null>(null);
  const [result, setResult] = useState<GenerationResult | null>(null);
  // Accumulate every generated path so Bundle can zip them all
  const [generatedPaths, setGeneratedPaths] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const generate = async (format: GenFormat) => {
    // Snapshot accumulated paths BEFORE clearing state — Bundle needs them
    const pathsSnapshot = generatedPaths.slice();
    setBusy(format);
    setError(null);
    setResult(null);
    try {
      let resp: Response;
      if (format === "excel") {
        resp = await apiFetch(`${GEN_BASE}/generate/excel`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId }),
        });
      } else if (format === "slides") {
        resp = await apiFetch(`${GEN_BASE}/generate/slides`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId }),
        });
      } else if (format === "bundle") {
        if (pathsSnapshot.length === 0) {
          throw new Error("Generate at least one format first, then Bundle will zip them all.");
        }
        resp = await apiFetch(`${GEN_BASE}/generate/bundle`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            work_id: workId,
            paths: pathsSnapshot,
            name: "bundle",
          }),
        });
      } else {
        // PDF or DOCX report
        const fmt = format === "report-pdf" ? "pdf" : "docx";
        resp = await apiFetch(`${GEN_BASE}/generate/report`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ work_id: workId, format: fmt }),
        });
      }
      if (!resp.ok) {
        const body = await resp.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `Generation failed (${resp.status})`);
      }
      const data: GenerationResult = await resp.json();
      setResult(data);
      // Accumulate path (deduplicated) so Bundle can zip all outputs for this session
      if (format !== "bundle") {
        setGeneratedPaths((prev) => prev.includes(data.path) ? prev : [...prev, data.path]);
      }
      toast.success(`${data.filename} ready`, {
        description: "Click Download to save the file.",
        duration: 8000,
        action: {
          label: "Download",
          onClick: () => {
            const a = document.createElement("a");
            a.href = `${GEN_BASE}/generate/download?path=${encodeURIComponent(data.path)}`;
            a.download = data.filename;
            a.click();
          },
        },
      });
    } catch (e: any) {
      setError(e.message ?? "Generation failed");
      toast.error(e.message ?? "Generation failed");
    } finally {
      setBusy(null);
    }
  };

  const downloadResult = () => {
    if (!result) return;
    const a = document.createElement("a");
    a.href = `${GEN_BASE}/generate/download?path=${encodeURIComponent(result.path)}`;
    a.download = result.filename;
    a.click();
  };

  const formats: { id: GenFormat; label: string; icon: React.ElementType; desc: string }[] = [
    { id: "excel",      label: "Excel Workbook",  icon: FileSpreadsheet, desc: "Knowledge, docs & tasks as .xlsx" },
    { id: "report-pdf", label: "Report (PDF)",    icon: FileType,        desc: "Research report as .pdf" },
    { id: "report-docx",label: "Report (Word)",   icon: FileText,        desc: "Research report as .docx" },
    { id: "slides",     label: "Slide Deck",      icon: Presentation,    desc: "Knowledge slides as .pptx" },
    { id: "bundle",     label: "Bundle (ZIP)",    icon: Package,         desc: "Zip all generated outputs" },
  ];

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button
          size="sm"
          variant="outline"
          className="gap-1.5 text-xs border-emerald-600/30 text-emerald-700 hover:bg-emerald-50 dark:text-emerald-400 dark:border-emerald-400/30 dark:hover:bg-emerald-900/20"
          disabled={!!busy}
        >
          {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
          Generate
          {!busy && <ChevronDown className="w-3 h-3 opacity-60" />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
          Export formats
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        {formats.map(({ id, label, icon: Icon, desc }) => (
          <DropdownMenuItem
            key={id}
            onClick={() => generate(id)}
            disabled={!!busy}
            className="gap-2 cursor-pointer"
          >
            {busy === id
              ? <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
              : <Icon className="w-4 h-4 text-muted-foreground" />}
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium">{label}</div>
              <div className="text-[11px] text-muted-foreground truncate">{desc}</div>
            </div>
          </DropdownMenuItem>
        ))}
        {result && (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuItem onClick={downloadResult} className="gap-2 text-emerald-700 dark:text-emerald-400 cursor-pointer">
              <Download className="w-4 h-4" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium">Download last output</div>
                <div className="text-[11px] truncate text-muted-foreground">{result.filename}</div>
              </div>
            </DropdownMenuItem>
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

// ─── Quick chat button (header shortcut) ──────────────────────────────────────

function QuickChatButton({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const createConv = useCreateConversation();

  const handleClick = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  return (
    <button
      onClick={handleClick}
      disabled={createConv.isPending}
      title="Start a new discussion about this work"
      className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-primary transition-colors px-2 py-1 rounded hover:bg-primary/5"
    >
      {createConv.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageSquarePlus className="w-3.5 h-3.5" />}
      Chat
    </button>
  );
}

// ─── Conversations tab ────────────────────────────────────────────────────────

function ConversationsTab({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const { data: convResp, isLoading } = useGetWorkConversations(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkConversationsQueryKey(workId) },
  });
  const createConv = useCreateConversation();

  const handleNewDiscussion = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const conversations = convResp?.conversations ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Conversations</h3>
        <Button
          size="sm"
          variant="outline"
          className="gap-2"
          onClick={handleNewDiscussion}
          disabled={createConv.isPending}
        >
          {createConv.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Plus className="w-4 h-4" />
          )}
          New Discussion
        </Button>
      </div>

      {conversations.length > 0 ? (
        <div className="grid gap-3">
          {conversations.map((conv) => (
            <Link key={conv.id} href={`/chat?id=${conv.id}`}>
              <Card className="hover-elevate cursor-pointer">
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="space-y-1">
                    <h4 className="font-medium text-lg">{conv.title || "Untitled Conversation"}</h4>
                    <p className="text-sm text-muted-foreground truncate max-w-xl">
                      {conv.last_message || "No messages yet."}
                    </p>
                  </div>
                  <div className="text-right text-xs font-mono text-muted-foreground space-y-1 shrink-0">
                    <div>{conv.message_count || 0} msgs</div>
                    <div>{conv.updated_at ? format(new Date(conv.updated_at), "MMM d") : ""}</div>
                    {(conv as any).model && (
                      <div className="text-[10px] opacity-60 font-mono">
                        {String((conv as any).model).split("/").pop()?.split("-").slice(0, 3).join("-")}
                      </div>
                    )}
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <MessageSquare className="w-8 h-8 mx-auto mb-3 opacity-20" />
          <p className="text-muted-foreground">No conversations linked to this work.</p>
          <Button size="sm" variant="outline" className="gap-2 mt-4" onClick={handleNewDiscussion} disabled={createConv.isPending}>
            <Plus className="w-4 h-4" /> Start a Discussion
          </Button>
        </div>
      )}
    </div>
  );
}

// ─── Search tab ───────────────────────────────────────────────────────────────

function SearchTab({ workId, initialQuery = "" }: { workId: string; initialQuery?: string }) {
  const [, navigate] = useLocation();
  const [query, setQuery] = useState(initialQuery);
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<{ knowledge: any[]; chunks: any[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);
  // Monotonic counter so a slow earlier response can't clobber a newer one.
  const searchSeq = useRef(0);

  // Embeddings circuit-breaker status (#203) — no network call, just reads in-process state
  const { data: embedStatus } = useGetEmbeddingsStatus({
    query: { queryKey: getGetEmbeddingsStatusQueryKey(), staleTime: 30_000, refetchInterval: 30_000 },
  });

  // Focus the input when the tab is first activated (mount). Deferred so it
  // runs after Radix Tabs' own focus management (which focuses the trigger).
  useEffect(() => {
    const t = setTimeout(() => inputRef.current?.focus(), 50);
    return () => clearTimeout(t);
  }, []);

  const runSearch = async (q: string) => {
    const seq = ++searchSeq.current;
    setSubmitted(q);
    setLoading(true);
    setError(null);
    try {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const res = await apiFetch(`${base}/works/${workId}/search?q=${encodeURIComponent(q)}&limit=20`);
      if (!res.ok) throw new Error(`Search failed: ${res.status}`);
      const data = await res.json();
      if (seq === searchSeq.current) setResults(data);
    } catch (err: any) {
      if (seq === searchSeq.current) setError(err.message ?? "Search failed");
    } finally {
      if (seq === searchSeq.current) setLoading(false);
    }
  };

  // Search-as-you-type with a 350 ms debounce.
  useEffect(() => {
    const q = query.trim();
    if (!q) {
      // Cleared input → drop results and invalidate any in-flight request.
      searchSeq.current++;
      setResults(null);
      setSubmitted("");
      setError(null);
      setLoading(false);
      return;
    }
    if (q === submitted) return; // already showing results for this query
    const t = setTimeout(() => runSearch(q), 350);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [query]);

  const handleSearch = (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = query.trim();
    if (!q) return;
    runSearch(q); // explicit re-submission bypasses the debounce
  };

  const total = (results?.knowledge.length ?? 0) + (results?.chunks.length ?? 0);

  return (
    <div className="space-y-6">
      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            ref={inputRef}
            className="pl-9 font-mono text-sm"
            placeholder="Search knowledge and documents…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            autoFocus
          />
        </div>
        <Button type="submit" disabled={!query.trim() || loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
      </form>

      {/* Semantic search readiness banner (#203) */}
      {embedStatus?.circuit_open && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-md border border-amber-200 bg-amber-50 text-amber-800 text-xs font-mono">
          <Search className="w-3.5 h-3.5 shrink-0" />
          <span>Semantic search is temporarily unavailable — showing keyword results only. The embeddings service will retry automatically.</span>
        </div>
      )}

      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}

      {results && (
        <div className="space-y-8">
          <p className="text-xs font-mono text-muted-foreground">
            {total} result{total !== 1 ? "s" : ""} for <span className="text-foreground">"{submitted}"</span>
          </p>

          {/* Knowledge hits */}
          {results.knowledge.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <Network className="w-3.5 h-3.5" /> Knowledge ({results.knowledge.length})
              </h3>
              <div className="space-y-2">
                {results.knowledge.map((item: any) => (
                  <Card key={item.id} className="p-3">
                    <div className="flex items-start gap-3">
                      <Badge variant="secondary" className="text-[10px] shrink-0 mt-0.5">
                        {item.kind ?? "fact"}
                      </Badge>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm">{item.text}</p>
                        {item.subject && (
                          <p className="text-xs font-mono text-muted-foreground mt-1">
                            {item.subject}
                            {item.relation && <> · {item.relation}</>}
                            {item.object && <> · {item.object}</>}
                          </p>
                        )}
                      </div>
                      {item.confidence != null && (() => {
                        const pct = item.confidence * 100;
                        const tier =
                          pct >= 80 ? { label: "High", color: "text-emerald-700 bg-emerald-50 border-emerald-200" }
                          : pct >= 50 ? { label: "Med", color: "text-amber-700 bg-amber-50 border-amber-200" }
                          : { label: "Low", color: "text-red-700 bg-red-50 border-red-200" };
                        return (
                          <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border shrink-0 ${tier.color}`}>
                            {pct.toFixed(0)}% {tier.label}
                          </span>
                        );
                      })()}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Document chunk hits */}
          {results.chunks.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <FileText className="w-3.5 h-3.5" /> Documents ({results.chunks.length})
              </h3>
              <div className="space-y-2">
                {results.chunks.map((chunk: any) => (
                  <Card
                    key={chunk.id}
                    className="p-3 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => navigate(`/library/${chunk.doc_id}`)}
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium truncate">{chunk.doc_title ?? chunk.doc_id}</span>
                        {chunk.doc_kind && (
                          <Badge variant="outline" className="text-[10px]">{chunk.doc_kind}</Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground line-clamp-3">{chunk.text}</p>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {total === 0 && (
            <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
              <Search className="w-8 h-8 mx-auto mb-3 opacity-20" />
              <p className="text-muted-foreground text-sm">No results found for "{submitted}"</p>
              <p className="text-xs text-muted-foreground mt-1">Try different keywords or check that documents have been fully extracted.</p>
            </div>
          )}
        </div>
      )}

      {!results && !loading && (
        <div className="text-center py-16 text-muted-foreground">
          <Search className="w-10 h-10 mx-auto mb-4 opacity-15" />
          <p className="text-sm">Search across all knowledge items and document text in this Work.</p>
        </div>
      )}
    </div>
  );
}

// ─── Learn tab (adaptive Socratic study) ─────────────────────────────────────

const API_BASE_WORKS = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type LearnPhase = "loading" | "seeding" | "question" | "assessing" | "feedback" | "all_done";
type RouteAction = "STEP_FORWARD" | "STEP_BACKWARD" | "STAY_HERE";

interface LearningSession {
  concept_id: string;
  subject: string;
  description: string;
  question: string;
  context_snippet: string;
}

interface AssessResult {
  score: number;
  feedback: string;
  route: RouteAction;
  graduated: boolean;
  next_concept_id: string | null;
  summary: { total: number; graduated: number; mastery_pct: number };
}

// ─── Gaps tab ────────────────────────────────────────────────────────────────

interface GapItem {
  kind: string; title: string; description: string; severity: string;
  metadata: Record<string, unknown>;
}
interface GapReport {
  coverage_pct: number; total_chapters: number;
  gaps: GapItem[]; suggested_queries: string[]; evaluated_at: string;
}

const GAP_SEVERITY_STYLE: Record<string, string> = {
  high:   "border-red-200 bg-red-50/40 text-red-900",
  medium: "border-amber-200 bg-amber-50/40 text-amber-900",
  low:    "border-blue-200 bg-blue-50/40 text-blue-900",
};
const GAP_DOT: Record<string, string> = {
  high: "bg-red-500", medium: "bg-amber-400", low: "bg-blue-400",
};

function GapsTab({ workId, onBrainstorm }: { workId: string; onBrainstorm?: (seed: string) => void }) {
  const [, navigate] = useLocation();
  const [actionPending, setActionPending] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const createTask = useCreateWorkTask();

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
            className={`h-full rounded-full transition-all duration-500 ${
              data.coverage_pct >= 80 ? "bg-emerald-500" :
              data.coverage_pct >= 50 ? "bg-amber-400" : "bg-red-400"
            }`}
            style={{ width: `${data.coverage_pct}%` }}
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
                  <span className={`w-2 h-2 rounded-full ${GAP_DOT[sev]}`} />
                  {sev} priority ({items.length})
                </h4>
                {items.map((g, i) => {
                  const chapTitle = (g.metadata.chapter_title as string | undefined) ?? g.title;
                  const docId     = g.metadata.doc_id as string | undefined;
                  const isResearchPending = actionPending === chapTitle;
                  const isExtractPending  = actionPending === docId;
                  return (
                    <div key={i} className={`p-3.5 rounded-lg border ${GAP_SEVERITY_STYLE[sev]}`}>
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
                            className="flex items-center gap-1.5 text-[11px] font-mono text-violet-500 opacity-80 hover:opacity-100 transition-opacity"
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
          <div className="flex flex-wrap gap-2">
            {data.suggested_queries.map((q, i) => (
              <button
                key={i}
                onClick={() => onBrainstorm?.(q)}
                className="px-3 py-1.5 rounded-full text-xs font-mono border border-border/60 bg-muted/20 text-muted-foreground hover:text-foreground hover:border-primary/40 hover:bg-primary/5 transition-colors cursor-pointer flex items-center gap-1.5 group"
                title="Brainstorm this query"
              >
                <Lightbulb className="w-3 h-3 opacity-50 group-hover:opacity-100 group-hover:text-primary transition-colors" />
                {q}
              </button>
            ))}
          </div>
          {onBrainstorm && (
            <p className="text-[10px] text-muted-foreground/60 font-mono">Tap a query to brainstorm it in the Ideas tab</p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Completeness tab ─────────────────────────────────────────────────────────

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

function CompletenessTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch, isFetching } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/completeness`).then((r) => {
        if (!r.ok) throw new Error("completeness fetch failed");
        return r.json();
      }),
    staleTime: 60_000,
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

function LearnTab({ workId }: { workId: string }) {
  const [phase, setPhase]       = useState<LearnPhase>("loading");
  const [session, setSession]   = useState<LearningSession | null>(null);
  const [answer, setAnswer]     = useState("");
  const [result, setResult]     = useState<AssessResult | null>(null);
  const [summary, setSummary]   = useState<{ total: number; graduated: number; mastery_pct: number } | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [showConcepts, setShowConcepts] = useState(false);
  const [concepts, setConcepts] = useState<any[]>([]);
  const [resettingConcept, setResettingConcept] = useState<string | null>(null);

  const apiBase = API_BASE_WORKS;

  const loadSummary = async () => {
    const r = await apiFetch(`${apiBase}/works/${workId}/learning/summary`);
    if (!r.ok) throw new Error("Could not load learning summary");
    return r.json();
  };

  const startOrContinue = async (conceptId?: string | null) => {
    setError(null);
    setAnswer("");
    setResult(null);
    setPhase("question");
    try {
      const url = conceptId
        ? `${apiBase}/works/${workId}/learning/question?concept_id=${conceptId}`
        : `${apiBase}/works/${workId}/learning/question`;
      const r = await apiFetch(url);
      if (r.status === 422) {
        setPhase("all_done");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSession({
        concept_id:      data.concept_id,
        subject:         data.subject ?? "Concept",
        description:     data.description ?? "",
        question:        data.question,
        context_snippet: data.context_snippet ?? "",
      });
    } catch (e: any) {
      setError(e.message ?? "Could not load question");
      setPhase("feedback");
    }
  };

  const init = async () => {
    setPhase("loading");
    setError(null);
    try {
      const data = await loadSummary();
      setSummary(data);
      setConcepts(data.concepts ?? []);
      if (data.total === 0) {
        // Auto-seed
        setPhase("seeding");
        const sr = await apiFetch(`${apiBase}/works/${workId}/learning/seed`, { method: "POST" });
        if (!sr.ok) throw new Error("Could not seed concepts");
        const sd = await sr.json();
        if ((sd.concepts ?? []).length === 0) {
          setError("No knowledge items found. Import and process documents first.");
          setPhase("feedback");
          return;
        }
        const sumData = await loadSummary();
        setSummary(sumData);
        setConcepts(sumData.concepts ?? []);
      }
      if (data.mastery_pct === 100 && data.total > 0) {
        setPhase("all_done");
        return;
      }
      await startOrContinue(null);
    } catch (e: any) {
      setError(e.message ?? "Could not initialise learning");
      setPhase("feedback");
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { let active = true; void init().then(() => {}).catch(() => {}); return () => { active = false; }; }, [workId]);

  const submitAnswer = async () => {
    if (!session || !answer.trim()) return;
    setPhase("assessing");
    setError(null);
    try {
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/assess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id: session.concept_id,
          question:   session.question,
          answer:     answer.trim(),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AssessResult = await r.json();
      setResult(data);
      setSummary(data.summary);
      setPhase("feedback");
    } catch (e: any) {
      setError(e.message ?? "Could not assess answer");
      setPhase("feedback");
    }
  };

  const next = async () => {
    if (!result) { await startOrContinue(null); return; }
    if (result.summary.mastery_pct === 100) { setPhase("all_done"); return; }
    await startOrContinue(result.next_concept_id);
  };

  const resetConcept = async (conceptId: string, subject: string) => {
    if (!confirm(`Reset the mastery streak for "${subject}"? It will re-enter the study queue.`)) return;
    setResettingConcept(conceptId);
    try {
      const r = await apiFetch(`${apiBase}/works/${workId}/learning/concepts/${conceptId}/reset`, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      // Refresh concept list in-place
      const sumR = await apiFetch(`${apiBase}/works/${workId}/learning/summary`);
      if (sumR.ok) {
        const d = await sumR.json();
        setSummary(d);
        setConcepts(d.concepts ?? []);
      }
      toast.success("Streak reset — concept re-enters the study queue");
    } catch {
      toast.error("Could not reset concept streak");
    } finally {
      setResettingConcept(null);
    }
  };

  const routeLabel: Record<RouteAction, string> = {
    STEP_FORWARD:  "Great — moving to the next concept",
    STEP_BACKWARD: "Let's revisit a foundational concept first",
    STAY_HERE:     "Keep practising this concept",
  };

  // ── Mastery bar ────────────────────────────────────────────────────────────
  const MasteryBar = () => {
    if (!summary) return null;
    const pct = summary.mastery_pct;
    return (
      <div className="space-y-1.5 mb-6">
        <div className="flex items-center justify-between text-xs font-mono text-muted-foreground">
          <span>{summary.graduated}/{summary.total} concepts graduated</span>
          <span className="font-semibold text-foreground">{pct}%</span>
        </div>
        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-700"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    );
  };

  // ── All done ───────────────────────────────────────────────────────────────
  if (phase === "all_done") {
    const handleReset = async () => {
      try {
        await apiFetch(`${apiBase}/works/${workId}/learning/reset`, { method: "POST" });
        await init();
      } catch {/* init handles errors */}
    };
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-6">
        <div className="w-16 h-16 rounded-2xl bg-emerald-500/10 flex items-center justify-center">
          <Trophy className="w-8 h-8 text-emerald-500" />
        </div>
        <div className="text-center space-y-2">
          <h3 className="font-serif text-2xl font-medium">All concepts mastered!</h3>
          <p className="text-sm text-muted-foreground max-w-xs">
            You've graduated every concept in this Work. Add more documents to unlock new material,
            or reset your streaks to study it all again.
          </p>
        </div>
        {summary && <MasteryBar />}
        <Button variant="outline" size="sm" className="gap-2 mt-2" onClick={handleReset}>
          <RefreshCw className="w-3.5 h-3.5" /> Reset streaks &amp; study again
        </Button>
      </div>
    );
  }

  // ── Loading / seeding ──────────────────────────────────────────────────────
  if (phase === "loading" || phase === "seeding") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground font-mono">
          {phase === "seeding" ? "Seeding concepts from your knowledge base…" : "Loading your learning session…"}
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-4 space-y-6">
      <MasteryBar />

      {/* Error banner */}
      {error && (
        <div className="px-4 py-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
          {error}
          <Button size="sm" variant="ghost" className="ml-3" onClick={init}>Retry</Button>
        </div>
      )}

      {/* Active concept header */}
      {session && (
        <div className="border border-border/60 rounded-xl p-4 bg-muted/20 space-y-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-primary" />
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">Studying</span>
            </div>
          </div>
          <h3 className="font-serif text-lg font-semibold">{session.subject}</h3>
          {session.description && (
            <p className="text-sm text-muted-foreground leading-relaxed">{session.description}</p>
          )}
        </div>
      )}

      {/* Question */}
      {(phase === "question" || phase === "assessing" || phase === "feedback") && session && (
        <Card className="p-6 space-y-4">
          {session.context_snippet && (
            <div className="text-xs font-mono text-muted-foreground/70 pl-3 border-l-2 border-border/50 italic leading-relaxed">
              {session.context_snippet}
            </div>
          )}
          <p className="font-medium leading-relaxed text-base">{session.question}</p>

          {phase !== "feedback" ? (
            <>
              <textarea
                className="w-full rounded-lg border border-border/60 bg-background p-3 text-sm font-serif leading-relaxed resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 min-h-[100px]"
                placeholder="Write your answer here…"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                disabled={phase === "assessing"}
              />
              <div className="flex justify-end">
                <Button
                  onClick={submitAnswer}
                  disabled={!answer.trim() || phase === "assessing"}
                  className="gap-2"
                >
                  {phase === "assessing"
                    ? <><Loader2 className="w-4 h-4 animate-spin" /> Assessing…</>
                    : <><ChevronRight className="w-4 h-4" /> Submit Answer</>}
                </Button>
              </div>
            </>
          ) : result ? (
            /* Feedback */
            <div className="space-y-4">
              <div className="px-3 py-2 rounded bg-muted/40 text-sm font-serif text-muted-foreground italic">
                {answer}
              </div>

              {/* Score */}
              <div className={`flex items-center gap-3 p-3 rounded-lg border ${
                result.score >= 0.75
                  ? "bg-emerald-500/10 border-emerald-500/30"
                  : result.score >= 0.5
                  ? "bg-amber-500/10 border-amber-500/30"
                  : "bg-red-500/10 border-red-500/30"
              }`}>
                <div className="text-2xl font-bold font-mono">
                  {Math.round(result.score * 100)}%
                </div>
                <div className="flex-1">
                  <p className="text-sm leading-relaxed">{result.feedback}</p>
                </div>
                {result.graduated && (
                  <div className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-700 text-xs font-mono font-semibold">
                    <Trophy className="w-3 h-3" /> Graduated!
                  </div>
                )}
              </div>

              {/* Routing hint */}
              <p className="text-xs font-mono text-muted-foreground">
                → {routeLabel[result.route]}
              </p>

              <div className="flex justify-end">
                <Button onClick={next} className="gap-2">
                  {result.summary.mastery_pct === 100
                    ? <><Trophy className="w-4 h-4" /> Done!</>
                    : result.route === "STEP_FORWARD"
                    ? <><ChevronRight className="w-4 h-4" /> Next Concept</>
                    : <><RefreshCw className="w-4 h-4" /> Try Again</>}
                </Button>
              </div>
            </div>
          ) : null}
        </Card>
      )}

      {/* Concept map (collapsible) */}
      {concepts.length > 0 && (
        <div className="border border-border/50 rounded-xl overflow-hidden">
          <button
            onClick={() => setShowConcepts(!showConcepts)}
            className="w-full flex items-center justify-between px-4 py-3 text-xs font-mono uppercase tracking-wider text-muted-foreground hover:bg-muted/30 transition-colors"
          >
            <span>Concept map ({concepts.length})</span>
            <ChevronDown className={`w-4 h-4 transition-transform ${showConcepts ? "rotate-180" : ""}`} />
          </button>
          {showConcepts && (
            <div className="divide-y divide-border/30">
              {concepts.map((c: any) => {
                const hasProgress = c.consecutive_passes > 0 || c.graduated;
                const isResetting = resettingConcept === c.id;
                return (
                  <div key={c.id} className="flex items-center justify-between px-4 py-2.5 text-sm group">
                    <div className="flex items-center gap-2">
                      {c.graduated
                        ? <Check className="w-3.5 h-3.5 text-emerald-500" />
                        : c.consecutive_passes > 0
                        ? <div className="w-3.5 h-3.5 rounded-full border-2 border-amber-400" />
                        : <div className="w-3.5 h-3.5 rounded-full border border-muted-foreground/40" />}
                      <span className={c.graduated ? "text-emerald-700 dark:text-emerald-400 font-medium" : ""}>{c.subject}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-muted-foreground">
                        {c.graduated ? "✓ done" : c.consecutive_passes > 0 ? `${c.consecutive_passes}/3` : "—"}
                      </span>
                      {hasProgress && (
                        <button
                          onClick={() => resetConcept(c.id, c.subject)}
                          disabled={isResetting}
                          title="Reset streak — re-enter study queue"
                          className="opacity-0 group-hover:opacity-60 hover:!opacity-100 p-1 rounded text-muted-foreground hover:text-destructive transition-all"
                        >
                          {isResetting
                            ? <Loader2 className="w-3 h-3 animate-spin" />
                            : <RotateCcw className="w-3 h-3" />}
                        </button>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Graph tab ────────────────────────────────────────────────────────────────

const API_BASE_GRAPH = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// Entity kind filter config for the Works graph tab
const GRAPH_ENTITY_KINDS = [
  { value: "concept",   label: "Concepts",   color: "#8b5cf6" },
  { value: "person",    label: "People",     color: "#6366f1" },
  { value: "place",     label: "Places",     color: "#10b981" },
  { value: "theme",     label: "Themes",     color: "#f59e0b" },
  { value: "scripture", label: "Scripture",  color: "#ef4444" },
] as const;

function GraphTab({ workId }: { workId: string }) {
  const [, navigate]      = useLocation();
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set());

  const { data: graphData, isLoading, error } = useQuery({
    queryKey: ["workGraph", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE_GRAPH}/works/${workId}/graph?limit=150`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<{ nodes: any[]; edges: any[]; node_count: number; edge_count: number }>;
    },
    staleTime: 30_000,
  });

  const toggleKind = (kind: string) => {
    setHiddenKinds(prev => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind); else next.add(kind);
      return next;
    });
  };

  const handleNavigate = (node: GNode) => {
    if (node.type === "document") {
      navigate(`/library/${node.id}`);
    }
  };

  return (
    <div className="space-y-4">
      {/* Entity kind filter chips */}
      {!!graphData?.nodes?.length && (
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs text-muted-foreground font-medium">Show:</span>
          {GRAPH_ENTITY_KINDS.map(({ value, label, color }) => {
            const on = !hiddenKinds.has(value);
            return (
              <button
                key={value}
                onClick={() => toggleKind(value)}
                className={`flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-medium border transition-all
                  ${on
                    ? "bg-background border-border text-foreground"
                    : "bg-transparent border-border/30 text-muted-foreground/50"
                  }`}
              >
                <span className="w-2 h-2 rounded-full shrink-0"
                  style={{ background: on ? color : "#94a3b8" }} />
                {label}
              </button>
            );
          })}
        </div>
      )}

      <KnowledgeGraph
        nodes={graphData?.nodes ?? []}
        edges={graphData?.edges ?? []}
        hiddenKinds={hiddenKinds}
        onNavigate={handleNavigate}
        height={480}
        loading={isLoading}
        error={error ? String(error) : undefined}
        nodeCount={graphData?.node_count}
        edgeCount={graphData?.edge_count}
      />
    </div>
  );
}


// ─── Quiz tab ─────────────────────────────────────────────────────────────────

interface QuizQuestion {
  q: string;
  options: string[];
  answer: number;
  explanation: string;
  /** Concept this question tests — present when the Work has seeded concepts */
  concept_id?: string;
}

interface QuizFeedback {
  is_correct: boolean;
  feedback: string;
  score: number;
  route: string;
  /** True when correctness came from the backend /learning/assess call; false = local quiz-answer-index contract */
  assessed: boolean;
}

interface MasterySummary {
  total: number;
  graduated: number;
  mastery_pct: number;
}

type QuizPhase = "idle" | "loading" | "question" | "submitting" | "feedback" | "summary";

const API_BASE_QUIZ = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

function QuizTab({ workId, workTitle }: { workId: string; workTitle: string }) {
  const [phase, setPhase]           = useState<QuizPhase>("idle");
  const [questions, setQuestions]   = useState<QuizQuestion[]>([]);
  const [conceptIds, setConceptIds] = useState<string[]>([]);
  const [current, setCurrent]       = useState(0);
  const [selected, setSelected]     = useState<number | null>(null);
  const [feedback, setFeedback]     = useState<QuizFeedback | null>(null);
  const [sessionResults, setSessionResults] = useState<{ correct: boolean }[]>([]);
  const [masterySummary, setMasterySummary] = useState<MasterySummary | null>(null);
  const [error, setError]           = useState<string | null>(null);

  const reset = () => {
    setPhase("idle");
    setQuestions([]);
    setConceptIds([]);
    setCurrent(0);
    setSelected(null);
    setFeedback(null);
    setSessionResults([]);
    setMasterySummary(null);
    setError(null);
  };

  const generate = async () => {
    setPhase("loading");
    setError(null);
    try {
      // Fetch quiz questions and concepts in parallel
      const [quizResp, conceptsResp] = await Promise.all([
        apiFetch(`${API_BASE_QUIZ}/works/${workId}/quiz`, { method: "POST" }),
        apiFetch(`${API_BASE_QUIZ}/works/${workId}/learning/concepts`),
      ]);
      if (!quizResp.ok) {
        const body = await quizResp.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `HTTP ${quizResp.status}`);
      }
      const quizData = await quizResp.json();
      const qs: QuizQuestion[] = quizData.questions ?? [];
      if (qs.length === 0) throw new Error("No questions returned — try again");

      // Extract concept IDs for mastery submission (may be empty)
      const cIds: string[] = conceptsResp.ok
        ? ((await conceptsResp.json()).concepts ?? []).map((c: any) => c.id as string)
        : [];

      setQuestions(qs);
      setConceptIds(cIds);
      setCurrent(0);
      setSelected(null);
      setFeedback(null);
      setSessionResults([]);
      setPhase("question");
    } catch (err: any) {
      setError(err.message ?? "Failed to generate quiz");
      setPhase("idle");
    }
  };

  const submitAnswer = async (optionIndex: number) => {
    const q = questions[current];
    if (!q || phase === "submitting") return;
    setSelected(optionIndex);
    setPhase("submitting");

    // Graduation / pass threshold — must match learning.py _GRAD_THRESHOLD = 0.75
    const PASS_THRESHOLD = 0.75;

    // Local fallback — quiz generator's canonical answer is authoritative.
    // assessed:false so the render path uses q.answer for option highlighting.
    let fb: QuizFeedback = {
      is_correct: optionIndex === q.answer,
      feedback: q.explanation,
      score: optionIndex === q.answer ? 1.0 : 0.0,
      route: optionIndex === q.answer ? "STEP_FORWARD" : "STAY_HERE",
      assessed: false,
    };

    // Only call assess when the backend has tagged this question with a validated concept_id.
    // Round-robin assignment is intentionally absent — an untagged question skips mastery.
    if (q.concept_id) {
      try {
        const r = await apiFetch(`${API_BASE_QUIZ}/works/${workId}/learning/assess`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            concept_id: q.concept_id,
            question: q.q,
            answer: q.options[optionIndex],
          }),
        });
        if (r.ok) {
          const data = await r.json();
          // Replace the local fallback entirely — assessed:true signals the render path
          // to use backend score as the single correctness contract (no q.answer mixing).
          fb = {
            is_correct: (data.score ?? 0) >= PASS_THRESHOLD,
            feedback: data.feedback || q.explanation,
            score: data.score ?? fb.score,
            route: data.route ?? fb.route,
            assessed: true,
          };
          if (data.summary) setMasterySummary(data.summary);
        }
        // If r.ok is false: leave local fb (assessed:false), do not update mastery
      } catch {
        // Network/parse error — leave local fb, never block the quiz
      }
    }

    setFeedback(fb);
    setSessionResults(prev => [...prev, { correct: fb.is_correct }]);
    setPhase("feedback");
  };

  const nextQuestion = () => {
    if (current + 1 >= questions.length) {
      setPhase("summary");
    } else {
      setCurrent(c => c + 1);
      setSelected(null);
      setFeedback(null);
      setPhase("question");
    }
  };

  // ── Idle / Loading ──────────────────────────────────────────────────────────
  if (phase === "idle" || phase === "loading") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-6">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
          <GraduationCap className="w-8 h-8 text-primary" />
        </div>
        <div className="text-center space-y-1 max-w-sm">
          <h3 className="font-serif text-xl font-medium">Adaptive Quiz</h3>
          <p className="text-sm text-muted-foreground">
            Test your understanding of <span className="font-medium text-foreground">{workTitle}</span>.
            Orivellum generates 5 questions from your knowledge base and updates your mastery score as you go.
          </p>
        </div>
        {error && (
          <div className="px-4 py-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive max-w-sm text-center">
            {error}
          </div>
        )}
        <Button onClick={generate} disabled={phase === "loading"} className="gap-2 px-8">
          {phase === "loading"
            ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating…</>
            : <><Sparkles className="w-4 h-4" /> Generate Quiz</>}
        </Button>
      </div>
    );
  }

  // ── Summary ─────────────────────────────────────────────────────────────────
  if (phase === "summary") {
    const correctCount = sessionResults.filter(r => r.correct).length;
    const total = sessionResults.length;
    const pct = total > 0 ? Math.round((correctCount / total) * 100) : 0;
    const tier = pct >= 80 ? "excellent" : pct >= 60 ? "good" : "review";
    return (
      <div className="max-w-lg mx-auto py-12 space-y-6 text-center">
        <div className={`p-6 rounded-2xl border space-y-3 ${
          tier === "excellent" ? "bg-emerald-500/10 border-emerald-500/30"
          : tier === "good"    ? "bg-amber-500/10 border-amber-500/30"
          : "bg-red-500/10 border-red-500/30"}`}>
          <Trophy className={`w-10 h-10 mx-auto ${tier === "excellent" ? "text-emerald-500" : tier === "good" ? "text-amber-500" : "text-red-500"}`} />
          <p className="text-3xl font-serif font-bold">{correctCount}/{total}</p>
          <p className="text-sm text-muted-foreground">
            {tier === "excellent" ? "Excellent! You've mastered this material." : tier === "good" ? "Good effort — a bit more practice and you'll have it." : "Keep studying — review the knowledge items for this Work."}
          </p>
        </div>
        {masterySummary && (
          <div className="p-4 rounded-xl bg-muted/30 border border-border/40 space-y-2">
            <p className="text-xs font-mono uppercase tracking-wider text-muted-foreground">Mastery updated</p>
            <div className="flex items-center gap-3">
              <div className="flex-1 h-2 bg-muted rounded-full overflow-hidden">
                <div
                  className="h-full bg-emerald-500/70 rounded-full transition-all duration-700"
                  style={{ width: `${masterySummary.mastery_pct}%` }}
                />
              </div>
              <span className="text-sm font-semibold font-mono tabular-nums">
                {masterySummary.mastery_pct}%
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              {masterySummary.graduated}/{masterySummary.total} concepts graduated
            </p>
          </div>
        )}
        {!masterySummary && conceptIds.length === 0 && (
          <p className="text-xs text-muted-foreground italic">
            Seed learning concepts in the Learn tab to track mastery progress.
          </p>
        )}
        <Button onClick={reset} variant="outline" className="gap-2">
          <RefreshCw className="w-4 h-4" /> New Quiz
        </Button>
      </div>
    );
  }

  // ── Active question / feedback ───────────────────────────────────────────────
  const q = questions[current];
  if (!q) return null;
  const isFeedback = phase === "feedback";
  const isSubmitting = phase === "submitting";

  return (
    <div className="max-w-2xl mx-auto py-6 space-y-5">
      {/* Progress bar */}
      <div className="flex items-center gap-3">
        <div className="flex-1 h-1.5 bg-muted rounded-full overflow-hidden">
          <div
            className="h-full bg-primary/60 rounded-full transition-all duration-300"
            style={{ width: `${(current / questions.length) * 100}%` }}
          />
        </div>
        <span className="text-xs font-mono text-muted-foreground shrink-0">
          {current + 1} / {questions.length}
        </span>
      </div>

      {/* Question card */}
      <Card className="p-6 space-y-4">
        <p className="font-medium text-base leading-relaxed">{q.q}</p>

        <div className="space-y-2.5">
          {q.options.map((opt, oi) => {
            const isChosen = selected === oi;
            // Single correctness contract:
            // - assessed:true  → backend score is authoritative; only highlight the chosen option
            // - assessed:false → quiz answer index is authoritative; also highlight the canonical answer
            const showCorrect = isFeedback && feedback
              ? feedback.assessed
                ? isChosen && feedback.is_correct           // backend says we got it right
                : oi === q.answer                           // quiz canonical answer
              : false;
            const showWrong = isFeedback && feedback
              ? isChosen && !feedback.is_correct            // same contract: chosen was wrong
              : false;

            let cls = "flex items-center gap-3 px-4 py-3 rounded-lg border text-sm transition-colors ";
            if (isFeedback) {
              if (showCorrect)   cls += "bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-400";
              else if (showWrong) cls += "bg-red-500/10 border-red-500/40 text-red-700 dark:text-red-400";
              else               cls += "border-border/30 text-muted-foreground/60";
            } else {
              cls += isChosen
                ? "bg-primary/10 border-primary/50 text-primary cursor-pointer"
                : "border-border/50 hover:bg-muted/40 hover:border-border cursor-pointer";
              if (isSubmitting) cls += " opacity-60 pointer-events-none";
            }
            return (
              <div key={oi} className={cls} onClick={() => !isFeedback && !isSubmitting && submitAnswer(oi)}>
                <span className="w-6 h-6 rounded-full border border-current flex items-center justify-center text-[10px] font-mono shrink-0">
                  {String.fromCharCode(65 + oi)}
                </span>
                <span className="flex-1">{opt}</span>
                {showCorrect && <Check className="w-4 h-4 text-emerald-500 shrink-0" />}
                {showWrong   && <X    className="w-4 h-4 text-red-500    shrink-0" />}
              </div>
            );
          })}
        </div>

        {isSubmitting && (
          <div className="flex items-center gap-2 text-xs text-muted-foreground pt-1">
            <Loader2 className="w-3 h-3 animate-spin" />
            <span>Scoring…</span>
          </div>
        )}

        {/* Feedback block */}
        {isFeedback && feedback && (
          <div className={`rounded-lg border p-3 space-y-1 ${feedback.is_correct ? "bg-emerald-500/8 border-emerald-500/30" : "bg-amber-500/8 border-amber-500/30"}`}>
            <p className={`text-xs font-semibold ${feedback.is_correct ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"}`}>
              {feedback.is_correct ? "✓ Correct" : "✗ Incorrect"}
            </p>
            {feedback.feedback && (
              <p className="text-xs text-muted-foreground leading-relaxed">{feedback.feedback}</p>
            )}
          </div>
        )}
      </Card>

      {/* Next button */}
      {isFeedback && (
        <div className="flex justify-end">
          <Button onClick={nextQuestion} className="gap-2">
            {current + 1 >= questions.length ? <><Trophy className="w-4 h-4" /> See Results</> : <><ChevronRight className="w-4 h-4" /> Next Question</>}
          </Button>
        </div>
      )}
    </div>
  );
}
