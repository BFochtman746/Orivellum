/**
 * Calibration (MCOS) dashboard — /mcos
 *
 * Benchmark suites, recent runs, per-case results and LLM telemetry.
 * Data fetching mirrors the direct-fetch pattern used by the document
 * detail page's knowledge tab (apiFetch + BASE, react-query wrappers).
 */
import { Fragment, useState, useRef, useEffect } from "react";
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
  Dialog, DialogContent, DialogHeader, DialogFooter, DialogTitle,
  DialogDescription, DialogTrigger, DialogClose,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Gauge, Play, Loader2, RefreshCw, AlertCircle, Sparkles,
  ArrowUp, ArrowDown, ChevronDown, ChevronRight, Activity,
  FlaskConical, Trash2, Plus, CheckCircle2, SlidersHorizontal,
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

// ── Prompt Lab (Phase 4, multi-slot Phase 189) ─────────────────────────────────

const DEFAULT_SLOT = "chat.base";

interface SlotInfo {
  slot: string;
  label: string;
  benchmarkable: boolean;
  active_name: string | null;
  active_version: number | null;
  prompt_count: number;
}

interface Prompt {
  id: string;
  slot: string;
  name: string;
  content: string;
  version: number;
  active: boolean;
  created_at: string | null;
  notes: string | null;
  last_benchmark: unknown | null;
}

interface BenchPerSuite {
  benchmark_id: string;
  avg_score: number | null;
  status: string;
}

interface BenchSide {
  avg: number | null;
  per_suite: BenchPerSuite[];
}

interface PromptBenchmark {
  status: "running" | "done" | "none";
  candidate: BenchSide | null;
  active: BenchSide | null;
  delta: number | null;
}

