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
  ShieldCheck,
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
  finding_key?: string;
}
// Chao1 + Good–Turing coverage estimate — an UPPER bound on entity/term
// coverage ("at most"), with estimated unseen counts. Replaces the removed
// self-referential coverage_pct.
interface CoverageEstimate {
  n: number; s_obs: number; f1: number; f2: number;
  s_est: number | null;
  unseen_est: number | null; unseen_low: number | null; unseen_high: number | null;
  good_turing: number | null;
  completeness: number | null;
  band: "under_sampled" | "moderate" | "well_sampled" | "no_data";
  summary: string;
}
interface CoverageClass extends CoverageEstimate { class: string }
interface CoverageReport {
  method: string; framing: string; scope_note: string;
  overall: CoverageEstimate;
  classes: CoverageClass[];
  under_sampled_classes: string[];
  well_sampled_classes: string[];
}
interface GapReport {
  coverage: CoverageReport | null; total_chapters: number | null;
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

// ── Completeness assertions (review §4.1) ────────────────────────────────────
// Signed "I have all of X" region closures — the opposite sign of a gap.
// Visible and retractable right beside the gaps they silence.

interface CompletenessAssertion {
  id: string;
  gap_class: string;
  scope: string;
  basis: string;
  no_value: number;
  status: "proposed" | "active" | "retracted";
  signed_by: string;
  updated_at: string;
}

const GAP_CLASS_OPTIONS = [
  "citation_closure",
  "mentioned_never_explained",
  "dead_end_citation",
  "failure_clustering",
  "domain_coverage",
  "domain_frontier",
  "graph_pair",
];

function CompletenessAssertions({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [showForm, setShowForm] = useState(false);
  const [gapClass, setGapClass] = useState("");
  const [scope, setScope] = useState("");
  const [basis, setBasis] = useState("");
  const [signedBy, setSignedBy] = useState("");
  const [noValue, setNoValue] = useState(false);

  const { data } = useQuery<{ assertions: CompletenessAssertion[] }>({
    queryKey: ["work-completeness", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/completeness-assertions`).then((r) => {
        if (!r.ok) throw new Error("completeness fetch failed");
        return r.json();
      }),
    staleTime: 60_000,
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["work-completeness", workId] });
    queryClient.invalidateQueries({ queryKey: ["work-gaps", workId] });
  };

  const assertMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${WORK_API_BASE}/works/${workId}/completeness-assertions`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          gap_class: gapClass, scope, basis, signed_by: signedBy, no_value: noValue,
        }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || "assert failed");
      return r.json();
    },
    onSuccess: (row: { closed_gap_ids?: string[] }) => {
      const n = row.closed_gap_ids?.length ?? 0;
      toast.success(
        n > 0
          ? `Region asserted complete — ${n} open gap${n === 1 ? "" : "s"} closed`
          : "Region asserted complete"
      );
      setShowForm(false); setGapClass(""); setScope(""); setBasis(""); setNoValue(false);
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const retractMutation = useMutation({
    mutationFn: async ({ id, reason, signed_by }: { id: string; reason: string; signed_by: string }) => {
      const r = await apiFetch(`${WORK_API_BASE}/completeness-assertions/${id}/retract`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason, signed_by }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || "retract failed");
      return r.json();
    },
    onSuccess: (row: { reopened_gap_ids?: string[] }) => {
      const n = row.reopened_gap_ids?.length ?? 0;
      toast.success(
        n > 0
          ? `Assertion retracted — ${n} gap${n === 1 ? "" : "s"} re-opened`
          : "Assertion retracted"
      );
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const ratifyMutation = useMutation({
    mutationFn: async ({ id, signed_by }: { id: string; signed_by: string }) => {
      const r = await apiFetch(`${WORK_API_BASE}/completeness-assertions/${id}/ratify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ signed_by }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail || "ratify failed");
      return r.json();
    },
    onSuccess: (row: { closed_gap_ids?: string[] }) => {
      const n = row.closed_gap_ids?.length ?? 0;
      toast.success(
        n > 0
          ? `Proposal ratified — ${n} open gap${n === 1 ? "" : "s"} closed`
          : "Proposal ratified — region closed"
      );
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const handleRatify = (a: CompletenessAssertion) => {
    const signed_by = window.prompt(
      `Ratify "${a.gap_class} / ${a.scope}" as complete? Sign with your name:`
    );
    if (!signed_by?.trim()) return;
    ratifyMutation.mutate({ id: a.id, signed_by: signed_by.trim() });
  };

  const handleRetract = (a: CompletenessAssertion) => {
    const verb = a.status === "proposed" ? "decline" : "retract";
    const reason = window.prompt(`Why ${verb} "${a.gap_class} / ${a.scope}"? A reason is required.`);
    if (!reason?.trim()) return;
    const signed_by = window.prompt(`Sign the ${verb} (your name):`, a.status === "proposed" ? "" : a.signed_by);
    if (!signed_by?.trim()) return;
    retractMutation.mutate({ id: a.id, reason: reason.trim(), signed_by: signed_by.trim() });
  };

  const all = data?.assertions ?? [];
  const assertions = all.filter((a) => a.status === "active");
  const proposals = all.filter((a) => a.status === "proposed");

  return (
    <div className="p-4 rounded-xl border border-border/50 bg-muted/10 space-y-3">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="w-4 h-4 text-primary" />
          <span className="font-medium text-sm">Closed regions</span>
          <span className="text-[10px] font-mono text-muted-foreground">
            signed “I have all of X” assertions — detectors stop asking
          </span>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="text-[10px] font-mono text-muted-foreground hover:text-foreground"
        >
          {showForm ? "cancel" : "+ assert complete"}
        </button>
      </div>

      {assertions.length === 0 && proposals.length === 0 && !showForm && (
        <p className="text-xs text-muted-foreground">
          No regions asserted complete. When you know you hold everything on a topic,
          assert it here — gap detectors and research runs will stop re-asking.
        </p>
      )}

      {proposals.length > 0 && (
        <div className="space-y-2">
          <p className="text-[10px] font-mono text-muted-foreground">
            {proposals.length} machine-proposed closure{proposals.length === 1 ? "" : "s"} — nothing
            closes until you sign
          </p>
          {proposals.map((a) => (
            <div
              key={a.id}
              className="flex items-start gap-2 px-3 py-2 rounded-lg border border-dashed border-border/60 bg-background/30"
              data-testid={`proposal-${a.id}`}
            >
              <ShieldCheck className="w-3.5 h-3.5 mt-0.5 text-muted-foreground shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-mono font-medium">
                    {a.gap_class} / {a.scope === "*" ? "entire class" : a.scope}
                  </span>
                  <span className="text-[9px] font-mono px-1.5 py-0.5 rounded-full border border-border/60 text-muted-foreground">
                    proposed
                  </span>
                </div>
                <p className="text-[11px] text-muted-foreground leading-snug mt-0.5">{a.basis}</p>
                <p className="text-[10px] font-mono text-muted-foreground/70 mt-0.5">
                  {a.signed_by}
                </p>
              </div>
              <div className="flex items-center gap-2 shrink-0">
                <button
                  onClick={() => handleRatify(a)}
                  disabled={ratifyMutation.isPending}
                  className="text-[10px] font-mono text-emerald-600 hover:text-emerald-500 disabled:opacity-40"
                  data-testid={`button-ratify-${a.id}`}
                >
                  ratify
                </button>
                <button
                  onClick={() => handleRetract(a)}
                  disabled={retractMutation.isPending}
                  className="text-[10px] font-mono text-muted-foreground hover:text-destructive disabled:opacity-40"
                  data-testid={`button-decline-${a.id}`}
                >
                  decline
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {assertions.map((a) => (
        <div
          key={a.id}
          className="flex items-start gap-2 px-3 py-2 rounded-lg border border-border/60 bg-background/40"
        >
          <ShieldCheck className="w-3.5 h-3.5 mt-0.5 text-emerald-500 shrink-0" />
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-xs font-mono font-medium">
                {a.gap_class} / {a.scope === "*" ? "entire class" : a.scope}
              </span>
              {a.no_value === 1 && (
                <span className="text-[9px] font-mono px-1.5 py-0.5 rounded-full border border-border/60 text-muted-foreground">
                  empty-but-complete
                </span>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground leading-snug mt-0.5">{a.basis}</p>
            <p className="text-[10px] font-mono text-muted-foreground/70 mt-0.5">
              signed {a.signed_by}
            </p>
          </div>
          <button
            onClick={() => handleRetract(a)}
            disabled={retractMutation.isPending}
            className="text-[10px] font-mono text-muted-foreground hover:text-destructive disabled:opacity-40 shrink-0"
          >
            retract
          </button>
        </div>
      ))}

      {showForm && (
        <div className="space-y-2 pt-1 border-t border-border/40">
          <div className="grid grid-cols-2 gap-2">
            <Select value={gapClass} onValueChange={setGapClass}>
              <SelectTrigger className="h-8 text-xs" data-testid="select-assert-class">
                <SelectValue placeholder="Gap class" />
              </SelectTrigger>
              <SelectContent>
                {GAP_CLASS_OPTIONS.map((c) => (
                  <SelectItem key={c} value={c} className="text-xs font-mono">{c}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              value={scope}
              onChange={(e) => setScope(e.target.value)}
              placeholder="Scope (or * for whole class)"
              className="h-8 text-xs font-mono"
              data-testid="input-assert-scope"
            />
          </div>
          <Input
            value={basis}
            onChange={(e) => setBasis(e.target.value)}
            placeholder="Basis — why is this region complete?"
            className="h-8 text-xs"
            data-testid="input-assert-basis"
          />
          <div className="flex items-center gap-2">
            <Input
              value={signedBy}
              onChange={(e) => setSignedBy(e.target.value)}
              placeholder="Signed by"
              className="h-8 text-xs w-40"
              data-testid="input-assert-signer"
            />
            <label className="flex items-center gap-1.5 text-[11px] text-muted-foreground cursor-pointer">
              <Checkbox
                checked={noValue}
                onCheckedChange={(v) => setNoValue(v === true)}
                data-testid="checkbox-assert-novalue"
              />
              empty-but-complete
            </label>
            <div className="flex-1" />
            <Button
              size="sm"
              className="h-8 text-xs"
              disabled={
                assertMutation.isPending ||
                !gapClass || !scope.trim() || !basis.trim() || !signedBy.trim()
              }
              onClick={() => assertMutation.mutate()}
              data-testid="button-assert-complete"
            >
              {assertMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : "Assert complete"}
            </Button>
          </div>
          <p className="text-[10px] font-mono text-muted-foreground">
            Open gaps in the region close with this assertion cited; retracting it
            re-opens exactly those gaps. Everything is signed and ledgered.
          </p>
        </div>
      )}
    </div>
  );
}

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

  /** Permanently dismiss a hygiene finding — it never reappears. */
  const dismissFinding = async (findingKey?: string) => {
    if (!findingKey) return;
    setActionPending(findingKey);
    try {
      const r = await apiFetch(`${WORK_API_BASE}/works/${workId}/hygiene/dismiss`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ finding_key: findingKey, reason: "dismissed from Hygiene tab" }),
      });
      if (r.ok) {
        toast.success("Finding dismissed — it won't come back");
        refetch();
      } else { toast.error("Could not dismiss finding"); }
    } catch { toast.error("Network error"); }
    finally { setActionPending(null); }
  };

  if (isLoading) return (
    <div className="space-y-3">{[1,2,3].map(i => <Skeleton key={i} className="h-20 w-full" />)}</div>
  );
  if (error || !data) return (
    <div className="text-center py-16 text-muted-foreground border border-dashed rounded-lg">
      <AlertTriangle className="w-8 h-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">Could not load hygiene analysis.</p>
    </div>
  );

  const byKind = data.gaps.reduce<Record<string, GapItem[]>>((acc, g) => {
    (acc[g.severity] ??= []).push(g);
    return acc;
  }, {});

  return (
    <div className="space-y-6">
      {/* Coverage estimate — Chao1/Good–Turing upper bound, never a bare % */}
      <div className="p-4 rounded-xl border border-border/50 bg-muted/10 space-y-2">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <TrendingUp className="w-4 h-4 text-primary" />
            <span className="font-medium text-sm">Entity coverage (estimated)</span>
          </div>
          <div className="flex items-center gap-2">
            <Link
              href={`/works/${workId}/gap-oracle`}
              className="text-[10px] font-mono text-muted-foreground hover:text-foreground"
            >
              oracle
            </Link>
            <span className="text-lg font-mono font-bold">
              {data.coverage?.overall?.completeness != null
                ? `≤ ${Math.round(data.coverage.overall.completeness * 100)}%`
                : "—"}
            </span>
            <button
              onClick={() => { setForceRefresh(true); refetch(); }}
              disabled={isFetching}
              className="text-[10px] font-mono text-muted-foreground hover:text-foreground"
            >
              {isFetching ? "…" : "refresh"}
            </button>
          </div>
        </div>
        {data.coverage?.overall?.completeness != null ? (
          <>
            <p className="text-xs text-muted-foreground">{data.coverage.overall.summary}</p>
            {data.coverage.classes.length > 0 && (
              <div className="flex flex-wrap gap-1.5 pt-1">
                {data.coverage.classes.map((c) => (
                  <span
                    key={c.class}
                    title={c.summary}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full border text-[10px] font-mono"
                    style={
                      c.band === "under_sampled" ? GAP_SEVERITY_STYLE.high :
                      c.band === "well_sampled" ? GAP_SEVERITY_STYLE.low :
                      GAP_SEVERITY_STYLE.medium
                    }
                  >
                    {c.class}
                    {" ≤"}{c.completeness != null ? `${Math.round(c.completeness * 100)}%` : "—"}
                    {c.unseen_est != null && ` · ~${Math.round(c.unseen_est)} unseen`}
                    {c.band === "under_sampled" && " · under-sampled"}
                    {c.band === "well_sampled" && " · well-sampled"}
                  </span>
                ))}
              </div>
            )}
          </>
        ) : (
          <p className="text-xs text-muted-foreground">
            No entity mentions extracted yet — coverage cannot be estimated.
          </p>
        )}
        <p className="text-[10px] font-mono text-muted-foreground">
          Upper bound (Chao1 + Good–Turing) over entity mentions — measures what's been
          sampled, not understanding.
        </p>
      </div>

      {/* Completeness assertions — signed "I have all of X" region closures */}
      <CompletenessAssertions workId={workId} />

      {/* Hygiene findings list */}
      {data.gaps.length === 0 ? (
        <div className="text-center py-10 border border-dashed rounded-lg text-muted-foreground text-sm">
          No hygiene findings — all chapters have sufficient research coverage.
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
                      {/* One-click actions — Add task is available on all finding kinds */}
                      <div className="flex items-center justify-end gap-3 mt-2 pt-2 border-t border-current/10">
                        {g.finding_key && (
                          <button
                            disabled={actionPending === g.finding_key}
                            onClick={() => dismissFinding(g.finding_key)}
                            className="flex items-center gap-1.5 text-[11px] font-mono opacity-50 hover:opacity-100 disabled:opacity-30 transition-opacity"
                            title="Dismiss permanently — this finding will never reappear"
                          >
                            <X className="w-3 h-3" />
                            Dismiss
                          </button>
                        )}
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

      {/* Interpretive frame (Domain Model) — G-M5/G-M6 */}
      <DomainModelSection workId={workId} />
    </div>
  );
}

// ─── Interpretive frame (Domain Model) ────────────────────────────────────────

interface DomainSource {
  id: string; domain: string; doc_id: string; kind: string; doc_title?: string | null;
}
interface DomainNode {
  id: string; domain: string; node_key: string; label: string; status: string;
  node_class: string; agreement: number; source_count: number; centrality: number;
}
interface RecallPeer {
  mode: string; peer_title?: string; peer_total: number; matched: number;
  relative_recall: number; missing: { cited: string }[];
}

const NODE_CLASS_STYLE: Record<string, React.CSSProperties> = {
  required:  { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" },
  contested: { borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)", color: "var(--rust)" },
  optional:  { borderColor: "var(--border)", background: "transparent", color: "var(--muted-foreground)" },
};

function DomainModelSection({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [domainInput, setDomainInput] = useState("");
  const [docPick, setDocPick] = useState("");
  const [kindPick, setKindPick] = useState("structure");
  const [busy, setBusy] = useState<string | null>(null);

  const { data: docsData } = useGetWorkDocuments(workId);
  const docs: any[] = (docsData as any)?.documents ?? [];

  const { data: srcData, refetch: refetchSources } = useQuery<{ sources: DomainSource[] }>({
    queryKey: ["domain-sources", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/domain/sources`).then((r) => {
        if (!r.ok) throw new Error("domain sources fetch failed");
        return r.json();
      }),
  });
  const sources = srcData?.sources ?? [];
  const domains = [...new Set(sources.map((s) => s.domain))];

  const { data: nodeData, refetch: refetchNodes } = useQuery<{ nodes: DomainNode[] }>({
    queryKey: ["domain-nodes", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/domain/nodes`).then((r) => {
        if (!r.ok) throw new Error("domain nodes fetch failed");
        return r.json();
      }),
  });
  const nodes = nodeData?.nodes ?? [];

  const { data: recall } = useQuery<{ peers: RecallPeer[]; note?: string }>({
    queryKey: ["relative-recall", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/relative-recall`).then((r) => {
        if (!r.ok) throw new Error("relative recall fetch failed");
        return r.json();
      }),
    enabled: sources.length > 0,
    staleTime: 120_000,
  });

  const post = async (label: string, path: string, body?: unknown) => {
    setBusy(label);
    try {
      const r = await apiFetch(`${WORK_API_BASE}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      if (!r.ok) {
        const d = await r.json().catch(() => ({}));
        toast.error(d.detail || "Request failed");
        return null;
      }
      return await r.json();
    } catch {
      toast.error("Network error");
      return null;
    } finally {
      setBusy(null);
    }
  };

  const addSource = async () => {
    const domain = domainInput.trim();
    if (!domain || !docPick) { toast.error("Pick a domain name and a document"); return; }
    const d = await post("add-source", `/works/${workId}/domain/sources`, {
      domain, doc_id: docPick, kind: kindPick,
    });
    if (d) { toast.success("Source registered"); setDocPick(""); refetchSources(); }
  };

  const removeSource = async (id: string) => {
    setBusy(id);
    try {
      const r = await apiFetch(`${WORK_API_BASE}/works/${workId}/domain/sources/${id}`, { method: "DELETE" });
      if (r.ok) { refetchSources(); } else { toast.error("Could not remove source"); }
    } catch { toast.error("Network error"); }
    finally { setBusy(null); }
  };

  const harvest = async (domain: string) => {
    const d = await post(`harvest-${domain}`, `/works/${workId}/domain/harvest`, { domain });
    if (d) {
      toast.success(
        `Proposed ${d.proposed} node${d.proposed === 1 ? "" : "s"} from ${d.sources} source${d.sources === 1 ? "" : "s"}` +
        (d.note ? ` — ${d.note}` : "")
      );
      refetchNodes();
    }
  };

  const scan = async () => {
    const d = await post("scan", `/works/${workId}/domain/scan`);
    if (d) {
      toast.success(`Scan done — ${d.coverage.emitted} coverage gap(s), ${d.frontier.emitted} decision(s) owed`);
      queryClient.invalidateQueries({ queryKey: ["work-gaps", workId] });
    }
  };

  const byStatus = (s: string) => nodes.filter((n) => n.status === s);
  const ratified = byStatus("ratified");
  const proposed = byStatus("proposed");
  const frontier = ratified.filter((n) => n.node_class === "contested");

  return (
    <div className="space-y-3 pt-4 border-t border-border/50">
      <div className="flex items-center justify-between">
        <h4 className="text-[11px] font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-2">
          <Network className="w-3.5 h-3.5" /> Interpretive frame (Domain Model)
        </h4>
        {ratified.length > 0 && (
          <button
            onClick={scan}
            disabled={!!busy}
            className="text-[10px] font-mono text-primary/80 hover:text-primary border border-primary/25 rounded px-2 py-0.5 hover:bg-primary/5 transition-colors disabled:opacity-40"
          >
            {busy === "scan" ? "…" : "Scan ratified frame →"}
          </button>
        )}
      </div>
      <p className="text-[11px] text-muted-foreground/70 leading-relaxed">
        This measures the <em>interpretive frame</em> — what triangulated reference structures
        say the domain contains — distinct from the factual-spine detectors above. Nodes are
        harvested from registered reference documents (tables of contents, syllabi, reading
        lists) and generate no gap until you ratify them in the review inbox with a signature.
      </p>

      {/* Source registration */}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          value={domainInput}
          onChange={(e) => setDomainInput(e.target.value)}
          placeholder="Domain name (e.g. theodicy)"
          className="h-7 w-44 text-xs font-mono"
        />
        <Select value={docPick} onValueChange={setDocPick}>
          <SelectTrigger className="h-7 w-52 text-xs"><SelectValue placeholder="Reference document…" /></SelectTrigger>
          <SelectContent>
            {docs.map((d: any) => (
              <SelectItem key={d.id} value={d.id} className="text-xs">{d.title}</SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select value={kindPick} onValueChange={setKindPick}>
          <SelectTrigger className="h-7 w-32 text-xs"><SelectValue /></SelectTrigger>
          <SelectContent>
            <SelectItem value="structure" className="text-xs">structure</SelectItem>
            <SelectItem value="bibliography" className="text-xs">bibliography</SelectItem>
          </SelectContent>
        </Select>
        <Button size="sm" variant="outline" className="h-7 text-xs" disabled={!!busy} onClick={addSource}>
          {busy === "add-source" ? "…" : "Register source"}
        </Button>
      </div>

      {/* Registered sources, grouped by domain, with harvest buttons */}
      {domains.map((domain) => {
        const ds = sources.filter((s) => s.domain === domain);
        const structural = ds.filter((s) => s.kind === "structure").length;
        return (
          <div key={domain} className="p-3 rounded-lg border border-border/50 bg-muted/10 space-y-1.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-mono font-medium">{domain}</span>
              <button
                onClick={() => harvest(domain)}
                disabled={!!busy || structural === 0}
                className="text-[10px] font-mono text-muted-foreground hover:text-foreground border border-border/50 rounded px-2 py-0.5 disabled:opacity-40"
                title={structural < 3 ? "Fewer than 3 structure sources — nothing can be proposed as required core" : "Harvest node proposals"}
              >
                {busy === `harvest-${domain}` ? "…" : `Harvest (${structural} structure source${structural === 1 ? "" : "s"})`}
              </button>
            </div>
            {structural > 0 && structural < 3 && (
              <p className="text-[10px] font-mono" style={{ color: "var(--gilt)" }}>
                {structural} of 3 independent sources — required-core triangulation needs at least 3.
              </p>
            )}
            {ds.map((s) => (
              <div key={s.id} className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                <span className="text-[9px] uppercase border border-border/50 rounded px-1">{s.kind}</span>
                <span className="flex-1 truncate">{s.doc_title || s.doc_id}</span>
                <button onClick={() => removeSource(s.id)} disabled={!!busy} className="opacity-40 hover:opacity-100">
                  <X className="w-3 h-3" />
                </button>
              </div>
            ))}
          </div>
        );
      })}

      {/* Node list */}
      {nodes.length > 0 && (
        <div className="space-y-1.5">
          {proposed.length > 0 && (
            <p className="text-[10px] font-mono text-muted-foreground">
              {proposed.length} proposal{proposed.length === 1 ? "" : "s"} awaiting your signature in the{" "}
              <Link href="/review" className="underline hover:text-foreground">review inbox</Link>.
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {nodes.filter((n) => n.status !== "rejected").map((n) => (
              <span
                key={n.id}
                className="text-[10px] font-mono border rounded px-1.5 py-0.5"
                style={NODE_CLASS_STYLE[n.node_class] ?? NODE_CLASS_STYLE.optional}
                title={`${n.node_class} · agreement ${n.agreement}/${n.source_count} · ${n.status}`}
              >
                {n.label}
                {n.status === "proposed" && <span className="opacity-50"> ?</span>}
                {n.status === "ratified" && <span className="opacity-70"> ✓</span>}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Decisions owed (G4 frontier) */}
      {frontier.length > 0 && (
        <div className="p-3 rounded-lg border space-y-1" style={NODE_CLASS_STYLE.contested}>
          <p className="text-xs font-medium">Decisions owed ({frontier.length})</p>
          <p className="text-[11px] opacity-80 leading-relaxed">
            Reference structures disagree on where these belong. That is a scoping decision for
            you, not a deficiency — scan routes them to the decision queue, never as critical gaps.
          </p>
          <p className="text-[11px] font-mono opacity-70">{frontier.map((n) => n.label).join(" · ")}</p>
        </div>
      )}

      {/* Relative recall vs peers */}
      {recall && recall.peers.length > 0 && (
        <div className="p-3 rounded-lg border border-border/50 bg-muted/10 space-y-2">
          <p className="text-xs font-medium flex items-center gap-2">
            <BarChart2 className="w-3.5 h-3.5" /> Relative recall vs peer references
          </p>
          {recall.peers.map((p, i) => (
            <div key={i} className="space-y-1">
              <div className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
                <span className="truncate">{p.peer_title || p.mode} ({p.mode})</span>
                <span>{p.matched}/{p.peer_total} · {Math.round(p.relative_recall * 100)}%</span>
              </div>
              <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                <div className="h-full rounded-full" style={{ width: `${Math.round(p.relative_recall * 100)}%`, background: "var(--gilt)" }} />
              </div>
              {p.missing.length > 0 && (
                <p className="text-[10px] font-mono text-muted-foreground/70 truncate" title={p.missing.map((m) => m.cited).join(", ")}>
                  missing: {p.missing.slice(0, 4).map((m) => m.cited).join(", ")}{p.missing.length > 4 ? "…" : ""}
                </p>
              )}
            </div>
          ))}
          {recall.note && <p className="text-[10px] text-muted-foreground/60 leading-snug">{recall.note}</p>}
        </div>
      )}
    </div>
  );
}

// ─── Completeness tab ─────────────────────────────────────────────────────────

