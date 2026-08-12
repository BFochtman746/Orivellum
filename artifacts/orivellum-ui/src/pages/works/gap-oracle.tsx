import { useState } from "react";
import { useParams, Link } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ArrowLeft,
  Check,
  X,
  HelpCircle,
  Loader2,
  FlaskConical,
  Scale,
} from "lucide-react";
import { toast } from "sonner";

const API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

const DETECTORS = [
  { id: "citation_graph_closure", name: "Cited but not held" },
  { id: "mentioned_never_explained", name: "Mentioned, never explained" },
  { id: "dead_end_citation", name: "Dead-end citation" },
  { id: "failure_clustering", name: "Failure clustering" },
];

type Candidate = {
  pair_key: string;
  frequency: number;
  frequency_band?: string;
  term?: string;
  author?: string;
  year?: string;
  prereq_subject?: string;
  dependent_subjects?: string[];
  top_doc_title?: string;
  label: string | null;
  labeled_by: string | null;
};

type UnflaggedLabel = {
  pair_key: string;
  frequency: number;
  label: string;
  labeled_by: string;
};

function candidateTitle(c: Candidate): string {
  if (c.term) return c.term;
  if (c.author) return `${c.author} (${c.year})`;
  if (c.prereq_subject) return c.prereq_subject;
  return c.pair_key;
}

function candidateDetail(c: Candidate): string {
  if (c.dependent_subjects?.length)
    return `Failing dependents: ${c.dependent_subjects.join(", ")}`;
  if (c.top_doc_title) return `Top source: ${c.top_doc_title}`;
  return "";
}

const LABEL_STYLES: Record<string, string> = {
  is_gap: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400",
  is_not_gap: "bg-red-500/15 text-red-600 dark:text-red-400",
  unknown: "bg-muted text-muted-foreground",
};

function pct(v: number | null | undefined): string {
  return v == null ? "—" : `${Math.round(v * 100)}%`;
}

