/**
 * Calibration (MCOS) dashboard — /mcos
 *
 * Benchmark suites, recent runs, per-case results and LLM telemetry.
 * Data fetching mirrors the direct-fetch pattern used by the document
 * detail page's knowledge tab (apiFetch + BASE, react-query wrappers).
 */
import { Fragment, useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  Table, TableHeader, TableBody, TableHead, TableRow, TableCell,
} from "@/components/ui/table";
import {
  Collapsible, CollapsibleContent, CollapsibleTrigger,
} from "@/components/ui/collapsible";
import {
  Gauge, Play, Loader2, RefreshCw, AlertCircle, Sparkles,
  ArrowUp, ArrowDown, ChevronDown, ChevronRight, Activity,
} from "lucide-react";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types (mirror the fixed /api/mcos contract) ────────────────────────────────

interface LastRun {
  id: string;
  avg_score: number | null;
  status: string;
  finished_at: string | null;
}

interface Benchmark {
  id: string;
  name: string;
  description: string | null;
  category: string;
  kind: string;
  version: string | number | null;
  enabled: boolean;
  case_count: number;
  last_run: LastRun | null;
}

interface RunMeta {
  delta?: number | null;
  regressed?: boolean;
  [k: string]: unknown;
}

interface Run {
  id: string;
  benchmark_id: string;
  benchmark_name: string;
  started_at: string | null;
  finished_at: string | null;
  model: string | null;
  status: string;
  total_cases: number | null;
  avg_score: number | null;
  meta: RunMeta | null;
}

interface JudgeScores {
  rule?: number;
  llm?: number;
  grounding?: number;
  consensus?: number;
  llm_reason?: string;
  [k: string]: number | string | undefined;
}

interface CaseResult {
  case_id: string;
  question: string;
  score: number | null;
  judge_scores: JudgeScores | null;
  response: string | null;
  latency_ms: number | null;
  error: string | null;
}

// Per-judge badges rendered next to the main case score (llm-kind cases only).
const JUDGE_BADGES: Array<{ key: "rule" | "llm" | "grounding" | "retrieval"; label: string }> = [
  { key: "rule",      label: "Rule" },
  { key: "llm",       label: "LLM" },
  { key: "grounding", label: "Grounding" },
  { key: "retrieval", label: "Retrieval" },
];

interface TelemetryPurpose {
  purpose: string;
  calls: number;
  avg_latency_ms: number | null;
  total_prompt_tokens: number | null;
  total_completion_tokens: number | null;
  error_rate: number | null;
}

interface TelemetryDay {
  day: string;
  calls: number;
  errors: number;
  avg_latency_ms: number | null;
}

// ── Small helpers ───────────────────────────────────────────────────────────────

function scoreColor(score: number | null | undefined): string {
  if (score == null) return "text-muted-foreground";
  if (score >= 0.8) return "text-emerald-600 dark:text-emerald-400";
  if (score >= 0.5) return "text-amber-600 dark:text-amber-400";
  return "text-red-600 dark:text-red-400";
}

function scorePct(score: number | null | undefined): string {
  if (score == null) return "—";
  return `${Math.round(score * 100)}%`;
}

function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return format(d, "MMM d, HH:mm");
}

