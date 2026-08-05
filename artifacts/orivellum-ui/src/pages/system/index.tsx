import { useGetSystemHealth, useListCapabilities, getGetSystemHealthQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Activity, Database, Cpu, CheckCircle2, XCircle, AlertCircle, AlertTriangle, Terminal, Sparkles, Moon, Brain, Trash2, ScrollText, User, Settings, Image as ImageIcon, Eye, Loader2, FileSearch, ClipboardCopy, ChevronDown, ChevronRight, Zap, Download, RotateCcw, FolderOpen, FolderPlus, Plus, X } from "lucide-react";
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

// ─── Watched folders card ─────────────────────────────────────────────────────

type WatchDir = {
  path: string;
  work_id: string | null;
  enabled: boolean;
  last_scan_files_imported?: number;
  last_scan_error?: string | null;
};

type WatchDirsResponse = {
  dirs: WatchDir[];
  scanned_at: string | null;
};

function WatchedFoldersCard() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [newPath, setNewPath] = useState("");
  const [newWorkId, setNewWorkId] = useState<string>("");

  const { data, isLoading } = useQuery<WatchDirsResponse>({
    queryKey: ["system", "watch-dirs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/watch-dirs`);
      if (!r.ok) throw new Error("watch dirs fetch failed");
      return r.json();
    },
    staleTime: 15_000,
    refetchInterval: 30_000,
  });

  // Works list for the optional work picker
  const { data: worksData } = useQuery<{ works: { id: string; title: string }[] }>({
    queryKey: ["works", "list-mini"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/works?limit=100`);
      if (!r.ok) return { works: [] };
      return r.json();
    },
    staleTime: 60_000,
  });
  const works = worksData?.works ?? [];

  const invalidate = () => qc.invalidateQueries({ queryKey: ["system", "watch-dirs"] });

  const addMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/watch-dirs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: newPath.trim(), work_id: newWorkId || null, enabled: true }),
      });
      if (r.status === 409) throw new Error("already_watched");
      if (!r.ok) throw new Error("add failed");
      return r.json();
    },
    onSuccess: () => {
      invalidate();
      setAdding(false);
      setNewPath("");
      setNewWorkId("");
      toast.success("Folder added — files will be imported within 60 seconds");
    },
    onError: (e: Error) =>
      toast.error(e.message === "already_watched"
        ? "That folder is already being watched"
        : "Could not add folder"),
  });

  const toggleMutation = useMutation({
    mutationFn: async ({ index, dir }: { index: number; dir: WatchDir }) => {
      const r = await apiFetch(`${API_BASE}/api/system/watch-dirs/${index}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: dir.path, work_id: dir.work_id, enabled: !dir.enabled }),
      });
      if (!r.ok) throw new Error("update failed");
      return r.json();
    },
    onSuccess: () => { invalidate(); },
    onError: () => toast.error("Could not update folder"),
  });

  const removeMutation = useMutation({
    mutationFn: async (index: number) => {
      const r = await apiFetch(`${API_BASE}/api/system/watch-dirs/${index}`, { method: "DELETE" });
      if (!r.ok) throw new Error("delete failed");
    },
    onSuccess: () => { invalidate(); toast.success("Folder removed"); },
    onError: () => toast.error("Could not remove folder"),
  });

  const dirs = data?.dirs ?? [];

  return (
    <Card>
      <CardContent className="p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <FolderOpen className="w-5 h-5 text-primary mt-0.5 shrink-0" />
            <div>
              <h3 className="font-medium text-sm">Watched Folders</h3>
              <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
                Drop files into a watched folder and they appear in your library automatically —
                scanned every 60 seconds. Already-imported files are never re-imported.
              </p>
              {data?.scanned_at && (
                <p className="text-[11px] font-mono text-muted-foreground mt-1">
                  Last scan: {relativeTime(data.scanned_at)}
                </p>
              )}
            </div>
          </div>
          <Button
            size="sm" variant="outline" className="gap-1.5 text-xs shrink-0"
            onClick={() => setAdding(true)}
            disabled={adding}
          >
            <Plus className="w-3.5 h-3.5" />
            Add Folder
          </Button>
        </div>

        {/* Add-folder form */}
        {adding && (
          <div className="rounded-lg border border-border/60 bg-muted/20 p-4 space-y-3">
            <p className="text-xs font-medium">Add a watched folder</p>
            <div className="space-y-2">
              <input
                type="text"
                placeholder="/absolute/path/to/folder"
                value={newPath}
                onChange={e => setNewPath(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter" && newPath.trim()) addMutation.mutate(); }}
                className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm font-mono placeholder:text-muted-foreground/50 focus:outline-none focus:ring-1 focus:ring-primary/50"
              />
              {works.length > 0 && (
                <select
                  value={newWorkId}
                  onChange={e => setNewWorkId(e.target.value)}
                  className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                >
                  <option value="">No work assignment (goes to library root)</option>
                  {works.map(w => (
                    <option key={w.id} value={w.id}>{w.title}</option>
                  ))}
                </select>
              )}
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm" variant="default" className="text-xs gap-1.5"
                onClick={() => addMutation.mutate()}
                disabled={!newPath.trim() || addMutation.isPending}
              >
                {addMutation.isPending
                  ? <><Loader2 className="w-3 h-3 animate-spin" />Adding…</>
                  : <><FolderPlus className="w-3 h-3" />Watch this folder</>}
              </Button>
              <Button size="sm" variant="ghost" className="text-xs"
                onClick={() => { setAdding(false); setNewPath(""); setNewWorkId(""); }}>
                Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Directory list */}
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : dirs.length === 0 ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground px-3 py-4 rounded-lg border border-dashed">
            <FolderOpen className="w-4 h-4 shrink-0" />
            No folders watched yet — click <span className="font-medium mx-1">Add Folder</span> to get started.
          </div>
        ) : (
          <div className="space-y-2">
            {dirs.map((dir, i) => (
              <div
                key={i}
                className={`flex items-start gap-3 p-3 rounded-lg border transition-colors ${
                  dir.enabled
                    ? "border-border/50 bg-background"
                    : "border-border/30 bg-muted/20 opacity-60"
                }`}
              >
                <FolderOpen className={`w-4 h-4 mt-0.5 shrink-0 ${dir.enabled ? "text-primary" : "text-muted-foreground"}`} />
                <div className="flex-1 min-w-0 space-y-0.5">
                  <p className="text-sm font-mono font-medium truncate" title={dir.path}>{dir.path}</p>
                  <div className="flex items-center gap-3 flex-wrap">
                    {dir.work_id && works.find(w => w.id === dir.work_id) && (
                      <span className="text-[11px] text-muted-foreground">
                        → {works.find(w => w.id === dir.work_id)?.title}
                      </span>
                    )}
                    {dir.last_scan_error ? (
                      <span className="flex items-center gap-1 text-[11px] text-red-600">
                        <XCircle className="w-3 h-3" />
                        {dir.last_scan_error}
                      </span>
                    ) : dir.last_scan_files_imported !== undefined && dir.last_scan_files_imported > 0 ? (
                      <span className="flex items-center gap-1 text-[11px] text-emerald-700">
                        <CheckCircle2 className="w-3 h-3" />
                        {dir.last_scan_files_imported} imported last scan
                      </span>
                    ) : data?.scanned_at ? (
                      <span className="text-[11px] text-muted-foreground font-mono">
                        {dir.enabled ? "no new files" : "paused"}
                      </span>
                    ) : null}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <Switch
                    checked={dir.enabled}
                    onCheckedChange={() => toggleMutation.mutate({ index: i, dir })}
                    disabled={toggleMutation.isPending}
                    aria-label={dir.enabled ? "Pause watching" : "Resume watching"}
                  />
                  <button
                    onClick={() => removeMutation.mutate(i)}
                    disabled={removeMutation.isPending}
                    className="p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/5 transition-colors"
                    title="Remove this folder"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Extraction Templates card ────────────────────────────────────────────────

const DOC_KINDS = [
  { value: "pdf",      label: "PDF" },
  { value: "docx",     label: "Word / DOCX" },
  { value: "excel",    label: "Excel / XLSX" },
  { value: "csv",      label: "CSV" },
  { value: "markdown", label: "Markdown" },
  { value: "text",     label: "Plain text" },
  { value: "image",    label: "Image" },
  { value: "audio",    label: "Audio" },
  { value: "pptx",     label: "Presentation / PPTX" },
  { value: "html",     label: "HTML" },
  { value: "json",     label: "JSON" },
  { value: "code",     label: "Code" },
];

type ExtractionTemplate = {
  id: string;
  name: string;
  kind_label: string | null;
  system_prompt: string;
  field_hints: string[];
  work_id: string | null;
  created_at: string;
  updated_at: string;
};

function ExtractionTemplatesCard() {
  const qc = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [editing, setEditing] = useState<ExtractionTemplate | null>(null);
  const [form, setForm] = useState({
    name: "", kind_label: "", system_prompt: "", field_hints: "",
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ["system", "extraction-templates"] });

  const { data, isLoading } = useQuery<{ templates: ExtractionTemplate[]; count: number }>({
    queryKey: ["system", "extraction-templates"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/extraction-templates`);
      if (!r.ok) throw new Error("fetch failed");
      return r.json();
    },
    staleTime: 15_000,
  });

  // Works list for optional work assignment
  const { data: worksData } = useQuery<{ works: { id: string; title: string }[] }>({
    queryKey: ["works", "list-mini"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/works?limit=100`);
      if (!r.ok) return { works: [] };
      return r.json();
    },
    staleTime: 60_000,
  });
  const works = worksData?.works ?? [];

  const openAdd = () => {
    setForm({ name: "", kind_label: "", system_prompt: "", field_hints: "" });
    setEditing(null);
    setAdding(true);
  };
  const openEdit = (t: ExtractionTemplate) => {
    setForm({
      name: t.name,
      kind_label: t.kind_label ?? "",
      system_prompt: t.system_prompt,
      field_hints: (t.field_hints ?? []).join("\n"),
    });
    setEditing(t);
    setAdding(true);
  };
  const closeForm = () => { setAdding(false); setEditing(null); };

  const saveMutation = useMutation({
    mutationFn: async () => {
      const hints = form.field_hints.split("\n").map(s => s.trim()).filter(Boolean);
      const body = {
        name: form.name.trim(),
        kind_label: form.kind_label || null,
        system_prompt: form.system_prompt.trim(),
        field_hints: hints,
        work_id: null,
      };
      const url = editing
        ? `${API_BASE}/api/system/extraction-templates/${editing.id}`
        : `${API_BASE}/api/system/extraction-templates`;
      const r = await apiFetch(url, {
        method: editing ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) { const e = await r.json().catch(() => ({})); throw new Error((e as any).detail ?? "Save failed"); }
      return r.json();
    },
    onSuccess: () => {
      invalidate();
      closeForm();
      toast.success(editing ? "Template updated" : "Template created");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deleteMutation = useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`${API_BASE}/api/system/extraction-templates/${id}`, { method: "DELETE" });
      if (!r.ok) throw new Error("Delete failed");
    },
    onSuccess: () => { invalidate(); toast.success("Template deleted"); },
    onError: () => toast.error("Could not delete template"),
  });

  const reharvest = useMutation({
    mutationFn: async (id: string) => {
      const r = await apiFetch(`${API_BASE}/api/system/extraction-templates/${id}/reharvest`, { method: "POST" });
      if (r.status === 409) { const e = await r.json().catch(() => ({})); throw new Error((e as any).detail ?? "AI extraction is disabled"); }
      if (!r.ok) throw new Error("Reharvest failed");
      return r.json() as Promise<{ queued: number }>;
    },
    onSuccess: (d) => toast.success(`Re-harvesting ${d.queued} document${d.queued !== 1 ? "s" : ""} in the background`),
    onError: (e: Error) => toast.error(e.message),
  });

  const templates = data?.templates ?? [];

  return (
    <Card>
      <CardContent className="p-6 space-y-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-start gap-3">
            <Sparkles className="w-5 h-5 text-primary mt-0.5 shrink-0" />
            <div>
              <h3 className="font-medium text-sm">Extraction Templates</h3>
              <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
                Define custom AI prompts for specific document types or Works.
                A template for <em>Meeting notes</em> can extract decisions and action items;
                one for <em>Research papers</em> can target hypothesis, methods, and findings.
                Templates override the generic extraction prompt when AI extraction is enabled.
              </p>
            </div>
          </div>
          {!adding && (
            <Button size="sm" variant="outline" className="gap-1.5 text-xs shrink-0" onClick={openAdd}>
              <Plus className="w-3.5 h-3.5" />
              New Template
            </Button>
          )}
        </div>

        {/* Add / Edit form */}
        {adding && (
          <div className="rounded-lg border border-border/60 bg-muted/20 p-4 space-y-3">
            <p className="text-xs font-medium">{editing ? "Edit template" : "New extraction template"}</p>
            <div className="grid gap-3">
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Template name *</label>
                  <input
                    type="text"
                    placeholder="Meeting Notes"
                    value={form.name}
                    onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs text-muted-foreground">Document kind (optional)</label>
                  <select
                    value={form.kind_label}
                    onChange={e => setForm(f => ({ ...f, kind_label: e.target.value }))}
                    className="w-full rounded-md border border-border bg-background px-3 py-1.5 text-sm focus:outline-none focus:ring-1 focus:ring-primary/50"
                  >
                    <option value="">All document types</option>
                    {DOC_KINDS.map(k => (
                      <option key={k.value} value={k.value}>{k.label}</option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">
                  Extraction prompt *
                  <span className="ml-1 opacity-60">— use <code className="bg-muted rounded px-0.5">{"{title}"}</code> and <code className="bg-muted rounded px-0.5">{"{chunk}"}</code> as placeholders</span>
                </label>
                <textarea
                  rows={6}
                  placeholder={`You are an expert at extracting structured information from meeting notes.\nGiven the document chunk below, extract:\n- Decisions made (as claims)\n- Action items with owners (as relationships: person → action → deadline)\n- Attendees (as entities)\n\nDocument title: {title}\n\nChunk:\n{chunk}`}
                  value={form.system_prompt}
                  onChange={e => setForm(f => ({ ...f, system_prompt: e.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm font-mono resize-y min-h-[120px] focus:outline-none focus:ring-1 focus:ring-primary/50"
                />
              </div>

              <div className="space-y-1">
                <label className="text-xs text-muted-foreground">
                  Field hints (optional) — one per line, shown to the AI as extraction guidance
                </label>
                <textarea
                  rows={3}
                  placeholder={"Extract the meeting date from the header\nCapture all action items with assignee names\nList all decisions as factual claims"}
                  value={form.field_hints}
                  onChange={e => setForm(f => ({ ...f, field_hints: e.target.value }))}
                  className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm resize-y min-h-[72px] focus:outline-none focus:ring-1 focus:ring-primary/50"
                />
              </div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                onClick={() => saveMutation.mutate()}
                disabled={saveMutation.isPending || !form.name.trim() || !form.system_prompt.trim()}
                className="gap-1.5 text-xs"
              >
                {saveMutation.isPending ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                {editing ? "Save changes" : "Create template"}
              </Button>
              <Button size="sm" variant="ghost" onClick={closeForm} className="text-xs">Cancel</Button>
            </div>
          </div>
        )}

        {/* Template list */}
        {isLoading ? (
          <Skeleton className="h-16 w-full" />
        ) : templates.length === 0 ? (
          <p className="text-sm text-muted-foreground py-2">
            No templates yet — create one above to override the generic extraction prompt for a document type.
          </p>
        ) : (
          <div className="space-y-2">
            {templates.map(t => (
              <div key={t.id} className="rounded-lg border border-border/40 bg-muted/10 p-4 space-y-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="text-sm font-medium">{t.name}</span>
                      {t.kind_label ? (
                        <Badge variant="secondary" className="text-[10px] h-4">
                          {DOC_KINDS.find(k => k.value === t.kind_label)?.label ?? t.kind_label}
                        </Badge>
                      ) : (
                        <Badge variant="outline" className="text-[10px] h-4 text-muted-foreground">all types</Badge>
                      )}
                      {t.work_id && works.find(w => w.id === t.work_id) && (
                        <Badge variant="outline" className="text-[10px] h-4">
                          {works.find(w => w.id === t.work_id)?.title}
                        </Badge>
                      )}
                    </div>
                    <p className="text-xs text-muted-foreground mt-1 font-mono line-clamp-2">
                      {t.system_prompt.slice(0, 180)}
                      {t.system_prompt.length > 180 ? "…" : ""}
                    </p>
                    {t.field_hints?.length > 0 && (
                      <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                        {t.field_hints.length} field hint{t.field_hints.length !== 1 ? "s" : ""}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <Button
                      size="sm" variant="ghost"
                      className="h-7 px-2 text-[11px] gap-1 text-muted-foreground hover:text-foreground"
                      onClick={() => reharvest.mutate(t.id)}
                      disabled={reharvest.isPending}
                      title="Re-run AI extraction for all matching documents using this template"
                    >
                      <RotateCcw className="w-3 h-3" />
                      Re-harvest
                    </Button>
                    <Button
                      size="sm" variant="ghost"
                      className="h-7 px-2 text-[11px] text-muted-foreground hover:text-foreground"
                      onClick={() => openEdit(t)}
                    >
                      Edit
                    </Button>
                    <button
                      onClick={() => deleteMutation.mutate(t.id)}
                      disabled={deleteMutation.isPending}
                      className="p-1.5 rounded text-muted-foreground hover:text-destructive hover:bg-destructive/5 transition-colors"
                      title="Delete template"
                    >
                      <X className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </div>
            ))}
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

      {/* Watched Folders */}
      <WatchedFoldersCard />

      {/* Extraction Templates */}
      <ExtractionTemplatesCard />

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

      {/* Hardware Telemetry */}
      <HardwareCard />

      {/* Background Jobs */}
      <JobsCard />

      {/* LLM Health */}
      <LlmHealthCard />

      {/* Action history */}
      <ActionHistoryCard />

      {/* Audit log */}
      <AuditLogCard />
    </div>
  );
}

// ─── Hardware telemetry card ─────────────────────────────────────────────────

interface HwGpu {
  name: string;
  vram_used_mb: number | null;
  vram_total_mb: number | null;
  utilization_percent: number | null;
  temp_c: number | null;
}

interface HwData {
  cpu_percent: number;
  cpu_count: number;
  ram: { used_gb: number; total_gb: number; percent: number } | null;
  disk: { used_gb: number; total_gb: number; percent: number } | null;
  gpus: HwGpu[];
  gpu_available: boolean;
  uptime_seconds: number | null;
  error: string | null;
}

function HardwareCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<HwData | null>({
    queryKey: ["system", "hardware"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/hardware`);
      if (!r.ok) return null;
      return r.json();
    },
    // Task spec: live gauges that update every 15 seconds
    refetchInterval: 15_000,
    staleTime: 13_000,
  });

  function bar(pct: number | null | undefined) {
    const p = pct ?? 0;
    const color = p > 90 ? "bg-destructive" : p > 70 ? "bg-amber-500" : "bg-emerald-500";
    return (
      <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-700 ${color}`}
             style={{ width: `${Math.min(p, 100)}%` }} />
      </div>
    );
  }

  function uptimeLabel(secs: number | null | undefined): string {
    if (!secs) return "";
    if (secs < 3600) return `up ${Math.round(secs / 60)}m`;
    if (secs < 86400) return `up ${Math.round(secs / 3600)}h`;
    return `up ${Math.floor(secs / 86400)}d ${Math.round((secs % 86400) / 3600)}h`;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Cpu className="w-5 h-5 text-muted-foreground" />
          Hardware
          {data?.uptime_seconds != null && (
            <span className="text-xs font-mono text-muted-foreground/60">
              {uptimeLabel(data.uptime_seconds)}
            </span>
          )}
        </h2>
        <button onClick={() => refetch()} disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors">
          {isFetching ? "refreshing…" : "refresh"}
        </button>
      </div>

      {isLoading ? (
        [1, 2, 3].map(i => <Skeleton key={i} className="h-8 w-full" />)
      ) : !data || data.error === "psutil not installed" ? (
        <p className="text-sm text-muted-foreground">
          Hardware telemetry unavailable —{" "}
          <code className="text-xs bg-muted px-1 rounded">psutil</code> is not installed on this server.
        </p>
      ) : (
        <div className="grid md:grid-cols-3 gap-4">
          {/* CPU */}
          <div className="space-y-1.5 p-4 rounded-lg border border-border/50 bg-muted/10">
            <div className="flex items-center justify-between text-xs font-mono mb-2">
              <span className="text-muted-foreground">CPU</span>
              <span className="font-medium">
                {(data.cpu_percent ?? 0).toFixed(1)}%
                {data.cpu_count > 0 && <span className="text-muted-foreground"> · {data.cpu_count} cores</span>}
              </span>
            </div>
            {bar(data.cpu_percent)}
          </div>

          {/* RAM */}
          <div className="space-y-1.5 p-4 rounded-lg border border-border/50 bg-muted/10">
            <div className="flex items-center justify-between text-xs font-mono mb-2">
              <span className="text-muted-foreground">RAM</span>
              {data.ram ? (
                <span className="font-medium">
                  {data.ram.used_gb.toFixed(1)} / {data.ram.total_gb.toFixed(1)} GB
                </span>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </div>
            {bar(data.ram?.percent)}
          </div>

          {/* Disk */}
          <div className="space-y-1.5 p-4 rounded-lg border border-border/50 bg-muted/10">
            <div className="flex items-center justify-between text-xs font-mono mb-2">
              <span className="text-muted-foreground">Disk</span>
              {data.disk ? (
                <span className="font-medium">
                  {data.disk.used_gb.toFixed(1)} / {data.disk.total_gb.toFixed(1)} GB
                </span>
              ) : (
                <span className="text-muted-foreground">—</span>
              )}
            </div>
            {bar(data.disk?.percent)}
          </div>

          {/* GPU(s) — full-width row per GPU */}
          {data.gpu_available && data.gpus.length > 0 ? (
            data.gpus.map((gpu, i) => {
              const vramPct = gpu.vram_used_mb && gpu.vram_total_mb
                ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100
                : null;
              return (
                <div key={i} className="md:col-span-3 space-y-1.5 p-4 rounded-lg border border-border/50 bg-muted/10">
                  <div className="flex items-center justify-between text-xs font-mono mb-2 gap-2">
                    <span className="text-muted-foreground truncate">
                      GPU{data.gpus.length > 1 ? ` ${i + 1}` : ""} — {gpu.name}
                    </span>
                    <span className="font-medium shrink-0 flex items-center gap-3">
                      {gpu.vram_used_mb != null && gpu.vram_total_mb != null && (
                        <span>
                          {(gpu.vram_used_mb / 1024).toFixed(1)} / {(gpu.vram_total_mb / 1024).toFixed(1)} GB VRAM
                        </span>
                      )}
                      {gpu.utilization_percent != null && (
                        <span className="text-muted-foreground">{gpu.utilization_percent}% util</span>
                      )}
                      {gpu.temp_c != null && (
                        <span className={gpu.temp_c > 85 ? "text-destructive" : gpu.temp_c > 70 ? "text-amber-600" : "text-muted-foreground"}>
                          {gpu.temp_c}°C
                        </span>
                      )}
                    </span>
                  </div>
                  {vramPct != null ? bar(vramPct) : (
                    gpu.utilization_percent != null ? bar(gpu.utilization_percent) : null
                  )}
                </div>
              );
            })
          ) : (
            <div className="md:col-span-3 p-4 rounded-lg border border-dashed border-border/40 text-xs font-mono text-muted-foreground/60">
              GPU — Not available (no nvidia-smi or rocm-smi detected)
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Background jobs card ─────────────────────────────────────────────────────

interface BgJob {
  id: string; kind: string; label: string;
  state: "running" | "done" | "failed";
  started_at: number; finished_at: number | null; error: string | null;
}

const KIND_LABELS: Record<string, string> = {
  document:   "doc",
  embeddings: "embed",
  tts:        "tts",
  nightshift: "night",
  background: "bg",
};

function JobsCard() {
  const qc = useQueryClient();

  const { data, isLoading, refetch, isFetching } = useQuery<{
    jobs: BgJob[]; running: number; failed: number; total: number;
  }>({
    queryKey: ["system", "jobs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/jobs?limit=50`);
      if (!r.ok) return { jobs: [], running: 0, failed: 0, total: 0 };
      return r.json();
    },
    // Poll every 5 s while jobs are running; every 10 s when idle so new jobs
    // submitted from other pages appear promptly without hammering the server.
    refetchInterval: (query) => (query.state.data?.running ?? 0) > 0 ? 5_000 : 10_000,
    staleTime: 4_000,
  });

  const retryMutation = useMutation({
    mutationFn: async (jobId: string) => {
      const r = await apiFetch(`${API_BASE}/api/system/jobs/${jobId}/retry`, { method: "POST" });
      if (r.status === 404) throw new Error("Job not found — it may have been evicted from the registry.");
      if (r.status === 409) throw new Error("Only failed jobs can be retried.");
      if (r.status === 501) throw new Error("This job pre-dates retry support; re-upload the file to retry.");
      if (!r.ok) throw new Error("Retry failed");
    },
    onSuccess: () => {
      toast.success("Job re-queued");
      qc.invalidateQueries({ queryKey: ["system", "jobs"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const jobs = data?.jobs ?? [];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Zap className="w-5 h-5 text-muted-foreground" />
          Background Jobs
          {(data?.running ?? 0) > 0 && (
            <Badge variant="secondary" className="font-mono text-[10px] animate-pulse">
              {data!.running} running
            </Badge>
          )}
          {(data?.failed ?? 0) > 0 && (
            <Badge variant="destructive" className="font-mono text-[10px]">
              {data!.failed} failed
            </Badge>
          )}
        </h2>
        <button onClick={() => refetch()} disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors">
          {isFetching ? "refreshing…" : `${data?.total ?? 0} total · refresh`}
        </button>
      </div>

      {isLoading ? (
        [1, 2, 3].map(i => <Skeleton key={i} className="h-10 w-full" />)
      ) : jobs.length === 0 ? (
        <div className="text-center py-8 text-muted-foreground text-sm border border-dashed rounded-lg">
          No background jobs recorded yet — jobs appear when you import files or run AI tasks.
        </div>
      ) : (
        <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30 max-h-72 overflow-y-auto">
          {jobs.map(j => {
            const elapsed = j.finished_at != null
              ? (j.finished_at - j.started_at).toFixed(1) + "s"
              : j.state === "running"
                ? `${(Date.now() / 1000 - j.started_at).toFixed(0)}s…`
                : null;
            const isRetrying = retryMutation.isPending && retryMutation.variables === j.id;

            return (
              <div key={j.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors">
                {/* State dot */}
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  j.state === "done"    ? "bg-emerald-500" :
                  j.state === "failed"  ? "bg-destructive" :
                  "bg-amber-400 animate-pulse"
                }`} />

                {/* Kind badge */}
                <span className="shrink-0 font-mono text-[9px] uppercase px-1.5 py-0.5 rounded bg-muted/60 text-muted-foreground">
                  {KIND_LABELS[j.kind] ?? j.kind}
                </span>

                {/* Label + error */}
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-mono font-medium truncate block">{j.label}</span>
                  {j.error && (
                    <span className="text-[11px] text-destructive block truncate" title={j.error}>
                      {j.error.slice(0, 120)}
                    </span>
                  )}
                </div>

                {/* Right side: state + elapsed + retry */}
                <div className="flex items-center gap-2 shrink-0">
                  <span className={`text-[10px] font-mono ${
                    j.state === "done"   ? "text-emerald-600" :
                    j.state === "failed" ? "text-red-600" :
                    "text-amber-600"
                  }`}>{j.state}</span>

                  {elapsed && (
                    <span className="text-[10px] font-mono text-muted-foreground/50">{elapsed}</span>
                  )}

                  {j.state === "failed" && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="h-6 px-2 text-[10px] gap-1 text-muted-foreground hover:text-foreground"
                      onClick={() => retryMutation.mutate(j.id)}
                      disabled={isRetrying}
                      title="Re-queue this job"
                    >
                      {isRetrying
                        ? <Loader2 className="w-3 h-3 animate-spin" />
                        : <RotateCcw className="w-3 h-3" />}
                      Retry
                    </Button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── LLM health card ──────────────────────────────────────────────────────────

function LlmHealthCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<{
    overall: "ok" | "degraded" | "down";
    primary: { ok: boolean; model: string; latency_ms: number; error: string | null };
    fallback: { ok: boolean; model: string; latency_ms: number; error: string | null } | null;
    base_url: string;
  }>({
    queryKey: ["system", "llm-health"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/llm-health`);
      if (!r.ok) return null;
      return r.json();
    },
    refetchInterval: 60_000,
    staleTime: 30_000,
  });

  const overallColor = data?.overall === "ok" ? "text-emerald-600" : data?.overall === "degraded" ? "text-amber-600" : "text-destructive";

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Brain className="w-5 h-5 text-muted-foreground" />
          LLM Health
          {data && <span className={`text-sm font-mono ${overallColor}`}>{data.overall}</span>}
        </h2>
        <button onClick={() => refetch()} disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors">
          {isFetching ? "probing…" : "probe now"}
        </button>
      </div>
      {isLoading ? <Skeleton className="h-16 w-full" /> : !data ? (
        <p className="text-sm text-muted-foreground">Could not reach /api/system/llm-health.</p>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between p-3 rounded-lg border border-border/50 bg-muted/10">
            <div>
              <span className="text-xs font-mono font-medium">{data.primary.model}</span>
              <span className="text-[10px] text-muted-foreground ml-2">primary</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[10px] font-mono text-muted-foreground">{data.primary.latency_ms}ms</span>
              <span className={`text-[10px] font-mono ${data.primary.ok ? "text-emerald-600" : "text-destructive"}`}>
                {data.primary.ok ? "ok" : "down"}
              </span>
            </div>
          </div>
          {data.fallback && (
            <div className="flex items-center justify-between p-3 rounded-lg border border-border/50 bg-muted/10">
              <div>
                <span className="text-xs font-mono font-medium">{data.fallback.model}</span>
                <span className="text-[10px] text-muted-foreground ml-2">fallback</span>
              </div>
              <div className="flex items-center gap-3">
                <span className="text-[10px] font-mono text-muted-foreground">{data.fallback.latency_ms}ms</span>
                <span className={`text-[10px] font-mono ${data.fallback.ok ? "text-emerald-600" : "text-destructive"}`}>
                  {data.fallback.ok ? "ok" : "down"}
                </span>
              </div>
            </div>
          )}
          {!data.primary.ok && data.primary.error && (
            <p className="text-xs text-destructive font-mono px-1">{data.primary.error}</p>
          )}
          <p className="text-[10px] font-mono text-muted-foreground">Base URL: {data.base_url}</p>
        </div>
      )}
    </div>
  );
}

// ─── Action history card ───────────────────────────────────────────────────────

interface ActionRun {
  id: string;
  action_name: string;
  status: "running" | "done" | "error";
  output_label: string | null;
  output_path: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

function ActionHistoryCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<{ runs: ActionRun[]; count: number }>({
    queryKey: ["actions", "runs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/actions/runs?limit=20`);
      if (!r.ok) throw new Error("action runs fetch failed");
      return r.json();
    },
    staleTime: 30_000,
  });

  const runs = data?.runs ?? [];

  const handleDownload = (run: ActionRun) => {
    if (!run.output_path) return;
    window.open(`${API_BASE}/api/studio/outputs/serve?path=${encodeURIComponent(run.output_path)}`, "_blank");
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Zap className="w-5 h-5 text-muted-foreground" />
          Action History
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors flex items-center gap-1"
        >
          {isFetching ? "refreshing…" : `${data?.count ?? 0} runs · refresh`}
        </button>
      </div>

      {isLoading ? (
        [1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
      ) : runs.length === 0 ? (
        <div className="text-center py-10 text-muted-foreground text-sm border border-dashed rounded-lg">
          No actions run yet — visit the{" "}
          <a href={`${import.meta.env.BASE_URL}actions`} className="underline">Actions page</a>{" "}
          or ask the AI to run one.
        </div>
      ) : (
        <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30 max-h-80 overflow-y-auto">
          {runs.map((run) => {
            const isOk = run.status === "done";
            const isErr = run.status === "error";
            return (
              <div key={run.id} className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors">
                <span
                  className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    isOk ? "bg-emerald-500" : isErr ? "bg-destructive" : "bg-amber-400 animate-pulse"
                  }`}
                />
                <div className="flex-1 min-w-0">
                  <span className="text-xs font-mono font-medium">
                    {run.action_name.replace(/_/g, " ")}
                  </span>
                  {run.output_label && !isErr && (
                    <span className="text-[11px] font-mono text-muted-foreground ml-2">{run.output_label}</span>
                  )}
                  {run.error && (
                    <span className="text-[11px] text-destructive ml-2">{run.error.slice(0, 60)}</span>
                  )}
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  {isOk && run.output_path && (
                    <Button
                      variant="ghost"
                      size="sm"
                      className="h-6 text-[10px] gap-1 px-2"
                      onClick={() => handleDownload(run)}
                    >
                      <Download className="w-3 h-3" />
                      Download
                    </Button>
                  )}
                  <span className={`text-[10px] font-mono ${isOk ? "text-emerald-600" : isErr ? "text-red-600" : "text-amber-600"}`}>
                    {run.status}
                  </span>
                  <span className="text-[10px] font-mono text-muted-foreground/50">
                    {run.created_at ? new Date(run.created_at).toLocaleTimeString() : ""}
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