export default function GapOraclePage() {
  const { workId } = useParams<{ workId: string }>();
  const queryClient = useQueryClient();
  const [detector, setDetector] = useState(DETECTORS[0].id);
  const [signature, setSignature] = useState(
    () => localStorage.getItem("gap-oracle-signature") || ""
  );

  const { data, isLoading, error } = useQuery<{
    candidates: Candidate[];
    unflagged_labels: UnflaggedLabel[];
  }>({
    queryKey: ["gap-oracle-candidates", workId, detector],
    queryFn: () =>
      apiFetch(
        `${API}/works/${workId}/gap-oracle/candidates?detector=${detector}`
      ).then((r) => {
        if (!r.ok) throw new Error("candidates fetch failed");
        return r.json();
      }),
    enabled: !!workId,
  });

  const { data: measurements } = useQuery<{
    measurements: any[];
    min_labeled_for_blocking: number;
  }>({
    queryKey: ["gap-oracle-measurements"],
    queryFn: () =>
      apiFetch(`${API}/gap-oracle/measurements`).then((r) => r.json()),
  });

  const label = useMutation({
    mutationFn: async (args: { pair_key: string; label: string; frequency: number }) => {
      const r = await apiFetch(`${API}/works/${workId}/gap-oracle/labels`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ detector, signed_by: signature.trim(), ...args }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "label failed");
      return r.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["gap-oracle-candidates", workId, detector],
      });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const evaluate = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API}/gap-oracle/evaluate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ detector }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "evaluate failed");
      return r.json();
    },
    onSuccess: (m) => {
      queryClient.invalidateQueries({ queryKey: ["gap-oracle-measurements"] });
      toast.success(
        `Measured: precision ${pct(m.precision)}, recall ${pct(m.recall)} over ${m.n_labeled} labels`
      );
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const submitLabel = (pair_key: string, value: string, frequency: number) => {
    if (!signature.trim()) {
      toast.error("Sign your labels first — enter your name above");
      return;
    }
    localStorage.setItem("gap-oracle-signature", signature.trim());
    label.mutate({ pair_key, label: value, frequency });
  };

  const latest = measurements?.measurements.find((m) => m.detector === detector);
  const floor = measurements?.min_labeled_for_blocking ?? 20;

  return (
    <div className="mx-auto max-w-3xl space-y-6 p-4 md:p-6">
        <div className="flex items-center gap-3">
          <Link href={`/works/${workId}`}>
            <Button variant="ghost" size="icon" aria-label="Back to Work">
              <ArrowLeft className="h-4 w-4" />
            </Button>
          </Link>
          <div>
            <h1 className="font-display text-xl font-semibold">Gap Oracle</h1>
            <p className="text-sm text-muted-foreground">
              Hand-label detector candidates so precision and recall can be
              measured. Unknowns are stored but never scored.
            </p>
          </div>
        </div>

        <Card>
          <CardContent className="space-y-3 p-4">
            <div className="flex flex-col gap-3 sm:flex-row">
              <Select value={detector} onValueChange={setDetector}>
                <SelectTrigger className="sm:w-64" aria-label="Detector">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {DETECTORS.map((d) => (
                    <SelectItem key={d.id} value={d.id}>
                      {d.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                value={signature}
                onChange={(e) => setSignature(e.target.value)}
                placeholder="Signed by (your name)"
                className="sm:w-56"
              />
              <Button
                variant="outline"
                onClick={() => evaluate.mutate()}
                disabled={evaluate.isPending}
              >
                {evaluate.isPending ? (
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                ) : (
                  <FlaskConical className="mr-2 h-4 w-4" />
                )}
                Evaluate
              </Button>
            </div>
            {latest ? (
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <Scale className="h-4 w-4 text-muted-foreground" />
                <span>
                  Precision {pct(latest.precision_overall)} · Recall{" "}
                  {pct(latest.recall_overall)} · κ{" "}
                  {latest.kappa == null ? "—" : latest.kappa.toFixed(2)} ·{" "}
                  {latest.n_labeled} labels ({latest.n_unknown_excluded} unknown
                  excluded)
                </span>
                {latest.strata?.rare && (
                  <span className="text-muted-foreground">
                    rare: {pct(latest.strata.rare.precision)}/
                    {pct(latest.strata.rare.recall)} · common:{" "}
                    {pct(latest.strata.common?.precision)}/
                    {pct(latest.strata.common?.recall)}
                  </span>
                )}
                <Badge
                  variant="outline"
                  className={
                    latest.meets_blocking_floor
                      ? "border-emerald-500/40 text-emerald-600 dark:text-emerald-400"
                      : "text-muted-foreground"
                  }
                >
                  {latest.meets_blocking_floor
                    ? "blocking unlocked"
                    : `blocking locked — needs ${floor}+ labels`}
                </Badge>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">
                Not measured yet. This detector cannot produce blocking-severity
                gaps until it is evaluated over at least {floor} labels.
              </p>
            )}
          </CardContent>
        </Card>

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-16 w-full" />
            <Skeleton className="h-16 w-full" />
          </div>
        ) : error ? (
          <p className="text-sm text-destructive">Could not load candidates.</p>
        ) : (
          <div className="space-y-2">
            {(data?.candidates ?? []).map((c) => (
              <Card key={c.pair_key}>
                <CardContent className="flex flex-wrap items-center gap-3 p-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{candidateTitle(c)}</span>
                      <Badge variant="outline" className="shrink-0 text-xs">
                        ×{c.frequency} {c.frequency_band}
                      </Badge>
                      {c.label && (
                        <Badge className={`shrink-0 text-xs ${LABEL_STYLES[c.label]}`}>
                          {c.label.replace(/_/g, " ")} — {c.labeled_by}
                        </Badge>
                      )}
                    </div>
                    {candidateDetail(c) && (
                      <p className="truncate text-xs text-muted-foreground">
                        {candidateDetail(c)}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 gap-1">
                    <Button
                      size="sm"
                      variant={c.label === "is_gap" ? "default" : "outline"}
                      onClick={() => submitLabel(c.pair_key, "is_gap", c.frequency)}
                      disabled={label.isPending}
                    >
                      <Check className="mr-1 h-3.5 w-3.5" /> Gap
                    </Button>
                    <Button
                      size="sm"
                      variant={c.label === "is_not_gap" ? "default" : "outline"}
                      onClick={() => submitLabel(c.pair_key, "is_not_gap", c.frequency)}
                      disabled={label.isPending}
                    >
                      <X className="mr-1 h-3.5 w-3.5" /> Not a gap
                    </Button>
                    <Button
                      size="sm"
                      variant={c.label === "unknown" ? "default" : "outline"}
                      onClick={() => submitLabel(c.pair_key, "unknown", c.frequency)}
                      disabled={label.isPending}
                    >
                      <HelpCircle className="mr-1 h-3.5 w-3.5" /> Unknown
                    </Button>
                  </div>
                </CardContent>
              </Card>
            ))}
            {data && data.candidates.length === 0 && (
              <p className="py-8 text-center text-sm text-muted-foreground">
                This detector found no candidates for this Work.
              </p>
            )}
            {data && data.unflagged_labels.length > 0 && (
              <div className="pt-2">
                <p className="mb-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
                  Labeled but no longer flagged (detector misses)
                </p>
                {data.unflagged_labels.map((u) => (
                  <div
                    key={u.pair_key}
                    className="flex items-center gap-2 py-1 text-sm text-muted-foreground"
                  >
                    <span className="truncate">{u.pair_key}</span>
                    <Badge className={`text-xs ${LABEL_STYLES[u.label]}`}>
                      {u.label.replace(/_/g, " ")} — {u.labeled_by}
                    </Badge>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
    </div>
  );
}
