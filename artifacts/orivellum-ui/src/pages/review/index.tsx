/**
 * Review Queue — /review
 *
 * Unified governance inbox aggregating every item that needs a human decision
 * before the system treats it as fact:
 *   - knowledge   : AI-extracted knowledge awaiting approval
 *   - reclassify  : documents flagged for reclassification
 *   - suggestion  : system suggestions (work assignments, version links, …)
 *   - duplicate   : unresolved near-duplicate document pairs
 *
 * Items are sorted most-uncertain first (confidence ascending). Each card
 * offers Approve / Reject / Defer (defer snoozes for 7 days).
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Inbox, ThumbsUp, ThumbsDown, Clock, CheckCircle2, Sparkles,
  RefreshCw, Copy, FileQuestion, Lightbulb, Loader2, ExternalLink,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReviewItem {
  id: string;
  item_type: "knowledge" | "reclassify" | "suggestion" | "duplicate";
  title: string;
  description: string;
  confidence: number | null;
  work_id: string | null;
  work_title: string | null;
  evidence: Record<string, unknown>;
  created_at: string;
}

interface QueueResponse {
  items: ReviewItem[];
  count: number;
  counts_by_type: Record<string, number>;
}

// ── Type styling ──────────────────────────────────────────────────────────────

const TYPE_META: Record<ReviewItem["item_type"], {
  label: string; icon: typeof Sparkles; badge: string; border: string;
}> = {
  knowledge: {
    label: "AI knowledge", icon: Sparkles,
    badge: "border-violet-200 text-violet-700 bg-violet-50/70",
    border: "border-l-violet-400",
  },
  reclassify: {
    label: "Reclassify", icon: FileQuestion,
    badge: "border-amber-200 text-amber-700 bg-amber-50/70",
    border: "border-l-amber-400",
  },
  suggestion: {
    label: "Suggestion", icon: Lightbulb,
    badge: "border-sky-200 text-sky-700 bg-sky-50/70",
    border: "border-l-sky-400",
  },
  duplicate: {
    label: "Duplicate", icon: Copy,
    badge: "border-rose-200 text-rose-700 bg-rose-50/70",
    border: "border-l-rose-400",
  },
};

type TypeFilter = "all" | ReviewItem["item_type"];

function confidenceColor(c: number | null): string {
  if (c == null) return "bg-muted-foreground/40";
  if (c < 0.5) return "bg-red-400";
  if (c < 0.8) return "bg-amber-400";
  return "bg-emerald-400";
}

// ── Evidence rendering ────────────────────────────────────────────────────────

function EvidenceLine({ item }: { item: ReviewItem }) {
  const ev = item.evidence ?? {};
  const parts: React.ReactNode[] = [];

  if (item.item_type === "knowledge") {
    if (ev.subject && ev.predicate) {
      parts.push(
        <span key="spo" className="font-mono text-[11px]">
          {String(ev.subject)} → {String(ev.predicate)}{ev.object ? ` → ${String(ev.object)}` : ""}
        </span>,
      );
    }
    if (ev.source_doc && ev.source_doc_id) {
      parts.push(
        <Link key="doc" href={`/library/${ev.source_doc_id}`}
              className="inline-flex items-center gap-1 text-primary hover:underline">
          <ExternalLink className="w-3 h-3" />{String(ev.source_doc)}
        </Link>,
      );
    }
  } else if (item.item_type === "reclassify" && ev.doc_id) {
    parts.push(
      <Link key="doc" href={`/library/${ev.doc_id}`}
            className="inline-flex items-center gap-1 text-primary hover:underline">
        <ExternalLink className="w-3 h-3" />{String(ev.doc_title ?? "Document")}
      </Link>,
      <span key="kind" className="font-mono text-[11px]">
        currently: {String(ev.current_kind ?? "?")}
      </span>,
    );
  } else if (item.item_type === "duplicate") {
    for (const side of ["a", "b"] as const) {
      const id = ev[`doc_${side}_id`];
      if (id) {
        parts.push(
          <Link key={side} href={`/library/${id}`}
                className="inline-flex items-center gap-1 text-primary hover:underline">
            <ExternalLink className="w-3 h-3" />{String(ev[`doc_${side}_title`] ?? `Document ${side.toUpperCase()}`)}
          </Link>,
        );
      }
    }
  } else if (item.item_type === "suggestion" && Array.isArray(ev.doc_ids)) {
    parts.push(
      <span key="n" className="font-mono text-[11px]">{(ev.doc_ids as unknown[]).length} documents</span>,
    );
  }

  if (item.work_title && item.work_id) {
    parts.push(
      <Link key="work" href={`/works/${item.work_id}`}
            className="text-muted-foreground hover:text-foreground hover:underline">
        in {item.work_title}
      </Link>,
    );
  }

  if (parts.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
      {parts}
    </div>
  );
}

// ── Card ──────────────────────────────────────────────────────────────────────

function ReviewCard({ item, onResolved }: { item: ReviewItem; onResolved: () => void }) {
  const [pending, setPending] = useState<"approve" | "reject" | "defer" | null>(null);
  const isDupe = item.item_type === "duplicate";
  const [canonical, setCanonical] = useState<string | null>(
    isDupe ? String(item.evidence?.doc_a_id ?? "") || null : null,
  );
  const meta = TYPE_META[item.item_type];
  const Icon = meta.icon;

  const resolve = async (decision: "approve" | "reject" | "defer") => {
    setPending(decision);
    try {
      const r = await apiFetch(`${BASE}/review/${item.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          decision,
          reason: "",
          ...(isDupe && decision === "approve" && canonical
            ? { canonical_doc_id: canonical }
            : {}),
        }),
      });
      if (!r.ok) throw new Error(`Resolve failed (${r.status})`);
      toast.success(
        decision === "approve" ? "Approved" :
        decision === "reject" ? "Rejected" : "Deferred for 7 days",
      );
      onResolved();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Resolve failed");
    } finally {
      setPending(null);
    }
  };

  const pct = item.confidence != null ? Math.round(item.confidence * 100) : null;

  return (
    <div className={`border border-l-4 ${meta.border} rounded-lg bg-card p-4 space-y-2`}
         data-testid={`review-card-${item.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Badge variant="outline" className={`shrink-0 gap-1 ${meta.badge}`}>
            <Icon className="w-3 h-3" />{meta.label}
          </Badge>
          <span className="text-sm font-medium truncate">{item.title}</span>
        </div>
        {pct != null && (
          <div className="flex items-center gap-1.5 shrink-0" title={`Confidence ${pct}%`}>
            <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className={`h-full ${confidenceColor(item.confidence)}`}
                   style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[10px] font-mono text-muted-foreground">{pct}%</span>
          </div>
        )}
      </div>

      <p className="text-sm text-foreground/80 whitespace-pre-wrap break-words">{item.description}</p>
      <EvidenceLine item={item} />

      {isDupe && (
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <span className="text-muted-foreground">Keep on approve:</span>
          {(["a", "b"] as const).map(side => {
            const id = String(item.evidence?.[`doc_${side}_id`] ?? "");
            if (!id) return null;
            const title = String(item.evidence?.[`doc_${side}_title`] ?? `Document ${side.toUpperCase()}`);
            return (
              <button key={side} onClick={() => setCanonical(id)}
                      className={`px-2 py-0.5 rounded-md border transition-colors truncate max-w-[180px] ${
                        canonical === id
                          ? "border-primary/50 bg-primary/10 text-primary font-medium"
                          : "border-border text-muted-foreground hover:text-foreground"
                      }`}
                      data-testid={`canonical-${side}-${item.id}`}>
                {title}
              </button>
            );
          })}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="outline" disabled={pending != null}
                onClick={() => resolve("approve")}
                className="h-7 gap-1.5 text-emerald-700 border-emerald-200 hover:bg-emerald-50"
                data-testid={`approve-${item.id}`}>
          {pending === "approve" ? <Loader2 className="w-3 h-3 animate-spin" /> : <ThumbsUp className="w-3 h-3" />}
          Approve
        </Button>
        <Button size="sm" variant="outline" disabled={pending != null}
                onClick={() => resolve("reject")}
                className="h-7 gap-1.5 text-red-600 border-red-200 hover:bg-red-50"
                data-testid={`reject-${item.id}`}>
          {pending === "reject" ? <Loader2 className="w-3 h-3 animate-spin" /> : <ThumbsDown className="w-3 h-3" />}
          Reject
        </Button>
        <Button size="sm" variant="ghost" disabled={pending != null}
                onClick={() => resolve("defer")}
                className="h-7 gap-1.5 text-muted-foreground"
                data-testid={`defer-${item.id}`}>
          {pending === "defer" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Clock className="w-3 h-3" />}
          Defer
        </Button>
      </div>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

const FILTERS: { key: TypeFilter; label: string }[] = [
  { key: "all",        label: "All" },
  { key: "knowledge",  label: "AI knowledge" },
  { key: "suggestion", label: "Suggestions" },
  { key: "duplicate",  label: "Duplicates" },
  { key: "reclassify", label: "Reclassify" },
];

export default function ReviewPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<TypeFilter>("all");

  const { data, isLoading, isFetching, refetch } = useQuery<QueueResponse>({
    queryKey: ["review-queue"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/review/queue`);
      if (!r.ok) throw new Error("Failed to load review queue");
      return r.json();
    },
    refetchInterval: 60_000,
  });

  const items = (data?.items ?? []).filter(i => filter === "all" || i.item_type === filter);
  const counts = data?.counts_by_type ?? {};

  const onResolved = () => {
    refetch();
    qc.invalidateQueries({ queryKey: ["review-queue-count"] });
  };

  return (
    <div className="max-w-3xl mx-auto p-4 md:p-6 space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Inbox className="w-5 h-5 text-primary" />
          <h1 className="text-lg font-semibold">Review Queue</h1>
          {data && data.count > 0 && (
            <Badge variant="secondary" className="font-mono">{data.count}</Badge>
          )}
        </div>
        <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}
                className="h-7 gap-1.5" data-testid="refresh-queue">
          <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      <p className="text-xs text-muted-foreground">
        Everything here needs your decision before it becomes fact. Most uncertain items first.
        Deferring snoozes an item for 7 days.
      </p>

      <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg w-fit flex-wrap">
        {FILTERS.map(f => {
          const n = f.key === "all" ? (data?.count ?? 0) : (counts[f.key] ?? 0);
          return (
            <button key={f.key} onClick={() => setFilter(f.key)}
                    className={`px-2.5 py-1 rounded-md text-xs transition-colors ${
                      filter === f.key
                        ? "bg-background shadow-sm font-medium"
                        : "text-muted-foreground hover:text-foreground"
                    }`}
                    data-testid={`filter-${f.key}`}>
              {f.label}{n > 0 && <span className="ml-1 font-mono text-[10px] opacity-60">{n}</span>}
            </button>
          );
        })}
      </div>

      {isLoading ? (
        <div className="space-y-3">
          {[...Array(4)].map((_, i) => <Skeleton key={i} className="h-28 rounded-lg" />)}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-16 text-center space-y-2">
          <CheckCircle2 className="w-10 h-10 text-emerald-400" />
          <p className="text-sm font-medium">All clear</p>
          <p className="text-xs text-muted-foreground">
            {filter === "all"
              ? "Nothing needs your review right now."
              : "No items of this type need review."}
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <ReviewCard key={item.id} item={item} onResolved={onResolved} />
          ))}
        </div>
      )}
    </div>
  );
}
