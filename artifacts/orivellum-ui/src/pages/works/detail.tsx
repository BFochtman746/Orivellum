import { useState, useRef } from "react";
import { useParams, Link, useLocation, useSearch } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import {
  useGetWork,
  useGetWorkStats,
  useUpdateWork,
  useDeleteWork,
  getGetWorkQueryKey,
  getGetWorkStatsQueryKey,
  getListWorksQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient, useQuery, useMutation } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Sheet, SheetContent, SheetHeader, SheetTitle,
} from "@/components/ui/sheet";
import {
  ArrowLeft,
  FileText,
  Network,
  CheckSquare,
  MessageSquare,
  Clock,
  Loader2,
  Pencil,
  Check,
  X,
  Trash2,
  GraduationCap,
  Search,
  BookOpen,
  BarChart2,
  AlertTriangle,
  Lightbulb,
  Brain,
  Share2,
  Film,
  Scroll,
  ImagePlus,
  LayoutDashboard,
  Sparkles,
  Wrench,
  GitBranch,
  Gauge,
  Package as PackageIcon,
  History as HistoryIcon,
  ChevronRight,
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
import { toast } from "sonner";
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

import {
  Page, Panel, Section, ErrorState, Status,
} from "@/components/primitives";

// ─── View / segment IA (WP3 restructure) ─────────────────────────────────────
// FIVE primary views replace the old ~14 flat tabs. Each view owns inner
// segments; advanced tools live in a single Tools overflow menu, never as a
// primary view.
type PrimaryView = "overview" | "create" | "knowledge" | "review" | "activity";

const VIEW_SEGMENTS: Record<PrimaryView, string[]> = {
  overview: [],
  create: ["book", "brainstorm", "genesis"],
  knowledge: ["knowledge", "search", "graph"],
  review: ["gaps", "completeness", "quiz", "study"],
  activity: ["conversations", "tasks"],
};

// BACK-COMPAT: every old ?tab= value maps to a { view, segment } pair so deep
// links from other pages (intelligence, review queue, intake, brainstorm) keep
// landing on the right content. `trailer` opens the Tools trailer surface.
const LEGACY_TAB_MAP: Record<string, { view: PrimaryView; segment?: string; trailer?: boolean }> = {
  book:          { view: "create",   segment: "book" },
  brainstorm:    { view: "create",   segment: "brainstorm" },
  genesis:       { view: "create",   segment: "genesis" },
  documents:     { view: "overview" },
  docs:          { view: "overview" },
  knowledge:     { view: "knowledge", segment: "knowledge" },
  graph:         { view: "knowledge", segment: "graph" },
  search:        { view: "knowledge", segment: "search" },
  gaps:          { view: "review",   segment: "gaps" },
  completeness:  { view: "review",   segment: "completeness" },
  quiz:          { view: "review",   segment: "quiz" },
  learn:         { view: "review",   segment: "study" },
  conversations: { view: "activity", segment: "conversations" },
  tasks:         { view: "activity", segment: "tasks" },
  trailer:       { view: "overview", trailer: true },
};

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
            className="w-24 h-36 object-cover rounded-md border border-border shadow-sm"
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
          className="w-24 h-36 rounded-md border border-dashed border-border flex flex-col items-center justify-center gap-1.5 text-muted-foreground/50 hover:text-muted-foreground hover:border-border hover:bg-muted/30 transition-colors"
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
      style={{ color: "var(--gd-bronze)", borderColor: "var(--gd-bronze-soft)", background: "var(--gd-bronze-soft)" }}
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
  const { data: workResp, isLoading: loadingWork, isError: workError, refetch: refetchWork } =
    useGetWork(workId!, {
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
  const { data: pipelineData } = useQuery<{ pipeline: any | null }>({
    queryKey: ["pipeline", workId],
    queryFn: () => apiFetch(`${PIPELINE_BASE}/works/${workId}/pipeline`).then(r => r.json()),
    enabled: !!workId,
    staleTime: 30_000,
  });
  const hasPipeline = !!(pipelineData?.pipeline);
  const pipelineStatus = pipelineData?.pipeline?.status ?? null;

  // ── View / segment routing ──────────────────────────────────────────────
  const _searchStr   = useSearch();
  const _urlParams   = new URLSearchParams(_searchStr);
  const _legacyTab   = _urlParams.get("tab");
  const _mapped      = _legacyTab ? LEGACY_TAB_MAP[_legacyTab] : undefined;
  const _initialSearchQuery = _urlParams.get("q") ?? "";

  const [view, setView] = useState<PrimaryView>(() => _mapped?.view ?? "overview");
  const [segment, setSegment] = useState<Record<PrimaryView, string>>(() => ({
    overview: "",
    create: VIEW_SEGMENTS.create[0],
    knowledge: VIEW_SEGMENTS.knowledge[0],
    review: VIEW_SEGMENTS.review[0],
    activity: VIEW_SEGMENTS.activity[0],
  }));
  const [trailerOpen, setTrailerOpen] = useState(() => _mapped?.trailer ?? false);

  // Jump helper — moves to a view and (optionally) its inner segment. Replaces
  // the old flat setActiveTab so internal jumps (pipeline → book, stat cards,
  // brainstorm-from-gap) keep working across the new IA.
  const goto = (v: PrimaryView, seg?: string) => {
    setView(v);
    if (seg) setSegment((prev) => ({ ...prev, [v]: seg }));
  };
    const setViewSegment = (v: PrimaryView) => (seg: string) =>
    setSegment((prev) => ({ ...prev, [v]: seg }));

  // Brainstorm seed — set when user clicks "Brainstorm this" on a gap card
  const [brainstormSeed, setBrainstormSeed] = useState(_initialSearchQuery);
  const [brainstormContext, setBrainstormContext] = useState("general");
  const handleBrainstormGap = (seed: string) => {
    setBrainstormSeed(seed);
    setBrainstormContext("research_planning");
    goto("create", "brainstorm");
  };

  const startPipeline = useMutation({
    mutationFn: () =>
      apiFetch(`${PIPELINE_BASE}/works/${workId}/pipeline`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: (work as any)?.title ?? "" }),
      }).then(r => { if (!r.ok) throw new Error("failed"); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["pipeline", workId] });
      goto("create", "book");
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

  // ── Advanced tools overflow (contextually available, never a primary view) ─
  const toolsMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" className="gap-1.5 text-xs min-h-11" data-testid="button-tools">
          <Wrench className="w-3.5 h-3.5" /> Tools
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-56">
        <DropdownMenuLabel className="text-xs font-mono uppercase tracking-wider text-muted-foreground">
          Advanced tools
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => setTrailerOpen(true)} className="gap-2 cursor-pointer" data-testid="tool-trailer">
          <Film className="w-4 h-4 text-muted-foreground" /> Trailer
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate(`/works/${workId}/gap-oracle`)} className="gap-2 cursor-pointer">
          <Sparkles className="w-4 h-4 text-muted-foreground" /> Gap Oracle
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate(`/works/${workId}/intelligence`)} className="gap-2 cursor-pointer">
          <Brain className="w-4 h-4 text-muted-foreground" /> Intelligence
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate(`/works/${workId}/continuity`)} className="gap-2 cursor-pointer">
          <GitBranch className="w-4 h-4 text-muted-foreground" /> Continuity
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate(`/works/${workId}/pacing`)} className="gap-2 cursor-pointer">
          <Gauge className="w-4 h-4 text-muted-foreground" /> Pacing
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => navigate(`/works/${workId}/handoff`)} className="gap-2 cursor-pointer">
          <PackageIcon className="w-4 h-4 text-muted-foreground" /> Handoff
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onClick={() => goto("create", "book")} className="gap-2 cursor-pointer">
          <Wrench className="w-4 h-4 text-muted-foreground" /> Drafting cockpit
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => goto("create", "book")} className="gap-2 cursor-pointer">
          <HistoryIcon className="w-4 h-4 text-muted-foreground" /> Chapter history
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const headerActions = work ? (
    <>
      <WorkRenderingIndicator workId={workId!} />
      <GenerateMenu workId={workId!} />
      <QuickChatButton workId={workId!} />
      {toolsMenu}
      <button
        onClick={handleDelete}
        disabled={deleteWork.isPending}
        className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-destructive transition-colors px-2 py-1 rounded hover:bg-destructive/5 min-h-11"
        data-testid="button-delete-work"
      >
        {deleteWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
        Delete
      </button>
    </>
  ) : null;

  // ── Page-level error / loading gates (six-state contract) ─────────────────
  if (workError) {
    return (
      <Page wide>
        <ErrorState
          title="Could not load this work"
          detail="The work failed to load. Check your connection and try again."
          onRetry={() => refetchWork()}
        />
      </Page>
    );
  }

  return (
    <Page wide>
      {/* Breadcrumb */}
      <div className="flex items-center gap-4 text-sm font-mono uppercase tracking-widest text-muted-foreground -mb-1">
        <Link href="/works" className="hover:text-foreground transition-colors flex items-center gap-1">
          <ArrowLeft className="w-3 h-3" /> Works
        </Link>
        <span>/</span>
        <span className="text-foreground truncate">
          {loadingWork ? <Skeleton className="w-20 h-4 inline-block align-middle" /> : work?.title}
        </span>
      </div>

      {/* Header actions row */}
      {work && (
        <div className="flex flex-wrap items-center gap-2 justify-end">
          {headerActions}
        </div>
      )}

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
                <Button size="sm" onClick={saveEdit} disabled={updateWork.isPending || !editTitle.trim()} className="gap-1.5 min-h-11">
                  {updateWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={updateWork.isPending} className="gap-1.5 min-h-11">
                  <X className="w-3.5 h-3.5" /> Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div className="flex items-start gap-5">
              <WorkCover workId={workId!} coverPath={(work as any).cover_path} />
              <div className="flex-1 min-w-0">
                <div className="flex items-start gap-3">
                  <h1 className="page-h1 min-w-0 break-words">{work.title}</h1>
                  <button
                    onClick={startEdit}
                    className="mt-3 p-1.5 rounded text-muted-foreground/50 hover:text-muted-foreground hover:bg-muted/50 transition-colors"
                    title="Edit title and description"
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                </div>
                {work.description ? (
                  <p className="text-sm mt-1.5 max-w-3xl leading-relaxed text-muted-foreground">
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
        </div>
      ) : null}

      {/* Primary views */}
      {work && (
        <div className="pt-4">
          <Tabs value={view} onValueChange={(v) => setView(v as PrimaryView)} className="w-full">
            <TabsList className="w-full justify-start border-b border-border rounded-none bg-transparent h-auto p-0 gap-2 flex-wrap">
              {[
                { value: "overview", icon: LayoutDashboard, label: "Overview" },
                { value: "create",   icon: BookOpen,        label: "Create" },
                { value: "knowledge",icon: Network,         label: "Knowledge" },
                { value: "review",   icon: BarChart2,       label: "Review" },
                { value: "activity", icon: MessageSquare,   label: "Activity", badge: pendingTaskCount || null },
              ].map(({ value, icon: Icon, label, badge }) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  data-testid={`view-${value}`}
                  className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-2 font-mono text-xs uppercase tracking-wider min-h-11 touch-manipulation"
                >
                  <Icon className="w-4 h-4 mr-2" /> {label}
                  {badge ? (
                    <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-primary/10 text-primary leading-none">
                      {badge}
                    </span>
                  ) : null}
                </TabsTrigger>
              ))}
            </TabsList>

            <div className="mt-6">
              {/* 1 — OVERVIEW: meta/stats + documents + pipeline status card */}
              <TabsContent value="overview" className="space-y-6">
                {stats && (
                  <div className="flex flex-wrap items-center gap-4">
                    {[
                      {
                        label: "Documents",
                        value: Object.values(stats.documents_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                        onClick: undefined,
                      },
                      {
                        label: "Knowledge",
                        value: Object.values(stats.knowledge_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                        onClick: () => goto("knowledge", "knowledge"),
                      },
                      {
                        label: "Pending tasks",
                        value: (stats.tasks_by_status as Record<string, number> ?? {}).pending ?? 0,
                        onClick: () => goto("activity", "tasks"),
                      },
                      {
                        label: "Conversations",
                        value: stats.conversation_count ?? 0,
                        onClick: () => goto("activity", "conversations"),
                      },
                    ].map(({ label, value, onClick }) => (
                      <button
                        key={label}
                        className={`text-center group ${onClick ? "cursor-pointer hover:opacity-70 transition-opacity" : ""}`}
                        onClick={onClick}
                        title={onClick ? `Go to ${label}` : undefined}
                      >
                        <div className={`text-lg font-semibold font-mono leading-none ${onClick ? "text-primary group-hover:underline" : ""}`}>{value}</div>
                        <div className="text-[10px] font-mono uppercase text-muted-foreground mt-0.5">{label}</div>
                      </button>
                    ))}
                    {(stats as any).concept_count > 0 && (
                      <div className="flex items-center gap-3 ml-2 pl-4 border-l border-border">
                        <div className="text-center">
                          <div className="text-lg font-semibold font-mono leading-none">{(stats as any).avg_mastery_pct ?? 0}%</div>
                          <div className="text-[10px] font-mono uppercase text-muted-foreground mt-0.5">Mastery</div>
                        </div>
                        <div className="w-20 h-1.5 bg-muted rounded-full overflow-hidden">
                          <div
                            className="h-full rounded-full transition-all duration-700"
                            style={{ width: `${(stats as any).avg_mastery_pct ?? 0}%`, background: "var(--gd-primary)" }}
                          />
                        </div>
                      </div>
                    )}
                    {(() => {
                      const byR = stats.documents_by_readiness as Record<string, number> ?? {};
                      const processing = byR.imported ?? 0;
                      const errors = (byR.error ?? 0) + (byR.no_text ?? 0);
                      if (processing === 0 && errors === 0) return null;
                      return (
                        <div className="flex items-center gap-3 ml-2 pl-4 border-l border-border">
                          {processing > 0 && <Status kind="busy" label={`${processing} processing`} />}
                          {errors > 0 && <Status kind="danger" label={`${errors} error${errors !== 1 ? "s" : ""}`} />}
                        </div>
                      );
                    })()}
                  </div>
                )}

                {/* Pipeline status card — links into Create */}
                <Panel>
                  <div className="flex items-center justify-between gap-3 flex-wrap">
                    <div className="flex items-center gap-3 min-w-0">
                      <BookOpen className="w-5 h-5 text-primary shrink-0" />
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-foreground">Book pipeline</div>
                        <div className="text-xs text-muted-foreground">
                          {hasPipeline && pipelineStatus
                            ? <span className="inline-flex items-center gap-2"><Status kind="busy" label={String(pipelineStatus)} /></span>
                            : pipelineData !== undefined
                              ? "No pipeline yet — start one to draft this Work into a book."
                              : "Checking pipeline status…"}
                        </div>
                      </div>
                    </div>
                    <div className="flex items-center gap-2 shrink-0">
                      {!hasPipeline && pipelineData !== undefined ? (
                        <Button
                          size="sm"
                          variant="outline"
                          disabled={startPipeline.isPending}
                          onClick={() => startPipeline.mutate()}
                          className="gap-1.5 text-xs min-h-11"
                          data-testid="button-start-pipeline"
                        >
                          {startPipeline.isPending
                            ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Starting…</>
                            : <><BookOpen className="w-3.5 h-3.5" /> Start Book Pipeline</>}
                        </Button>
                      ) : (
                        <Button
                          size="sm"
                          onClick={() => goto("create", "book")}
                          className="gap-1.5 text-xs min-h-11"
                          data-testid="button-open-book"
                        >
                          Open Create <ChevronRight className="w-3.5 h-3.5" />
                        </Button>
                      )}
                    </div>
                  </div>
                </Panel>

                <Section label="Documents">
                  <ErrorBoundary label="documents tab"><DocumentsTab workId={workId!} /></ErrorBoundary>
                </Section>
              </TabsContent>

              {/* 2 — CREATE: Book / Brainstorm / Genesis */}
              <TabsContent value="create">
                <SegmentedView
                  segments={[
                    { id: "book",       icon: BookOpen,   label: "Book" },
                    { id: "brainstorm", icon: Lightbulb,  label: "Brainstorm" },
                    { id: "genesis",    icon: Scroll,     label: "Origination" },
                  ]}
                  value={segment.create}
                  onChange={setViewSegment("create")}
                >
                  {segment.create === "book" && (
                    <ErrorBoundary label="book tab"><BookTab workId={workId!} /></ErrorBoundary>
                  )}
                  {segment.create === "brainstorm" && (
                    <ErrorBoundary label="brainstorm tab">
                      <BrainstormTab key={brainstormSeed} workId={workId!} initialSeed={brainstormSeed} initialContext={brainstormContext} />
                    </ErrorBoundary>
                  )}
                  {segment.create === "genesis" && (
                    <ErrorBoundary label="genesis tab"><GenesisTab workId={workId!} /></ErrorBoundary>
                  )}
                </SegmentedView>
              </TabsContent>

              {/* 3 — KNOWLEDGE: Knowledge / Search / Graph */}
              <TabsContent value="knowledge">
                <SegmentedView
                  segments={[
                    { id: "knowledge", icon: Network, label: "Knowledge" },
                    { id: "search",    icon: Search,  label: "Search" },
                    { id: "graph",     icon: Share2,  label: "Graph" },
                  ]}
                  value={segment.knowledge}
                  onChange={setViewSegment("knowledge")}
                >
                  {segment.knowledge === "knowledge" && (
                    <ErrorBoundary label="knowledge tab"><KnowledgeTab workId={workId!} /></ErrorBoundary>
                  )}
                  {segment.knowledge === "search" && (
                    <ErrorBoundary label="search tab"><SearchTab workId={workId!} initialQuery={_initialSearchQuery} /></ErrorBoundary>
                  )}
                  {segment.knowledge === "graph" && (
                    <ErrorBoundary label="graph tab"><GraphTab workId={workId!} /></ErrorBoundary>
                  )}
                </SegmentedView>
              </TabsContent>

              {/* 4 — REVIEW: Gaps / Completeness / Quiz / Study */}
              <TabsContent value="review">
                <SegmentedView
                  segments={[
                    { id: "gaps",         icon: AlertTriangle, label: "Gaps" },
                    { id: "completeness", icon: BarChart2,     label: "Completeness" },
                    { id: "quiz",         icon: GraduationCap, label: "Quiz" },
                    { id: "study",        icon: BookOpen,      label: "Study" },
                  ]}
                  value={segment.review}
                  onChange={setViewSegment("review")}
                >
                  {segment.review === "gaps" && (
                    <ErrorBoundary label="hygiene tab"><GapsTab workId={workId!} onBrainstorm={handleBrainstormGap} /></ErrorBoundary>
                  )}
                  {segment.review === "completeness" && (
                    <ErrorBoundary label="completeness tab"><CompletenessTab workId={workId!} /></ErrorBoundary>
                  )}
                  {segment.review === "quiz" && (
                    <ErrorBoundary label="quiz tab"><QuizTab workId={workId!} workTitle={(work as any)?.title ?? "this Work"} /></ErrorBoundary>
                  )}
                  {segment.review === "study" && (
                    <ErrorBoundary label="learn tab"><LearnTab workId={workId!} /></ErrorBoundary>
                  )}
                </SegmentedView>
              </TabsContent>

              {/* 5 — ACTIVITY: Conversations / Tasks */}
              <TabsContent value="activity">
                <SegmentedView
                  segments={[
                    { id: "conversations", icon: MessageSquare, label: "Conversations" },
                    { id: "tasks",         icon: CheckSquare,   label: "Tasks", badge: pendingTaskCount || null },
                  ]}
                  value={segment.activity}
                  onChange={setViewSegment("activity")}
                >
                  {segment.activity === "conversations" && (
                    <ErrorBoundary label="conversations tab"><ConversationsTab workId={workId!} /></ErrorBoundary>
                  )}
                  {segment.activity === "tasks" && (
                    <ErrorBoundary label="tasks tab"><TasksTab workId={workId!} /></ErrorBoundary>
                  )}
                </SegmentedView>
              </TabsContent>
            </div>
          </Tabs>
        </div>
      )}

      {/* Trailer — a dedicated surface reached from Tools, NOT a primary view */}
      <Sheet open={trailerOpen} onOpenChange={setTrailerOpen}>
        <SheetContent side="right" className="w-full sm:max-w-3xl overflow-y-auto">
          <SheetHeader>
            <SheetTitle className="flex items-center gap-2 font-serif">
              <Film className="w-5 h-5 text-primary" /> Trailer
            </SheetTitle>
          </SheetHeader>
          <div className="mt-4">
            {workId && (
              <ErrorBoundary label="trailer tab"><TrailerTab workId={workId} /></ErrorBoundary>
            )}
          </div>
        </SheetContent>
      </Sheet>
    </Page>
  );
}

// ─── Inner segmented control (≥44px) for views that host multiple tabs ────────

function SegmentedView({
  segments,
  value,
  onChange,
  children,
}: {
  segments: { id: string; icon: React.ElementType; label: string; badge?: number | null }[];
  value: string;
  onChange: (id: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-1.5 flex-wrap" role="tablist">
        {segments.map(({ id, icon: Icon, label, badge }) => (
          <button
            key={id}
            role="tab"
            aria-selected={value === id}
            data-active={value === id}
            data-testid={`segment-${id}`}
            onClick={() => onChange(id)}
            className="gd-chip min-h-11 px-3.5 text-xs font-mono uppercase tracking-wider inline-flex items-center gap-1.5 touch-manipulation"
          >
            <Icon className="w-3.5 h-3.5" /> {label}
            {badge ? (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-primary/10 text-primary leading-none">
                {badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>
      <div>{children}</div>
    </div>
  );
}
