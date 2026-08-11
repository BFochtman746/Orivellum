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
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useGdDark } from "@/lib/useGdDark";
import { toast } from "sonner";
import {
  ScrollText, Loader2, Plus, X, ShieldCheck, Landmark, Sparkles, GitBranch,
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
  HISTORICAL: { label: "Historical", icon: Landmark, style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" } },
  INFERRED: { label: "Inferred", icon: GitBranch, style: { color: "var(--green-2)", borderColor: "var(--green-2)", background: "var(--green-soft)" } },
  INVENTED: { label: "Invented", icon: Sparkles, style: { color: "var(--rust)", borderColor: "var(--rust)", background: "var(--rust-soft)" } },
};

const CLASSIFICATIONS: Classification[] = ["HISTORICAL", "INFERRED", "INVENTED"];

function FactCard({ fact }: { fact: CanonFact }) {
  const meta = CLASS_META[fact.classification];
  const Icon = meta.icon;
  const dimmed = fact.status !== "active";
  return (
    <div
      className="border border-l-4 rounded-xl bg-card p-4 space-y-2"
      style={{ borderLeftColor: meta.style.color as string, opacity: dimmed ? 0.55 : 1 }}
      data-testid={`fact-${fact.id}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant="outline" className="gap-1 border" style={meta.style}>
            <Icon className="w-3 h-3" />{meta.label}
          </Badge>
          <Badge variant="outline" className="text-[10px]"
                 style={{ borderColor: "var(--line-2)", color: "var(--ink-soft)" }}>
            {fact.work_id ? "This book" : "Series-wide"}
          </Badge>
          {fact.status !== "active" && (
            <Badge variant="outline" className="text-[10px]"
                   style={{ borderColor: "var(--line-2)", color: "var(--ink-soft)" }}>
              {fact.status}
            </Badge>
          )}
        </div>
      </div>
      <p className="text-sm text-foreground whitespace-pre-wrap break-words">{fact.statement}</p>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {fact.source_ref && <span className="font-mono text-[11px]">source: {fact.source_ref}</span>}
        {fact.parent_ids.length > 0 && (
          <span className="font-mono text-[11px]">from {fact.parent_ids.length} parent fact(s)</span>
        )}
        {fact.signed_by && (
          <span className="inline-flex items-center gap-1">
            <ShieldCheck className="w-3 h-3" />signed {fact.signed_by}
          </span>
        )}
        {fact.superseded_by && <span className="font-mono text-[11px]">revised</span>}
      </div>
    </div>
  );
}

export default function CanonPage() {
  useGdDark();
  const qc = useQueryClient();
  const [workFilter, setWorkFilter] = useState<string | null>(null);
  const [classFilter, setClassFilter] = useState<Classification | "all">("all");
  const [showRetired, setShowRetired] = useState(false);
  const [adding, setAdding] = useState(false);

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

  const { data, isLoading, refetch } = useQuery<FactsResponse>({
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

  return (
    <div className="mx-auto max-w-4xl px-4 py-8 space-y-6">
      {/* Header */}
      <div className="space-y-1">
        <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground flex items-center gap-2">
          <ScrollText className="w-3.5 h-3.5" /> Authority
        </div>
        <h1 className="text-3xl font-serif">Canon</h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          The classified, sourced record for the trilogy. A HISTORICAL fact needs a source,
          an INFERRED fact traces to parent facts, and an INVENTED fact is signed by you.
          Nothing enters canon unchecked, and revisions never silently overwrite —
          {" "}{totalActive} active fact(s).
        </p>
      </div>

      {/* Filters */}
      <div className="flex flex-wrap items-center gap-2">
        <select
          value={workFilter ?? "series"}
          onChange={(e) => setWorkFilter(e.target.value === "series" ? null : e.target.value)}
          className="h-8 px-2 rounded-md border bg-background text-xs"
          style={{ borderColor: "var(--line-2)" }}
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
                ? { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }
                : { borderColor: "var(--line-2)" }}
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
          <Button size="sm" variant="outline" className="h-8 gap-1.5"
                  style={{ borderColor: "var(--line-2)" }}
                  onClick={() => setAdding((v) => !v)}
                  data-testid="canon-add-toggle">
            {adding ? <X className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
            {adding ? "Cancel" : "New fact"}
          </Button>
        </div>
      </div>

      {adding && (
        <NewFactForm
          works={works}
          defaultWorkId={workFilter}
          onCreated={() => { setAdding(false); refresh(); }}
        />
      )}

      {/* List */}
      {isLoading ? (
        <div className="space-y-3">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-24 w-full rounded-xl" />)}
        </div>
      ) : facts.length === 0 ? (
        <div className="text-center py-16 text-muted-foreground">
          <ScrollText className="w-8 h-8 mx-auto mb-3 opacity-40" />
          <p className="text-sm">No canon facts yet in this view.</p>
          <p className="text-xs mt-1">
            Facts land here when you pass the G3 Canon Seed gate or ratify a proposal in the review inbox.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {facts.map((f) => <FactCard key={f.id} fact={f} />)}
        </div>
      )}
    </div>
  );
}

function NewFactForm({
  works, defaultWorkId, onCreated,
}: {
  works: WorkLite[];
  defaultWorkId: string | null;
  onCreated: () => void;
}) {
  const [statement, setStatement] = useState("");
  const [classification, setClassification] = useState<Classification>("HISTORICAL");
  const [sourceRef, setSourceRef] = useState("");
  const [signedBy, setSignedBy] = useState("");
  const [workId, setWorkId] = useState<string | "series">(defaultWorkId ?? "series");
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
        }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `Create failed (${r.status})`);
      }
      toast.success("Fact added to canon");
      onCreated();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Create failed");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="border rounded-xl bg-card p-4 space-y-3" style={{ borderColor: "var(--line-2)" }}>
      <textarea
        value={statement}
        onChange={(e) => setStatement(e.target.value)}
        placeholder="State the fact plainly (e.g. 'Job lived in the land of Uz')"
        className="w-full min-h-[70px] p-2 rounded-md border bg-background text-sm"
        style={{ borderColor: "var(--line-2)" }}
        data-testid="new-fact-statement"
      />
      <div className="flex flex-wrap items-center gap-2">
        <select value={classification} onChange={(e) => setClassification(e.target.value as Classification)}
                className="h-8 px-2 rounded-md border bg-background text-xs"
                style={{ borderColor: "var(--line-2)" }} data-testid="new-fact-class">
          {CLASSIFICATIONS.map((c) => <option key={c} value={c}>{CLASS_META[c].label}</option>)}
        </select>
        <select value={workId} onChange={(e) => setWorkId(e.target.value)}
                className="h-8 px-2 rounded-md border bg-background text-xs"
                style={{ borderColor: "var(--line-2)" }} data-testid="new-fact-work">
          <option value="series">Series-wide</option>
          {works.map((w) => <option key={w.id} value={w.id}>{w.title}</option>)}
        </select>
      </div>
      <input
        value={sourceRef}
        onChange={(e) => setSourceRef(e.target.value)}
        placeholder={classification === "HISTORICAL" ? "Source (required, e.g. Job 1:1)" : "Source (optional)"}
        className="w-full h-8 px-2 rounded-md border bg-background text-xs"
        style={{ borderColor: "var(--line-2)" }}
        data-testid="new-fact-source"
      />
      <input
        value={signedBy}
        onChange={(e) => setSignedBy(e.target.value)}
        placeholder={classification === "INVENTED" ? "Sign as (required for invented facts)" : "Sign as"}
        className="w-full h-8 px-2 rounded-md border bg-background text-xs"
        style={{ borderColor: "var(--line-2)" }}
        data-testid="new-fact-signed"
      />
      <div className="flex justify-end">
        <Button size="sm" disabled={saving || !statement.trim()} onClick={submit}
                data-testid="new-fact-submit">
          {saving ? <Loader2 className="w-3 h-3 animate-spin mr-1.5" /> : <Plus className="w-3 h-3 mr-1.5" />}
          Add to canon
        </Button>
      </div>
    </div>
  );
}
