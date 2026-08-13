import { useState } from "react";
import { useParams, Link } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  ArrowLeft,
  AlertTriangle,
  CheckCircle2,
  Loader2,
  ShieldCheck,
  ScrollText,
  Play,
} from "lucide-react";
import { toast } from "sonner";
import { Page, EmptyState, ErrorState, LoadingState } from "@/components/primitives";

const API = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

const MODE_LABELS: Record<string, string> = {
  chapter_vs_book: "Chapter vs. book",
  book_vs_series: "Book vs. series so far",
  full_series: "Full series review",
  terminology_audit: "Terminology audit",
  canon_audit: "Canon audit",
  change_impact: "Change impact",
  release_gate: "Release gate",
};

const RESOLUTION_LABELS: Record<string, string> = {
  update_book_text: "Update the book text",
  approve_canon_correction: "Approve a canon correction",
  add_bridge_scene: "Add a bridge scene",
  clarify_time_jump: "Clarify the time jump",
  retag_entity: "Re-tag the entity",
  accept_intentional_ambiguity: "Accept as intentional ambiguity",
  defer: "Defer",
  dismiss: "Dismiss",
};

const SEVERITY_STYLES: Record<string, string> = {
  critical: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30",
  high: "bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30",
  medium: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30",
  low: "bg-muted text-muted-foreground border-border",
};

type Run = {
  id: string;
  mode: string;
  status: string;
  effective_status: string;
  partial: number;
  coverage: any;
  gate: { verdict: string; blocking_findings: number; partial_coverage: boolean } | null;
  created_at: string;
  operation: { id: string; state: string; steps: any[] } | null;
};

type Finding = {
  id: string;
  finding_type: string;
  severity: string;
  subject: string;
  explanation: string;
  evidence: {
    work_title: string;
    chapter_seq: number | null;
    quote: string;
    offset: number | null;
    statement: string;
    source?: string;
    source_ref?: string;
  }[];
  canon_class: string | null;
  status: string;
  resolution: string | null;
  resolution_note: string;
};

