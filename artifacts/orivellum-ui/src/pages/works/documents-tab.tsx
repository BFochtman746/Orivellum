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
import { LoadingState, EmptyState, ErrorState, ConfirmAction } from "@/components/primitives";


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

export function DocumentsTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const [open, setOpen] = useState(false);
  const [docFilter, setDocFilter] = useState("");
  const [readinessFilter, setReadinessFilter] = useState<ReadinessFilter>("all");
  const [docSort, setDocSort] = useState<DocSortKey>("date");
  const [retrying, setRetrying] = useState<string | null>(null);

  const { data: docsResp, isLoading, isError, refetch } = useGetWorkDocuments(workId, {
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

  // Contributing collections (provenance for Works ratified from a proposal)
  const { data: provResp } = useQuery({
    queryKey: ["work-collections", workId],
    queryFn: async () => {
      const r = await apiFetch(`${DOC_BASE}/works/${workId}/collections`);
      if (!r.ok) return { collections: [] };
      return r.json() as Promise<{ collections: any[] }>;
    },
    enabled: !!workId,
    staleTime: 60_000,
  });
  const provCollections = provResp?.collections ?? [];

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

  if (isLoading) return <LoadingState rows={5} label="Loading documents" />;
  if (isError) return (
    <ErrorState
      title="Couldn't load documents"
      detail="The source material for this work failed to load."
      onRetry={() => refetch()}
    />
  );
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
          <Button size="sm" variant="outline" className="gap-2 min-h-11" onClick={() => setOpen(true)}>
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
              <span className="font-semibold" style={{ color: "var(--gd-success)" }}>{readyCnt} ready</span>
              {processingCnt > 0 && (
                <>
                  <span className="text-muted-foreground/40">·</span>
                  <span className="flex items-center gap-1" style={{ color: "var(--gd-bronze)" }}>
                    <span className="w-1.5 h-1.5 rounded-full animate-pulse inline-block" style={{ background: "var(--gd-bronze)" }} />
                    {processingCnt} processing
                  </span>
                </>
              )}
              {errorCnt > 0 && (
                <>
                  <span className="text-muted-foreground/40">·</span>
                  <span style={{ color: "var(--gd-danger)" }}>{errorCnt} error{errorCnt !== 1 ? "s" : ""}</span>
                </>
              )}
            </div>
          ) : (
            <div className="text-[11px] font-mono" style={{ color: "var(--gd-success)" }}>
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
                }`}
                style={key === "error" && count > 0 && readinessFilter !== key ? { color: "var(--gd-danger)" } : undefined}
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
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--gd-bronze)" }}>
            <GitBranch className="w-4 h-4" />
            <span>Version relationships detected</span>
            <span className="text-[10px] font-mono rounded px-1.5 py-0.5 border" style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)", opacity: 0.8 }}>
              {dupePairs.length} pair{dupePairs.length !== 1 ? "s" : ""}
            </span>
          </div>
          {dupePairs.map((pair: any) => (
            <div
              key={pair.id}
              className="rounded-lg p-3 space-y-2 border"
              style={{ background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" }}
            >
              <p className="text-xs leading-relaxed" style={{ color: "var(--gd-bronze)" }}>
                <span className="font-semibold text-foreground">{pair.doc_a_title || "Untitled"}</span>
                <span className="mx-1.5" style={{ color: "var(--gd-bronze)" }}>&amp;</span>
                <span className="font-semibold text-foreground">{pair.doc_b_title || "Untitled"}</span>
                <span className="ml-1.5 opacity-80">
                  ({pair.kind === "near_duplicate" ? "near duplicates" : "likely revisions"} · {Math.round(pair.similarity * 100)}% similar)
                </span>
              </p>
              <p className="text-[11px] text-muted-foreground">
                Declare one as canonical — the other will be marked as superseded.
              </p>
              <div className="flex items-center gap-2 flex-wrap">
                <button
                  disabled={resolvingDupe === pair.id}
                  onClick={() => handleDeclareCanonicaL(pair.id, pair.doc_a_id)}
                  className="text-[11px] px-2.5 min-h-11 rounded border bg-background hover:bg-muted/40 transition-colors disabled:opacity-50 font-mono"
                  style={{ borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)", color: "var(--gd-bronze)" }}
                >
                  {pair.doc_a_title || "Doc A"} is canonical
                </button>
                <button
                  disabled={resolvingDupe === pair.id}
                  onClick={() => handleDeclareCanonicaL(pair.id, pair.doc_b_id)}
                  className="text-[11px] px-2.5 min-h-11 rounded border bg-background hover:bg-muted/40 transition-colors disabled:opacity-50 font-mono"
                  style={{ borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)", color: "var(--gd-bronze)" }}
                >
                  {pair.doc_b_title || "Doc B"} is canonical
                </button>
                <button
                  onClick={() => setDismissedDupes((prev) => new Set([...prev, pair.id]))}
                  className="text-[11px] px-2 min-h-11 rounded text-muted-foreground hover:text-foreground transition-colors font-mono"
                >
                  Dismiss
                </button>
                {resolvingDupe === pair.id && <Loader2 className="w-3.5 h-3.5 animate-spin" style={{ color: "var(--gd-bronze)" }} />}
              </div>
            </div>
          ))}
        </div>
      )}

      {provCollections.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium" style={{ color: "var(--gd-bronze)" }}>
            <GitBranch className="w-4 h-4" />
            <span>Contributed by</span>
          </div>
          <div className="flex flex-wrap gap-2">
            {provCollections.map((c: any) => (
              <span
                key={c.collection_id}
                className="text-[11px] font-mono rounded px-2 py-1 border"
                style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" }}
                title={c.source_kind ? `source: ${c.source_kind}` : undefined}
              >
                {c.label || "collection"} · {c.doc_count} doc{c.doc_count !== 1 ? "s" : ""}
              </span>
            ))}
          </div>
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
              className="hover-elevate cursor-pointer group"
              style={isError ? { borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" } : undefined}
              onClick={() => navigate(`/library/${doc.id}`)}
            >
              <CardContent className="p-4 flex items-center justify-between gap-3 min-h-11">
                <div className="flex items-center gap-3 min-w-0">
                  <FileText className={`w-5 h-5 shrink-0 ${isError ? "" : "text-muted-foreground"}`} style={isError ? { color: "var(--gd-danger)" } : undefined} />
                  <div className="min-w-0">
                    <h4 className="font-medium truncate">{doc.title || doc.source || "Untitled"}</h4>
                    <div className="flex gap-2 mt-1 flex-wrap">
                      <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                      <Badge
                        variant="outline"
                        className="text-[10px] uppercase font-mono"
                        style={isError
                          ? { color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" }
                          : isProcessing
                          ? { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" }
                          : { color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}
                      >
                        {isProcessing && <span className="w-1.5 h-1.5 rounded-full animate-pulse mr-1 inline-block" style={{ background: "var(--gd-bronze)" }} />}
                        {doc.readiness}
                      </Badge>
                      {(doc as any).lifecycle === "canonical" && (
                        <span
                          className="text-[10px] font-mono flex items-center gap-0.5 rounded px-1.5 py-0.5 border"
                          style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" }}
                        >
                          <Star className="w-2.5 h-2.5" />canonical
                        </span>
                      )}
                      {(doc as any).lifecycle === "superseded" && (
                        <span className="text-[10px] font-mono bg-muted/50 border border-border text-muted-foreground rounded px-1.5 py-0.5 line-through">
                          superseded
                        </span>
                      )}
                      {(doc as any).lifecycle === "reference" && (
                        <span className="text-[10px] font-mono rounded px-1.5 py-0.5 border text-muted-foreground" style={{ borderColor: "var(--gd-line-control)" }}>
                          reference
                        </span>
                      )}
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <div className="text-xs font-mono text-muted-foreground">
                    {doc.created_at ? format(new Date(doc.created_at), "MMM d, yyyy") : ""}
                  </div>
                  {/* Retry button — visible on hover for error/no_text docs */}
                  {isError && (
                    <button
                      onClick={(e) => handleRetry(e, doc.id!)}
                      disabled={retrying === doc.id}
                      title="Retry extraction"
                      data-testid={`retry-doc-${doc.id}`}
                      className="opacity-0 group-hover:opacity-100 transition-opacity min-h-11 min-w-11 flex items-center justify-center rounded disabled:opacity-40 hover:bg-muted/40"
                      style={{ color: "var(--gd-bronze)" }}
                    >
                      {retrying === doc.id
                        ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        : <RefreshCw className="w-3.5 h-3.5" />}
                    </button>
                  )}
                  <ConfirmAction
                    title="Unlink this document?"
                    consequence="The document stays in your library but is detached from this work. You can re-link it later."
                    confirmLabel="Unlink"
                    destructive
                    onConfirm={async () => {
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
                    trigger={
                      <button
                        onClick={(e) => e.stopPropagation()}
                        title="Unlink from this work"
                        data-testid={`unlink-doc-${doc.id}`}
                        className="opacity-0 group-hover:opacity-100 transition-opacity min-h-11 min-w-11 flex items-center justify-center rounded text-muted-foreground/50 hover:text-destructive hover:bg-destructive/5"
                      >
                        <Unlink className="w-3.5 h-3.5" />
                      </button>
                    }
                  />
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      ) : docFilter.trim() || readinessFilter !== "all" ? (
        <EmptyState
          icon={<Search />}
          title={docFilter.trim()
            ? `No documents match "${docFilter}"`
            : `No ${readinessFilter === "imported" ? "processing" : readinessFilter} documents`}
          description="Adjust or clear the filter to see all source material."
          action={
            <Button
              variant="outline"
              size="sm"
              className="min-h-11"
              onClick={() => { setDocFilter(""); setReadinessFilter("all"); }}
            >
              Clear filter
            </Button>
          }
        />
      ) : (
        <EmptyState
          icon={<FileText />}
          title="No documents added to this work yet"
          description="Link a document from your library to give this work source material."
          action={
            <Button size="sm" variant="outline" className="gap-2 min-h-11" onClick={() => setOpen(true)}>
              <Plus className="w-4 h-4" /> Add from Library
            </Button>
          }
        />
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
                    className="w-full text-left flex items-center gap-3 p-3 min-h-11 rounded-lg border border-border/50 hover:bg-muted/50 transition-colors disabled:opacity-50"
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

