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
import { enqueueOp, isNetworkError } from "@/lib/outbox";
import { KnowledgeGraph, GNode } from "@/components/knowledge-graph";
import { LearnTab } from "@/pages/learning/learn-tab";
import { LoadingState, EmptyState, ErrorState, ConfirmAction } from "@/components/primitives";


const BASE_KN = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

async function setKnowledgeReview(itemId: string, status: string, force = false): Promise<void> {
  const url = `${BASE_KN}/knowledge/${itemId}/review`;
  const body = { review_status: status, force };
  let resp: Response;
  try {
    resp = await apiFetch(url, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      // force is required to deliberately flip an already-finalized decision;
      // without it the API rejects stale/concurrent overwrites with 409.
      body: JSON.stringify(body),
    });
  } catch (err) {
    if (isNetworkError(err)) {
      // Offline — queue the decision on this device; latest decision per
      // item wins when the outbox flushes on reconnect.
      await enqueueOp("api_call", { method: "PATCH", url, body, label: "Knowledge review" },
        { replaceKey: `kn-review-${itemId}` });
      return;
    }
    throw err;
  }
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

// ─── Domain re-harvest panel (THE RE-PROJECTION Phases 5-6) ──────────────────
// Run one ratified Work under its closed domain ontology, read the report
// (including off-schema discards), sample the fresh output, and sign off on
// the pilot to unlock batch re-harvest.
function ReharvestPanel({ workId, domain }: { workId: string; domain: string | null }) {
  const API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
  const queryClient = useQueryClient();
  const [signAuthor, setSignAuthor] = useState("");
  const [showSample, setShowSample] = useState(false);
  const [busy, setBusy] = useState(false);

  const { data: rep } = useQuery({
    queryKey: ["reharvestReport", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API}/works/${workId}/reharvest/report`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<{
        status: { state: string };
        report: any | null;
        pilot_work_id: string | null;
        pilot_signed_by: string | null;
      }>;
    },
    enabled: !!domain,
    refetchInterval: (q) => (q.state.data?.status?.state === "running" ? 3000 : false),
  });

  const { data: sample } = useQuery({
    queryKey: ["reharvestSample", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API}/works/${workId}/reharvest/sample?limit=100`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      return r.json() as Promise<{ items: any[]; count: number }>;
    },
    enabled: showSample,
  });

  if (!domain) return null; // re-harvest exists only for ratified Works

  const running = rep?.status?.state === "running";
  const report = rep?.report;
  const isPilot = rep?.pilot_work_id === workId;
  const signed = !!rep?.pilot_signed_by;
  const pilotElsewhere = !signed && !!rep?.pilot_work_id && !isPilot;

  const post = async (path: string, body?: any, okMsg?: string) => {
    setBusy(true);
    try {
      const r = await apiFetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body ? JSON.stringify(body) : undefined,
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(d?.detail || `HTTP ${r.status}`);
      if (okMsg) toast.success(okMsg);
      queryClient.invalidateQueries({ queryKey: ["reharvestReport", workId] });
      queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
    } catch (e: any) {
      toast.error(String(e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-border/60 p-3 space-y-2 bg-muted/20">
      <div className="flex items-center gap-2 flex-wrap">
        <Sparkles className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-violet)" }} />
        <span className="text-xs font-medium">Domain re-harvest</span>
        <Badge variant="outline" className="text-[10px]">{domain}</Badge>
        {running && (
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Loader2 className="w-3 h-3 animate-spin" /> Running…
          </span>
        )}
        <div className="flex-1" />
        <Button
          size="sm" variant="outline" className="h-7 text-xs gap-1"
          disabled={busy || running || pilotElsewhere}
          onClick={() => post(`/works/${workId}/reharvest`, undefined, "Re-harvest started")}
        >
          <RefreshCw className="w-3 h-3" />
          {report ? "Re-run" : signed ? "Re-harvest" : "Run pilot"}
        </Button>
        {report && (
          <Button size="sm" variant="ghost" className="h-7 text-xs" onClick={() => setShowSample(true)}>
            Read output
          </Button>
        )}
      </div>
      {pilotElsewhere && (
        <p className="text-[11px] text-muted-foreground">
          The pilot re-harvest is scoped to another Work. Read and sign off on its output first.
        </p>
      )}
      {report && (
        <div className="flex items-center gap-3 flex-wrap text-[11px] text-muted-foreground font-mono">
          <span>state: {report.state}</span>
          <span>docs: {report.docs_processed}</span>
          <span>created: {report.items_created}</span>
          <span style={report.items_discarded_off_schema > 0 ? { color: "var(--gd-caution)" } : undefined}>
            off-schema discarded: {report.items_discarded_off_schema}
          </span>
          {report.docs_skipped_doc_type > 0 && <span>skipped (doc type): {report.docs_skipped_doc_type}</span>}
          {report.llm_calls_failed > 0 && <span style={{ color: "var(--gd-danger)" }}>LLM failures: {report.llm_calls_failed}</span>}
        </div>
      )}
      {isPilot && !signed && report?.state === "done" && (
        <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-border/40">
          <span className="text-[11px] text-muted-foreground">
            Read the output, then sign off to unlock re-harvesting every ratified Work:
          </span>
          <Input
            value={signAuthor}
            onChange={(e) => setSignAuthor(e.target.value)}
            placeholder="Your name"
            className="h-7 w-36 text-xs"
          />
          <Button
            size="sm" variant="outline" className="h-7 text-xs"
            disabled={busy || !signAuthor.trim()}
            onClick={() => post("/reharvest/pilot-signoff", { author: signAuthor.trim() }, "Pilot signed off")}
          >
            Sign off
          </Button>
        </div>
      )}
      {signed && (
        <div className="flex items-center gap-2 flex-wrap pt-1 border-t border-border/40">
          <span className="text-[11px] text-muted-foreground">
            Pilot signed by {rep?.pilot_signed_by}.
          </span>
          <Button
            size="sm" variant="ghost" className="h-7 text-xs"
            disabled={busy}
            onClick={() => post("/reharvest/all", undefined, "Batch re-harvest queued")}
          >
            Re-harvest all ratified Works
          </Button>
        </div>
      )}
      <Dialog open={showSample} onOpenChange={setShowSample}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Re-harvested knowledge sample</DialogTitle>
            <DialogDescription>
              Fresh machine-extracted items ({sample?.count ?? 0}) — every kind comes from the
              “{domain}” ontology. Approve or dismiss them in the list below.
            </DialogDescription>
          </DialogHeader>
          <div className="max-h-[50vh] overflow-y-auto space-y-2">
            {(sample?.items ?? []).map((it) => (
              <div key={it.id} className="rounded border border-border/50 p-2 text-xs">
                <Badge variant="outline" className="text-[10px] mr-2">{it.kind}</Badge>
                {it.text}
              </div>
            ))}
            {sample && sample.items.length === 0 && (
              <p className="text-xs text-muted-foreground">No re-harvested items yet.</p>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

type KnowledgeFilter = "all" | "pending" | "approved" | "rejected";
type KnowledgeKindFilter = "all" | "entity" | "claim" | "relationship" | "summary";
type KnowledgeConfFilter = "all" | "high" | "med" | "low";

export function KnowledgeTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  // Deep-link: ?tab=knowledge&item=<id> scrolls to and flashes the target card.
  const _knSearch = useSearch();
  const highlightItemId = useMemo(() => new URLSearchParams(_knSearch).get("item"), [_knSearch]);
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
  const { data: workResp } = useGetWork(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkQueryKey(workId) },
  });
  const workDomain: string | null = ((workResp as any)?.domain ?? null) || null;
  const { data: knowResp, isLoading, isError, refetch } = useGetWorkKnowledge(workId, {}, {
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

  // Scroll-to-item when arriving from the review queue with ?tab=knowledge&item=ID.
  // Runs after data has loaded so the card exists in the DOM.
  useEffect(() => {
    if (!highlightItemId || isLoading) return;
    const timer = setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-item-id="${highlightItemId}"]`);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("chapter-highlight");
      setTimeout(() => el.classList.remove("chapter-highlight"), 2000);
    }, 250);
    return () => clearTimeout(timer);
  }, [highlightItemId, isLoading]);

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
      <ReharvestPanel workId={workId} domain={workDomain} />
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
                    }`}
                    style={key === "pending" && pendingCount > 0 ? { color: "var(--gd-bronze)" } : undefined}
                  >
                    {label}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>

      {isLoading ? (
        <LoadingState rows={5} label="Loading knowledge" />
      ) : isError ? (
        <ErrorState
          title="Couldn't load knowledge"
          detail="The structured knowledge for this work failed to load."
          onRetry={() => refetch()}
        />
      ) : knowledge.length > 0 ? (
        <div className="grid gap-3">
          {knowledge.map((item) => {
            const isAI = item.review_status === "ai_auto";
            const isApproved = item.review_status === "approved";
            const isRejected = item.review_status === "rejected";
            const isReviewing = reviewing === item.id;
            return (
            <Card key={item.id} data-item-id={item.id!} className={`transition-opacity ${isRejected ? "opacity-50" : ""}`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">
                        {item.kind}
                      </Badge>
                      {item.review_status === "ai_auto" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" }}>
                          <Sparkles className="w-2.5 h-2.5" /> AI
                        </span>
                      ) : item.review_status === "approved" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}>
                          ✓ approved
                        </span>
                      ) : item.review_status === "rejected" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" }}>
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
                      <p className="text-sm font-serif leading-relaxed line-clamp-5 break-words">{item.text}</p>
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
                        pct >= 80 ? { label: "High", style: { color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" } as React.CSSProperties, bar: { background: "var(--gd-success)" } as React.CSSProperties }
                        : pct >= 50 ? { label: "Med",  style: { color: "var(--gd-bronze)",   background: "var(--gd-bronze-soft)",  borderColor: "color-mix(in srgb, var(--gd-bronze) 45%, transparent)" } as React.CSSProperties, bar: { background: "var(--gd-bronze)" } as React.CSSProperties }
                        : { label: "Low",  style: { color: "var(--gd-danger)",  background: "var(--gd-danger-soft)", borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)" } as React.CSSProperties, bar: { background: "var(--gd-danger)" } as React.CSSProperties };
                      return (
                        <div className="flex flex-col items-end gap-0.5" title={`Confidence: ${pct.toFixed(1)}% (estimated) — ${tier.label === "High" ? "Well-evidenced: corroborated by multiple sources, recent, reviewed" : tier.label === "Med" ? "Partially evidenced: some corroboration or review present" : "Thin evidence: single source, unreviewed, or old — verify before relying on this"}`}>
                          <span className="text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border" style={tier.style}>
                            {pct.toFixed(0)}% {tier.label}
                          </span>
                          <div className="w-12 h-1 rounded-full bg-muted overflow-hidden">
                            <div
                              className="h-full rounded-full transition-all"
                              style={{ width: `${pct}%`, ...tier.bar }}
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
                          className="min-h-11 min-w-11 flex items-center justify-center rounded transition-colors disabled:opacity-40"
                          style={isApproved ? { color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)" } : undefined}
                        >
                          <ThumbsUp className="w-3.5 h-3.5" />
                        </button>
                        <button
                          disabled={isReviewing || isRejected}
                          onClick={() => handleReview(item.id!, "rejected", isApproved)}
                          title="Dismiss"
                          className="min-h-11 min-w-11 flex items-center justify-center rounded transition-colors disabled:opacity-40"
                          style={isRejected ? { color: "var(--gd-danger)", background: "var(--gd-danger-soft)" } : undefined}
                        >
                          <ThumbsDown className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                    <ConfirmAction
                      title="Delete knowledge item?"
                      consequence="This knowledge item will be permanently removed from this work. This cannot be undone."
                      confirmLabel="Delete"
                      destructive
                      onConfirm={() => handleDeleteKnowledge(item.id!)}
                      trigger={
                        <button
                          title="Delete item"
                          data-testid={`delete-knowledge-${item.id}`}
                          className="min-h-11 min-w-11 flex items-center justify-center rounded text-muted-foreground/40 hover:text-destructive hover:bg-destructive/5 transition-colors"
                        >
                          <Trash2 className="w-3.5 h-3.5" />
                        </button>
                      }
                    />
                  </div>
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      ) : searchText.trim() ? (
        <EmptyState
          icon={<Search />}
          title={`No knowledge items match "${searchText}"`}
          description="Try a different search or clear the filter to see all items."
          action={
            <Button variant="outline" size="sm" className="min-h-11" onClick={() => setSearchText("")}>
              Clear filter
            </Button>
          }
        />
      ) : (
        <EmptyState
          icon={<Sparkles />}
          title="No knowledge extracted yet"
          description="Link a document and Orivellum will extract concepts, facts, and excerpts automatically."
        />
      )}
    </div>
  );
}

// ─── Tasks tab ────────────────────────────────────────────────────────────────