export default function ContinuityPage() {
  const { workId } = useParams<{ workId: string }>();
  const queryClient = useQueryClient();
  const [mode, setMode] = useState("full_series");
  const [chapterId, setChapterId] = useState<string>("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const needsChapter = mode === "chapter_vs_book" || mode === "change_impact";

  const { data: modesData } = useQuery({
    queryKey: ["review-modes"],
    queryFn: async () => {
      const r = await apiFetch(`${API}/review-runs/modes`);
      if (!r.ok) throw new Error("Failed to load review modes");
      return r.json();
    },
    staleTime: Infinity,
  });

  const { data: runsData, isLoading: runsLoading, isError: runsError, refetch: refetchRuns } = useQuery({
    queryKey: ["review-runs", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API}/review-runs?work_id=${workId}`);
      if (!r.ok) throw new Error("Failed to load review runs");
      return r.json();
    },
    refetchInterval: (q) =>
      (q.state.data?.runs ?? []).some(
        (run: Run) => run.effective_status === "running" || run.effective_status === "pending",
      )
        ? 3000
        : false,
  });

  const runs: Run[] = runsData?.runs ?? [];
  const activeRun = runs.find((r) => r.id === selectedRunId) ?? runs[0] ?? null;

  const { data: chaptersData } = useQuery({
    queryKey: ["work-chapters", workId],
    queryFn: async () => {
      const r = await apiFetch(`${API}/works/${workId}/chapters`);
      if (!r.ok) throw new Error("Failed to load chapters");
      return r.json();
    },
    enabled: needsChapter,
    staleTime: 60_000,
  });
  const chapters: { id: string; seq: number; title: string | null }[] =
    chaptersData?.chapters ?? [];

  const startRun = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API}/review-runs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          mode,
          work_id: workId,
          chapter_id: needsChapter ? chapterId : null,
        }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "Could not start the review");
      return r.json();
    },
    onSuccess: (data) => {
      setSelectedRunId(data.run.id);
      queryClient.invalidateQueries({ queryKey: ["review-runs", workId] });
      toast.success("Continuity review started");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Page
      wide
      eyebrow="Continuity review"
      title="Continuity review"
      actions={
        <Link href={`/works/${workId}`}>
          <Button variant="ghost" size="sm" className="min-h-11" data-testid="button-back-to-work">
            <ArrowLeft className="mr-1 h-4 w-4" /> Back to work
          </Button>
        </Link>
      }
    >
      <div className="space-y-6">
      <Card>
        <CardContent className="flex flex-wrap items-end gap-3 p-4">
          <div className="space-y-1">
            <div className="text-xs font-medium text-muted-foreground">Review mode</div>
            <Select value={mode} onValueChange={setMode}>
              <SelectTrigger className="w-60" data-testid="select-review-mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {(modesData?.modes ?? Object.keys(MODE_LABELS)).map((m: string) => (
                  <SelectItem key={m} value={m}>
                    {MODE_LABELS[m] ?? m}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {needsChapter && (
            <div className="space-y-1">
              <div className="text-xs font-medium text-muted-foreground">Chapter</div>
              <Select value={chapterId} onValueChange={setChapterId}>
                <SelectTrigger className="w-64" data-testid="select-review-chapter">
                  <SelectValue placeholder="Pick the chapter to review" />
                </SelectTrigger>
                <SelectContent>
                  {chapters.map((c) => (
                    <SelectItem key={c.id} value={c.id}>
                      Ch. {c.seq}
                      {c.title ? ` — ${c.title}` : ""}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          )}
          <Button
            onClick={() => startRun.mutate()}
            disabled={startRun.isPending || (needsChapter && !chapterId)}
            data-testid="button-start-review"
          >
            {startRun.isPending ? (
              <Loader2 className="mr-1 h-4 w-4 animate-spin" />
            ) : (
              <Play className="mr-1 h-4 w-4" />
            )}
            Start review
          </Button>
          <p className="w-full text-xs text-muted-foreground">
            Reviews run as durable jobs: one evidence ledger per book, then a
            deterministic cross-book reconciliation. Every run reports exactly
            what it did — and did not — check.
          </p>
        </CardContent>
      </Card>

      {runsLoading && <LoadingState rows={3} label="Loading review runs" />}

      {!runsLoading && runsError && (
        <ErrorState
          title="Couldn't load review runs"
          detail="The continuity review runs failed to load."
          onRetry={() => refetchRuns()}
        />
      )}

      {!runsLoading && !runsError && runs.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {runs.slice(0, 8).map((r) => (
            <button
              key={r.id}
              onClick={() => setSelectedRunId(r.id)}
              className={`rounded-md border px-3 py-1.5 text-xs transition-colors ${
                activeRun?.id === r.id
                  ? "border-primary bg-primary/10"
                  : "border-border hover:bg-muted"
              }`}
              data-testid={`button-run-${r.id}`}
            >
              <span className="font-medium">{MODE_LABELS[r.mode] ?? r.mode}</span>
              <span className="ml-2 text-muted-foreground">
                {new Date(r.created_at).toLocaleString()}
              </span>
              <RunStatusBadge run={r} />
            </button>
          ))}
        </div>
      )}

      {activeRun && <RunDetail run={activeRun} workId={workId!} />}

      {!runsLoading && !runsError && runs.length === 0 && (
        <EmptyState
          icon={<ShieldCheck />}
          title="No continuity reviews yet"
          description="Start one to build per-book evidence ledgers and reconcile them across the series."
        />
      )}
      </div>
    </Page>
  );
}

function RunStatusBadge({ run }: { run: Run }) {
  const s = run.effective_status;
  if (s === "running" || s === "pending")
    return <Loader2 className="ml-2 inline h-3 w-3 animate-spin" />;
  if (s === "failed" || s === "cancelled")
    return <span className="ml-2 text-red-500">{s}</span>;
  return null;
}

function RunDetail({ run, workId }: { run: Run; workId: string }) {
  if (run.effective_status === "running" || run.effective_status === "pending") {
    const steps = run.operation?.steps ?? [];
    const done = steps.filter((s: any) => s.state === "done").length;
    return (
      <Card>
        <CardContent className="flex items-center gap-3 p-4 text-sm">
          <Loader2 className="h-4 w-4 animate-spin text-primary" />
          <span>
            Reviewing… {done}/{steps.length || "?"} steps complete
          </span>
        </CardContent>
      </Card>
    );
  }
  if (run.effective_status === "failed" || run.effective_status === "cancelled") {
    return (
      <Card className="border-red-500/30">
        <CardContent className="flex items-center gap-2 p-4 text-sm text-red-600 dark:text-red-400">
          <AlertTriangle className="h-4 w-4" />
          This review {run.effective_status} before finishing — its results are
          incomplete and are not shown.
        </CardContent>
      </Card>
    );
  }
  return (
    <div className="space-y-4">
      <CoverageBanner run={run} />
      {run.gate && <GateBanner gate={run.gate} />}
      <FindingsTray runId={run.id} workId={workId} />
    </div>
  );
}

function CoverageBanner({ run }: { run: Run }) {
  const cov = run.coverage ?? {};
  const partial = Boolean(run.partial);
  const counts = cov.chapter_counts ?? {};
  const unreviewed: any[] = cov.unreviewed_regions ?? [];
  return (
    <Card className={partial ? "border-amber-500/40" : "border-emerald-500/30"}>
      <CardContent className="space-y-2 p-4">
        <div className="flex items-center gap-2">
          {partial ? (
            <>
              <AlertTriangle className="h-4 w-4 text-amber-500" />
              <span className="font-medium text-amber-600 dark:text-amber-400" data-testid="text-coverage-partial">
                Partial review — some material was not checked
              </span>
            </>
          ) : (
            <>
              <CheckCircle2 className="h-4 w-4 text-emerald-500" />
              <span className="font-medium text-emerald-600 dark:text-emerald-400" data-testid="text-coverage-full">
                Full review — everything in scope was checked
              </span>
            </>
          )}
        </div>
        <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
          <span>{(cov.books ?? []).length} book(s)</span>
          <span>· {counts.parsed ?? 0} chapters reviewed</span>
          {(counts.skipped ?? 0) > 0 && <span>· {counts.skipped} empty</span>}
          {(counts.failed ?? 0) > 0 && <span>· {counts.failed} unextracted</span>}
          {(counts.stale ?? 0) > 0 && <span>· {counts.stale} changed since ledger</span>}
          <span>· {cov.tool_version}</span>
        </div>
        {unreviewed.length > 0 && (
          <div className="space-y-1 rounded-md bg-amber-500/5 p-2 text-xs">
            <div className="font-medium">Not reviewed:</div>
            {unreviewed.slice(0, 12).map((u, i) => (
              <div key={i} className="text-muted-foreground">
                {u.work_title}
                {u.title ? ` — ch. ${u.seq} “${u.title}”` : u.seq ? ` — ch. ${u.seq}` : ""}
                {" · "}
                {u.reason}
              </div>
            ))}
            {unreviewed.length > 12 && (
              <div className="text-muted-foreground">…and {unreviewed.length - 12} more</div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function GateBanner({ gate }: { gate: NonNullable<Run["gate"]> }) {
  const blocked = gate.verdict === "blocked";
  return (
    <Card className={blocked ? "border-red-500/40" : "border-emerald-500/30"}>
      <CardContent className="flex items-center gap-2 p-4 text-sm">
        {blocked ? (
          <AlertTriangle className="h-4 w-4 text-red-500" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-emerald-500" />
        )}
        <span data-testid="text-gate-verdict">
          Release gate: <span className="font-semibold">{gate.verdict}</span>
          {blocked &&
            ` — ${gate.blocking_findings} open high-severity finding(s)` +
              (gate.partial_coverage ? ", coverage incomplete" : "")}
        </span>
      </CardContent>
    </Card>
  );
}

function FindingsTray({ runId, workId }: { runId: string; workId: string }) {
  const queryClient = useQueryClient();
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["review-findings", runId],
    queryFn: async () => {
      const r = await apiFetch(`${API}/review-runs/${runId}/findings`);
      if (!r.ok) throw new Error("Failed to load findings");
      return r.json();
    },
  });
  const findings: Finding[] = data?.findings ?? [];
  if (isLoading) return <LoadingState rows={3} label="Loading findings" />;
  if (isError)
    return (
      <ErrorState
        title="Couldn't load findings"
        detail="The review findings failed to load."
        onRetry={() => refetch()}
      />
    );
  if (findings.length === 0)
    return (
      <EmptyState
        icon={<CheckCircle2 />}
        title="No continuity findings in this run"
        description="This review completed without recording any findings."
      />
    );
  const open = findings.filter((f) => f.status === "open");
  const closed = findings.filter((f) => f.status !== "open");
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm font-medium">
        <ScrollText className="h-4 w-4" />
        {open.length} open finding(s), {closed.length} already handled
      </div>
      {[...open, ...closed].map((f) => (
        <FindingCard
          key={f.id}
          finding={f}
          onChanged={() =>
            queryClient.invalidateQueries({ queryKey: ["review-findings", runId] })
          }
        />
      ))}
    </div>
  );
}

function FindingCard({ finding, onChanged }: { finding: Finding; onChanged: () => void }) {
  const [resolution, setResolution] = useState<string>("");
  const [note, setNote] = useState("");
  const [expanded, setExpanded] = useState(false);

  const disposition = useMutation({
    mutationFn: async (status: string) => {
      const r = await apiFetch(`${API}/review-findings/${finding.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status, resolution: resolution || null, note }),
      });
      if (!r.ok) throw new Error((await r.json()).detail || "Could not save");
      return r.json();
    },
    onSuccess: () => {
      toast.success("Finding updated");
      onChanged();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const isClosed = finding.status !== "open";
  return (
    <Card className={isClosed ? "opacity-60" : ""}>
      <CardContent className="space-y-2 p-4">
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="outline" className={SEVERITY_STYLES[finding.severity] ?? ""}>
            {finding.severity}
          </Badge>
          <span className="text-sm font-medium">
            {finding.finding_type.replace(/_/g, " ")}
          </span>
          {finding.canon_class && (
            <Badge variant="outline" className="text-xs">
              canon: {finding.canon_class}
            </Badge>
          )}
          {isClosed && (
            <Badge variant="secondary" className="text-xs">
              {finding.status}
              {finding.resolution ? ` · ${RESOLUTION_LABELS[finding.resolution] ?? finding.resolution}` : ""}
            </Badge>
          )}
        </div>
        <p className="text-sm text-muted-foreground">{finding.explanation}</p>
        <div className="space-y-1">
          {finding.evidence.map((e, i) =>
            e.source === "canon" ? (
              <div
                key={i}
                className="rounded-md border border-primary/20 bg-primary/5 p-2 text-xs"
                data-testid={`evidence-canon-${i}`}
              >
                <Badge variant="outline" className="mr-2 text-[10px] uppercase">
                  Canon evidence
                </Badge>
                <span className="font-medium">{e.work_title}</span>
                {e.statement && <span className="ml-2 italic">“{e.statement}”</span>}
                {e.source_ref && (
                  <span className="ml-2 text-muted-foreground">— {e.source_ref}</span>
                )}
              </div>
            ) : (
              <div key={i} className="rounded-md bg-muted/50 p-2 text-xs">
                <span className="font-medium">
                  {e.work_title}
                  {e.chapter_seq != null ? `, ch. ${e.chapter_seq}` : ""}
                  {e.offset != null ? ` @ ${e.offset}` : ""}
                </span>
                {e.quote && <span className="ml-2 italic">“{e.quote}”</span>}
              </div>
            ),
          )}
        </div>
        {!isClosed && !expanded && (
          <Button size="sm" variant="outline" onClick={() => setExpanded(true)} data-testid={`button-resolve-${finding.id}`}>
            Resolve…
          </Button>
        )}
        {!isClosed && expanded && (
          <div className="space-y-2 border-t pt-2">
            <Select value={resolution} onValueChange={setResolution}>
              <SelectTrigger className="w-72" data-testid={`select-resolution-${finding.id}`}>
                <SelectValue placeholder="Choose a resolution" />
              </SelectTrigger>
              <SelectContent>
                {Object.entries(RESOLUTION_LABELS).map(([k, v]) => (
                  <SelectItem key={k} value={k}>
                    {v}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Textarea
              placeholder="Optional note"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              className="min-h-16"
            />
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={!resolution || disposition.isPending}
                onClick={() => disposition.mutate("resolved")}
              >
                Mark resolved
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={!resolution || disposition.isPending}
                onClick={() => disposition.mutate("intentional")}
              >
                Intentional
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={disposition.isPending}
                onClick={() => {
                  setResolution("defer");
                  disposition.mutate("deferred");
                }}
              >
                Defer
              </Button>
              <Button
                size="sm"
                variant="ghost"
                disabled={!resolution || disposition.isPending}
                onClick={() => disposition.mutate("dismissed")}
              >
                Dismiss
              </Button>
            </div>
          </div>
        )}
        {finding.resolution_note && isClosed && (
          <p className="text-xs italic text-muted-foreground">“{finding.resolution_note}”</p>
        )}
      </CardContent>
    </Card>
  );
}
