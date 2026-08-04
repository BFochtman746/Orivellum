import { useGetSystemHealth, useListCapabilities, getGetSystemHealthQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Activity, Database, Cpu, CheckCircle2, XCircle, AlertCircle, AlertTriangle, Terminal, Sparkles, Moon, Brain, Trash2, ScrollText, User, Settings, Image as ImageIcon, Eye, Loader2, FileSearch, ClipboardCopy, ChevronDown, ChevronRight } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// ─── Relative time helper ───────────────────────────────────────────────────

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const diff = Date.now() - then;
  const sec = Math.round(diff / 1000);
  if (sec < 60) return "just now";
  const min = Math.round(sec / 60);
  if (min < 60) return `${min} minute${min === 1 ? "" : "s"} ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr} hour${hr === 1 ? "" : "s"} ago`;
  const day = Math.round(hr / 24);
  if (day < 30) return `${day} day${day === 1 ? "" : "s"} ago`;
  return new Date(iso).toLocaleDateString();
}

// ─── Diagnostics card ─────────────────────────────────────────────────────────

type DiagCheck = { name: string; status: "ok" | "warn" | "error" | "info"; value: string | number; detail: string };
type DiagSection = { title: string; checks: DiagCheck[] };
type DiagResult = {
  generated_at: string;
  schema_version: string;
  elapsed_ms: number;
  summary: { ok: number; warn: number; error: number; info: number; total: number; health: string };
  sections: DiagSection[];
  all_checks: DiagCheck[];
  markdown_report: string;
  vacuum_ran: boolean;
};

const STATUS_ICON: Record<string, string> = { ok: "✅", warn: "⚠️", error: "❌", info: "ℹ️" };
const STATUS_CLS: Record<string, string> = {
  ok:    "text-emerald-600",
  warn:  "text-amber-600",
  error: "text-destructive",
  info:  "text-muted-foreground",
};

