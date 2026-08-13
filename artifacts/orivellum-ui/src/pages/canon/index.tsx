/**
 * Canon — /canon
 *
 * The authority substrate for the trilogy: classified, sourced, signed facts.
 * Every fact carries a classification (HISTORICAL / INFERRED / INVENTED), a
 * source reference, and an author signature. Facts scoped to a single Work
 * apply to that book; series-wide facts (no work) hold across all three books.
 *
 * This is a read + light-authoring surface. New facts and ratification of
 * machine proposals happen here and in the Chancery review inbox; the insert
 * path refuses facts that violate the authority rules.
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Page, EmptyState, ErrorState } from "@/components/primitives";
import { toast } from "sonner";
import {
  ScrollText, Loader2, Plus, X, ShieldCheck, Landmark, Sparkles, GitBranch, Waves, PenLine,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type Classification = "HISTORICAL" | "INFERRED" | "INVENTED";

interface CanonFact {
  id: string;
  work_id: string | null;
  statement: string;
  classification: Classification;
  source_ref: string;
  parent_ids: string[];
  supersedes: string | null;
  superseded_by: string | null;
  status: "active" | "superseded" | "retracted";
  signed_by: string;
  origin: string;
  created_at: string;
}

interface FactsResponse {
  facts: CanonFact[];
  count: number;
}

interface WorkLite {
  id: string;
  title: string;
}

const CLASS_META: Record<Classification, { label: string; icon: typeof Landmark; style: React.CSSProperties }> = {
  HISTORICAL: { label: "Historical", icon: Landmark, style: { color: "var(--gd-bronze)", borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", background: "var(--gd-bronze-soft)" } },
  INFERRED: { label: "Inferred", icon: GitBranch, style: { color: "var(--gd-success)", borderColor: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 12%, transparent)" } },
  INVENTED: { label: "Invented", icon: Sparkles, style: { color: "var(--gd-danger)", borderColor: "var(--gd-danger)", background: "var(--gd-danger-soft)" } },
};

const CLASSIFICATIONS: Classification[] = ["HISTORICAL", "INFERRED", "INVENTED"];

interface RippleReport {
  affected_chapters: { chapter_id: string; seq: number | null; title: string; nodes: string[] }[];
  affected_characters: { name: string }[];
  affected_facts: { canon_fact_id: string; statement?: string }[];
  counts: { nodes: number; chapters: number; characters: number; facts: number };
  truncated: boolean;
}

/** Blast radius of changing this canon fact (RIPPLE, E12): which chapters,
 *  characters, and downstream facts depend on it — reported BEFORE any
 *  change is committed. Loaded on demand; a fact with no graph link
 *  reports that refusal verbatim. */
