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
  ImagePlus,
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

import { DocumentsTab } from "./documents-tab";
import { KnowledgeTab } from "./knowledge-tab";
import { TasksTab } from "./tasks-tab";
import { GenerateMenu, QuickChatButton } from "./generate-menu";
import { ConversationsTab } from "./conversations-tab";
import { SearchTab } from "./search-tab";
import { GapsTab } from "./gaps-tab";
import { CompletenessTab } from "./completeness-tab";
import { GraphTab } from "./graph-tab";
import { QuizTab } from "./quiz-tab";
// ─── Work cover image ─────────────────────────────────────────────────────────
// Upload / replace / remove a Work's cover. Shown beside the title and used as
// lock-screen artwork when listening to this Work's documents.

function WorkCover({ workId, coverPath }: { workId: string; coverPath?: string | null }) {
  const queryClient = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  // Bump to cache-bust the <img> after a replace (server sends no-cache, but
  // a fresh URL guarantees the new cover shows immediately).
  const [version, setVersion] = useState(0);
  const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

  const refresh = () => {
    queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
    queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
    setVersion((v) => v + 1);
  };

  const handleFile = async (file: File) => {
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", file);
      const resp = await apiFetch(`${BASE}/works/${workId}/cover`, { method: "POST", body: form });
      if (!resp.ok) {
        const detail = (await resp.json().catch(() => null))?.detail;
        throw new Error(typeof detail === "string" ? detail : "Upload failed");
      }
      refresh();
      toast.success("Cover image updated");
    } catch (e: any) {
      toast.error(e?.message || "Could not upload the cover image");
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleRemove = async () => {
    setBusy(true);
    try {
      const resp = await apiFetch(`${BASE}/works/${workId}/cover`, { method: "DELETE" });
      if (!resp.ok) throw new Error("Remove failed");
      refresh();
      toast.success("Cover image removed");
    } catch (e: any) {
      toast.error(e?.message || "Could not remove the cover image");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="shrink-0">
      <input
        ref={fileRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
      />
      {coverPath ? (
        <div className="relative group/cover w-24">
          <img
            src={`${BASE}/works/${workId}/cover?v=${version}`}
            alt="Work cover"
            className="w-24 h-36 object-cover rounded-md border border-border/60 shadow-md"
          />
          <div className="absolute inset-0 rounded-md bg-background/70 opacity-0 group-hover/cover:opacity-100 focus-within:opacity-100 transition-opacity flex flex-col items-center justify-center gap-1.5">
            {busy ? (
              <Loader2 className="w-4 h-4 animate-spin text-muted-foreground" />
            ) : (
              <>
                <button
                  onClick={() => fileRef.current?.click()}
                  className="text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded bg-muted/80 hover:bg-muted text-foreground transition-colors"
                >
                  Replace
                </button>
                <button
                  onClick={handleRemove}
                  className="text-[10px] font-mono uppercase tracking-wider px-2 py-1 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/10 transition-colors"
                >
                  Remove
                </button>
              </>
            )}
          </div>
        </div>
      ) : (
        <button
          onClick={() => fileRef.current?.click()}
          disabled={busy}
          title="Add a cover image (PNG, JPEG, or WebP)"
          className="w-24 h-36 rounded-md border border-dashed border-border/60 flex flex-col items-center justify-center gap-1.5 text-muted-foreground/50 hover:text-muted-foreground hover:border-border hover:bg-muted/30 transition-colors"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <ImagePlus className="w-4 h-4" />}
          <span className="text-[10px] font-mono uppercase tracking-wider">Cover</span>
        </button>
      )}
    </div>
  );
}

// ─── Audiobook rendering indicator ───────────────────────────────────────────
// A 20–30 minute book render runs in the Studio's background — surface it on
// the Work's own page so users don't have to open the Studio to check.
// Clicking jumps to the Studio's Build Audiobook tab, which auto-reconnects.

function WorkRenderingIndicator({ workId }: { workId: string }) {
  const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
  const { data } = useQuery<{ jobs: any[] }>({
    queryKey: ["studio-active-work-tts"],
    queryFn: () =>
      apiFetch(`${BASE}/studio/tts/work/active`).then(r => (r.ok ? r.json() : { jobs: [] })),
    // Poll faster while THIS Work is rendering so the progress stays live;
    // otherwise a slow background check is enough to discover a new render.
    refetchInterval: query => {
      const jobs = ((query.state.data as any)?.jobs ?? []) as any[];
      return jobs.some(j => j.work_id === workId) ? 10_000 : 30_000;
    },
  });
  const job = (data?.jobs ?? []).find((j: any) => j.work_id === workId);
  if (!job) return null;

  const chapterTotal: number = job.total_chapters ?? 0;
  const chapterNow = Math.min((job.chapter_idx ?? 0) + 1, chapterTotal);
  const segsTotal: number = job.total_segments ?? 0;
  const pct = segsTotal > 0 ? Math.round(((job.segments_done ?? 0) / segsTotal) * 100) : null;

  return (
    <Link
      href="/studio?tool=voice&vtab=audiobook"
      title="An audiobook is rendering for this Work — open the Studio to see full progress"
      className="flex items-center gap-1.5 text-[11px] font-mono px-2 py-1 rounded-full border transition-colors"
      style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }}
      data-testid="badge-work-rendering"
    >
      <Loader2 className="w-3 h-3 animate-spin" />
      Narrating
      {chapterTotal > 0 && ` ch ${chapterNow}/${chapterTotal}`}
      {pct !== null && ` · ${pct}%`}
    </Link>
  );
}

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
            {/* Live audiobook render indicator — links to the Studio */}
            <WorkRenderingIndicator workId={workId!} />
            {/* Start Book Pipeline — only shown when no pipeline exists yet */}
            {!hasPipeline && pipelineData !== undefined && (
              <Button
                size="sm"
                variant="outline"
                disabled={startPipeline.isPending}
                onClick={() => startPipeline.mutate()}
                className="gap-1.5 text-xs transition-opacity hover:opacity-80"
                style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)" }}
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
            <div className="flex items-start gap-5">
              <WorkCover workId={workId!} coverPath={(work as any).cover_path} />
              <div className="flex-1 min-w-0">
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
                      className="h-full rounded-full transition-all duration-700"
                      style={{ width: `${(stats as any).avg_mastery_pct ?? 0}%`, background: 'var(--green-2)' }}
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
                      <span className="flex items-center gap-1 text-[10px] font-mono rounded px-1.5 py-0.5"
                            style={{ color: 'var(--gilt)', background: 'var(--gilt-soft)', border: '1px solid var(--gilt-line)' }}>
                        <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--gilt)' }} />
                        {processing} processing
                      </span>
                    )}
                    {errors > 0 && (
                      <span className="flex items-center gap-1 text-[10px] font-mono rounded px-1.5 py-0.5"
                            style={{ color: 'var(--rust)', background: 'var(--rust-soft)', border: '1px solid var(--rust)' }}>
                        <span className="w-1.5 h-1.5 rounded-full" style={{ background: 'var(--rust)' }} />
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
              { value: "gaps",         icon: AlertTriangle, label: "Hygiene",      badge: null },
              { value: "tasks",        icon: CheckSquare,   label: "Tasks",        badge: pendingTaskCount ?? null },
              { value: "conversations",icon: MessageSquare, label: "Conversations",badge: null },
              { value: "search",       icon: Search,        label: "Search",       badge: null },
              { value: "quiz",         icon: GraduationCap, label: "Quiz",         badge: null },
              { value: "learn",        icon: BookOpen,      label: "Learn",        badge: null },
              { value: "brainstorm",   icon: Lightbulb,     label: "Brainstorm",   badge: null },
              { value: "trailer",      icon: Film,          label: "Trailer",       badge: ((stats as any)?.trailer_count > 0 ? (stats as any)?.trailer_count : null) as number | null },
              { value: "genesis",      icon: Scroll,        label: "Origination",       badge: null },
            ].map(({ value, icon: Icon, label, badge }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider min-h-[44px] touch-manipulation"
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
            <TabsContent value="gaps"><ErrorBoundary label="hygiene tab"><GapsTab workId={workId!} onBrainstorm={handleBrainstormGap} /></ErrorBoundary></TabsContent>
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
