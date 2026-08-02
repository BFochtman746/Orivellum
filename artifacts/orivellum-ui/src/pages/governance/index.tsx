/**
 * Governance Review Queue — /governance
 *
 * Lists all AI-extracted knowledge items awaiting human approval.
 * Users can approve or dismiss items in bulk or one at a time.
 * This is the MONARCH "governance review queue" feature (#151).
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Shield, ThumbsUp, ThumbsDown, RefreshCw, CheckCircle2,
  Sparkles, Link, Info,
} from "lucide-react";
import { useLocation } from "wouter";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface PendingItem {
  id: string; work_id: string | null; kind: string; text: string;
  subject: string | null; predicate: string | null; object: string | null;
  confidence: number | null; review_status: string;
  work_title: string | null; doc_title: string | null; created_at: string;
}

interface GovernanceStats {
  pending: number; approved: number; rejected: number; auto: number; total: number;
}

async function reviewItem(itemId: string, status: "approved" | "rejected") {
  const r = await apiFetch(`${BASE}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status }),
  });
  if (!r.ok) throw new Error("Review failed");
}

// ── Kind filter ────────────────────────────────────────────────────────────────

type KindFilter = "all" | "entity" | "claim" | "relationship";
const KIND_FILTERS: { key: KindFilter; label: string }[] = [
  { key: "all",          label: "All" },
  { key: "entity",       label: "Entities" },
  { key: "claim",        label: "Claims" },
  { key: "relationship", label: "Relationships" },
];

// ── Main page ──────────────────────────────────────────────────────────────────

export default function GovernancePage() {
  const [, navigate] = useLocation();
  const qc = useQueryClient();
  const [reviewing, setReviewing] = useState<Set<string>>(new Set());
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");

  const { data: statsData } = useQuery<GovernanceStats>({
    queryKey: ["governance", "stats"],
    queryFn: () => apiFetch(`${BASE}/governance/stats`).then((r) => r.json()),
    staleTime: 30_000, refetchInterval: 60_000,
  });

  const { data, isLoading, refetch, isFetching } = useQuery<{ items: PendingItem[]; count: number }>({
    queryKey: ["governance", "pending"],
    queryFn: () => apiFetch(`${BASE}/governance/pending?limit=200`).then((r) => r.json()),
    staleTime: 30_000,
  });

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["governance"] });
  };

  const handleReview = async (id: string, status: "approved" | "rejected") => {
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
  };

  const handleApproveAll = async (items: PendingItem[]) => {
    if (!window.confirm(`Approve all ${items.length} visible items?`)) return;
    let ok = 0;
    for (const item of items) {
      try { await reviewItem(item.id, "approved"); ok++; } catch { /* skip */ }
    }
    toast.success(`Approved ${ok} item${ok !== 1 ? "s" : ""}`);
    invalidate();
  };

  const allItems = data?.items ?? [];
  const filtered = kindFilter === "all" ? allItems : allItems.filter((i) => i.kind === kindFilter);

  // Group by work
  const byWork = filtered.reduce<Record<string, { title: string; items: PendingItem[] }>>((acc, item) => {
    const key = item.work_id ?? "__unlinked__";
    if (!acc[key]) acc[key] = { title: item.work_title ?? "Unlinked", items: [] };
    acc[key].items.push(item);
    return acc;
  }, {});

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
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
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

      {/* Kind filter + bulk approve */}
      {allItems.length > 0 && (
        <div className="flex items-center justify-between flex-wrap gap-3">
          <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
            {KIND_FILTERS.map(({ key, label }) => (
              <button
                key={key}
                onClick={() => setKindFilter(key)}
                className={`px-3 py-1.5 rounded text-xs font-mono transition-colors ${
                  kindFilter === key
                    ? "bg-background text-foreground shadow-sm font-semibold"
                    : "text-muted-foreground hover:text-foreground"
                }`}
              >
                {label}
                {key !== "all" && (
                  <span className="ml-1.5 opacity-60">
                    ({allItems.filter(i => i.kind === key).length})
                  </span>
                )}
              </button>
            ))}
          </div>
          {filtered.length > 0 && (
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleApproveAll(filtered)}
              className="gap-1.5 text-xs border-emerald-200 text-emerald-700 hover:bg-emerald-50"
            >
              <CheckCircle2 className="w-3.5 h-3.5" />
              Approve all ({filtered.length})
            </Button>
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
          <p className="font-medium">No items pending review</p>
          <p className="text-sm opacity-70 mt-1">
            AI-extracted knowledge will appear here when documents are processed with AI extraction enabled.
          </p>
          <Button variant="link" size="sm" className="mt-4 text-muted-foreground" onClick={() => navigate("/system")}>
            Configure AI extraction →
          </Button>
        </div>
      ) : (
        <div className="space-y-6">
          {Object.entries(byWork).map(([workId, { title, items }]) => (
            <div key={workId} className="space-y-2">
              {/* Work header */}
              <div className="flex items-center gap-2 pb-1 border-b border-border/30">
                <h3 className="text-sm font-mono font-semibold text-muted-foreground">{title}</h3>
                <Badge variant="outline" className="text-[10px] font-mono">{items.length}</Badge>
                {workId !== "__unlinked__" && (
                  <button
                    onClick={() => navigate(`/works/${workId}`)}
                    className="ml-auto text-[11px] font-mono text-muted-foreground/60 hover:text-primary transition-colors flex items-center gap-1"
                  >
                    <Link className="w-3 h-3" /> Open Work
                  </button>
                )}
              </div>

              {/* Items */}
              {items.map((item) => {
                const isReviewing = reviewing.has(item.id);
                return (
                  <div key={item.id}
                    className="flex items-start gap-3 p-3.5 rounded-lg border border-violet-100 bg-violet-50/30 hover:bg-violet-50/50 transition-colors"
                  >
                    <Sparkles className="w-3.5 h-3.5 text-violet-500 mt-0.5 shrink-0" />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">
                          {item.kind}
                        </Badge>
                        {item.doc_title && (
                          <span className="text-[10px] font-mono text-muted-foreground/60 truncate max-w-[200px]">
                            from {item.doc_title}
                          </span>
                        )}
                        {item.confidence != null && (
                          <span className="text-[10px] font-mono text-muted-foreground/60">
                            {Math.round(item.confidence * 100)}% confidence
                          </span>
                        )}
                      </div>
                      {item.kind === "relationship" && item.subject && item.predicate && item.object ? (
                        <p className="text-sm font-mono">
                          <span className="font-semibold text-primary">{item.subject}</span>
                          {" "}<span className="text-muted-foreground">{item.predicate}</span>{" "}
                          <span className="font-semibold">{item.object}</span>
                        </p>
                      ) : (
                        <p className="text-sm leading-snug">{item.text}</p>
                      )}
                    </div>
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        disabled={isReviewing}
                        onClick={() => handleReview(item.id, "approved")}
                        title="Approve"
                        className="p-1.5 rounded transition-colors text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50 disabled:opacity-40"
                      >
                        <ThumbsUp className="w-3.5 h-3.5" />
                      </button>
                      <button
                        disabled={isReviewing}
                        onClick={() => handleReview(item.id, "rejected")}
                        title="Dismiss"
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