function StatusBadge({ status }: { status: string }) {
  const cfg: Record<string, string> = {
    running:  "text-blue-600 border-blue-200 bg-blue-50 dark:text-blue-300 dark:border-blue-900 dark:bg-blue-950",
    success:  "text-emerald-600 border-emerald-200 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-900 dark:bg-emerald-950",
    complete: "text-emerald-600 border-emerald-200 bg-emerald-50 dark:text-emerald-300 dark:border-emerald-900 dark:bg-emerald-950",
    error:    "text-red-600 border-red-200 bg-red-50 dark:text-red-300 dark:border-red-900 dark:bg-red-950",
    failed:   "text-red-600 border-red-200 bg-red-50 dark:text-red-300 dark:border-red-900 dark:bg-red-950",
  };
  const cls = cfg[status] ?? "text-muted-foreground";
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono font-medium border ${cls}`}>
      {status === "running" && <Loader2 className="w-3 h-3 animate-spin" />}
      {status}
    </span>
  );
}

function DeltaArrow({ delta }: { delta: number | null | undefined }) {
  if (delta == null || delta === 0) return <span className="text-muted-foreground">—</span>;
  const up = delta > 0;
  return (
    <span className={`inline-flex items-center gap-0.5 text-xs font-mono ${up ? "text-emerald-600 dark:text-emerald-400" : "text-red-600 dark:text-red-400"}`}>
      {up ? <ArrowUp className="w-3 h-3" /> : <ArrowDown className="w-3 h-3" />}
      {up ? "+" : ""}{Math.round(delta * 100)}%
    </span>
  );
}

// ── Error / empty helpers ─────────────────────────────────────────────────────

function ErrorState({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-10 text-center">
      <AlertCircle className="w-8 h-8 text-red-500" />
      <p className="text-sm text-muted-foreground">{message}</p>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="w-3.5 h-3.5 mr-1.5" />
        Retry
      </Button>
    </div>
  );
}

// ── Run detail (per-case results) ───────────────────────────────────────────────

function CaseRow({ result }: { result: CaseResult }) {
  const [open, setOpen] = useState(false);
  const js = result.judge_scores;
  const judgeBadges = js
    ? JUDGE_BADGES.filter((b) => typeof js[b.key] === "number")
    : [];
  const llmReason = js && typeof js.llm_reason === "string" ? js.llm_reason : null;
  return (
    <div className="border border-border/60 rounded-md">
      <Collapsible open={open} onOpenChange={setOpen}>
        <CollapsibleTrigger asChild>
          <button className="w-full flex items-start gap-2 p-3 text-left hover:bg-muted/40 transition-colors">
            {open ? <ChevronDown className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 mt-0.5 shrink-0 text-muted-foreground" />}
            <div className="flex-1 min-w-0">
              <p className="text-sm line-clamp-2">{result.question || "(no question)"}</p>
              <div className="flex flex-wrap items-center gap-2 mt-1 text-[11px] font-mono text-muted-foreground">
                <span className={scoreColor(result.score)}>{scorePct(result.score)}</span>
                {judgeBadges.map((b) => (
                  <span key={b.key} className="px-1.5 py-0.5 rounded border text-[10px] text-muted-foreground bg-muted/30">
                    {b.label} {Math.round((js![b.key] as number) * 100)}%
                  </span>
                ))}
                <span>{result.latency_ms != null ? `${result.latency_ms} ms` : "—"}</span>
                {result.error && <span className="text-red-500">error</span>}
              </div>
              {llmReason && (
                <p className="text-[11px] text-muted-foreground/80 italic mt-1 line-clamp-2">{llmReason}</p>
              )}
            </div>
          </button>
        </CollapsibleTrigger>
        <CollapsibleContent>
          <div className="px-3 pb-3 pl-9 space-y-2">
            {result.error && (
              <div className="text-xs font-mono text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-950/40 border border-red-200 dark:border-red-900 rounded p-2">
                {result.error}
              </div>
            )}
            <div>
              <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground mb-1">Response</p>
              <pre className="text-xs whitespace-pre-wrap break-words bg-muted/50 rounded p-2 max-h-64 overflow-auto">
                {result.response || "(no response)"}
              </pre>
            </div>
          </div>
        </CollapsibleContent>
      </Collapsible>
    </div>
  );
}

function RunDetail({ runId }: { runId: string }) {
  const { data, isLoading, isError, refetch } = useQuery<{ run: Run; results: CaseResult[] }>({
    queryKey: ["mcos", "run", runId],
    queryFn: () => apiFetch(`${BASE}/mcos/runs/${runId}`).then((r) => {
      if (!r.ok) throw new Error("Failed to load run");
      return r.json();
    }),
    staleTime: 10_000,
  });

  if (isLoading) {
    return (
      <div className="space-y-2 p-3">
        <Skeleton className="h-12 w-full" />
        <Skeleton className="h-12 w-full" />
      </div>
    );
  }
  if (isError || !data) {
    return <ErrorState message="Could not load run details." onRetry={() => refetch()} />;
  }
  if (data.results.length === 0) {
    return <p className="text-sm text-muted-foreground p-3">No case results recorded for this run.</p>;
  }
  return (
    <div className="space-y-2 p-3 bg-muted/20">
      {data.results.map((r) => (
        <CaseRow key={r.case_id} result={r} />
      ))}
    </div>
  );
}

// ── Recent runs table ─────────────────────────────────────────────────────────

function RunsTable({ runs, isLoading, isError, onRetry }: {
  runs: Run[];
  isLoading: boolean;
  isError: boolean;
  onRetry: () => void;
}) {
  const [expanded, setExpanded] = useState<string | null>(null);

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-2 mb-4">
          <Activity className="w-4 h-4 text-primary" />
          <h2 className="font-mono text-sm uppercase tracking-wider">Recent Runs</h2>
        </div>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError ? (
          <ErrorState message="Could not load runs." onRetry={onRetry} />
        ) : runs.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">No runs yet. Start one from a benchmark card above.</p>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-6" />
                <TableHead>Benchmark</TableHead>
                <TableHead>Started</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Score</TableHead>
                <TableHead className="text-right">Δ</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {runs.map((run) => {
                const isOpen = expanded === run.id;
                return (
                  <Fragment key={run.id}>
                    <TableRow
                      className="cursor-pointer"
                      onClick={() => setExpanded(isOpen ? null : run.id)}
                    >
                      <TableCell className="text-muted-foreground">
                        {isOpen ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
                      </TableCell>
                      <TableCell className="font-medium">{run.benchmark_name}</TableCell>
                      <TableCell className="text-sm text-muted-foreground">{fmtTime(run.started_at)}</TableCell>
                      <TableCell><StatusBadge status={run.status} /></TableCell>
                      <TableCell className={`text-right font-mono ${scoreColor(run.avg_score)}`}>{scorePct(run.avg_score)}</TableCell>
                      <TableCell className="text-right">
                        <div className="flex items-center justify-end gap-2">
                          <DeltaArrow delta={run.meta?.delta} />
                          {run.meta?.regressed && (
                            <Badge variant="outline" className="text-[10px] text-red-600 border-red-300 dark:text-red-400 dark:border-red-900">
                              Regression
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                    </TableRow>
                    {isOpen && (
                      <TableRow>
                        <TableCell colSpan={6} className="p-0">
                          <RunDetail runId={run.id} />
                        </TableCell>
                      </TableRow>
                    )}
                  </Fragment>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}

// ── Telemetry card ──────────────────────────────────────────────────────────────

function TelemetryCard() {
  const { data, isLoading, isError, refetch } = useQuery<{
    by_purpose: TelemetryPurpose[];
    daily: TelemetryDay[];
  }>({
    queryKey: ["mcos", "telemetry"],
    queryFn: () => apiFetch(`${BASE}/mcos/telemetry?days=7`).then((r) => {
      if (!r.ok) throw new Error("Failed to load telemetry");
      return r.json();
    }),
    staleTime: 30_000,
  });

  const dailyTotals = data?.daily?.reduce(
    (acc, d) => {
      acc.calls += d.calls;
      acc.errors += d.errors;
      return acc;
    },
    { calls: 0, errors: 0 },
  );

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-2 mb-4">
          <Gauge className="w-4 h-4 text-primary" />
          <h2 className="font-mono text-sm uppercase tracking-wider">LLM Telemetry (7 days)</h2>
        </div>
        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : isError || !data ? (
          <ErrorState message="Could not load telemetry." onRetry={() => refetch()} />
        ) : data.by_purpose.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">No LLM calls recorded in the last 7 days.</p>
        ) : (
          <>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Purpose</TableHead>
                  <TableHead className="text-right">Calls</TableHead>
                  <TableHead className="text-right">Avg latency</TableHead>
                  <TableHead className="text-right">Prompt tok</TableHead>
                  <TableHead className="text-right">Compl. tok</TableHead>
                  <TableHead className="text-right">Error rate</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.by_purpose.map((p) => (
                  <TableRow key={p.purpose}>
                    <TableCell className="font-medium">{p.purpose}</TableCell>
                    <TableCell className="text-right font-mono">{p.calls}</TableCell>
                    <TableCell className="text-right font-mono">{p.avg_latency_ms != null ? `${Math.round(p.avg_latency_ms)} ms` : "—"}</TableCell>
                    <TableCell className="text-right font-mono">{p.total_prompt_tokens ?? 0}</TableCell>
                    <TableCell className="text-right font-mono">{p.total_completion_tokens ?? 0}</TableCell>
                    <TableCell className={`text-right font-mono ${(p.error_rate ?? 0) > 0.05 ? "text-red-600 dark:text-red-400" : ""}`}>
                      {p.error_rate != null ? `${Math.round(p.error_rate * 100)}%` : "—"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
            {dailyTotals && (
              <p className="text-xs font-mono text-muted-foreground mt-3">
                Daily totals: {dailyTotals.calls} calls · {dailyTotals.errors} errors across {data.daily.length} day{data.daily.length === 1 ? "" : "s"}
              </p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ── Benchmark card ──────────────────────────────────────────────────────────────

function BenchmarkCard({ bench, running, onRun }: {
  bench: Benchmark;
  running: boolean;
  onRun: () => void;
}) {
  return (
    <Card className="flex flex-col">
      <CardContent className="p-5 flex flex-col flex-1 gap-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <h3 className="font-serif font-semibold truncate">{bench.name}</h3>
            {bench.description && (
              <p className="text-xs text-muted-foreground mt-0.5 line-clamp-2">{bench.description}</p>
            )}
          </div>
          <Badge variant="outline" className="text-[10px] font-mono shrink-0">{bench.kind}</Badge>
        </div>

        <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-muted-foreground">
          <span className="px-1.5 py-0.5 rounded border">{bench.category}</span>
          <span>{bench.case_count} case{bench.case_count === 1 ? "" : "s"}</span>
        </div>

        <div className="flex items-end justify-between gap-2 mt-auto pt-2">
          <div>
            <p className={`text-2xl font-mono font-semibold ${scoreColor(bench.last_run?.avg_score)}`}>
              {scorePct(bench.last_run?.avg_score)}
            </p>
            <p className="text-[11px] text-muted-foreground">
              {bench.last_run ? `last run ${fmtTime(bench.last_run.finished_at)}` : "never run"}
            </p>
          </div>
          <Button size="sm" onClick={onRun} disabled={running || !bench.enabled}>
            {running ? (
              <>
                <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" />
                Running
              </>
            ) : (
              <>
                <Play className="w-3.5 h-3.5 mr-1.5" />
                Run
              </>
            )}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Mcos() {
  const qc = useQueryClient();

  // Recent runs — used to determine polling and per-benchmark "running" state.
  const runsQuery = useQuery<{ runs: Run[] }>({
    queryKey: ["mcos", "runs"],
    queryFn: () => apiFetch(`${BASE}/mcos/runs?limit=20`).then((r) => {
      if (!r.ok) throw new Error("Failed to load runs");
      return r.json();
    }),
    staleTime: 5_000,
    refetchInterval: (query) =>
      query.state.data?.runs?.some((r) => r.status === "running") ? 3_000 : false,
  });

  const runs = runsQuery.data?.runs ?? [];
  const anyRunning = runs.some((r) => r.status === "running");

  const benchmarksQuery = useQuery<{ benchmarks: Benchmark[] }>({
    queryKey: ["mcos", "benchmarks"],
    queryFn: () => apiFetch(`${BASE}/mcos/benchmarks`).then((r) => {
      if (!r.ok) throw new Error("Failed to load benchmarks");
      return r.json();
    }),
    staleTime: 5_000,
    // Poll while any run is in progress so scores/last-run refresh live.
    refetchInterval: () => (anyRunning ? 3_000 : false),
  });

  const benchmarks = benchmarksQuery.data?.benchmarks ?? [];

  // Which benchmark ids currently have a running run.
  const runningBenchIds = new Set(
    runs.filter((r) => r.status === "running").map((r) => r.benchmark_id),
  );

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["mcos", "runs"] });
    qc.invalidateQueries({ queryKey: ["mcos", "benchmarks"] });
  };

  // ── Mutations ────────────────────────────────────────────────────────────────

  const seed = useMutation<{ benchmarks: number; cases: number }, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/seed`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error("Seed failed");
      return r.json();
    }),
    onSuccess: (d) => {
      toast.success(`Seeded ${d.benchmarks} benchmark${d.benchmarks === 1 ? "" : "s"} (${d.cases} cases)`);
      invalidate();
    },
    onError: () => toast.error("Could not seed benchmarks"),
  });

  const runAll = useMutation<{ started: string[] }, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/run-all`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error("Run-all failed");
      return r.json();
    }),
    onSuccess: (d) => {
      toast.success(`Started ${d.started.length} run${d.started.length === 1 ? "" : "s"}`);
      invalidate();
    },
    onError: () => toast.error("Could not start runs"),
  });

  const runOne = useMutation<{ run_id: string }, Error, string>({
    mutationFn: (benchId) => apiFetch(`${BASE}/mcos/run/${benchId}`, { method: "POST" }).then(async (r) => {
      if (r.status === 409) throw new Error("409");
      if (!r.ok) throw new Error("Run failed");
      return r.json();
    }),
    onSuccess: () => {
      toast.success("Run started");
      invalidate();
    },
    onError: (err) => {
      if (err.message === "409") toast.error("A run is already in progress for this benchmark");
      else toast.error("Could not start run");
    },
  });

  const benchLoading = benchmarksQuery.isLoading;
  const benchError = benchmarksQuery.isError;
  const isEmpty = !benchLoading && !benchError && benchmarks.length === 0;

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-6xl mx-auto">
      {/* Header */}
      <div className="border-b border-border/50 pb-4 flex items-start justify-between gap-4">
        <div>
          <h1 className="text-3xl font-serif font-semibold tracking-tight flex items-center gap-2">
            <Gauge className="w-7 h-7 text-primary" />
            Calibration
          </h1>
          <p className="text-muted-foreground mt-1 font-serif">
            Model behaviour benchmark suites, run history and LLM telemetry.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {isEmpty && (
            <Button variant="outline" onClick={() => seed.mutate()} disabled={seed.isPending}>
              {seed.isPending ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1.5" />}
              Seed benchmarks
            </Button>
          )}
          <Button
            onClick={() => runAll.mutate()}
            disabled={runAll.isPending || isEmpty || benchmarks.length === 0}
          >
            {runAll.isPending ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Play className="w-4 h-4 mr-1.5" />}
            Run all
          </Button>
        </div>
      </div>

      {/* Benchmarks */}
      {benchLoading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[0, 1, 2].map((i) => <Skeleton key={i} className="h-40 w-full" />)}
        </div>
      ) : benchError ? (
        <Card>
          <CardContent className="p-6">
            <ErrorState message="Could not load benchmarks. The calibration service may still be starting." onRetry={() => benchmarksQuery.refetch()} />
          </CardContent>
        </Card>
      ) : isEmpty ? (
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="p-8 text-center space-y-3">
            <Sparkles className="w-8 h-8 text-primary mx-auto" />
            <h3 className="font-serif font-semibold text-lg">No benchmarks yet</h3>
            <p className="text-sm text-muted-foreground max-w-md mx-auto">
              Seed the default calibration suites to start measuring model behaviour.
              Seeding is idempotent — it creates the standard benchmarks and their cases,
              and refreshing again is safe.
            </p>
            <Button onClick={() => seed.mutate()} disabled={seed.isPending}>
              {seed.isPending ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Sparkles className="w-4 h-4 mr-1.5" />}
              Seed benchmarks
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {benchmarks.map((b) => (
            <BenchmarkCard
              key={b.id}
              bench={b}
              running={runningBenchIds.has(b.id) || (runOne.isPending && runOne.variables === b.id)}
              onRun={() => runOne.mutate(b.id)}
            />
          ))}
        </div>
      )}

      <Separator />

      {/* Recent runs */}
      <RunsTable
        runs={runs}
        isLoading={runsQuery.isLoading}
        isError={runsQuery.isError}
        onRetry={() => runsQuery.refetch()}
      />

      {/* Telemetry */}
      <TelemetryCard />
    </div>
  );
}