function FactRipple({ fact, fallbackWorkId }: { fact: CanonFact; fallbackWorkId: string | null }) {
  const [open, setOpen] = useState(false);
  const workId = fact.work_id ?? fallbackWorkId;
  const { data, isLoading, error } = useQuery<RippleReport>({
    queryKey: ["canon-ripple", fact.id, workId],
    enabled: open && !!workId,
    staleTime: 60_000,
    retry: false,
    queryFn: async () => {
      const params = workId && !fact.work_id ? `?work_id=${workId}` : "";
      const r = await apiFetch(`${BASE}/canon/facts/${fact.id}/ripple${params}`);
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `ripple failed (${r.status})`);
      }
      return r.json();
    },
  });

  if (!workId) return null; // series fact with no book selected — no scope to walk
  return (
    <div className="pt-1">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1.5 text-[11px] font-mono uppercase tracking-widest text-muted-foreground hover:text-foreground"
        data-testid={`ripple-${fact.id}`}
      >
        <Waves className="w-3 h-3" style={{ color: "var(--gd-bronze)" }} />
        {open ? "Hide ripple" : "Preview ripple"}
      </button>
      {open && (
        <div className="mt-1.5 text-[11px] space-y-1">
          {isLoading && (
            <span className="inline-flex items-center gap-1.5 text-muted-foreground">
              <Loader2 className="w-3 h-3 animate-spin" /> Walking the world graph…
            </span>
          )}
          {error instanceof Error && (
            <span className="text-muted-foreground">{error.message}</span>
          )}
          {data && (
            <>
              <div className="text-foreground/80">
                {data.counts.chapters} chapter{data.counts.chapters === 1 ? "" : "s"} ·{" "}
                {data.counts.characters} character{data.counts.characters === 1 ? "" : "s"} ·{" "}
                {data.counts.facts} downstream fact{data.counts.facts === 1 ? "" : "s"}
                {data.truncated ? " (truncated)" : ""}
              </div>
              {data.affected_chapters.length > 0 && (
                <div className="text-muted-foreground">
                  {data.affected_chapters.slice(0, 8).map((c) =>
                    c.seq != null ? `Ch. ${c.seq}` : c.title || c.chapter_id.slice(0, 6)
                  ).join(", ")}
                  {data.affected_chapters.length > 8 ? ` +${data.affected_chapters.length - 8} more` : ""}
                </div>
              )}
              {data.affected_characters.length > 0 && (
                <div className="text-muted-foreground">
                  Characters: {data.affected_characters.map((c) => c.name).join(", ")}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  );
}

function FactCard({
  fact, fallbackWorkId, onRevise, onJumpToFact,
}: {
  fact: CanonFact;
  fallbackWorkId: string | null;
  onRevise: (fact: CanonFact) => void;
  onJumpToFact: (factId: string) => void;
}) {
  const meta = CLASS_META[fact.classification];
  const Icon = meta.icon;
  const dimmed = fact.status !== "active";
  return (
    <div
      id={`fact-${fact.id}`}
      className="border border-l-4 rounded-xl bg-card p-4 space-y-2 scroll-mt-20"
      style={{ borderLeftColor: meta.style.color as string, opacity: dimmed ? 0.55 : 1 }}
      data-testid={`fact-${fact.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline" className="gap-1 border" style={meta.style}>
            <Icon className="w-3 h-3" />{meta.label}
          </Badge>
          <Badge variant="outline" className="text-[10px] text-muted-foreground"
                 style={{ borderColor: "var(--gd-line-control)" }}>
            {fact.work_id ? "This book" : "Series-wide"}
          </Badge>
          {fact.status !== "active" && (
            <Badge variant="outline" className="text-[10px] text-muted-foreground"
                   style={{ borderColor: "var(--gd-line-control)" }}>
              {fact.status}
            </Badge>
          )}
        </div>
        {fact.status === "active" && (
          <button
            type="button"
            onClick={() => onRevise(fact)}
            className="inline-flex items-center gap-1 text-[11px] font-mono uppercase tracking-widest text-muted-foreground hover:text-foreground shrink-0"
            title="Revise this fact — the replacement explicitly supersedes it, keeping the audit trail"
            data-testid={`fact-revise-${fact.id}`}
          >
            <PenLine className="w-3 h-3" style={{ color: "var(--gd-bronze)" }} />
            Revise
          </button>
        )}
      </div>
      <p className="text-sm text-foreground whitespace-pre-wrap break-words">{fact.statement}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {fact.source_ref && <span className="font-mono text-[11px]">source: {fact.source_ref}</span>}
        {fact.parent_ids.length > 0 && (
          <span className="font-mono text-[11px]">from {fact.parent_ids.length} parent fact(s)</span>
        )}
        {fact.origin === "wa_archive" && (
          <span className="inline-flex items-center gap-1 text-[11px]"
                style={{ color: "var(--gd-bronze)" }}
                data-testid={`fact-origin-archive-${fact.id}`}>
            <ScrollText className="w-3 h-3" />ratified from archive
          </span>
        )}
        {fact.signed_by && (
          <span className="inline-flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" />signed {fact.signed_by}
          </span>
        )}
        {fact.superseded_by && (
          <button
            type="button"
            onClick={() => onJumpToFact(fact.superseded_by!)}
            className="font-mono text-[11px] underline underline-offset-2 hover:text-foreground"
            title="Jump to the fact that replaced this one"
            data-testid={`fact-successor-${fact.id}`}
          >
            revised → successor
          </button>
        )}
      </div>
      {fact.status === "active" && <FactRipple fact={fact} fallbackWorkId={fallbackWorkId} />}
    </div>
  );
}

export default function CanonPage() {
  const qc = useQueryClient();
  const [workFilter, setWorkFilter] = useState<string | null>(null);
  const [classFilter, setClassFilter] = useState<Classification | "all">("all");
  const [showRetired, setShowRetired] = useState(false);
  const [adding, setAdding] = useState(false);
  const [revising, setRevising] = useState<CanonFact | null>(null);
  const [jumpTo, setJumpTo] = useState<string | null>(null);

  const { data: worksData } = useQuery<{ works: WorkLite[] }>({
    queryKey: ["works-lite"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/works`);
      if (!r.ok) throw new Error("Failed to load works");
      const j = await r.json();
      return { works: (j.works ?? j.items ?? j ?? []).map((w: any) => ({ id: w.id, title: w.title })) };
    },
  });
  const works = worksData?.works ?? [];

  const { data, isLoading, isError, refetch } = useQuery<FactsResponse>({
    queryKey: ["canon-facts", workFilter, classFilter, showRetired],
    queryFn: async () => {
      const params = new URLSearchParams();
      if (workFilter) params.set("work_id", workFilter);
      else params.set("series_only", "true");
      if (classFilter !== "all") params.set("classification", classFilter);
      if (!showRetired) params.set("status", "active");
      const r = await apiFetch(`${BASE}/canon/facts?${params.toString()}`);
      if (!r.ok) throw new Error("Failed to load canon");
      return r.json();
    },
  });

  const { data: counts } = useQuery<{ counts: Record<string, Record<string, number>> }>({
    queryKey: ["canon-counts"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/canon/counts`);
      if (!r.ok) throw new Error("Failed to load counts");
      return r.json();
    },
  });

  const facts = data?.facts ?? [];
  const activeTotals = counts?.counts ?? {};
  const totalActive = CLASSIFICATIONS.reduce((n, c) => n + (activeTotals[c]?.active ?? 0), 0);

  const refresh = () => {
    refetch();
    qc.invalidateQueries({ queryKey: ["canon-counts"] });
  };

  // Successor navigation: the replacement may sit outside the current
  // classification filter, so jumping first widens the filter, then scrolls
  // once the target card is actually in the DOM.
  useEffect(() => {
    if (!jumpTo) return;
    const el = document.getElementById(`fact-${jumpTo}`);
    if (el) {
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      setJumpTo(null);
    }
  }, [jumpTo, facts]);

  const jumpToFact = (factId: string) => {
    setClassFilter("all"); // a revision may have changed classification
    setJumpTo(factId);
  };

  return (
    <Page eyebrow="Authority" title="Canon">
      <div className="space-y-6">
      <p className="text-sm text-muted-foreground max-w-2xl -mt-2 flex items-start gap-2">
        <ScrollText className="w-3.5 h-3.5 mt-0.5 shrink-0" aria-hidden />
        <span>
          The classified, sourced record for the trilogy. A HISTORICAL fact needs a source,
          an INFERRED fact traces to parent facts, and an INVENTED fact is signed by you.
          Nothing enters canon unchecked, and revisions never silently overwrite —
          {" "}{totalActive} active fact(s).
        </span>
      </p>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={workFilter ?? "series"}
          onChange={(e) => setWorkFilter(e.target.value === "series" ? null : e.target.value)}
          className="h-8 px-2 rounded-md border bg-background text-xs"
          style={{ borderColor: "var(--gd-line-control)" }}
          data-testid="canon-work-filter"
        >
          <option value="series">Series-wide only</option>
          {works.map((w) => (
            <option key={w.id} value={w.id}>{w.title} (+ series)</option>
          ))}
        </select>
        <div className="flex items-center gap-1">
          {(["all", ...CLASSIFICATIONS] as const).map((c) => (
            <button
              key={c}
              onClick={() => setClassFilter(c)}
              className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
                classFilter === c ? "font-medium" : "text-muted-foreground"
              }`}
              style={classFilter === c
                ? { borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", background: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }
                : { borderColor: "var(--gd-line-control)" }}
              data-testid={`canon-class-${c}`}
            >
              {c === "all" ? "All" : CLASS_META[c].label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer">
          <input type="checkbox" checked={showRetired}
                 onChange={(e) => setShowRetired(e.target.checked)}
                 data-testid="canon-show-retired" />
          Show superseded / retracted
        </label>
        <div className="ml-auto">
          <Button size="sm" variant="outline" className="min-h-11 gap-1.5"
                  style={{ borderColor: "var(--gd-line-control)" }}
                  onClick={() => { setRevising(null); setAdding((v) => !v); }}
                  data-testid="canon-add-toggle">
            {adding ? <X className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
            {adding ? "Cancel" : "New fact"}
          </Button>
        </div>
      </div>

      {adding && !revising && (
        <NewFactForm
          works={works}
          defaultWorkId={workFilter}
          onCreated={() => { setAdding(false); refresh(); }}
        />
      )}

      {revising && (
        <NewFactForm
          key={revising.id}
          works={works}
          defaultWorkId={workFilter}
          revises={revising}
          onCancel={() => setRevising(null)}
          onCreated={() => { setRevising(null); refresh(); }}
        />
      )}

      {/* List */}
      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
        </div>
      ) : isError ? (
        <ErrorState
          title="Couldn't load canon"
          detail="The canon record failed to load. Check your connection and try again."
          onRetry={() => refresh()}
        />
      ) : facts.length === 0 ? (
        <EmptyState
          icon={<ScrollText />}
          title="No canon facts yet in this view"
          description="Facts land here when you pass the G3 Canon Seed gate or ratify a proposal in the review inbox."
        />
      ) : (
        <div className="space-y-3">
          {facts.map((f) => (
            <FactCard
              key={f.id}
              fact={f}
              fallbackWorkId={workFilter}
              onJumpToFact={jumpToFact}
              onRevise={(fact) => {
                setAdding(false);
                setRevising(fact);
                window.scrollTo({ top: 0, behavior: "smooth" });
              }}
            />
          ))}
        </div>
      )}
      </div>
    </Page>
  );
}

function NewFactForm({
  works, defaultWorkId, onCreated, revises, onCancel,
}: {
  works: WorkLite[];
  defaultWorkId: string | null;
  onCreated: () => void;
  /** Revise mode: the active fact the new one explicitly supersedes.
   *  Pre-fills the form; scope is locked to the predecessor's (a revision
   *  changes what a fact SAYS, never where it applies). */
  revises?: CanonFact;
  onCancel?: () => void;
}) {
  const [statement, setStatement] = useState(revises?.statement ?? "");
  const [classification, setClassification] = useState<Classification>(
    revises?.classification ?? "HISTORICAL"
  );
  const [sourceRef, setSourceRef] = useState(revises?.source_ref ?? "");
  const [signedBy, setSignedBy] = useState(revises?.signed_by ?? "");
  const [workId, setWorkId] = useState<string | "series">(
    revises ? (revises.work_id ?? "series") : (defaultWorkId ?? "series")
  );
  const [saving, setSaving] = useState(false);

  const submit = async () => {
    setSaving(true);
    try {
      const r = await apiFetch(`${BASE}/canon/facts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          statement,
          classification,
          source_ref: sourceRef,
          signed_by: signedBy,
          work_id: workId === "series" ? null : workId,
          ...(revises
            ? { supersedes: revises.id, parent_ids: revises.parent_ids }
            : {}),
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `${revises ? "Revise" : "Create"} failed (${r.status})`);
      }
      toast.success(revises ? "Fact revised — predecessor marked superseded" : "Fact added to canon");
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : (revises ? "Revise failed" : "Create failed"));
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border rounded-xl bg-card p-4 space-y-3"
         style={{ borderColor: revises ? "color-mix(in srgb, var(--gd-bronze) 40%, transparent)" : "var(--gd-line-control)" }}>
      {revises && (
        <div className="flex items-start justify-between gap-3">
          <p className="text-xs text-muted-foreground">
            <span className="font-mono uppercase tracking-widest" style={{ color: "var(--gd-bronze)" }}>
              Revising
            </span>{" "}
            — the new fact will explicitly supersede{" "}
            <span className="font-mono">{revises.id.slice(0, 8)}</span>; the old
            statement stays in the record as superseded.
          </p>
          {onCancel && (
            <button type="button" onClick={onCancel}
                    aria-label="Cancel revision"
                    title="Cancel revision"
                    className="text-muted-foreground hover:text-foreground shrink-0"
                    data-testid="revise-cancel">
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
      )}
      <textarea
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
        placeholder="State the fact plainly (e.g. 'Job lived in the land of Uz')"
        className="w-full min-h-[70px] p-2 rounded-md border bg-background text-sm"
        style={{ borderColor: "var(--gd-line-control)" }}
        data-testid="new-fact-statement"
      />
      <div className="flex flex-wrap items-center gap-2">
        <select value={classification} onChange={(e) => setClassification(e.target.value as Classification)}
                className="h-8 px-2 rounded-md border bg-background text-xs"
                style={{ borderColor: "var(--gd-line-control)" }} data-testid="new-fact-class">
          {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{CLASS_META[c].label}</option>)}
        </select>
        <select value={workId} onChange={(e) => setWorkId(e.target.value)}
                disabled={!!revises}
                title={revises ? "A revision keeps its predecessor's scope" : undefined}
                className="h-8 px-2 rounded-md border bg-background text-xs disabled:opacity-60"
                style={{ borderColor: "var(--gd-line-control)" }} data-testid="new-fact-work">
          <option value="series">Series-wide</option>
          {works.map((w) => <option key={w.id} value={w.id}>{w.title}</option>)}
        </select>
      </div>
      <input
        value={sourceRef}
        onChange={(e) => setSourceRef(e.target.value)}
        placeholder={classification === "HISTORICAL" ? "Source (required, e.g. Job 1:1)" : "Source (optional)"}
        className="w-full h-8 px-2 rounded-md border bg-background text-xs"
        style={{ borderColor: "var(--gd-line-control)" }}
        data-testid="new-fact-source"
      />
      <input
        value={signedBy}
        onChange={(e) => setSignedBy(e.target.value)}
        placeholder={classification === "INVENTED" ? "Sign as (required for invented facts)" : "Sign as"}
        className="w-full h-8 px-2 rounded-md border bg-background text-xs"
        style={{ borderColor: "var(--gd-line-control)" }}
        data-testid="new-fact-signed"
      />
      <div className="flex justify-end">
        <Button size="sm" disabled={saving || !statement.trim()} onClick={submit}
                data-testid="new-fact-submit">
          {saving
            ? <Loader2 className="w-3 h-3 animate-spin mr-1.5" />
            : revises
              ? <PenLine className="w-3 h-3 mr-1.5" />
              : <Plus className="w-3 h-3 mr-1.5" />}
          {revises ? "Revise canon" : "Add to canon"}
        </Button>
      </div>
    </div>
  );
}
