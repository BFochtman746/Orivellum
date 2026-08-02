/**
 * Governance Review Queue — /governance
 *
 * Lists all AI-extracted knowledge items awaiting human approval.
 * Users can approve or dismiss items in bulk or one at a time.
 *
 * Keyboard shortcuts:
 *   j / ↓  move focus down
 *   k / ↑  move focus up
 *   a       approve focused item
 *   r       reject focused item
 *   Escape  clear focus
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Shield, ThumbsUp, ThumbsDown, RefreshCw, CheckCircle2,
  Sparkles, Link, Keyboard, AlertTriangle,
} from "lucide-react";
import { useLocation } from "wouter";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

interface PendingItem {
  id: string; work_id: string | null; kind: string; text: string;
  subject: string | null; predicate: string | null; object: string | null;
  confidence: number | null; review_status: string;
  work_title: string | null; doc_title: string | null; created_at: string;
}

interface GovernanceStats {
  pending: number; approved: number; rejected: number; auto: number; total: number;
}

// ── Confidence tier ───────────────────────────────────────────────────────────

type ConfidenceTier = "all" | "high" | "medium" | "low";

function getConfidenceTier(c: number | null): "high" | "medium" | "low" {
  if (c == null || c < 0.5) return "low";
  if (c < 0.8) return "medium";
  return "high";
}

const TIER_BADGE: Record<string, string> = {
  high:   "border-emerald-200 text-emerald-700 bg-emerald-50/70",
  medium: "border-amber-200   text-amber-700   bg-amber-50/70",
  low:    "border-red-200     text-red-600     bg-red-50/70",
};
const TIER_LABEL: Record<string, string> = { high: "High", medium: "Med", low: "Low" };

// ── Filter constants ──────────────────────────────────────────────────────────

type KindFilter = "all" | "entity" | "claim" | "relationship";

const KIND_FILTERS: { key: KindFilter; label: string }[] = [
  { key: "all",          label: "All" },
  { key: "entity",       label: "Entities" },
  { key: "claim",        label: "Claims" },
  { key: "relationship", label: "Relationships" },
];

const CONF_FILTERS: { key: ConfidenceTier; label: string }[] = [
  { key: "all",    label: "Any" },
  { key: "high",   label: "≥80%" },
  { key: "medium", label: "50–79%" },
  { key: "low",    label: "<50%" },
];

// ── API helpers ───────────────────────────────────────────────────────────────

async function reviewItem(itemId: string, status: "approved" | "rejected") {
  const r = await apiFetch(`${BASE}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status }),
  });
  if (!r.ok) throw new Error("Review failed");
}

async function batchReview(itemIds: string[], status: "approved" | "rejected"): Promise<number> {
  const r = await apiFetch(`${BASE}/governance/batch-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds, status }),
  });
  if (!r.ok) throw new Error("Batch review failed");
  const data = await r.json();
  return data.updated ?? itemIds.length;
}

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spin({ className }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className ?? "w-3 h-3"}`} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
  );
}

// ── FilterBar ─────────────────────────────────────────────────────────────────

function FilterBar<K extends string>({
  filters, active, counts, onChange,
}: {
  filters: { key: K; label: string }[];
  active: K;
  counts?: Partial<Record<K, number>>;
  onChange: (k: K) => void;
}) {
  return (
    <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg shrink-0">
      {filters.map(({ key, label }) => (
        <button key={key} onClick={() => onChange(key)}
          className={`px-2.5 py-1.5 rounded text-xs font-mono transition-colors ${
            active === key
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}>
          {label}
          {counts && counts[key] != null && key !== ("all" as unknown as K) && (
            <span className="ml-1 opacity-60">({counts[key]})</span>
          )}
        </button>
      ))}
    </div>
  );
}

// ── Conflicts section ─────────────────────────────────────────────────────────

interface Conflict {
  id: string; conflict_type: string; created_at: string;
  a_id: string; a_text: string; a_subject: string | null; a_confidence: number | null;
  b_id: string; b_text: string; b_subject: string | null; b_confidence: number | null;
  work_id: string | null; work_title: string | null;
}

function ConflictsSection() {
  const qc = useQueryClient();
  const [resolving, setResolving] = useState<Set<string>>(new Set());

  const { data } = useQuery<{ conflicts: Conflict[]; count: number }>({
    queryKey: ["governance", "conflicts"],
    queryFn: () => apiFetch(`${BASE}/governance/conflicts`).then((r) => r.json()),
    staleTime: 30_000,
  });

  const conflicts = data?.conflicts ?? [];
  if (conflicts.length === 0) return null;

  const resolve = async (id: string, resolution: "keep_a" | "keep_b" | "keep_both") => {
    setResolving((s) => new Set([...s, id]));
    try {
      const r = await apiFetch(`${BASE}/governance/conflicts/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution }),
      });
      if (!r.ok) throw new Error();
      toast.success(resolution === "keep_both" ? "Kept both claims" : "Conflict resolved");
      qc.invalidateQueries({ queryKey: ["governance"] });
    } catch {
      toast.error("Could not resolve conflict");
    } finally {
      setResolving((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4 text-amber-600" />
        <h2 className="text-sm font-mono font-semibold text-amber-700">
          Contradicting claims ({conflicts.length})
        </h2>
      </div>
      <div className="space-y-2">
        {conflicts.map((c) => {
          const busy = resolving.has(c.id);
          return (
            <div key={c.id} className="rounded-lg border border-amber-200/70 bg-amber-50/30 p-3 space-y-2">
              <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                <Badge variant="outline" className="border-amber-300 text-amber-700 text-[10px]">
                  {c.conflict_type === "negation" ? "Negation" : "Conflicting values"}
                </Badge>
                {c.work_title && <span>{c.work_title}</span>}
              </div>
              <div className="grid sm:grid-cols-2 gap-2">
                {[
                  { label: "A", text: c.a_text, conf: c.a_confidence, res: "keep_a" as const },
                  { label: "B", text: c.b_text, conf: c.b_confidence, res: "keep_b" as const },
                ].map(({ label, text, conf, res }) => (
                  <div key={label} className="rounded border border-border/50 bg-background p-2.5 flex flex-col gap-2">
                    <p className="text-xs font-serif leading-relaxed flex-1">{text}</p>
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono text-muted-foreground">
                        {conf != null ? `${Math.round(conf * 100)}% conf` : "—"}
                      </span>
                      <Button size="sm" variant="outline" disabled={busy}
                        onClick={() => resolve(c.id, res)}
                        className="h-6 text-[11px] gap-1 border-emerald-200 text-emerald-700 hover:bg-emerald-50">
                        {busy ? <Spin /> : <ThumbsUp className="w-3 h-3" />} Keep {label}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex justify-end">
                <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => resolve(c.id, "keep_both")}
                  className="h-6 text-[11px] text-muted-foreground">
                  Keep both (not a real conflict)
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function GovernancePage() {
  const [, navigate] = useLocation();
  const qc = useQueryClient();

  const [reviewing, setReviewing]   = useState<Set<string>>(new Set());
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [confFilter, setConfFilter] = useState<ConfidenceTier>("all");
  const [focusedIdx, setFocusedIdx] = useState<number | null>(null);
  const [bulkPending, setBulkPending] = useState(false);

  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);

  // ── Data ─────────────────────────────────────────────────────────────────────

  const { data: statsData } = useQuery<GovernanceStats>({
    queryKey: ["governance", "stats"],
    queryFn: () => apiFetch(`${BASE}/governance/stats`).then((r) => r.json()),
    staleTime: 30_000, refetchInterval: 60_000,
  });

  const { data, isLoading, refetch, isFetching } = useQuery<{ items: PendingItem[]; count: number }>({
    queryKey: ["governance", "pending"],
    queryFn: () => apiFetch(`${BASE}/governance/pending?limit=500`).then((r) => r.json()),
    staleTime: 30_000,
  });

  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: ["governance"] }),
    [qc],
  );

  // ── Filtering ─────────────────────────────────────────────────────────────

  const allItems = data?.items ?? [];

  const filtered = allItems.filter((i) => {
    if (kindFilter !== "all" && i.kind !== kindFilter) return false;
    if (confFilter !== "all" && getConfidenceTier(i.confidence) !== confFilter) return false;
    return true;
  });

  const kindCounts = Object.fromEntries(
    KIND_FILTERS.filter(f => f.key !== "all").map(f => [f.key, allItems.filter(i => i.kind === f.key).length])
  ) as Partial<Record<KindFilter, number>>;

  // Group by work for display
  const byWork = filtered.reduce<Record<string, { title: string; items: PendingItem[] }>>(
    (acc, item) => {
      const key = item.work_id ?? "__unlinked__";
      if (!acc[key]) acc[key] = { title: item.work_title ?? "Unlinked", items: [] };
      acc[key].items.push(item);
      return acc;
    },
    {},
  );

  // ── Actions ───────────────────────────────────────────────────────────────

  const handleReview = useCallback(async (id: string, status: "approved" | "rejected") => {
    setReviewing((s) => new Set([...s, id]));
    try {
      await reviewItem(id, status);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      invalidate();
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  }, [invalidate]);

  const handleBatchApprove = useCallback(async (items: PendingItem[]) => {
    if (items.length === 0) return;
    if (!window.confirm(`Approve all ${items.length} item${items.length !== 1 ? "s" : ""}?`)) return;
    setBulkPending(true);
    try {
      const updated = await batchReview(items.map((i) => i.id), "approved");
      toast.success(`Approved ${updated} item${updated !== 1 ? "s" : ""}`);
      setFocusedIdx(null);
      invalidate();
    } catch {
      toast.error("Batch approval failed");
    } finally {
      setBulkPending(false);
    }
  }, [invalidate]);

  // ── Keyboard navigation ───────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = i == null ? 0 : Math.min(i + 1, filtered.length - 1);
          itemRefs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return next;
        });
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = i == null ? 0 : Math.max(i - 1, 0);
          itemRefs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return next;
        });
      } else if (e.key === "a" && focusedIdx != null) {
        e.preventDefault();
        const item = filtered[focusedIdx];
        if (item) { handleReview(item.id, "approved"); setFocusedIdx((i) => (i != null && i < filtered.length - 1 ? i : i)); }
      } else if (e.key === "r" && focusedIdx != null) {
        e.preventDefault();
        const item = filtered[focusedIdx];
        if (item) handleReview(item.id, "rejected");
      } else if (e.key === "Escape") {
        setFocusedIdx(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [filtered, focusedIdx, handleReview]);

  // Reset focus when filters change
  useEffect(() => { setFocusedIdx(null); }, [kindFilter, confFilter]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="space-y-6 animate-in fade-in duration-300 max-w-4xl">

      {/* Header */}
      <div className="border-b border-border/50 pb-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Shield className="w-6 h-6 text-primary" />
            <div>
              <h1 className="text-2xl font-serif font-semibold tracking-tight">Governance</h1>
              <p className="text-muted-foreground text-sm font-serif mt-0.5">
                Review AI-extracted knowledge before it becomes a fact.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <span className="hidden sm:flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground/50 border border-border/30 rounded px-2 py-1">
              <Keyboard className="w-3 h-3" /> j/k navigate · a approve · r reject
            </span>
            <button onClick={() => refetch()} disabled={isFetching}
              className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground hover:text-foreground transition-colors">
              <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>
        </div>
      </div>

      {/* Stats strip */}
      {statsData && (
        <div className="grid grid-cols-4 gap-3">
          {[
            { label: "Pending",  value: statsData.pending,  cls: statsData.pending > 0 ? "text-amber-600" : "text-muted-foreground" },
            { label: "Approved", value: statsData.approved, cls: "text-emerald-600" },
            { label: "Rejected", value: statsData.rejected, cls: "text-red-600" },
            { label: "Total",    value: statsData.total,    cls: "text-muted-foreground" },
          ].map(({ label, value, cls }) => (
            <div key={label} className="p-3 rounded-lg border border-border/50 bg-muted/10 text-center">
              <p className={`text-2xl font-mono font-bold ${cls}`}>{value}</p>
              <p className="text-[11px] font-mono text-muted-foreground uppercase tracking-wide mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Contradicting claims */}
      <ConflictsSection />

      {/* Filters + bulk approve */}
      {allItems.length > 0 && (
        <div className="space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <FilterBar filters={KIND_FILTERS} active={kindFilter} counts={kindCounts} onChange={setKindFilter} />
            <FilterBar filters={CONF_FILTERS} active={confFilter} onChange={setConfFilter} />
          </div>

          {filtered.length > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-xs font-mono text-muted-foreground">
                {filtered.length} item{filtered.length !== 1 ? "s" : ""}
                {focusedIdx != null && (
                  <span className="text-primary/60"> · {focusedIdx + 1}/{filtered.length} focused</span>
                )}
              </p>
              <Button size="sm" variant="outline"
                onClick={() => handleBatchApprove(filtered)}
                disabled={bulkPending}
                className="gap-1.5 text-xs border-emerald-200 text-emerald-700 hover:bg-emerald-50">
                {bulkPending
                  ? <><Spin /> Approving…</>
                  : <><CheckCircle2 className="w-3.5 h-3.5" /> Approve all ({filtered.length})</>}
              </Button>
            </div>
          )}
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <div className="space-y-3">
          {[1,2,3,4,5].map(i => <Skeleton key={i} className="h-20 w-full" />)}
        </div>
      ) : filtered.length === 0 ? (
        <div className="text-center py-20 border border-dashed rounded-xl text-muted-foreground">
          <CheckCircle2 className="w-10 h-10 mx-auto mb-4 opacity-40 text-emerald-500" />
          <p className="font-medium">
            {allItems.length > 0 ? "No items match the current filters" : "No items pending review"}
          </p>
          <p className="text-sm opacity-70 mt-1">
            {allItems.length > 0
              ? "Try a different filter combination."
              : "AI-extracted knowledge will appear here when documents are processed with AI extraction enabled."}
          </p>
          {allItems.length === 0 ? (
            <Button variant="link" size="sm" className="mt-4 text-muted-foreground" onClick={() => navigate("/system")}>
              Configure AI extraction →
            </Button>
          ) : (
            <Button variant="link" size="sm" className="mt-2 text-muted-foreground"
              onClick={() => { setKindFilter("all"); setConfFilter("all"); }}>
              Clear filters
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(byWork).map(([workId, { title, items: workItems }]) => (
            <div key={workId} className="space-y-2">

              {/* Work header */}
              <div className="flex items-center gap-2 pb-1 border-b border-border/30">
                <h3 className="text-sm font-mono font-semibold text-muted-foreground truncate">{title}</h3>
                <Badge variant="outline" className="text-[10px] font-mono shrink-0">{workItems.length}</Badge>
                {workId !== "__unlinked__" && (
                  <button onClick={() => navigate(`/works/${workId}`)}
                    className="text-[11px] font-mono text-muted-foreground/60 hover:text-primary transition-colors flex items-center gap-1 shrink-0">
                    <Link className="w-3 h-3" /> Open Work
                  </button>
                )}
                <div className="ml-auto shrink-0">
                  <Button size="sm" variant="ghost"
                    onClick={() => handleBatchApprove(workItems)}
                    disabled={bulkPending}
                    className="h-6 px-2 text-[10px] font-mono gap-1 text-emerald-700 hover:bg-emerald-50/50">
                    <CheckCircle2 className="w-3 h-3" /> Approve {workItems.length}
                  </Button>
                </div>
              </div>

              {/* Items */}
              {workItems.map((item) => {
                const globalIdx = filtered.indexOf(item);
                const isFocused = focusedIdx === globalIdx;
                const isReviewing = reviewing.has(item.id);
                const tier = getConfidenceTier(item.confidence);

                return (
                  <div key={item.id}
                    ref={(el) => { itemRefs.current[globalIdx] = el; }}
                    onClick={() => setFocusedIdx(isFocused ? null : globalIdx)}
                    className={`flex items-start gap-3 p-3.5 rounded-lg border transition-all cursor-pointer select-none ${
                      isFocused
                        ? "border-primary/40 bg-primary/5 ring-1 ring-primary/20 shadow-sm"
                        : "border-violet-100 bg-violet-50/30 hover:bg-violet-50/60 hover:border-violet-200"
                    }`}
                  >
                    <Sparkles className="w-3.5 h-3.5 text-violet-500 mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <Badge variant="outline"
                          className="text-[10px] uppercase font-mono border-primary/30 text-primary shrink-0">
                          {item.kind}
                        </Badge>
                        {item.confidence != null && (
                          <Badge variant="outline"
                            className={`text-[10px] font-mono shrink-0 ${TIER_BADGE[tier]}`}>
                            {TIER_LABEL[tier]} {Math.round(item.confidence * 100)}%
                          </Badge>
                        )}
                        {item.doc_title && (
                          <span className="text-[10px] font-mono text-muted-foreground/60 truncate">
                            {item.doc_title}
                          </span>
                        )}
                        {isFocused && (
                          <span className="text-[10px] font-mono text-primary/50 ml-auto hidden sm:block shrink-0">
                            a · r
                          </span>
                        )}
                      </div>
                      {item.kind === "relationship" && item.subject && item.predicate && item.object ? (
                        <p className="text-sm font-mono leading-relaxed">
                          <span className="font-semibold text-primary">{item.subject}</span>
                          {" "}<span className="text-muted-foreground italic">{item.predicate}</span>{" "}
                          <span className="font-semibold">{item.object}</span>
                        </p>
                      ) : (
                        <p className="text-sm leading-snug">{item.text}</p>
                      )}
                    </div>

                    {/* Action buttons */}
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        disabled={isReviewing}
                        onClick={(e) => { e.stopPropagation(); handleReview(item.id, "approved"); }}
                        title="Approve (a)"
                        className="p-1.5 rounded transition-colors text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
                      >
                        {isReviewing ? <Spin /> : <ThumbsUp className="w-3.5 h-3.5" />}
                      </button>
                      <button
                        disabled={isReviewing}
                        onClick={(e) => { e.stopPropagation(); handleReview(item.id, "rejected"); }}
                        title="Reject (r)"
                        className="p-1.5 rounded transition-colors text-muted-foreground hover:text-red-600 hover:bg-red-50 disabled:opacity-40"
                      >
                        <ThumbsDown className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

    </div>
  );
}