function DiagnosticsCard() {
  const [result, setResult] = useState<DiagResult | null>(null);
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(["🔴 Issues"]));
  const [copied, setCopied] = useState(false);

  const runMutation = useMutation({
    mutationFn: async (vacuum: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/diagnostics?vacuum=${vacuum}`);
      if (!r.ok) throw new Error("Diagnostic failed");
      return r.json() as Promise<DiagResult>;
    },
    onSuccess: (data) => {
      setResult(data);
      // Auto-expand issues section if problems found
      if (data.summary.error > 0 || data.summary.warn > 0) {
        setOpenSections(new Set(["🔴 Issues Requiring Attention"]));
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const handleCopy = async () => {
    if (!result?.markdown_report) return;
    try {
      await navigator.clipboard.writeText(result.markdown_report);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
      toast.success("Report copied to clipboard — paste it to an AI for evaluation");
    } catch {
      toast.error("Copy failed — try selecting the text manually");
    }
  };

  const toggleSection = (title: string) => {
    setOpenSections(prev => {
      const next = new Set(prev);
      if (next.has(title)) next.delete(title); else next.add(title);
      return next;
    });
  };

  const issues = result?.all_checks.filter(c => c.status === "error" || c.status === "warn") ?? [];

  return (
    <Card>
      <CardContent className="p-5 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-3">
            <FileSearch className="w-5 h-5 text-primary/70 shrink-0" />
            <div>
              <div className="font-medium text-sm">System Diagnostic</div>
              <p className="text-xs text-muted-foreground mt-0.5">
                Full health check across database, services, config, and data quality.
                Run this and copy the report to share with an AI for a complete evaluation.
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            {result && (
              <Button size="sm" variant="outline" className="gap-1.5 h-8 text-xs"
                onClick={handleCopy}>
                <ClipboardCopy className="w-3 h-3" />
                {copied ? "Copied!" : "Copy Report"}
              </Button>
            )}
            <Button size="sm" variant="default" className="gap-1.5 h-8 text-xs"
              onClick={() => runMutation.mutate(false)}
              disabled={runMutation.isPending}>
              {runMutation.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <FileSearch className="w-3 h-3" />}
              Run Diagnostic
            </Button>
            <Button size="sm" variant="outline" className="gap-1.5 h-8 text-xs"
              onClick={() => runMutation.mutate(true)}
              disabled={runMutation.isPending}
              title="Run diagnostic AND compact the database with VACUUM">
              {runMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : <Database className="w-3 h-3" />}
              + VACUUM
            </Button>
          </div>
        </div>

        {result && (
          <div className="space-y-3">
            {/* Summary bar */}
            <div className="flex items-center gap-4 p-3 rounded-lg bg-muted/30 border border-border/50 flex-wrap">
              <span className="text-[10px] font-mono text-muted-foreground">
                {result.schema_version} · {result.elapsed_ms}ms · {new Date(result.generated_at).toLocaleTimeString()}
              </span>
              <div className="flex items-center gap-3 ml-auto">
                {(["ok", "warn", "error", "info"] as const).map(s => (
                  <span key={s} className={`text-xs font-mono font-semibold ${STATUS_CLS[s]}`}>
                    {STATUS_ICON[s]} {result.summary[s]}
                  </span>
                ))}
                <span className="text-[10px] font-mono text-muted-foreground">/ {result.summary.total}</span>
              </div>
            </div>

            {/* Issues shortlist */}
            {issues.length > 0 && (
              <div className="space-y-1.5">
                <div className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground flex items-center gap-1.5">
                  <AlertTriangle className="w-3 h-3" />
                  {issues.length} issue{issues.length !== 1 ? "s" : ""} found
                </div>
                {issues.map((c, i) => (
                  <div key={i}
                    className={`flex items-start gap-2 text-xs px-3 py-2 rounded-lg border ${
                      c.status === "error"
                        ? "border-destructive/30 bg-destructive/5 text-destructive"
                        : "border-amber-200 bg-amber-50/50 text-amber-800"
                    }`}>
                    <span className="shrink-0">{STATUS_ICON[c.status]}</span>
                    <div className="min-w-0">
                      <span className="font-medium">{c.name}:</span>{" "}
                      <code className="text-[11px]">{String(c.value)}</code>
                      {c.detail && <span className="opacity-80"> — {c.detail}</span>}
                    </div>
                  </div>
                ))}
              </div>
            )}
            {issues.length === 0 && (
              <div className="flex items-center gap-2 text-xs text-emerald-700 px-3 py-2 rounded-lg border border-emerald-200 bg-emerald-50/50">
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                All {result.summary.total} checks passed — system is healthy
              </div>
            )}

            {/* Collapsible sections */}
            <div className="space-y-1">
              {result.sections.map(sec => {
                const isOpen = openSections.has(sec.title);
                const secIssues = sec.checks.filter(c => c.status !== "ok" && c.status !== "info").length;
                return (
                  <div key={sec.title} className="rounded-lg border border-border/40 overflow-hidden">
                    <button
                      className="w-full flex items-center justify-between px-3 py-2 hover:bg-muted/20 transition-colors text-left"
                      onClick={() => toggleSection(sec.title)}>
                      <span className="text-xs font-mono font-medium flex items-center gap-2">
                        {sec.title}
                        {secIssues > 0 && (
                          <span className="px-1.5 py-0.5 rounded-full text-[10px] bg-amber-100 text-amber-700 leading-none">
                            {secIssues}
                          </span>
                        )}
                      </span>
                      {isOpen
                        ? <ChevronDown className="w-3 h-3 text-muted-foreground" />
                        : <ChevronRight className="w-3 h-3 text-muted-foreground" />}
                    </button>
                    {isOpen && (
                      <div className="border-t border-border/30 divide-y divide-border/20">
                        {sec.checks.map((c, i) => (
                          <div key={i} className="flex items-start gap-3 px-3 py-1.5 text-xs hover:bg-muted/10">
                            <span className="shrink-0 mt-px">{STATUS_ICON[c.status]}</span>
                            <span className={`shrink-0 w-28 font-mono text-[10px] mt-0.5 ${STATUS_CLS[c.status]}`}>
                              {c.status.toUpperCase()}
                            </span>
                            <span className="font-medium min-w-0 flex-1">{c.name}</span>
                            <code className="shrink-0 text-[10px] text-muted-foreground max-w-[140px] truncate" title={String(c.value)}>
                              {String(c.value)}
                            </code>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Maintenance card (Run Maintenance Now + Last Night Report) ─────────────────

type NightshiftStatus = {
  running: boolean;
  started_at: string | null;
  last_run: { ran_at: string; docs_processed: number; items_added: number } | null;
};

type NightshiftReport = {
  ran_at?: string;
  docs_processed?: number;
  items_added?: number;
  report_markdown: string | null;
};

function MaintenanceCard() {
  const qc = useQueryClient();
  const [reportOpen, setReportOpen] = useState(false);

  const { data: status, isLoading } = useQuery<NightshiftStatus>({
    queryKey: ["system", "nightshift-status"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/nightshift/status`);
      if (!r.ok) throw new Error("status fetch failed");
      return r.json();
    },
    staleTime: 5_000,
    // Poll every 3s only while a run is in progress; stop when done.
    refetchInterval: (query) => (query.state.data?.running ? 3_000 : false),
  });

  const running = status?.running ?? false;

  const { data: report, isLoading: loadingReport } = useQuery<NightshiftReport>({
    queryKey: ["system", "nightshift-report"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/nightshift/last-report`);
      if (!r.ok) throw new Error("report fetch failed");
      return r.json();
    },
    staleTime: 30_000,
  });

  const runNow = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/nightshift/run-now`, { method: "POST" });
      if (r.status === 409) throw new Error("already running");
      if (!r.ok) throw new Error("trigger failed");
      return r.json();
    },
    onSuccess: () => {
      toast.success("Maintenance started — running in the background");
      qc.invalidateQueries({ queryKey: ["system", "nightshift-status"] });
    },
    onError: (e) =>
      toast.error(e instanceof Error && e.message === "already running"
        ? "Maintenance is already running"
        : "Could not start maintenance"),
  });

  // When a run finishes, refresh status + report so the UI updates.
  const prevRunning = useRef(running);
  useEffect(() => {
    if (prevRunning.current && !running) {
      qc.invalidateQueries({ queryKey: ["system", "nightshift-report"] });
      qc.invalidateQueries({ queryKey: ["system", "jobs"] });
    }
    prevRunning.current = running;
  }, [running, qc]);

  const lastRun = status?.last_run;
  const busy = running || runNow.isPending;

  // Pull extra summary facts out of the last report's markdown so the status
  // line covers recovery and space savings, not just harvest counts.
  const md = report?.report_markdown ?? "";
  const mbSaved = md.match(/VACUUM saved ([\d.]+) MB/)?.[1] ?? null;
  const docsRecovered = md.match(/re-queued (\d+) stuck document/)?.[1] ?? null;

  return (
    <Card>
      <CardContent className="p-6 space-y-5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Moon className="w-5 h-5 text-primary" />
            <h2 className="text-lg font-serif font-medium">Maintenance</h2>
          </div>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5 text-xs"
            onClick={() => runNow.mutate()}
            disabled={busy}
          >
            {busy ? (
              <><Activity className="w-3 h-3 animate-spin" />Running…</>
            ) : (
              <><Moon className="w-3 h-3" />Run Maintenance Now</>
            )}
          </Button>
        </div>

        {isLoading ? (
          <Skeleton className="h-8 w-full" />
        ) : lastRun ? (
          <div className="text-sm text-muted-foreground">
            Last run: <span className="text-foreground">{relativeTime(lastRun.ran_at)}</span>
            {" · "}
            <Badge variant="secondary" className="mx-0.5">{lastRun.docs_processed}</Badge> docs processed
            {" · "}
            <Badge variant="secondary" className="mx-0.5">{lastRun.items_added}</Badge> items added
            {docsRecovered && (
              <>
                {" · "}
                <Badge variant="secondary" className="mx-0.5">{docsRecovered}</Badge> docs recovered
              </>
            )}
            {mbSaved && (
              <>
                {" · "}
                <Badge variant="secondary" className="mx-0.5">{mbSaved} MB</Badge> saved
              </>
            )}
          </div>
        ) : (
          <p className="text-sm text-muted-foreground">
            No runs yet — nightshift fires at 3:00 AM.
          </p>
        )}

        {/* ── Last Night Report ── */}
        <div className="border-t border-border/40 pt-4">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-2">
              <ScrollText className="w-4 h-4 text-muted-foreground" />
              <h3 className="text-sm font-medium">Last Night Report</h3>
              {report?.ran_at && (
                <span className="text-xs text-muted-foreground font-mono">
                  {new Date(report.ran_at).toLocaleString()}
                </span>
              )}
            </div>
            {report?.report_markdown && (
              <Button
                size="sm"
                variant="ghost"
                className="text-xs"
                onClick={() => setReportOpen((o) => !o)}
              >
                {reportOpen ? "Hide" : "View"}
              </Button>
            )}
          </div>

          {loadingReport ? (
            <Skeleton className="h-8 w-full mt-3" />
          ) : !report?.report_markdown ? (
            <p className="text-sm text-muted-foreground mt-2">
              No runs yet — nightshift fires at 3:00 AM.
            </p>
          ) : reportOpen ? (
            <pre className="mt-3 text-xs font-mono whitespace-pre-wrap bg-muted/40 rounded-lg p-4 max-h-96 overflow-y-auto border border-border/40">
              {report.report_markdown}
            </pre>
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

// ─── User memory card ─────────────────────────────────────────────────────────

function UserMemoryCard() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["system", "user-memory"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/user-memory`);
      if (!r.ok) throw new Error("memory fetch failed");
      return r.json() as Promise<{ memories: { id: string; key: string; value: string; created_at: string }[] }>;
    },
    staleTime: 30_000,
  });

  const del = useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`${API_BASE}/api/system/user-memory/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("delete failed");
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["system", "user-memory"] }); toast.success("Memory deleted"); },
    onError: () => toast.error("Could not delete"),
  });

  const memories = data?.memories ?? [];
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-3 mb-4">
          <Brain className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-serif font-medium">My Memory</h2>
          <span className="text-xs text-muted-foreground">— facts Orivellum remembers about you</span>
        </div>
        {isLoading ? (
          <Skeleton className="h-12 w-full" />
        ) : memories.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            No memories yet. Say things like "Remember that I prefer concise answers" and Orivellum will retain them across conversations.
          </p>
        ) : (
          <div className="space-y-2">
            {memories.map(m => (
              <div key={m.id} className="flex items-start gap-3 p-3 rounded-lg bg-muted/20 border border-border/40 group">
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-mono text-muted-foreground">{m.key}</p>
                  <p className="text-sm mt-0.5">{m.value}</p>
                </div>
                <button
                  onClick={() => del.mutate(m.id)}
                  disabled={del.isPending}
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 p-1 text-destructive transition-opacity shrink-0"
                  title="Delete memory"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Version card ─────────────────────────────────────────────────────────────

function VersionCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["system", "version"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/version`);
      if (!r.ok) throw new Error("version fetch failed");
      return r.json() as Promise<{ version: string; product: string; python: string; platform: string }>;
    },
    staleTime: Infinity,
  });
  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-center gap-3 mb-3">
          <Terminal className="w-5 h-5 text-primary" />
          <h2 className="text-lg font-serif font-medium">About</h2>
        </div>
        {isLoading ? <Skeleton className="h-8 w-40" /> : (
          <div className="space-y-1 font-mono text-xs text-muted-foreground">
            <p><span className="text-foreground font-semibold">{data?.product}</span> v{data?.version}</p>
            <p>Python {data?.python?.split(" ")[0]}</p>
            <p className="truncate">{data?.platform}</p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── AI Extraction toggle ─────────────────────────────────────────────────────

function useAiExtractionSetting() {
  return useQuery({
    queryKey: ["system", "ai-extraction"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-extraction`);
      if (!r.ok) throw new Error("Failed to fetch AI extraction setting");
      return r.json() as Promise<{ enabled: boolean }>;
    },
    staleTime: 30_000,
  });
}

function useSetAiExtractionSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-extraction`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update AI extraction setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "ai-extraction"] });
    },
  });
}

// ─── Semantic search / embeddings card ───────────────────────────────────────

type EmbedProbeResult = { ok: boolean; dims?: number; status?: string; detail: string };

function SemanticSearchCard() {
  const [probeResult, setProbeResult] = useState<EmbedProbeResult | null>(null);
  const [probing, setProbing] = useState(false);

  const { data, refetch } = useQuery({
    queryKey: ["system", "embeddings-status"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/embeddings/status`);
      if (!r.ok) return null;
      return r.json() as Promise<{ circuit_open: boolean; available_at: number | null }>;
    },
    refetchInterval: 30_000,
    staleTime: 20_000,
  });

  async function probe() {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/embeddings/probe`, { method: "POST" });
      const json = await r.json() as EmbedProbeResult;
      setProbeResult(json);
      if (json.ok) refetch();
    } catch {
      setProbeResult({ ok: false, detail: "Probe request failed — check server logs." });
    } finally {
      setProbing(false);
    }
  }

  const circuitOpen = data?.circuit_open ?? false;

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Brain className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <h3 className="font-medium text-sm">Semantic Search (Embeddings)</h3>
                <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
                  When the embedding endpoint is reachable, searches use vector similarity in
                  addition to keyword matching. When unavailable, results are keyword-only (BM25).
                </p>
              </div>
              <Button
                size="sm" variant="outline" className="text-xs gap-1.5 shrink-0"
                onClick={probe} disabled={probing}
              >
                {probing
                  ? <><Loader2 className="w-3 h-3 animate-spin" />Testing…</>
                  : <><Brain className="w-3 h-3" />Test Embeddings</>}
              </Button>
            </div>

            {/* Status indicator */}
            {!probeResult && (
              circuitOpen ? (
                <div className="flex items-start gap-2 rounded-lg px-3 py-2 bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span className="text-xs">
                    Embedding endpoint is in cooldown after a recent failure.
                    Searches are keyword-only until the endpoint recovers.
                    Click <span className="font-medium">Test Embeddings</span> to retry now.
                  </span>
                </div>
              ) : (
                <p className="text-xs text-emerald-700 dark:text-emerald-400 font-mono flex items-center gap-1.5">
                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                  Circuit breaker closed — semantic search active
                </p>
              )
            )}

            {/* Probe result */}
            {probeResult && (
              <div className={`flex items-start gap-2 text-xs rounded-lg px-3 py-2 ${
                probeResult.ok
                  ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-400"
                  : "bg-destructive/10 border border-destructive/30 text-destructive"
              }`}>
                {probeResult.ok
                  ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  : <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                <span>{probeResult.detail}</span>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Database statistics card ─────────────────────────────────────────────────

function DatabaseStatsCard() {
  const { data, isLoading } = useQuery({
    queryKey: ["system", "stats"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/stats`);
      if (!r.ok) return null;
      return r.json() as Promise<{
        document_count: number; knowledge_count: number;
        work_count: number; db_size_bytes: number;
      }>;
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const fmt = (n: number) => n.toLocaleString();
  const fmtBytes = (b: number) => {
    if (b >= 1_073_741_824) return `${(b / 1_073_741_824).toFixed(1)} GB`;
    if (b >= 1_048_576)     return `${(b / 1_048_576).toFixed(1)} MB`;
    if (b >= 1_024)         return `${(b / 1_024).toFixed(0)} KB`;
    return `${b} B`;
  };

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Database className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div className="flex-1 space-y-2">
            <h3 className="font-medium text-sm">Database</h3>
            {isLoading ? (
              <p className="text-sm text-muted-foreground">Loading…</p>
            ) : !data ? (
              <p className="text-sm text-muted-foreground">Could not load stats.</p>
            ) : (
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 pt-1">
                {[
                  { label: "Works",     value: fmt(data.work_count) },
                  { label: "Documents", value: fmt(data.document_count) },
                  { label: "Knowledge", value: fmt(data.knowledge_count) },
                  { label: "DB Size",   value: fmtBytes(data.db_size_bytes) },
                ].map(({ label, value }) => (
                  <div key={label} className="flex flex-col gap-0.5">
                    <span className="text-xs text-muted-foreground">{label}</span>
                    <span className="text-sm font-semibold tabular-nums">{value}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Vision model card ────────────────────────────────────────────────────────

type VisionProbeResult = { ok: boolean; model: string; response?: string; error?: string };

function VisionModelCard() {
  const qc = useQueryClient();
  const { data, isLoading } = useQuery({
    queryKey: ["system", "vision-model"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/vision-model`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ model: string; stored: string; config_default: string }>;
    },
    staleTime: 60_000,
  });

  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [probeResult, setProbeResult] = useState<VisionProbeResult | null>(null);
  const [probing, setProbing] = useState(false);

  function startEdit() { setVal(data?.model ?? ""); setEditing(true); }

  async function save() {
    try {
      const r = await apiFetch(`${API_BASE}/api/system/settings/vision-model`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ model: val.trim() }),
      });
      if (!r.ok) throw new Error();
      qc.invalidateQueries({ queryKey: ["system", "vision-model"] });
      setProbeResult(null); // reset probe after model change
      toast.success(val.trim() ? "Vision model saved" : "Reverted to config default");
      setEditing(false);
    } catch {
      toast.error("Could not save vision model");
    }
  }

  async function probe() {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/vision/probe`, { method: "POST" });
      const json = await r.json() as VisionProbeResult;
      setProbeResult(json);
      if (json.ok) {
        toast.success(`Vision works — model replied: "${json.response?.slice(0, 60)}"`);
      } else {
        toast.warning("Vision test failed — model may not support images");
      }
    } catch {
      toast.error("Probe request failed");
    } finally {
      setProbing(false);
    }
  }

  const effectiveModel = data?.model;

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Eye className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <h3 className="font-medium text-sm">Vision Model (Image Understanding)</h3>
                <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
                  Used when you attach an image in chat and when importing image files into your library.
                  Set to a vision-capable model (e.g. <code className="bg-muted px-1 rounded">llava</code>,{" "}
                  <code className="bg-muted px-1 rounded">qwen2-vl</code>,{" "}
                  <code className="bg-muted px-1 rounded">llama3.2-vision</code>). Leave blank to use
                  your default workhorse model (only works if it supports vision).
                </p>
              </div>
              <div className="flex gap-2 shrink-0">
                <Button
                  size="sm"
                  variant="outline"
                  className="text-xs gap-1.5"
                  onClick={probe}
                  disabled={probing}
                >
                  {probing
                    ? <><Loader2 className="w-3 h-3 animate-spin" />Testing…</>
                    : <><Eye className="w-3 h-3" />Test Vision</>}
                </Button>
                {!editing && (
                  <Button size="sm" variant="outline" className="text-xs" onClick={startEdit}>
                    {effectiveModel ? "Edit" : "Set Model"}
                  </Button>
                )}
              </div>
            </div>

            {/* Current model display */}
            {!editing && (
              isLoading ? (
                <Skeleton className="h-6 w-64" />
              ) : effectiveModel ? (
                <p className="text-xs font-mono bg-muted/40 rounded px-2 py-1 truncate">{effectiveModel}</p>
              ) : (
                <div className="flex items-start gap-2 rounded-lg px-3 py-2 bg-amber-500/10 border border-amber-500/30 text-amber-700 dark:text-amber-400">
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <div className="text-xs leading-snug">
                    <span className="font-medium">No vision model configured.</span>{" "}
                    Image attachments in chat and image file imports will use your workhorse model,
                    which may not support images and will silently return no description.
                    Click <span className="font-medium">Set Model</span> to configure a multimodal model
                    (e.g. <code className="font-mono">llava</code>, <code className="font-mono">qwen2-vl</code>).
                  </div>
                </div>
              )
            )}

            {/* Edit field */}
            {editing && (
              <div className="flex gap-2">
                <input
                  autoFocus
                  value={val}
                  onChange={e => setVal(e.target.value)}
                  placeholder="e.g. llava, qwen2-vl, llama3.2-vision or leave blank"
                  className="flex-1 text-sm font-mono border border-border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                  onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
                />
                <Button size="sm" onClick={save}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            )}

            {/* Probe result */}
            {probeResult && (
              <div className={`flex items-start gap-2 text-xs rounded-lg px-3 py-2 mt-1 ${
                probeResult.ok
                  ? "bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-400"
                  : "bg-destructive/10 border border-destructive/30 text-destructive"
              }`}>
                {probeResult.ok
                  ? <CheckCircle2 className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  : <XCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                <div>
                  <span className="font-medium">{probeResult.ok ? "Vision supported" : "Vision not supported"}</span>
                  <span className="ml-1 opacity-75">({probeResult.model})</span>
                  {probeResult.ok && probeResult.response && (
                    <p className="mt-0.5 opacity-80">Reply: "{probeResult.response.slice(0, 120)}"</p>
                  )}
                  {!probeResult.ok && probeResult.error && (
                    <p className="mt-0.5 opacity-80">{probeResult.error.slice(0, 200)}</p>
                  )}
                </div>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}


function ImageGenUrlCard() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["system", "image-gen-url"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/image-gen`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ url: string }>;
    },
    staleTime: 60_000,
  });
  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");

  function startEdit() { setVal(data?.url ?? ""); setEditing(true); }

  async function save() {
    try {
      const r = await apiFetch(`${API_BASE}/api/system/settings/image-gen`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: val.trim() }),
      });
      if (!r.ok) throw new Error();
      qc.invalidateQueries({ queryKey: ["system", "image-gen-url"] });
      qc.invalidateQueries({ queryKey: ["studio", "image-status"] });
      toast.success(val.trim() ? "Image generation URL saved" : "Reverted to auto-detect");
      setEditing(false);
    } catch {
      toast.error("Could not save image generation URL");
    }
  }

  return (
    <Card>
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <ImageIcon className="w-5 h-5 text-primary mt-0.5 shrink-0" />
          <div className="flex-1 space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <h3 className="font-medium text-sm">Image Generation Backend</h3>
                <p className="text-sm text-muted-foreground mt-0.5">
                  Orivellum auto-detects Automatic1111 (port 7860) and ComfyUI (port 8188).
                  Set a custom URL here to override — e.g. a remote SD server or any
                  OpenAI-compatible <code className="bg-muted px-1 rounded">/images/generations</code> endpoint.
                  Leave blank to use auto-detection.
                </p>
              </div>
              {!editing && (
                <Button size="sm" variant="outline" className="ml-4 shrink-0 text-xs" onClick={startEdit}>
                  {data?.url ? "Edit" : "Set URL"}
                </Button>
              )}
            </div>
            {!editing && data?.url && (
              <p className="text-xs font-mono bg-muted/40 rounded px-2 py-1 truncate">{data.url}</p>
            )}
            {!editing && !data?.url && (
              <p className="text-xs text-muted-foreground/60 font-mono">Auto-detect (Automatic1111 · ComfyUI)</p>
            )}
            {editing && (
              <div className="flex gap-2">
                <input
                  autoFocus
                  value={val}
                  onChange={e => setVal(e.target.value)}
                  placeholder="http://localhost:7860 or leave blank for auto-detect"
                  className="flex-1 text-sm font-mono border border-border rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-primary"
                  onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
                />
                <Button size="sm" onClick={save}>Save</Button>
                <Button size="sm" variant="ghost" onClick={() => setEditing(false)}>Cancel</Button>
              </div>
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function System() {
  const { data: health, isLoading: loadingHealth } = useGetSystemHealth({ query: { queryKey: getGetSystemHealthQueryKey(), refetchInterval: 10_000, staleTime: 8_000 } });
  const { data: capsResp, isLoading: loadingCaps } = useListCapabilities();
  const { data: aiExtraction, isLoading: loadingAiExt } = useAiExtractionSetting();
  const setAiExtraction = useSetAiExtractionSetting();

  const aiStatus = (health?.services?.ai as Record<string, string> | undefined)?.status;
  const aiEndpoint = (health?.services?.ai as Record<string, string> | undefined)?.endpoint;
  const dbStatus = (health?.services?.database as Record<string, string> | undefined)?.status;
  const aiOnline = aiStatus === "ok";

  return (
    <div className="space-y-8 animate-in fade-in duration-500 max-w-5xl mx-auto">
      <div className="border-b border-border/50 pb-4">
        <h1 className="text-3xl font-serif font-semibold tracking-tight">System Status</h1>
        <p className="text-muted-foreground mt-1 font-serif">Infrastructure health and local AI capabilities.</p>
      </div>

      <div className="grid md:grid-cols-3 gap-4">
        {/* Overall */}
        <Card className="bg-primary/5 border-primary/20">
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4">
              <h3 className="font-mono text-sm uppercase tracking-wider">Overall Status</h3>
              <Activity className="w-5 h-5 text-primary" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                {health?.status === "ok" ? (
                  <CheckCircle2 className="w-6 h-6 text-emerald-500" />
                ) : (
                  <AlertCircle className="w-6 h-6 text-amber-500" />
                )}
                <span className="text-2xl font-serif font-semibold capitalize">
                  {health?.status || "Unknown"}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* Database */}
        <Card>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Database</h3>
              <Database className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="flex items-center gap-2">
                {dbStatus === "ok" ? (
                  <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                ) : (
                  <XCircle className="w-5 h-5 text-destructive" />
                )}
                <span className="text-xl font-medium">
                  {dbStatus === "ok" ? "Connected" : "Offline"}
                </span>
              </div>
            )}
          </CardContent>
        </Card>

        {/* AI Engine */}
        <Card className={aiOnline ? "" : "border-amber-500/30 bg-amber-500/5"}>
          <CardContent className="p-6">
            <div className="flex items-center justify-between mb-4 text-muted-foreground">
              <h3 className="font-mono text-sm uppercase tracking-wider">Local AI Engine</h3>
              <Cpu className="w-5 h-5" />
            </div>
            {loadingHealth ? (
              <Skeleton className="h-8 w-24" />
            ) : (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  {aiOnline ? (
                    <CheckCircle2 className="w-5 h-5 text-emerald-500" />
                  ) : (
                    <XCircle className="w-5 h-5 text-amber-500" />
                  )}
                  <span className="text-xl font-medium">
                    {aiOnline ? "Connected" : "Unavailable"}
                  </span>
                </div>
                {aiEndpoint && (
                  <p className="text-[11px] font-mono text-muted-foreground truncate" title={aiEndpoint}>
                    {aiEndpoint}
                  </p>
                )}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      {/* Database statistics */}
      <DatabaseStatsCard />

      {/* Semantic / Embedding Search */}
      <SemanticSearchCard />

      {/* Vision Model Setting */}
      <VisionModelCard />

      {/* Image Generation URL Setting */}
      <ImageGenUrlCard />

      {/* AI Extraction Setting */}
      <Card>
        <CardContent className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Sparkles className="w-5 h-5 text-primary mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h3 className="font-medium text-sm">AI-Powered Knowledge Extraction</h3>
                <p className="text-sm text-muted-foreground max-w-xl">
                  When enabled, newly imported documents are analysed by your local AI to extract
                  named entities, key claims, and relationships — richer than what rule-based
                  harvesting alone can surface. Documents are marked ready first; extraction runs
                  afterwards and does not delay access to your files.
                </p>
                {!aiOnline && (
                  <p className="text-xs text-amber-600 dark:text-amber-400 mt-1">
                    Requires the local AI engine to be running. Enable it now and it will activate
                    automatically once the AI service is available.
                  </p>
                )}
              </div>
            </div>
            <div className="shrink-0 pt-0.5">
              {loadingAiExt ? (
                <Skeleton className="h-6 w-11 rounded-full" />
              ) : (
                <Switch
                  checked={aiExtraction?.enabled ?? false}
                  onCheckedChange={(checked) => setAiExtraction.mutate(checked)}
                  disabled={setAiExtraction.isPending}
                  aria-label="Enable AI-powered knowledge extraction"
                />
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI offline setup guide */}
      {!loadingHealth && !aiOnline && (
        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4 text-amber-600" />
              <h3 className="font-mono text-sm font-semibold text-amber-700 dark:text-amber-400 uppercase tracking-wider">
                Local AI Setup
              </h3>
            </div>
            <p className="text-sm text-muted-foreground">
              Orivellum connects to a local AI server via the OpenAI-compatible API. No data leaves your machine.
              Choose one of the options below:
            </p>

            <div className="space-y-3">
              {/* Lemonade */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option A — Lemonade (recommended)</p>
                <p className="text-xs text-muted-foreground">
                  Lemonade is a local model server tuned for Orivellum. It listens on port 13305 by default.
                </p>
                <div className="space-y-1.5">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">macOS / Linux</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`pip install lemonade-server
lemonade-server --port 13305`}
                  </pre>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Windows (PowerShell)</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`pip install lemonade-server
lemonade-server --port 13305`}
                  </pre>
                </div>
              </div>

              {/* Ollama */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option B — Ollama</p>
                <p className="text-xs text-muted-foreground">
                  Ollama listens on port 11434. Pull a model then point Orivellum at it.
                </p>
                <div className="space-y-1.5">
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">macOS / Linux</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`ollama serve
ollama pull llama3.2
export ORIVELLUM_AI_URL=http://127.0.0.1:11434/v1`}
                  </pre>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Windows (PowerShell)</p>
                  <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`ollama serve
ollama pull llama3.2
$env:ORIVELLUM_AI_URL="http://127.0.0.1:11434/v1"`}
                  </pre>
                </div>
              </div>

              {/* Custom */}
              <div className="rounded-lg bg-background/60 border border-border/60 p-4 space-y-2">
                <p className="text-sm font-semibold">Option C — Any OpenAI-compatible server</p>
                <p className="text-xs text-muted-foreground">
                  Set <code className="bg-muted px-1 rounded">ORIVELLUM_AI_URL</code> to the base URL of your server
                  (must expose <code className="bg-muted px-1 rounded">/chat/completions</code>).
                  Optionally set the model name in <code className="bg-muted px-1 rounded">config.yaml</code>.
                </p>
                <pre className="text-xs font-mono bg-muted/60 rounded px-3 py-2 overflow-x-auto">
{`export ORIVELLUM_AI_URL=http://127.0.0.1:PORT/v1`}
                </pre>
              </div>
            </div>

            <p className="text-xs text-muted-foreground">
              After starting the server, reload this page — the AI status will update automatically.
              Your messages are always saved even when AI is offline.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Maintenance */}
      <MaintenanceCard />

      {/* System Diagnostics */}
      <DiagnosticsCard />

      {/* User Memory */}
      <UserMemoryCard />

      {/* Version info */}
      <VersionCard />

      {/* Capabilities */}
      <div className="space-y-4">
        <h2 className="text-xl font-serif font-medium border-b border-border/50 pb-2">Active Capabilities</h2>
        <div className="grid md:grid-cols-2 gap-4">
          {loadingCaps ? (
            [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)
          ) : (
            capsResp?.capabilities?.map((cap, i) => (
              <div
                key={i}
                className="flex items-center justify-between p-4 rounded-lg bg-muted/20 border border-border/50"
              >
                <div className="font-medium font-mono text-sm">{cap.name}</div>
                <Badge
                  variant={cap.status === "active" ? "default" : "secondary"}
                  className="font-mono text-[10px] uppercase"
                >
                  {cap.status}
                </Badge>
              </div>
            ))
          )}
        </div>
      </div>

      {/* Audit log */}
      <AuditLogCard />
    </div>
  );
}

// ─── Audit log card ───────────────────────────────────────────────────────────

const ACTOR_ICONS: Record<string, React.ElementType> = {
  pipeline: Cpu,
  system: Settings,
  user: User,
};

function AuditLogCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<{
    entries: Array<{
      id: string; timestamp: string; actor: string; operation: string;
      object_id: string | null; object_type: string | null;
      result: string; detail: string | null;
    }>;
    count: number;
  }>({
    queryKey: ["system", "audit-log"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/audit-log?limit=50`);
      if (!r.ok) throw new Error("audit log fetch failed");
      return r.json();
    },
    staleTime: 60_000,
  });

  const entries = data?.entries ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <ScrollText className="w-5 h-5 text-muted-foreground" />
          Audit Log
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
        >
          {isFetching ? "refreshing…" : `${data?.count ?? 0} entries · refresh`}
        </button>
      </div>

      {isLoading ? (
        [1,2,3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
      ) : entries.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground text-sm border border-dashed rounded-lg">
          No audit events recorded yet — actions will appear here as you use the system.
        </div>
      ) : (
        <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30 max-h-80 overflow-y-auto">
          {entries.map((e) => {
            const ActorIcon = ACTOR_ICONS[e.actor] ?? Activity;
            const isOk = e.result === "ok";
            return (
              <div key={e.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors">
                <ActorIcon className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-mono font-medium">{e.operation}</span>
                  {e.detail && (
                    <span className="text-[11px] font-mono text-muted-foreground ml-2">{e.detail}</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-[10px] font-mono ${isOk ? "text-emerald-600" : "text-red-600"}`}>
                    {e.result}
                  </span>
                  <span className="text-[10px] font-mono text-muted-foreground/50">
                    {e.timestamp ? new Date(e.timestamp).toLocaleTimeString() : ""}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
