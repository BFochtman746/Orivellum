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
import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Page, EmptyState, ErrorState, LoadingState } from "@/components/primitives";
import { toast } from "sonner";
import {
  ThumbsUp, ThumbsDown, Clock, CheckCircle2, Sparkles,
  RefreshCw, Copy, FileQuestion, Lightbulb, Loader2, ExternalLink,
  ShieldAlert,
  NotebookPen,
  ScrollText,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

interface ReviewItem {
  id: string;
  item_type: "knowledge" | "reclassify" | "suggestion" | "duplicate" | "quarantine" | "noteblock" | "canon_fact" | "work_proposal";
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
  label: string; icon: typeof Sparkles;
  badgeCls: string; badgeStyle: Record<string, string>;
  borderStyle: Record<string, string>;
}> = {
  knowledge: {
    label: "AI knowledge", icon: Sparkles,
    badgeCls: "border",
    badgeStyle: { borderColor: 'color-mix(in srgb, var(--gd-bronze) 40%, transparent)', color: 'var(--gd-bronze)', background: 'var(--gd-bronze-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-bronze)' },
  },
  reclassify: {
    label: "Reclassify", icon: FileQuestion,
    badgeCls: "border",
    badgeStyle: { borderColor: 'var(--gd-danger)', color: 'var(--gd-danger)', background: 'var(--gd-danger-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-danger)' },
  },
  suggestion: {
    label: "Suggestion", icon: Lightbulb,
    badgeCls: "border",
    badgeStyle: { borderColor: 'var(--gd-success)', color: 'var(--gd-success)', background: 'color-mix(in srgb, var(--gd-success) 14%, transparent)' },
    borderStyle: { borderLeftColor: 'var(--gd-success)' },
  },
  duplicate: {
    label: "Duplicate", icon: Copy,
    badgeCls: "border",
    badgeStyle: { borderColor: 'var(--gd-line-control)', color: 'var(--gd-dim)', background: 'transparent' },
    borderStyle: { borderLeftColor: 'var(--gd-dim)' },
  },
  quarantine: {
    label: "Quarantined", icon: ShieldAlert,
    badgeCls: "border",
    badgeStyle: { borderColor: 'var(--gd-danger)', color: 'var(--gd-danger)', background: 'var(--gd-danger-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-danger)' },
  },
  noteblock: {
    label: "Note filing", icon: NotebookPen,
    badgeCls: "border",
    badgeStyle: { borderColor: 'color-mix(in srgb, var(--gd-bronze) 40%, transparent)', color: 'var(--gd-bronze)', background: 'var(--gd-bronze-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-bronze)' },
  },
  canon_fact: {
    label: "Canon fact", icon: ScrollText,
    badgeCls: "border",
    badgeStyle: { borderColor: 'color-mix(in srgb, var(--gd-bronze) 40%, transparent)', color: 'var(--gd-bronze)', background: 'var(--gd-bronze-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-bronze)' },
  },
  work_proposal: {
    label: "Proposed Work", icon: Sparkles,
    badgeCls: "border",
    badgeStyle: { borderColor: 'color-mix(in srgb, var(--gd-bronze) 40%, transparent)', color: 'var(--gd-bronze)', background: 'var(--gd-bronze-soft)' },
    borderStyle: { borderLeftColor: 'var(--gd-bronze)' },
  },
};

const DOMAINS = ["narrative", "technical", "governance", "reference"] as const;

const CLASSIFICATIONS = ["HISTORICAL", "INFERRED", "INVENTED"] as const;

type TypeFilter = "all" | ReviewItem["item_type"];

function confidenceColor(c: number | null): string {
  if (c == null) return "color-mix(in srgb, var(--muted-foreground) 40%, transparent)";
  if (c < 0.5) return "var(--gd-danger)";
  if (c < 0.8) return "var(--gd-caution)";
  return "var(--gd-success)";
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
  } else if (item.item_type === "noteblock") {
    if (ev.day) {
      parts.push(
        <span key="day" className="font-mono text-[11px]">{String(ev.day)}</span>,
      );
    }
    if (Array.isArray(ev.categories) && ev.categories.length > 0) {
      parts.push(
        <span key="cats" className="font-mono text-[11px]">
          → {(ev.categories as string[]).join(", ")}
        </span>,
      );
    }
    if (Array.isArray(ev.actions) && ev.actions.length > 0) {
      // Show the full action text — the user must be able to review exactly
      // what will become a task, not just a count.
      for (const [idx, a] of (ev.actions as { text: string; due?: string }[]).entries()) {
        parts.push(
          <span key={`act-${idx}`} className="font-mono text-[11px]">
            task: {a.text}{a.due ? ` (due ${a.due})` : ""}
          </span>,
        );
      }
    }
  } else if (item.item_type === "canon_fact") {
    if (ev.classification) {
      parts.push(
        <span key="class" className="font-mono text-[11px]">{String(ev.classification)}</span>,
      );
    }
    if (ev.scope) {
      parts.push(
        <span key="scope" className="font-mono text-[11px]">scope: {String(ev.scope)}</span>,
      );
    }
    if (ev.source_path) {
      parts.push(
        <span key="src" className="font-mono text-[11px]">
          {String(ev.source_path)}{ev.source_location ? `#${String(ev.source_location)}` : ""}
        </span>,
      );
    }
  } else if (item.item_type === "quarantine" && ev.doc_id) {
    parts.push(
      <Link key="doc" href={`/library/${ev.doc_id}`}
            className="inline-flex items-center gap-1 text-primary hover:underline">
        <ExternalLink className="w-3 h-3" />{String(ev.doc_title ?? "Inspect document")}
      </Link>,
    );
    if (Array.isArray(ev.findings) && ev.findings.length > 0) {
      const kinds = Array.from(new Set(
        (ev.findings as Array<Record<string, unknown>>).map((f) => String(f.kind ?? "?")),
      ));
      parts.push(
        <span key="kinds" className="font-mono text-[11px]">{kinds.join(" · ")}</span>,
      );
    }
  } else if (item.item_type === "work_proposal") {
    parts.push(
      <span key="size" className="font-mono text-[11px]">
        {String(ev.size ?? "?")} docs · {String(ev.dominant_doc_type ?? "mixed")}
      </span>,
    );
    const spread = ev.collection_spread as Record<string, number> | undefined;
    if (spread) {
      parts.push(
        <span key="spread" className="font-mono text-[11px]">
          {Object.keys(spread).length} collection(s)
        </span>,
      );
    }
    if (ev.cohesion != null) {
      parts.push(
        <span key="coh" className="font-mono text-[11px]">cohesion {String(ev.cohesion)}</span>,
      );
    }
    if (ev.name_source) {
      parts.push(
        <span key="ns" className="font-mono text-[11px]">named: {String(ev.name_source)}</span>,
      );
    }
    if (Array.isArray(ev.exemplar_titles) && ev.exemplar_titles.length > 0) {
      parts.push(
        <span key="ex" className="font-mono text-[11px] truncate max-w-[280px]">
          e.g. {(ev.exemplar_titles as string[]).join(", ")}
        </span>,
      );
    }
  }

  if (item.work_title && item.work_id) {
    // For knowledge items, deep-link to the Knowledge tab with the item highlighted.
    // The review item id is namespaced "knowledge:{uuid}" — strip the prefix.
    const workHref = item.item_type === "knowledge"
      ? `/works/${item.work_id}?tab=knowledge&item=${item.id.replace(/^knowledge:/, "")}`
      : `/works/${item.work_id}`;
    parts.push(
      <Link key="work" href={workHref}
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
  const [pending, setPending] = useState<"approve" | "reject" | "defer" | "reclassify" | null>(null);
  const isDupe = item.item_type === "duplicate";
  const isCanon = item.item_type === "canon_fact";
  const isWorkProposal = item.item_type === "work_proposal";
  const [domain, setDomain] = useState<string>("");
  const [canonical, setCanonical] = useState<string | null>(
    isDupe ? String(item.evidence?.doc_a_id ?? "") || null : null,
  );
  // Canon ratification requires an author signature (and may reclassify).
  const [author, setAuthor] = useState("");
  const [reclass, setReclass] = useState<string>(
    isCanon ? String(item.evidence?.classification ?? "HISTORICAL") : "HISTORICAL",
  );
  const meta = TYPE_META[item.item_type];
  const Icon = meta.icon;
  const origClass = isCanon ? String(item.evidence?.classification ?? "") : "";

  const resolve = async (decision: "approve" | "reject" | "defer" | "reclassify") => {
    if (isCanon && decision !== "defer" && !author.trim()) {
      toast.error("Canon decisions need your signature — enter your name first");
      return;
    }
    if (isWorkProposal && decision !== "defer" && !author.trim()) {
      toast.error("Ratifying a Work needs your signature — enter your name first");
      return;
    }
    if (isWorkProposal && decision === "approve" && !domain) {
      toast.error("Pick a domain before creating the Work");
      return;
    }
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
          ...(isCanon
            ? {
                author: author.trim(),
                ...(decision === "reclassify" ? { classification: reclass } : {}),
              }
            : {}),
          ...(isWorkProposal
            ? {
                author: author.trim(),
                ...(decision === "approve" ? { domain } : {}),
              }
            : {}),
        }),
      });
      if (!r.ok) {
        const msg = await r.json().catch(() => null);
        throw new Error(msg?.detail || `Resolve failed (${r.status})`);
      }
      toast.success(
        decision === "reject" ? "Rejected" :
        decision === "defer" ? "Deferred for 7 days" :
        isCanon ? "Ratified into canon" :
        isWorkProposal ? "Work created" : "Approved",
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
    <div className="border border-l-4 rounded-xl bg-card p-4 space-y-2"
         style={meta.borderStyle}
         data-testid={`review-card-${item.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <Badge variant="outline" className={`shrink-0 gap-1 ${meta.badgeCls}`}
                 style={meta.badgeStyle}>
            <Icon className="w-3 h-3" />{meta.label}
          </Badge>
          <span className="text-sm font-medium truncate">{item.title}</span>
        </div>
        {pct != null && (
          <div className="flex items-center gap-1.5 shrink-0" title={`Confidence ${pct}%`}>
            <div className="w-16 h-1.5 rounded-full bg-muted overflow-hidden">
              <div className="h-full"
                   style={{ width: `${pct}%`, background: confidenceColor(item.confidence) }} />
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

      {isWorkProposal && (
        <div className="flex flex-wrap items-center gap-2 text-xs pt-1">
          <input
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            placeholder="Sign as (author)"
            className="min-h-11 px-2 rounded-md border bg-background text-xs w-40"
            style={{ borderColor: 'var(--gd-line-control)' }}
            data-testid={`work-proposal-author-${item.id}`}
          />
          <span className="text-muted-foreground">Domain:</span>
          <select
            value={domain}
            onChange={(e) => setDomain(e.target.value)}
            className="min-h-11 px-2 rounded-md border bg-background text-xs"
            style={{ borderColor: 'var(--gd-line-control)' }}
            data-testid={`work-proposal-domain-${item.id}`}
          >
            <option value="">Pick a domain…</option>
            {DOMAINS.map(d => (
              <option key={d} value={d}>{d}</option>
            ))}
          </select>
        </div>
      )}

      {isCanon && (
        <div className="flex flex-wrap items-center gap-2 text-xs pt-1">
          <input
            value={author}
            onChange={(e) => setAuthor(e.target.value)}
            placeholder="Sign as (author)"
            className="min-h-11 px-2 rounded-md border bg-background text-xs w-40"
            style={{ borderColor: 'var(--gd-line-control)' }}
            data-testid={`canon-author-${item.id}`}
          />
          <span className="text-muted-foreground">Reclassify:</span>
          <select
            value={reclass}
            onChange={(e) => setReclass(e.target.value)}
            className="min-h-11 px-2 rounded-md border bg-background text-xs"
            style={{ borderColor: 'var(--gd-line-control)' }}
            data-testid={`canon-class-${item.id}`}>
            {CLASSIFICATIONS.map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          {reclass !== origClass && (
            <Button size="sm" variant="outline" disabled={pending != null}
                    onClick={() => resolve("reclassify")}
                    className="gap-1.5 min-h-11"
                    style={{ color: 'var(--gd-bronze)', borderColor: 'var(--gd-line-control)' }}
                    data-testid={`reclassify-${item.id}`}>
              {pending === "reclassify" ? <Loader2 className="w-3 h-3 animate-spin" /> : <ScrollText className="w-3 h-3" />}
              Ratify as {reclass}
            </Button>
          )}
        </div>
      )}

      <div className="flex items-center gap-2 pt-1">
        <Button size="sm" variant="outline" disabled={pending != null}
                onClick={() => resolve("approve")}
                className="gap-1.5 min-h-11"
                style={{ color: 'var(--gd-success)', borderColor: 'var(--gd-line-control)' }}
                data-testid={`approve-${item.id}`}>
          {pending === "approve" ? <Loader2 className="w-3 h-3 animate-spin" /> : <ThumbsUp className="w-3 h-3" />}
          {isCanon || isWorkProposal ? "Ratify" : "Approve"}
        </Button>
        <Button size="sm" variant="outline" disabled={pending != null}
                onClick={() => resolve("reject")}
                className="gap-1.5 min-h-11 text-destructive"
                style={{ borderColor: 'var(--gd-line-control)' }}
                data-testid={`reject-${item.id}`}>
          {pending === "reject" ? <Loader2 className="w-3 h-3 animate-spin" /> : <ThumbsDown className="w-3 h-3" />}
          Reject
        </Button>
        <Button size="sm" variant="ghost" disabled={pending != null}
                onClick={() => resolve("defer")}
                className="gap-1.5 min-h-11 text-muted-foreground"
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
  { key: "canon_fact", label: "Canon facts" },
  { key: "knowledge",  label: "AI knowledge" },
  { key: "suggestion", label: "Suggestions" },
  { key: "duplicate",  label: "Duplicates" },
  { key: "reclassify", label: "Reclassify" },
  { key: "work_proposal", label: "Proposed Works" },
];

export default function ReviewPage() {
  const qc = useQueryClient();
  const [filter, setFilter] = useState<TypeFilter>("all");
  const [workFilter, setWorkFilter] = useState<string | null>(null);
  const [scanning, setScanning] = useState(false);

  const scanForSubjects = async () => {
    setScanning(true);
    try {
      const r = await apiFetch(`${BASE}/works/proposals/generate`, { method: "POST" });
      if (!r.ok) throw new Error(`Scan failed (${r.status})`);
      const result = await r.json();
      if (result.status === "skipped") {
        toast.info(`No subjects found — ${result.reason}`);
      } else if (result.proposals_upserted > 0) {
        toast.success(`${result.proposals_upserted} subject proposal(s) ready for your review`);
      } else {
        toast.info("No new subject clusters found in unassigned documents");
      }
      refetch();
      qc.invalidateQueries({ queryKey: ["review-queue-count"] });
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Scan failed");
    } finally {
      setScanning(false);
    }
  };

  const { data, isLoading, isFetching, isError, refetch } = useQuery<QueueResponse>({
    queryKey: ["review-queue"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/review/queue`);
      if (!r.ok) throw new Error("Failed to load review queue");
      return r.json();
    },
    refetchInterval: 60_000,
  });

  const allItems = data?.items ?? [];

  // Unique Works that appear in the queue — used to render the Work filter chips.
  const worksInQueue = Array.from(
    new Map(
      allItems
        .filter(i => i.work_id && i.work_title)
        .map(i => [i.work_id!, { id: i.work_id!, title: i.work_title! }])
    ).values()
  );

  // Auto-clear the work filter if the selected Work no longer has any items
  // (e.g. after resolving the last item, or after a background refresh removes it).
  // Without this, the queue shows an empty state with no visible way to escape.
  useEffect(() => {
    if (workFilter === null) return;
    const presentIds = new Set(allItems.map(i => i.work_id).filter(Boolean));
    if (!presentIds.has(workFilter)) setWorkFilter(null);
  }, [data, workFilter]);

  // Apply work filter first so type counts reflect the active Work selection.
  const workFiltered = workFilter === null
    ? allItems
    : allItems.filter(i => i.work_id === workFilter);

  const items = workFiltered.filter(i => filter === "all" || i.item_type === filter);

  // Counts for the type pills — reflect the active Work filter when one is set.
  const counts: Record<string, number> = workFilter === null
    ? (data?.counts_by_type ?? {})
    : workFiltered.reduce<Record<string, number>>((acc, i) => {
        acc[i.item_type] = (acc[i.item_type] ?? 0) + 1;
        return acc;
      }, {});

  const onResolved = () => {
    refetch();
    qc.invalidateQueries({ queryKey: ["review-queue-count"] });
  };

  return (
    <Page
      eyebrow="Governance"
      title="Review Queue"
      actions={
        <>
          <Button size="sm" variant="ghost" onClick={scanForSubjects} disabled={scanning}
                  className="min-h-11 gap-1.5" data-testid="scan-subjects">
            <Sparkles className={`w-3.5 h-3.5 ${scanning ? "animate-pulse" : ""}`} />
            {scanning ? "Scanning…" : "Scan for subjects"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => refetch()} disabled={isFetching}
                  className="min-h-11 gap-1.5" data-testid="refresh-queue">
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </>
      }
    >
      <div className="flex items-center gap-3 flex-wrap -mt-2">
        {data && data.count > 0 && (
          <Badge variant="outline" className="font-mono text-sm align-middle shrink-0">{data.count}</Badge>
        )}
        <p className="text-[13px] text-balance text-muted-foreground">
          Everything here needs your decision before it becomes fact. Most uncertain items first.
        </p>
      </div>

      {/* Type filter pills */}
      <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg w-fit flex-wrap">
        {FILTERS.map(f => {
          const n = f.key === "all" ? workFiltered.length : (counts[f.key] ?? 0);
          return (
            <button key={f.key} onClick={() => setFilter(f.key)}
                    className={`px-3 py-2 rounded-md text-xs transition-colors min-h-11 touch-manipulation ${
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

      {/* Work filter chips — only shown when the queue contains items from multiple Works */}
      {worksInQueue.length > 0 && (
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground shrink-0">Work</span>
          <button
            onClick={() => setWorkFilter(null)}
            className={`px-2.5 min-h-11 rounded-md text-xs border transition-colors ${
              workFilter === null
                ? "bg-primary/10 border-primary/40 text-primary font-medium"
                : "border-border text-muted-foreground hover:text-foreground"
            }`}
            data-testid="work-filter-all"
          >
            All Works
          </button>
          {worksInQueue.map(w => (
            <button
              key={w.id}
              onClick={() => setWorkFilter(w.id)}
              className={`px-2.5 min-h-11 rounded-md text-xs border transition-colors truncate max-w-[180px] ${
                workFilter === w.id
                  ? "bg-primary/10 border-primary/40 text-primary font-medium"
                  : "border-border text-muted-foreground hover:text-foreground"
              }`}
              data-testid={`work-filter-${w.id}`}
            >
              {w.title}
            </button>
          ))}
        </div>
      )}

      {isLoading ? (
        <LoadingState rows={4} label="Loading review queue" />
      ) : isError ? (
        <ErrorState
          title="Could not load the review queue"
          detail="The queue is unavailable right now."
          onRetry={() => refetch()}
        />
      ) : items.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 />}
          title="All clear"
          description={
            workFilter !== null
              ? "No items for this Work need review."
              : filter === "all"
              ? "Nothing needs your review right now."
              : "No items of this type need review."
          }
        />
      ) : (
        <div className="space-y-3">
          {items.map(item => (
            <ReviewCard key={item.id} item={item} onResolved={onResolved} />
          ))}
        </div>
      )}
    </Page>
  );
}