// Dialog for creating a new candidate prompt.
function NewCandidateDialog({ slot, benchmarkable, activeContent, onCreate, pending }: {
  slot: string;
  benchmarkable: boolean;
  activeContent: string;
  onCreate: (body: { name: string; content: string; notes: string }) => Promise<void>;
  pending: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [content, setContent] = useState(activeContent);
  const [notes, setNotes] = useState("");

  // Re-seed the textarea with the active content whenever the dialog opens.
  const handleOpenChange = (v: boolean) => {
    if (v) {
      setName("");
      setContent(activeContent);
      setNotes("");
    }
    setOpen(v);
  };

  const submit = async () => {
    if (!name.trim() || !content.trim()) return;
    await onCreate({ name: name.trim(), content, notes: notes.trim() });
    setOpen(false);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button size="sm" variant="outline">
          <Plus className="w-3.5 h-3.5 mr-1.5" />
          New candidate
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>New prompt candidate</DialogTitle>
          <DialogDescription>
            Create an inactive candidate for <span className="font-mono">{slot}</span>.{" "}
            {benchmarkable
              ? "Benchmark it against the active prompt before activating."
              : "Review and activate directly — this slot cannot be benchmarked."}
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="prompt-name">Name</Label>
            <Input id="prompt-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Concise system preamble" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="prompt-content">Content</Label>
            <Textarea
              id="prompt-content"
              value={content}
              onChange={(e) => setContent(e.target.value)}
              rows={10}
              className="font-mono text-xs"
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="prompt-notes">Notes (optional)</Label>
            <Input id="prompt-notes" value={notes} onChange={(e) => setNotes(e.target.value)} placeholder="What changed and why" />
          </div>
        </div>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button onClick={submit} disabled={pending || !name.trim() || !content.trim()}>
            {pending ? <Loader2 className="w-4 h-4 mr-1.5 animate-spin" /> : <Plus className="w-4 h-4 mr-1.5" />}
            Create candidate
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// A single candidate row with its benchmark-vs-active controls.
function CandidatePrompt({ prompt, slot, benchmarkable, onChanged }: {
  prompt: Prompt;
  slot: string;
  benchmarkable: boolean;
  onChanged: () => void;
}) {
  const [showSuites, setShowSuites] = useState(false);

  const benchQuery = useQuery<PromptBenchmark>({
    queryKey: ["mcos", "prompt-benchmark", prompt.id],
    queryFn: () => apiFetch(`${BASE}/mcos/prompts/${prompt.id}/benchmark`).then((r) => {
      if (!r.ok) throw new Error("Failed to load benchmark");
      return r.json();
    }),
    staleTime: 2_000,
    refetchInterval: (query) => (query.state.data?.status === "running" ? 3_000 : false),
    retry: false,
    enabled: benchmarkable,
  });

  const status = benchQuery.data?.status ?? "none";
  const running = status === "running";
  const done = status === "done";

  const startBench = useMutation<{ candidate_runs: string[]; active_runs: string[] }, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/prompts/${prompt.id}/benchmark`, { method: "POST" }).then(async (r) => {
      if (r.status === 409) throw new Error("409");
      if (!r.ok) throw new Error("Benchmark failed");
      return r.json();
    }),
    onSuccess: () => {
      toast.success("Benchmark started");
      benchQuery.refetch();
    },
    onError: (err) => {
      if (err.message === "409") toast.error("A prompt benchmark for this slot is already in progress");
      else toast.error("Could not start benchmark");
    },
  });

  const activate = useMutation<unknown, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/prompts/${prompt.id}/activate`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error("Activate failed");
      return r.json();
    }),
    onSuccess: () => {
      toast.success("Prompt activated");
      onChanged();
    },
    onError: () => toast.error("Could not activate prompt"),
  });

  const remove = useMutation<void, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/prompts/${prompt.id}`, { method: "DELETE" }).then((r) => {
      if (r.status === 409) throw new Error("409");
      if (!r.ok && r.status !== 204) throw new Error("Delete failed");
    }),
    onSuccess: () => {
      toast.success("Candidate deleted");
      onChanged();
    },
    onError: (err) => {
      if (err.message === "409") toast.error("Cannot delete the active prompt");
      else toast.error("Could not delete candidate");
    },
  });

  const handleActivate = () => {
    if (!window.confirm(`Activate "${prompt.name}" (v${prompt.version})? This deactivates the current active prompt for ${slot}.`)) return;
    activate.mutate();
  };
  const handleDelete = () => {
    if (!window.confirm(`Delete candidate "${prompt.name}" (v${prompt.version})?`)) return;
    remove.mutate();
  };

  const candAvg = benchQuery.data?.candidate?.avg ?? null;
  const actAvg = benchQuery.data?.active?.avg ?? null;
  const delta = benchQuery.data?.delta ?? null;

  return (
    <div className="rounded-lg border border-border/60 p-3 space-y-2">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="font-medium truncate">{prompt.name}</span>
            <Badge variant="outline" className="text-[10px] font-mono">v{prompt.version}</Badge>
          </div>
          <div className="flex items-center gap-3 mt-0.5 text-[11px] font-mono text-muted-foreground">
            <span>{prompt.created_at ? fmtTime(prompt.created_at) : "—"}</span>
            {prompt.notes && <span className="italic truncate">{prompt.notes}</span>}
          </div>
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {benchmarkable && (
            <Button size="sm" variant="outline" disabled={running || startBench.isPending}
              onClick={() => startBench.mutate()}>
              {running || startBench.isPending
                ? <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Benchmarking</>
                : <><FlaskConical className="w-3.5 h-3.5 mr-1.5" /> Benchmark vs active</>}
            </Button>
          )}
          <Button size="sm" disabled={activate.isPending} onClick={handleActivate}>
            {activate.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />}
            Activate
          </Button>
          <Button size="sm" variant="ghost" disabled={remove.isPending} onClick={handleDelete}
            className="text-muted-foreground hover:text-red-600">
            {remove.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
          </Button>
        </div>
      </div>

      {!benchmarkable && (
        <p className="text-[11px] text-muted-foreground italic">
          Version and activate only — benchmarking applies to the chat persona.
        </p>
      )}

      {benchmarkable && done && (
        <div className="rounded border border-border/50 bg-muted/20 p-2.5 space-y-2">
          <div className="flex items-center gap-4 text-xs font-mono">
            <span>candidate <span className={scoreColor(candAvg)}>{scorePct(candAvg)}</span></span>
            <span className="text-muted-foreground">vs</span>
            <span>active <span className={scoreColor(actAvg)}>{scorePct(actAvg)}</span></span>
            {delta != null && (
              <Badge variant="outline" className={`text-[10px] ${
                delta >= 0
                  ? "text-emerald-600 border-emerald-300 dark:text-emerald-400 dark:border-emerald-900"
                  : "text-red-600 border-red-300 dark:text-red-400 dark:border-red-900"
              }`}>
                {delta >= 0 ? "+" : ""}{Math.round(delta * 100)} pts
              </Badge>
            )}
          </div>
          {(benchQuery.data?.candidate?.per_suite?.length ?? 0) > 0 && (
            <Collapsible open={showSuites} onOpenChange={setShowSuites}>
              <CollapsibleTrigger asChild>
                <button className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground">
                  {showSuites ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                  Per-suite breakdown
                </button>
              </CollapsibleTrigger>
              <CollapsibleContent>
                <div className="mt-2 space-y-1">
                  {benchQuery.data!.candidate!.per_suite.map((s) => {
                    const actSuite = benchQuery.data?.active?.per_suite?.find((a) => a.benchmark_id === s.benchmark_id);
                    return (
                      <div key={s.benchmark_id} className="flex items-center justify-between text-[11px] font-mono">
                        <span className="text-muted-foreground truncate">{s.benchmark_id}</span>
                        <span className="flex items-center gap-3">
                          <span className={scoreColor(s.avg_score)}>{scorePct(s.avg_score)}</span>
                          <span className="text-muted-foreground/60">vs {scorePct(actSuite?.avg_score ?? null)}</span>
                        </span>
                      </div>
                    );
                  })}
                </div>
              </CollapsibleContent>
            </Collapsible>
          )}
        </div>
      )}
    </div>
  );
}

function PromptLabCard() {
  const qc = useQueryClient();
  const [slot, setSlot] = useState<string>(DEFAULT_SLOT);

  // Slot registry (multi-slot). 404-tolerant: falls back to the chat.base slot.
  const slotsQuery = useQuery<{ slots: SlotInfo[] }>({
    queryKey: ["mcos", "prompt-slots"],
    queryFn: () => apiFetch(`${BASE}/mcos/prompts/slots`).then((r) => {
      if (!r.ok) throw new Error("Failed to load slots");
      return r.json();
    }),
    staleTime: 30_000,
    retry: false,
  });

  const slots = slotsQuery.data?.slots ?? [];
  const currentSlot = slots.find((s) => s.slot === slot);
  // If the slots endpoint is unavailable, assume the default chat.base slot is benchmarkable.
  const benchmarkable = currentSlot?.benchmarkable ?? (slot === DEFAULT_SLOT);

  const { data, isLoading, isError, refetch } = useQuery<{ prompts: Prompt[] }>({
    queryKey: ["mcos", "prompts", slot],
    queryFn: () => apiFetch(`${BASE}/mcos/prompts?slot=${slot}`).then((r) => {
      if (!r.ok) throw new Error("Failed to load prompts");
      return r.json();
    }),
    staleTime: 10_000,
    retry: false,
  });

  const prompts = data?.prompts ?? [];
  const active = prompts.find((p) => p.active) ?? null;
  const candidates = prompts.filter((p) => !p.active);

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["mcos", "prompts", slot] });
    qc.invalidateQueries({ queryKey: ["mcos", "prompt-slots"] });
  };

  const create = useMutation<{ prompt: Prompt }, Error, { name: string; content: string; notes: string }>({
    mutationFn: (body) => apiFetch(`${BASE}/mcos/prompts`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ slot, ...body }),
    }).then((r) => {
      if (!r.ok) throw new Error("Create failed");
      return r.json();
    }),
    onSuccess: () => {
      toast.success("Candidate created");
      invalidate();
    },
    onError: () => toast.error("Could not create candidate"),
  });

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center justify-between gap-2 mb-4 flex-wrap">
          <div className="flex items-center gap-2">
            <FlaskConical className="w-4 h-4 text-primary" />
            <h2 className="font-mono text-sm uppercase tracking-wider">Prompt Lab</h2>
          </div>
          <div className="flex items-center gap-2">
            <Select value={slot} onValueChange={setSlot}>
              <SelectTrigger className="h-8 w-[200px] text-xs">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {slots.length > 0 ? (
                  slots.map((s) => (
                    <SelectItem key={s.slot} value={s.slot} className="text-xs">
                      {s.label}
                    </SelectItem>
                  ))
                ) : (
                  <SelectItem value={DEFAULT_SLOT} className="text-xs">Chat persona</SelectItem>
                )}
              </SelectContent>
            </Select>
            {!isLoading && !isError && (
              <NewCandidateDialog
                slot={slot}
                benchmarkable={benchmarkable}
                activeContent={active?.content ?? ""}
                onCreate={async (body) => { await create.mutateAsync(body); }}
                pending={create.isPending}
              />
            )}
          </div>
        </div>

        {isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-14 w-full" />
            <Skeleton className="h-14 w-full" />
          </div>
        ) : isError ? (
          <div className="rounded-lg border border-dashed border-border/60 p-4 flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">Prompt registry not available yet.</p>
            <Button size="sm" variant="outline" className="h-7 text-[11px] gap-1" onClick={() => refetch()}>
              <RefreshCw className="w-3 h-3" /> Retry
            </Button>
          </div>
        ) : prompts.length === 0 ? (
          <p className="text-sm text-muted-foreground py-6 text-center">No prompts registered for this slot yet.</p>
        ) : (
          <div className="space-y-3">
            {active && (
              <div className="rounded-lg border border-emerald-200/70 bg-emerald-50/30 dark:border-emerald-900/60 dark:bg-emerald-950/20 p-3">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium truncate">{active.name}</span>
                  <Badge variant="outline" className="text-[10px] font-mono">v{active.version}</Badge>
                  <Badge className="text-[10px] bg-emerald-600 hover:bg-emerald-600">Active</Badge>
                </div>
                <div className="flex items-center gap-3 mt-0.5 text-[11px] font-mono text-muted-foreground">
                  <span>{active.created_at ? fmtTime(active.created_at) : "—"}</span>
                  {active.notes && <span className="italic truncate">{active.notes}</span>}
                </div>
              </div>
            )}
            {candidates.map((p) => (
              <CandidatePrompt key={p.id} prompt={p} slot={slot} benchmarkable={benchmarkable} onChanged={invalidate} />
            ))}
            {candidates.length === 0 && (
              <p className="text-xs text-muted-foreground text-center py-2">
                {benchmarkable
                  ? "No candidates. Create one to benchmark against the active prompt."
                  : "No candidates. Create one to version and activate."}
              </p>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── RAG Calibration (Phase 5) ────────────────────────────────────────────────

interface RagConfig {
  target_words: number;
  overlap_words: number;
  defaults: { target_words: number; overlap_words: number };
}

interface SweepResult {
  target_words: number;
  overlap_words: number;
  score: number | null;
  chunk_count: number | null;
}

interface Sweep {
  id: string;
  started_at: string | null;
  finished_at: string | null;
  status: string;
  results: SweepResult[];
  best: { target_words: number; overlap_words: number; score: number | null } | null;
  docs_sampled: number | null;
}

interface ReprocessStatus {
  processing: number;
  total: number;
}

function RagCalibrationCard() {
  const qc = useQueryClient();
  const [showPast, setShowPast] = useState(false);
  // Set true once a re-chunk is kicked off so we poll status until it drains.
  const [reprocessActive, setReprocessActive] = useState(false);
  const notifiedDoneRef = useRef(false);
  // Timestamp (ms) recorded when a re-chunk is kicked off. Completion is only
  // trusted from a status observation fetched strictly AFTER this moment, so a
  // stale cached {processing:0} left over from a previous job can never trigger
  // an instant (false) completion.
  const reprocessStartedAtRef = useRef<number>(0);

  const configQuery = useQuery<RagConfig>({
    queryKey: ["mcos", "rag", "config"],
    queryFn: () => apiFetch(`${BASE}/mcos/rag/config`).then((r) => {
      if (!r.ok) throw new Error("Failed to load RAG config");
      return r.json();
    }),
    staleTime: 10_000,
    retry: false,
  });

  const sweepsQuery = useQuery<{ sweeps: Sweep[] }>({
    queryKey: ["mcos", "rag", "sweeps"],
    queryFn: () => apiFetch(`${BASE}/mcos/rag/sweeps?limit=5`).then((r) => {
      if (!r.ok) throw new Error("Failed to load sweeps");
      return r.json();
    }),
    staleTime: 5_000,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.sweeps?.some((s) => s.status === "running") ? 3_000 : false,
  });

  const sweeps = sweepsQuery.data?.sweeps ?? [];
  const latest = sweeps[0] ?? null;
  const pastSweeps = sweeps.slice(1);
  const anyRunning = sweeps.some((s) => s.status === "running");

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["mcos", "rag", "sweeps"] });
    qc.invalidateQueries({ queryKey: ["mcos", "rag", "config"] });
  };

  const runSweep = useMutation<{ sweep_id: string }, Error, void>({
    mutationFn: () => apiFetch(`${BASE}/mcos/rag/sweep`, { method: "POST" }).then((r) => {
      if (!r.ok) throw new Error("Sweep failed");
      return r.json();
    }),
    onSuccess: () => {
      toast.success("Sweep started");
      sweepsQuery.refetch();
    },
    onError: () => toast.error("Could not start sweep"),
  });

  // Re-chunk progress — polled every 3s while a reprocess is in flight.
  const reprocessQuery = useQuery<ReprocessStatus>({
    queryKey: ["mcos", "rag", "reprocess-status"],
    queryFn: () => apiFetch(`${BASE}/mcos/rag/reprocess-status`).then((r) => {
      if (!r.ok) throw new Error("Failed to load reprocess status");
      return r.json();
    }),
    enabled: reprocessActive,
    retry: false,
    refetchInterval: (query) => (query.state.data && query.state.data.processing > 0 ? 3_000 : false),
  });

  // A status observation is only trustworthy for completion once it was fetched
  // AFTER the current re-chunk started (dataUpdatedAt > start timestamp). This
  // discards the stale cached value from any previous job.
  const reprocessFresh = reprocessQuery.dataUpdatedAt > reprocessStartedAtRef.current;

  // Stop polling + toast when the library finishes re-chunking — but only when
  // the zero-processing reading came from a fetch that happened after we started.
  useEffect(() => {
    if (!reprocessActive) return;
    const st = reprocessQuery.data;
    if (st && st.processing === 0 && reprocessFresh && !notifiedDoneRef.current) {
      notifiedDoneRef.current = true;
      setReprocessActive(false);
      toast.success("Library re-chunk complete");
    }
  }, [reprocessActive, reprocessFresh, reprocessQuery.data, reprocessQuery.dataUpdatedAt]);

  type ApplyBody = { target_words: number; overlap_words: number; reprocess_library?: boolean };
  type ApplyResp = RagConfig & { reprocess_started?: number };

  const applyBest = useMutation<ApplyResp, Error, ApplyBody>({
    mutationFn: (body) => apiFetch(`${BASE}/mcos/rag/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }).then((r) => {
      if (!r.ok) throw new Error("Apply failed");
      return r.json();
    }),
    onSuccess: (resp, vars) => {
      invalidate();
      if (vars.reprocess_library && (resp.reprocess_started ?? 0) > 0) {
        notifiedDoneRef.current = false;
        // Mark the start moment and purge any stale cached status so the poll
        // lifecycle only ever observes data fetched after this point.
        reprocessStartedAtRef.current = Date.now();
        qc.removeQueries({ queryKey: ["mcos", "rag", "reprocess-status"] });
        setReprocessActive(true);
        toast.success(`Chunk config updated — re-chunking ${resp.reprocess_started} document${resp.reprocess_started === 1 ? "" : "s"}`);
        reprocessQuery.refetch();
      } else {
        toast.success("Chunk config updated");
      }
    },
    onError: () => toast.error("Could not apply chunk config"),
  });

  // Only surface status that was fetched after the current re-chunk started.
  const reprocessStatus = reprocessFresh ? reprocessQuery.data : undefined;
  const reprocessing = reprocessActive && (!reprocessFresh || (!!reprocessStatus && reprocessStatus.processing > 0));

  const cfg = configQuery.data;
  const best = latest?.best ?? null;
  const bestDiffers = !!(best && cfg && (best.target_words !== cfg.target_words || best.overlap_words !== cfg.overlap_words));

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center justify-between gap-2 mb-4">
          <div className="flex items-center gap-2">
            <SlidersHorizontal className="w-4 h-4 text-primary" />
            <h2 className="font-mono text-sm uppercase tracking-wider">RAG Calibration</h2>
          </div>
          <Button size="sm" variant="outline" disabled={anyRunning || runSweep.isPending}
            onClick={() => runSweep.mutate()}>
            {anyRunning || runSweep.isPending
              ? <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Sweeping</>
              : <><Play className="w-3.5 h-3.5 mr-1.5" /> Run sweep</>}
          </Button>
        </div>

        {/* Current config */}
        {configQuery.isLoading ? (
          <Skeleton className="h-6 w-64 mb-4" />
        ) : configQuery.isError || !cfg ? (
          <p className="text-xs text-muted-foreground mb-4">Chunk config not available yet.</p>
        ) : (
          <p className="text-sm font-mono mb-4">
            Chunk size: <span className="font-semibold">{cfg.target_words} words</span>
            {" · "}Overlap: <span className="font-semibold">{cfg.overlap_words} words</span>
          </p>
        )}

        {/* Sweeps */}
        {sweepsQuery.isLoading ? (
          <div className="space-y-2">
            <Skeleton className="h-8 w-full" />
            <Skeleton className="h-8 w-full" />
          </div>
        ) : sweepsQuery.isError ? (
          <div className="rounded-lg border border-dashed border-border/60 p-4 flex items-center justify-between gap-3">
            <p className="text-xs text-muted-foreground">Sweep history not available yet.</p>
            <Button size="sm" variant="outline" className="h-7 text-[11px] gap-1" onClick={() => sweepsQuery.refetch()}>
              <RefreshCw className="w-3 h-3" /> Retry
            </Button>
          </div>
        ) : !latest ? (
          <p className="text-sm text-muted-foreground py-6 text-center">No sweeps yet. Run one to find the best chunk configuration.</p>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
              <StatusBadge status={latest.status} />
              <span>{fmtTime(latest.started_at)}</span>
              {latest.docs_sampled != null && <span>· {latest.docs_sampled} docs sampled</span>}
            </div>

            {latest.results.length > 0 && (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="text-right">Target</TableHead>
                    <TableHead className="text-right">Overlap</TableHead>
                    <TableHead className="text-right">Score</TableHead>
                    <TableHead className="text-right">Chunks</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {latest.results.map((res) => {
                    const isBest = !!(best && res.target_words === best.target_words && res.overlap_words === best.overlap_words);
                    return (
                      <TableRow key={`${res.target_words}-${res.overlap_words}`}
                        className={isBest ? "bg-emerald-50/60 dark:bg-emerald-950/20" : undefined}>
                        <TableCell className="text-right font-mono">
                          {res.target_words}
                          {isBest && <Badge className="ml-2 text-[9px] bg-emerald-600 hover:bg-emerald-600">best</Badge>}
                        </TableCell>
                        <TableCell className="text-right font-mono">{res.overlap_words}</TableCell>
                        <TableCell className={`text-right font-mono ${scoreColor(res.score)}`}>{scorePct(res.score)}</TableCell>
                        <TableCell className="text-right font-mono">{res.chunk_count ?? "—"}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}

            {bestDiffers && best && (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2 flex-wrap">
                  <Button size="sm" disabled={applyBest.isPending || reprocessing}
                    onClick={() => applyBest.mutate({ target_words: best.target_words, overlap_words: best.overlap_words })}>
                    {applyBest.isPending ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5 mr-1.5" />}
                    Apply best ({best.target_words}/{best.overlap_words})
                  </Button>
                  <Button size="sm" variant="outline" disabled={applyBest.isPending || reprocessing}
                    onClick={() => applyBest.mutate({ target_words: best.target_words, overlap_words: best.overlap_words, reprocess_library: true })}>
                    {reprocessing ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <RefreshCw className="w-3.5 h-3.5 mr-1.5" />}
                    Apply &amp; re-chunk library
                  </Button>
                </div>
                <p className="text-[11px] text-muted-foreground">Applies to new imports and reprocessed documents.</p>
              </div>
            )}

            {reprocessing && reprocessStatus && (
              <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                <Loader2 className="w-3 h-3 animate-spin" />
                <span>
                  Re-chunking library — {reprocessStatus.processing} of {reprocessStatus.total} remaining
                </span>
              </div>
            )}

            {pastSweeps.length > 0 && (
              <Collapsible open={showPast} onOpenChange={setShowPast}>
                <CollapsibleTrigger asChild>
                  <button className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground">
                    {showPast ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
                    Past sweeps ({pastSweeps.length})
                  </button>
                </CollapsibleTrigger>
                <CollapsibleContent>
                  <div className="mt-2 space-y-1">
                    {pastSweeps.map((s) => (
                      <div key={s.id} className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
                        <span className="flex items-center gap-2">
                          <StatusBadge status={s.status} />
                          {fmtTime(s.started_at)}
                        </span>
                        <span>
                          {s.best ? `best ${s.best.target_words}/${s.best.overlap_words} · ${scorePct(s.best.score)}` : "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                </CollapsibleContent>
              </Collapsible>
            )}
          </div>
        )}
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

      <Separator />

      {/* Prompt Lab (Phase 4) */}
      <PromptLabCard />

      {/* RAG Calibration (Phase 5) */}
      <RagCalibrationCard />
    </div>
  );
}
