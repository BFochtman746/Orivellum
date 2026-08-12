import { useGetSystemHealth, useListCapabilities, getGetSystemHealthQueryKey } from "@workspace/api-client-react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { Activity, Database, Cpu, CheckCircle2, XCircle, AlertCircle, AlertTriangle, Terminal, Sparkles, Moon, Brain, Trash2, ScrollText, User, Settings, Image as ImageIcon, Eye, Loader2, FileSearch, ClipboardCopy, ChevronDown, ChevronRight, Zap, Download, RotateCcw, FolderOpen, FolderPlus, Plus, X, GitMerge, Archive, Save, Mic2, Network, Server, Plug, ExternalLink, ListOrdered, Gauge, Bell } from "lucide-react";
import { alertsEnabled, setAlertsEnabled, requestNotificationPermission, notificationsSupported } from "@/lib/notifications";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { useGdDark } from "@/lib/useGdDark";

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

// ─── Profile card ────────────────────────────────────────────────────────────

const COMM_STYLES = [
  { value: "",           label: "Default (no directive)" },
  { value: "casual",     label: "Casual — relaxed, conversational" },
  { value: "direct",     label: "Direct — concise, lead with answer" },
  { value: "socratic",   label: "Socratic — guide with questions" },
  { value: "formal",     label: "Formal — professional register" },
  { value: "technical",  label: "Technical — precise, assume domain familiarity" },
];

function ProfileCard() {
  const [name,    setName]    = useState("");
  const [bio,     setBio]     = useState("");
  const [style,   setStyle]   = useState("");
  const [saving,  setSaving]  = useState(false);
  const [loaded,  setLoaded]  = useState(false);

  useEffect(() => {
    apiFetch(`${API_BASE}/system/profile`).then(r => r.json()).then(d => {
      setName(d.user_name  ?? "");
      setBio (d.user_bio   ?? "");
      setStyle(d.communication_style ?? "");
      setLoaded(true);
    }).catch(() => setLoaded(true));
  }, []);

  const save = async () => {
    setSaving(true);
    try {
      await apiFetch(`${API_BASE}/system/profile`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_name: name, user_bio: bio, communication_style: style }),
      });
      toast.success("Profile saved");
    } catch {
      toast.error("Could not save profile");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card className="vellum-card" style={{ background: 'var(--green-soft)' }}>
      <CardContent className="p-6 space-y-4">
        <div className="flex items-center gap-2 mb-2">
          <User className="w-4 h-4 text-primary" />
          <h3 className="font-mono text-sm uppercase tracking-wider text-foreground">Your Profile</h3>
          <span className="ml-auto text-[10px] font-mono text-muted-foreground">Personalises AI responses &amp; briefing</span>
        </div>

        {!loaded ? (
          <div className="space-y-2"><Skeleton className="h-9 w-full" /><Skeleton className="h-16 w-full" /></div>
        ) : (
          <>
            <div className="grid sm:grid-cols-2 gap-4">
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-muted-foreground uppercase">Name</label>
                <Input
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder="e.g. Brian"
                  maxLength={120}
                />
              </div>
              <div className="space-y-1.5">
                <label className="text-xs font-mono text-muted-foreground uppercase">Communication style</label>
                <Select value={style} onValueChange={setStyle}>
                  <SelectTrigger aria-label="Communication style"><SelectValue placeholder="Default" /></SelectTrigger>
                  <SelectContent>
                    {COMM_STYLES.map(s => (
                      <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-mono text-muted-foreground uppercase">
                About you <span className="normal-case">(one line — injected into every AI prompt)</span>
              </label>
              <Textarea
                value={bio}
                onChange={e => setBio(e.target.value)}
                placeholder="e.g. Author working on a sci-fi trilogy, interested in hard science and world-building"
                maxLength={240}
                rows={2}
                className="resize-none"
              />
              <p className="text-[10px] text-muted-foreground">{bio.length}/240</p>
            </div>

            <Button size="sm" onClick={save} disabled={saving} className="gap-1.5">
              {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
              Save profile
            </Button>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ─── Persona card ─────────────────────────────────────────────────────────────
// Edits the active 'chat.persona' MCOS prompt.  Every save creates a new
// versioned row and activates it immediately — the next message Brian sends
// will use the updated persona without a server restart.

function PersonaCard() {
  const [content,   setContent]   = useState("");
  const [isDefault, setIsDefault] = useState(true);
  const [version,   setVersion]   = useState<number | null>(null);
  const [loaded,    setLoaded]    = useState(false);
  const [saving,    setSaving]    = useState(false);
  const [resetting, setResetting] = useState(false);

  const load = () =>
    apiFetch(`${API_BASE}/system/persona`).then(r => r.json()).then(d => {
      setContent(d.content ?? "");
      setIsDefault(d.is_default ?? true);
      setVersion(d.version ?? null);
      setLoaded(true);
    }).catch(() => setLoaded(true));

  useEffect(() => { load(); }, []);

  const save = async () => {
    if (!content.trim()) { toast.error("Persona cannot be empty"); return; }
    setSaving(true);
    try {
      const res = await apiFetch(`${API_BASE}/system/persona`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content, name: "Custom" }),
      });
      const d = await res.json();
      setIsDefault(d.is_default ?? false);
      setVersion(d.version ?? null);
      toast.success("Persona saved — takes effect on the next message");
    } catch {
      toast.error("Could not save persona");
    } finally {
      setSaving(false);
    }
  };

  const reset = async () => {
    setResetting(true);
    try {
      const res = await apiFetch(`${API_BASE}/system/persona/reset`, { method: "POST" });
      const d = await res.json();
      setContent(d.content ?? "");
      setIsDefault(true);
      setVersion(d.version ?? null);
      toast.success("Persona reset to A-01 default");
    } catch {
      toast.error("Could not reset persona");
    } finally {
      setResetting(false);
    }
  };

  return (
    <Card className="vellum-card" style={{ background: 'var(--page-bg)' }}>
      <CardContent className="p-6 space-y-4">
        <div className="flex items-center gap-2 mb-2">
          <Brain className="w-4 h-4 text-primary" />
          <h3 className="font-mono text-sm uppercase tracking-wider text-foreground">Copilot Persona</h3>
          <div className="ml-auto flex items-center gap-2">
            {version && (
              <span className="text-[10px] font-mono text-muted-foreground">v{version}</span>
            )}
            {isDefault ? (
              <Badge variant="outline" className="text-[10px] font-mono">A-01 default</Badge>
            ) : (
              <Badge className="text-[10px] font-mono" style={{ background: 'var(--gilt)', color: '#000' }}>customised</Badge>
            )}
          </div>
        </div>
        <p className="text-[11px] text-muted-foreground leading-relaxed">
          Every chat conversation speaks as this persona. Saves create a new governed version and take
          effect on the next message — no restart needed.
        </p>

        {!loaded ? (
          <div className="space-y-2">
            <Skeleton className="h-48 w-full" />
            <Skeleton className="h-8 w-32" />
          </div>
        ) : (
          <>
            <Textarea
              value={content}
              onChange={e => setContent(e.target.value)}
              rows={18}
              className="font-mono text-[12px] resize-y"
              spellCheck={false}
            />
            <div className="flex items-center gap-2 flex-wrap">
              <Button size="sm" onClick={save} disabled={saving || resetting} className="gap-1.5">
                {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Save className="w-3.5 h-3.5" />}
                Save persona
              </Button>
              {!isDefault && (
                <Button
                  size="sm"
                  variant="outline"
                  onClick={reset}
                  disabled={saving || resetting}
                  className="gap-1.5"
                >
                  {resetting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                  Reset to A-01 default
                </Button>
              )}
              <span className="ml-auto text-[10px] font-mono text-muted-foreground">
                {content.length} chars
              </span>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
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
const STATUS_STYLE: Record<string, string> = {
  ok:    'var(--green-2)',
  warn:  'var(--gilt)',
  error: 'var(--rust)',
  info:  'var(--ink-soft)',
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
    <Card className="vellum-card">
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
                  <span key={s} className="text-xs font-mono font-semibold"
                        style={{ color: STATUS_STYLE[s] }}>
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
                    className="flex items-start gap-2 text-xs px-3 py-2 rounded-lg border"
                    style={c.status === "error"
                      ? { borderColor: 'var(--rust)', background: 'var(--rust-soft)', color: 'var(--rust)' }
                      : { borderColor: 'var(--gilt-line)', background: 'var(--gilt-soft)', color: 'var(--gilt)' }}>
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
              <div className="flex items-center gap-2 text-xs px-3 py-2 rounded-lg border"
                   style={{ color: 'var(--green-2)', borderColor: 'var(--green-2)', background: 'var(--green-soft)' }}>
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
                          <span className="px-1.5 py-0.5 rounded-full text-[10px] leading-none"
                                style={{ background: 'var(--gilt-soft)', color: 'var(--gilt)' }}>
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
                            <span className="shrink-0 w-28 font-mono text-[10px] mt-0.5"
                                  style={{ color: STATUS_STYLE[c.status] }}>
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
    <Card className="vellum-card">
      <CardContent className="p-6 space-y-5">
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Moon className="w-5 h-5" style={{ color: 'var(--green-raw)' }} />
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
            No runs yet — the Night Scriptorium fires at 3:00 AM.
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
              No runs yet — the Night Scriptorium fires at 3:00 AM.
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
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "user-memory"] });
      toast.success("Memory deleted");
    },
    onError: () => toast.error("Could not delete"),
  });

  const clearAll = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/user-memory`, { method: "DELETE" });
      if (!r.ok) throw new Error("clear failed");
      return r.json() as Promise<{ deleted: number }>;
    },
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["system", "user-memory"] });
      toast.success(`Cleared ${result.deleted} memor${result.deleted === 1 ? "y" : "ies"}`);
    },
    onError: () => toast.error("Could not clear memories"),
  });

  const memories = data?.memories ?? [];
  const busy = del.isPending || clearAll.isPending;

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-center justify-between gap-3 mb-4">
          <div className="flex items-center gap-3">
            <Brain className="w-5 h-5" style={{ color: 'var(--green-raw)' }} />
            <h2 className="text-lg font-serif font-medium">My Memory</h2>
            <span className="text-xs text-muted-foreground">— facts Orivellum remembers about you</span>
          </div>
          {memories.length > 0 && (
            <Button
              size="sm"
              variant="ghost"
              className="text-xs text-destructive hover:text-destructive gap-1.5 shrink-0"
              disabled={busy}
              onClick={() => {
                if (confirm(`Delete all ${memories.length} stored memories? This cannot be undone.`)) {
                  clearAll.mutate();
                }
              }}
            >
              {clearAll.isPending
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <Trash2 className="w-3 h-3" />}
              Clear all
            </Button>
          )}
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
                  <div className="flex items-center gap-2">
                    <p className="text-xs font-mono text-muted-foreground">{m.key}</p>
                    <span className="text-[10px] text-muted-foreground/60">{relativeTime(m.created_at)}</span>
                  </div>
                  <p className="text-sm mt-0.5">{m.value}</p>
                </div>
                <button
                  onClick={() => del.mutate(m.id)}
                  disabled={busy}
                  className="opacity-0 group-hover:opacity-60 hover:!opacity-100 p-1 text-destructive transition-opacity shrink-0"
                  title="Delete this memory"
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
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-center gap-3 mb-3">
          <Terminal className="w-5 h-5" style={{ color: 'var(--green-raw)' }} />
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

// ─── Auto Dedup Card ──────────────────────────────────────────────────────────

type AutoDedupStats = {
  pending: number; superseded: number; versioned: number; dismissed: number; total: number;
};
type AutoDedupResult = {
  processed: number; superseded: number; versioned: number; skipped: number; errors: number;
};

function AutoDedupCard() {
  const API = `${API_BASE}/api`;
  const [enabled, setEnabled] = useState<boolean | null>(null);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);
  const [lastResult, setLastResult] = useState<AutoDedupResult | null>(null);

  const { data: stats, refetch: refetchStats } = useQuery<AutoDedupStats>({
    queryKey: ["auto-dedup-stats"],
    queryFn: async () => {
      const r = await apiFetch(`${API}/system/auto-dedup/stats`);
      if (!r.ok) throw new Error("stats fetch failed");
      return r.json();
    },
    refetchInterval: 30_000,
  });

  // Load current setting
  useEffect(() => {
    apiFetch(`${API}/system/settings/auto_dedup_enabled`)
      .then(r => r.ok ? r.json() : { value: "false" })
      .then(d => setEnabled((d?.value ?? "false").toString().toLowerCase() === "true"))
      .catch(() => setEnabled(false));
  }, [API]);

  const toggleEnabled = async (val: boolean) => {
    setSaving(true);
    try {
      await apiFetch(`${API}/system/settings/auto_dedup_enabled`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ value: val ? "true" : "false" }),
      });
      setEnabled(val);
      toast.success(val ? "Auto-dedup enabled" : "Auto-dedup disabled");
    } catch {
      toast.error("Could not save setting");
    } finally {
      setSaving(false);
    }
  };

  const runNow = async () => {
    setRunning(true);
    setLastResult(null);
    try {
      const r = await apiFetch(`${API}/system/auto-dedup/run-now`, { method: "POST" });
      if (!r.ok) throw new Error(await r.text());
      const result: AutoDedupResult = await r.json();
      setLastResult(result);
      refetchStats();
      toast.success(
        result.processed === 0
          ? "No pending pairs to process"
          : `Processed ${result.processed} pair(s) — ${result.superseded} superseded, ${result.versioned} versioned`,
      );
    } catch (err) {
      toast.error(`Auto-dedup failed: ${err}`);
    } finally {
      setRunning(false);
    }
  };

  const pending = stats?.pending ?? 0;

  return (
    <Card className="vellum-card">
      <CardContent className="p-6 space-y-5">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <GitMerge className="w-5 h-5 text-primary/70" />
            <div>
              <h3 className="font-mono text-sm uppercase tracking-wider">Automatic Deduplication</h3>
              <p className="text-xs text-muted-foreground mt-0.5 max-w-lg">
                Automatically resolve near-duplicate documents at import time and each night —
                newer/richer documents survive; older copies are archived as superseded or linked
                as version history.
              </p>
            </div>
          </div>
          {pending > 0 && (
            <Badge variant="secondary" className="shrink-0 font-mono">
              {pending} pending
            </Badge>
          )}
        </div>

        {/* Stats row */}
        {stats && (
          <div className="grid grid-cols-4 gap-3">
            {[
              { label: "Pending",    value: stats.pending },
              { label: "Superseded", value: stats.superseded },
              { label: "Versioned",  value: stats.versioned },
              { label: "Dismissed",  value: stats.dismissed },
            ].map(({ label, value }) => (
              <div key={label} className="rounded-md bg-muted/40 px-3 py-2 text-center">
                <p className="text-xl font-mono font-semibold"
                   style={{ color: label === "Pending" && pending > 0 ? 'var(--gilt)' : undefined }}>{value}</p>
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground mt-0.5">{label}</p>
              </div>
            ))}
          </div>
        )}

        {/* How it works */}
        <div className="rounded-md bg-muted/30 border border-border/40 px-4 py-3 space-y-1.5 text-xs text-muted-foreground">
          <p className="font-medium text-foreground/70 font-mono uppercase text-[10px] tracking-wider mb-1">Resolution rules</p>
          <div className="flex items-start gap-2"><Archive className="w-3 h-3 mt-0.5 shrink-0" style={{ color: 'var(--gilt)' }} /><span><strong>Near-duplicate (≥ 85% similar):</strong> the canonical/newer/richer document survives; the other is marked <em>superseded</em>.</span></div>
          <div className="flex items-start gap-2"><GitMerge className="w-3 h-3 mt-0.5 shrink-0 text-primary/60" /><span><strong>Likely revision (60–85% similar):</strong> a DERIVED_FROM version chain is created; both documents are kept.</span></div>
          <div className="flex items-start gap-2"><AlertTriangle className="w-3 h-3 mt-0.5 shrink-0 text-muted-foreground" /><span>If both documents are already set to <em>canonical</em> the pair is left in the Review Queue for you to decide.</span></div>
        </div>

        {/* Last result */}
        {lastResult && (
          <div className="rounded-md bg-muted/30 px-3 py-2 text-xs font-mono text-muted-foreground">
            Last run — processed: {lastResult.processed} · superseded: {lastResult.superseded} ·
            versioned: {lastResult.versioned} · skipped: {lastResult.skipped} · errors: {lastResult.errors}
          </div>
        )}

        {/* Controls */}
        <div className="flex items-center justify-between pt-1">
          <div className="flex items-center gap-3">
            <Switch
              checked={enabled ?? false}
              onCheckedChange={toggleEnabled}
              disabled={saving || enabled === null}
              aria-label="Enable automatic deduplication"
            />
            <span className="text-sm">
              {enabled === null ? "Loading…" : enabled ? "Runs automatically at import and each night" : "Manual only — use Run now to process pending pairs"}
            </span>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={runNow}
            disabled={running}
            className="gap-2 font-mono text-xs"
          >
            {running ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Zap className="w-3.5 h-3.5" />}
            {running ? "Running…" : "Run now"}
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

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
    <Card className="vellum-card">
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
                      <span className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--rust)' }}>
                        <XCircle className="w-3 h-3" />
                        {dir.last_scan_error}
                      </span>
                    ) : dir.last_scan_files_imported !== undefined && dir.last_scan_files_imported > 0 ? (
                      <span className="flex items-center gap-1 text-[11px]" style={{ color: 'var(--green-2)' }}>
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
    <Card className="vellum-card">
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
                      onClick={() => {
                        if (confirm(`Delete template "${t.name ?? t.id}"? This cannot be undone.`)) {
                          deleteMutation.mutate(t.id);
                        }
                      }}
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

// ─── Audio Enhancement (DeepFilterNet3) toggle ────────────────────────────────

type AudioEnhanceSetupProgress = {
  stage: "resolving" | "downloading" | "installing" | "verifying";
  detail: string | null;
  packages: number;
  done: number;
  total_mb: number;
  last_line: string | null;
  elapsed_s: number;
};

type AudioEnhanceStatus = {
  enabled: boolean;
  installed: boolean;
  mode: "in-process" | "sidecar" | null;
  setting_up: boolean;
  setup_progress: AudioEnhanceSetupProgress | null;
  model: string;
  install_hint: string | null;
  error: string | null;
  python: string | null;
};

const SETUP_STAGE_LABELS: Record<string, string> = {
  resolving:   "Preparing — working out what to download…",
  downloading: "Downloading packages…",
  installing:  "Installing into the helper environment…",
  verifying:   "Almost done — verifying the helper starts…",
};

function formatElapsed(s: number): string {
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

function useAudioEnhanceSetting() {
  return useQuery({
    queryKey: ["system", "audio-enhance"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/audio-enhance`);
      if (!r.ok) throw new Error("Failed to fetch audio enhancement setting");
      return r.json() as Promise<AudioEnhanceStatus>;
    },
    // While the one-time background setup runs, poll until it settles —
    // fast enough that the staged progress text feels live.
    refetchInterval: (query) => (query.state.data?.setting_up ? 2000 : false),
    staleTime: 30_000,
  });
}

function useSetAudioEnhanceSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/audio-enhance`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update audio enhancement setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "audio-enhance"] });
      toast.success("Audio enhancement setting saved");
    },
    onError: () => toast.error("Could not update audio enhancement setting"),
  });
}

function useReprobeAudioEnhance() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/audio-enhance/probe`, {
        method: "POST",
      });
      if (!r.ok) throw new Error("Probe request failed");
      return r.json() as Promise<{
        installed: boolean;
        setting_up: boolean;
        error: string | null;
        python: string | null;
      }>;
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["system", "audio-enhance"] });
      if (res.installed) {
        toast.success("DeepFilterNet3 ready — enhancement can now be enabled");
      } else if (res.setting_up) {
        toast.info("Setting up in the background — this takes a few minutes the first time");
      } else {
        toast.error("Setup did not succeed — see the details below the card title");
      }
    },
    onError: () => toast.error("Could not re-check availability"),
  });
}

function AudioEnhancementCard() {
  const { data, isLoading } = useAudioEnhanceSetting();
  const setEnhance = useSetAudioEnhanceSetting();
  const reprobe = useReprobeAudioEnhance();

  // Toast when the background setup finishes (polling flips setting_up off).
  const wasSettingUp = useRef(false);
  useEffect(() => {
    if (!data) return;
    if (data.setting_up) {
      wasSettingUp.current = true;
    } else if (wasSettingUp.current) {
      wasSettingUp.current = false;
      if (data.installed) {
        toast.success("DeepFilterNet3 ready — enhancement can now be enabled");
      } else {
        toast.error("Setup did not succeed — see the details on the card");
      }
    }
  }, [data]);

  const settingUp = data?.setting_up ?? false;

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Mic2 className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--green-raw)' }} />
            <div className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-medium text-sm">Audio Enhancement (DeepFilterNet3)</h3>
                {data && (
                  <Badge
                    variant={data.installed ? "default" : "secondary"}
                    className="text-[10px] h-4 px-1.5"
                  >
                    {data.installed
                      ? "installed"
                      : data.setting_up ? "setting up…" : "not installed"}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground max-w-xl">
                When enabled, audio files are denoised with{" "}
                <span className="font-medium text-foreground">DeepFilterNet3</span> before being
                sent to Whisper — removing background noise, room reverb, and crosstalk.
                Dramatically improves transcription accuracy on phone recordings and voice memos.
                Runs on CPU at ~0.2× real-time with no GPU required.
              </p>
              {data && !data.installed && settingUp && (
                <div className="text-xs mt-1 space-y-1">
                  <p className="flex items-center gap-1.5" style={{ color: 'var(--gilt)' }}>
                    <Loader2 className="w-3 h-3 animate-spin shrink-0" />
                    <span className="font-medium">
                      {SETUP_STAGE_LABELS[data.setup_progress?.stage ?? ""] ?? "Setting up in the background…"}
                    </span>
                    {data.setup_progress && (
                      <span className="font-mono text-muted-foreground">
                        {formatElapsed(data.setup_progress.elapsed_s)}
                      </span>
                    )}
                  </p>
                  {data.setup_progress?.stage === "downloading" && data.setup_progress.packages > 0 && (
                    <div className="pl-[18px] pr-1 flex items-center gap-2" data-testid="setup-progress-bar">
                      <div
                        className="flex-1 h-1.5 rounded-full overflow-hidden"
                        style={{ background: 'var(--gilt-soft)' }}
                        role="progressbar"
                        aria-valuemin={0}
                        aria-valuemax={data.setup_progress.packages}
                        aria-valuenow={Math.min(data.setup_progress.done, data.setup_progress.packages)}
                        aria-label="Package downloads completed"
                      >
                        <div
                          className="h-full rounded-full transition-all duration-500"
                          style={{
                            background: 'var(--gilt)',
                            width: `${Math.min(100, Math.round((data.setup_progress.done / data.setup_progress.packages) * 100))}%`,
                          }}
                        />
                      </div>
                      <span className="font-mono text-[10px] text-muted-foreground shrink-0">
                        {Math.min(data.setup_progress.done, data.setup_progress.packages)}/{data.setup_progress.packages}
                      </span>
                    </div>
                  )}
                  {data.setup_progress?.detail && (
                    <p className="font-mono text-[11px] text-muted-foreground break-all pl-[18px]">
                      {data.setup_progress.detail}
                      {data.setup_progress.stage === "downloading" && data.setup_progress.packages > 0 && (
                        <span>
                          {" · "}{data.setup_progress.packages} package{data.setup_progress.packages === 1 ? "" : "s"}
                          {data.setup_progress.total_mb > 0 && <> · ~{Math.round(data.setup_progress.total_mb)} MB so far</>}
                        </span>
                      )}
                    </p>
                  )}
                  <p className="text-muted-foreground pl-[18px]">
                    First-time setup downloads ~300 MB. You can leave this page —
                    the card updates itself when it's done.
                  </p>
                </div>
              )}
              {data && !data.installed && !settingUp && (
                <div className="space-y-1.5 mt-1">
                  <p className="text-xs" style={{ color: 'var(--gilt)' }}>
                    Not set up yet. Click{" "}
                    <span className="font-medium">Check again</span> and it is
                    installed automatically — no server restart needed.
                  </p>
                  {data.error && (
                    <p className="text-[11px] font-mono text-muted-foreground break-all">
                      Why: {data.error}
                    </p>
                  )}
                  {data.python && (
                    <p className="text-[11px] font-mono text-muted-foreground break-all">
                      Server Python: {data.python}
                      <span className="font-sans"> — run the install from this
                      environment (the project folder) or it won't be found.</span>
                    </p>
                  )}
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1.5 h-7 text-xs"
                    onClick={() => reprobe.mutate()}
                    disabled={reprobe.isPending}
                  >
                    {reprobe.isPending
                      ? <><Loader2 className="w-3 h-3 animate-spin" /> Starting…</>
                      : <><RotateCcw className="w-3 h-3" /> Check again</>}
                  </Button>
                </div>
              )}
              {data?.installed && data.enabled && (
                <p className="text-xs mt-1" style={{ color: 'var(--green-2)' }}>
                  Active — audio files will be enhanced before transcription
                  {data.mode === "sidecar" ? " (runs in a helper environment)" : ""}.
                </p>
              )}
            </div>
          </div>
          <div className="shrink-0 pt-0.5">
            {isLoading ? (
              <Skeleton className="h-6 w-11 rounded-full" />
            ) : (
              <Switch
                checked={data?.enabled ?? false}
                onCheckedChange={(checked) => setEnhance.mutate(checked)}
                disabled={setEnhance.isPending || (!data?.installed && !data?.enabled)}
                aria-label="Enable DeepFilterNet3 audio enhancement"
              />
            )}
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Docling layout-aware PDF extraction card ─────────────────────────────────

type DoclingStatus = {
  enabled: boolean;
  installed: boolean;
  error: string | null;
  install_hint: string | null;
};

function useDoclingSetting() {
  return useQuery({
    queryKey: ["system", "docling"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/docling`);
      if (!r.ok) throw new Error("Failed to fetch Docling setting");
      return r.json() as Promise<DoclingStatus>;
    },
    staleTime: 30_000,
  });
}

function useSetDoclingSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/docling`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update Docling setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "docling"] });
      toast.success("Docling setting saved");
    },
    onError: () => toast.error("Could not update Docling setting"),
  });
}

function useReprobeDocling() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/docling/probe`, { method: "POST" });
      if (!r.ok) throw new Error("Probe request failed");
      return r.json() as Promise<{ installed: boolean; error: string | null }>;
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["system", "docling"] });
      if (res.installed) {
        toast.success("Docling detected — layout-aware PDF parsing is active");
      } else {
        toast.error("Docling is still not installed — see the install hint on the card");
      }
    },
    onError: () => toast.error("Could not re-check availability"),
  });
}

function DoclingCard() {
  const { data, isLoading } = useDoclingSetting();
  const setDocling = useSetDoclingSetting();
  const reprobe = useReprobeDocling();

  return (
    <Card className="vellum-card" data-testid="card-docling">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <FileSearch className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--gilt)' }} />
            <div className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-medium text-sm">Layout-Aware PDF Parsing (Docling)</h3>
                {data && (
                  <Badge
                    variant={data.installed ? "default" : "secondary"}
                    className="text-[10px] h-4 px-1.5"
                  >
                    {data.installed ? "installed" : "not installed"}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground max-w-xl">
                When installed, complex PDFs — multi-column pages, borderless tables, mixed
                layouts — are parsed with <span className="font-medium text-foreground">Docling</span>{" "}
                as the first extraction tier: tables come out as structured Markdown and reading
                order stays correct. If it is missing or fails, extraction silently falls back to
                the existing tiers, so nothing breaks by leaving it off.
              </p>
              {data && !data.installed && data.install_hint && (
                <p className="font-mono text-[11px] text-muted-foreground break-all">
                  Install: {data.install_hint}
                </p>
              )}
              {data && !data.installed && (
                <Button
                  variant="outline"
                  size="sm"
                  className="h-7 text-xs mt-1"
                  disabled={reprobe.isPending}
                  onClick={() => reprobe.mutate()}
                  data-testid="button-docling-probe"
                >
                  {reprobe.isPending && <Loader2 className="w-3 h-3 mr-1.5 animate-spin" />}
                  Check again
                </Button>
              )}
            </div>
          </div>
          {isLoading ? (
            <Skeleton className="w-10 h-5 rounded-full" />
          ) : (
            <Switch
              checked={data?.enabled ?? true}
              disabled={setDocling.isPending}
              onCheckedChange={(v) => setDocling.mutate(v)}
              data-testid="switch-docling"
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ─── Cross-encoder Reranker card ──────────────────────────────────────────────

type RerankerStatus = {
  enabled: boolean;
  model: string;
  configured: boolean;
  circuit_open: boolean;
  retry_in_sec: number;
  pull_hint: string | null;
};

function useRerankerSetting() {
  return useQuery({
    queryKey: ["system", "reranker"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/reranker`);
      if (!r.ok) throw new Error("Failed to fetch reranker setting");
      return r.json() as Promise<RerankerStatus>;
    },
    staleTime: 30_000,
  });
}

function useSetRerankerSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/reranker`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update reranker setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "reranker"] });
      toast.success("Reranker setting saved");
    },
    onError: () => toast.error("Could not update reranker setting"),
  });
}

function RerankerCard() {
  const { data, isLoading, refetch } = useRerankerSetting();
  const setReranker = useSetRerankerSetting();
  const [probing, setProbing] = useState(false);
  const [probeResult, setProbeResult] = useState<{ ok: boolean; detail?: string; sane_ordering?: boolean } | null>(null);

  async function probe() {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/reranker/probe`, { method: "POST" });
      const json = await r.json() as { ok: boolean; detail?: string; sane_ordering?: boolean };
      setProbeResult(json);
      if (json.ok) refetch();
    } catch {
      setProbeResult({ ok: false, detail: "Probe request failed — check server logs." });
    } finally {
      setProbing(false);
    }
  }

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <ListOrdered className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--green-raw)' }} />
            <div className="space-y-1">
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-medium text-sm">Search Reranker (Cross-Encoder)</h3>
                {data && data.configured && (
                  <Badge
                    variant={data.circuit_open ? "secondary" : "default"}
                    className="text-[10px] h-4 px-1.5"
                  >
                    {data.circuit_open ? `unreachable — retry in ${data.retry_in_sec}s` : "ready"}
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground max-w-xl">
                Second-pass scoring that reads your query and each retrieved passage{" "}
                <span className="font-medium text-foreground">together</span>, improving which
                passages reach chat context, knowledge search, and web-search results
                (+5–10% retrieval precision). Falls back silently to standard ranking
                when the model isn't available.
              </p>
              {data?.configured && (
                <p className="text-xs text-muted-foreground font-mono mt-1">
                  Model: <span className="text-foreground">{data.model}</span>
                </p>
              )}
              {data?.configured && data.pull_hint && (
                <p className="text-xs mt-1 text-muted-foreground">
                  Not pulled yet? Run:{" "}
                  <code className="font-mono bg-muted px-1 rounded text-[11px]">{data.pull_hint}</code>
                </p>
              )}
              {data && !data.configured && (
                <p className="text-xs mt-1" style={{ color: 'var(--gilt)' }}>
                  No reranker model configured — set <code className="font-mono">serving.reranker_model</code> in config.yaml.
                </p>
              )}
              {probeResult && (
                <p className="text-xs mt-1" style={{ color: probeResult.ok ? 'var(--green-2)' : 'var(--gilt)' }}>
                  {probeResult.ok
                    ? `Reranker working${probeResult.sane_ordering === false ? " (unexpected score ordering — check the model)" : " — scores look correct."}`
                    : probeResult.detail}
                </p>
              )}
            </div>
          </div>
          <div className="shrink-0 pt-0.5 flex flex-col items-end gap-2">
            {isLoading ? (
              <Skeleton className="h-6 w-11 rounded-full" />
            ) : (
              <Switch
                checked={data?.enabled ?? false}
                onCheckedChange={(checked) => setReranker.mutate(checked)}
                disabled={setReranker.isPending || !data?.configured}
                aria-label="Enable cross-encoder reranking"
              />
            )}
            <Button
              size="sm" variant="outline" className="text-xs gap-1.5 shrink-0"
              onClick={probe} disabled={probing || !data?.configured}
            >
              {probing
                ? <><Loader2 className="w-3 h-3 animate-spin" />Testing…</>
                : <><ListOrdered className="w-3 h-3" />Test Reranker</>}
            </Button>
          </div>
        </div>
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

function useAiRerankingSetting() {
  return useQuery({
    queryKey: ["system", "ai-reranking"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-reranking`);
      if (!r.ok) throw new Error("Failed to fetch AI re-ranking setting");
      return r.json() as Promise<{ enabled: boolean }>;
    },
    staleTime: 30_000,
  });
}

function useSetAiRerankingSetting() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/ai-reranking`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Failed to update AI re-ranking setting");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "ai-reranking"] });
    },
  });
}

// ─── Semantic search / embeddings card ───────────────────────────────────────

type EmbedProbeResult = { ok: boolean; dims?: number; status?: string; detail: string };
type ReindexStatus = {
  running: boolean;
  done: number;
  total: number;
  stored_dim: number | null;
  live_dim: number | null;
  mismatch: boolean;
  embedder_model: string;
  error?: string | null;
  counts: {
    chunk_total: number; chunk_done: number;
    knowledge_total: number; knowledge_done: number;
    conv_chunk_total: number; conv_chunk_done: number;
    total: number; done: number;
  };
};

function SemanticSearchCard() {
  const [probeResult, setProbeResult] = useState<EmbedProbeResult | null>(null);
  const [probing, setProbing] = useState(false);
  const [reindexing, setReindexing] = useState(false);

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

  const { data: reindexStatus, refetch: refetchReindex } = useQuery({
    queryKey: ["system", "reindex-status"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/reindex/status`);
      if (!r.ok) return null;
      return r.json() as Promise<ReindexStatus>;
    },
    refetchInterval: (query) => {
      const d = query.state.data as ReindexStatus | null;
      return d?.running ? 2_000 : 30_000;
    },
    staleTime: 5_000,
  });

  // Sync local reindexing spinner with server state
  useEffect(() => {
    if (reindexStatus && !reindexStatus.running) setReindexing(false);
  }, [reindexStatus]);

  async function probe() {
    setProbing(true);
    setProbeResult(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/embeddings/probe`, { method: "POST" });
      const json = await r.json() as EmbedProbeResult;
      setProbeResult(json);
      if (json.ok) { refetch(); refetchReindex(); }
    } catch {
      setProbeResult({ ok: false, detail: "Probe request failed — check server logs." });
    } finally {
      setProbing(false);
    }
  }

  async function startReindex() {
    if (!confirm(
      "This will delete all existing vectors and re-embed your entire library " +
      "using the current embedder model.\n\n" +
      "Keyword search (BM25) stays active throughout — only semantic ranking " +
      "is unavailable while re-indexing runs.\n\n" +
      "Continue?"
    )) return;
    setReindexing(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/reindex`, { method: "POST" });
      const json = await r.json() as { ok: boolean; detail: string };
      if (json.ok) {
        toast.success("Re-indexing started");
        refetchReindex();
      } else {
        toast.error(json.detail || "Could not start re-index");
        setReindexing(false);
      }
    } catch {
      toast.error("Re-index request failed");
      setReindexing(false);
    }
  }

  const circuitOpen = data?.circuit_open ?? false;
  const rx = reindexStatus;
  const pct = rx && rx.total > 0 ? Math.round((rx.done / rx.total) * 100) : null;
  const coverage = rx && rx.counts.total > 0
    ? Math.round((rx.counts.done / rx.counts.total) * 100)
    : null;

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Brain className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--green-raw)' }} />
          <div className="flex-1 space-y-3">
            {/* Header row */}
            <div className="flex items-center justify-between gap-3 flex-wrap">
              <div>
                <h3 className="font-medium text-sm">Semantic Search (Embeddings)</h3>
                <p className="text-sm text-muted-foreground mt-0.5 max-w-xl">
                  Searches use vector similarity + keyword matching when the embedding endpoint is
                  reachable. Falls back to keyword-only (BM25) when unavailable.
                </p>
                {rx?.embedder_model && (
                  <p className="text-xs text-muted-foreground font-mono mt-1">
                    Model: <span className="text-foreground">{rx.embedder_model}</span>
                    {rx.live_dim != null && (
                      <span className="ml-2 opacity-60">{rx.live_dim}d</span>
                    )}
                  </p>
                )}
              </div>
              <div className="flex gap-2 flex-wrap">
                <Button
                  size="sm" variant="outline" className="text-xs gap-1.5 shrink-0"
                  onClick={probe} disabled={probing || reindexing}
                >
                  {probing
                    ? <><Loader2 className="w-3 h-3 animate-spin" />Testing…</>
                    : <><Brain className="w-3 h-3" />Test Embeddings</>}
                </Button>
                <Button
                  size="sm" variant="outline" className="text-xs gap-1.5 shrink-0"
                  onClick={startReindex}
                  disabled={reindexing || rx?.running}
                  title="Delete all vectors and re-embed everything with the current model"
                >
                  {(reindexing || rx?.running)
                    ? <><Loader2 className="w-3 h-3 animate-spin" />Re-indexing…</>
                    : <><RotateCcw className="w-3 h-3" />Re-index All</>}
                </Button>
              </div>
            </div>

            {/* Dimension mismatch warning */}
            {rx?.mismatch && !rx.running && (
              <div className="flex items-start gap-2 rounded-lg px-3 py-2 border"
                   style={{ background: 'var(--gilt-soft)', borderColor: 'var(--gilt-line)', color: 'var(--gilt)' }}>
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span className="text-xs">
                  <span className="font-semibold">Vector dimension mismatch</span> — stored vectors
                  are {rx.stored_dim}d but the live embedder returns {rx.live_dim}d. Searches will
                  only use keyword matching until you click <span className="font-medium">Re-index All</span> to
                  rebuild the vector index with the new model.
                </span>
              </div>
            )}

            {/* Last re-index stopped early (endpoint died mid-run) */}
            {rx?.error && !rx.running && (
              <div className="flex items-start gap-2 rounded-lg px-3 py-2 border"
                   style={{ background: 'var(--rust-soft)', borderColor: 'color-mix(in srgb, var(--rust) 28%, transparent)', color: 'var(--rust)' }}>
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span className="text-xs">{rx.error}</span>
              </div>
            )}

            {/* Re-index progress bar */}
            {(rx?.running || (reindexing && pct === null)) && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between text-xs text-muted-foreground">
                  <span>Re-indexing library…</span>
                  <span>{rx?.done ?? 0} / {rx?.total ?? "?"} items</span>
                </div>
                <div className="w-full h-2 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-500"
                    style={{ width: pct != null ? `${pct}%` : "15%",
                             animation: pct == null ? "pulse 1.5s ease-in-out infinite" : undefined }}
                  />
                </div>
                {pct != null && (
                  <p className="text-xs text-muted-foreground">{pct}% — keyword search is active during re-indexing</p>
                )}
              </div>
            )}

            {/* Vector coverage summary (when not running) */}
            {!rx?.running && !reindexing && rx && rx.counts.total > 0 && (
              <div className="text-xs text-muted-foreground font-mono flex items-center gap-2">
                <span>
                  {rx.counts.done.toLocaleString()} / {rx.counts.total.toLocaleString()} items vectorized
                </span>
                {coverage != null && (
                  <span style={{ color: coverage === 100 ? 'var(--green-2)' : coverage > 80 ? 'var(--gilt)' : 'var(--rust)' }}>
                    ({coverage}%)
                  </span>
                )}
              </div>
            )}

            {/* Circuit breaker / probe result */}
            {!probeResult && !rx?.running && (
              circuitOpen ? (
                <div className="flex items-start gap-2 rounded-lg px-3 py-2 border"
                     style={{ background: 'var(--gilt-soft)', borderColor: 'var(--gilt-line)', color: 'var(--gilt)' }}>
                  <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                  <span className="text-xs">
                    Embedding endpoint is in cooldown after a recent failure.
                    Searches are keyword-only until the endpoint recovers.
                    Click <span className="font-medium">Test Embeddings</span> to retry now.
                  </span>
                </div>
              ) : (
                <p className="text-xs font-mono flex items-center gap-1.5" style={{ color: 'var(--green-2)' }}>
                  <CheckCircle2 className="w-3.5 h-3.5 shrink-0" />
                  Circuit breaker closed — semantic search active
                </p>
              )
            )}

            {probeResult && (
              <div className="flex items-start gap-2 text-xs rounded-lg px-3 py-2 border"
                   style={probeResult.ok
                     ? { background: 'var(--green-soft)', borderColor: 'var(--green-2)', color: 'var(--green-2)' }
                     : { background: 'var(--rust-soft)', borderColor: 'var(--rust)', color: 'var(--rust)' }}>
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

// ── Measurement Lab — bench runs, live telemetry, retrieval eval ─────────────

type TelemetrySummary = {
  hours: number;
  total_calls: number;
  purposes: Record<string, {
    calls: number; errors: number;
    latency_ms_p50: number | null; latency_ms_p95: number | null;
    ttft_ms_p50: number | null; ttft_ms_p95: number | null;
    tok_per_s_median: number | null; measured_ttft: number;
  }>;
};

type BenchRun = { id: number; ts: string; kind: string; label: string; summary: Record<string, unknown> };

type EvalChannel = { ndcg: number | null; recall: number | null; scored: number; error: string | null };

function MeasurementLabCard() {
  const [runningBench, setRunningBench] = useState<string | null>(null);
  const [runningEval, setRunningEval] = useState(false);
  const [seeding, setSeeding] = useState(false);

  const { data: telemetry } = useQuery({
    queryKey: ["bench", "telemetry"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/bench/telemetry/summary?hours=24`);
      if (!r.ok) return null;
      return r.json() as Promise<TelemetrySummary>;
    },
    refetchInterval: 30_000,
    staleTime: 20_000,
  });

  const { data: benchStatus, refetch: refetchStatus } = useQuery({
    queryKey: ["bench", "status"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/bench/status`);
      if (!r.ok) return null;
      return r.json() as Promise<{ running: boolean; kind: string | null }>;
    },
    refetchInterval: (query) => {
      const d = query.state.data as { running: boolean } | null;
      return d?.running || runningBench ? 3_000 : 30_000;
    },
    staleTime: 2_000,
  });

  // Server status is the source of truth — clear the local spinner (and pull
  // fresh results) once the server reports the benchmark finished.
  useEffect(() => {
    if (benchStatus && !benchStatus.running && runningBench) {
      setRunningBench(null);
      refetchRuns();
    }
  }, [benchStatus, runningBench]);

  const { data: runsData, refetch: refetchRuns } = useQuery({
    queryKey: ["bench", "runs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/bench/runs?limit=6`);
      if (!r.ok) return null;
      return r.json() as Promise<{ runs: BenchRun[] }>;
    },
    refetchInterval: (query) => (runningBench || benchStatus?.running ? 5_000 : 60_000),
    staleTime: 5_000,
  });

  const { data: goldensData, refetch: refetchGoldens } = useQuery({
    queryKey: ["bench", "goldens"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/bench/goldens`);
      if (!r.ok) return null;
      return r.json() as Promise<{ goldens: unknown[] }>;
    },
    staleTime: 30_000,
  });

  async function startBench(kind: string) {
    setRunningBench(kind);
    try {
      const r = await apiFetch(`${API_BASE}/api/bench/run`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind }),
      });
      if (r.ok) {
        toast.success(`${kind} benchmark started — results appear below when done`);
        refetchStatus();
      } else {
        const j = await r.json().catch(() => null);
        toast.error(j?.detail || "Could not start benchmark");
        setRunningBench(null);
      }
    } catch {
      toast.error("Benchmark request failed");
      setRunningBench(null);
    }
  }

  async function autoSeed() {
    setSeeding(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/bench/goldens/auto-seed`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ n: 20 }),
      });
      const j = await r.json().catch(() => null);
      if (r.ok) {
        toast.success(`Seeded ${j?.created ?? 0} golden queries from your library`);
        refetchGoldens();
      } else {
        toast.error(j?.detail || "Auto-seed failed");
      }
    } catch {
      toast.error("Auto-seed request failed");
    } finally {
      setSeeding(false);
    }
  }

  async function runEval() {
    setRunningEval(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/bench/eval/retrieval`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ k: 5 }),
      });
      const j = await r.json().catch(() => null);
      if (r.ok) {
        toast.success("Retrieval eval complete");
        refetchRuns();
      } else {
        toast.error(j?.detail || "Eval failed");
      }
    } catch {
      toast.error("Eval request failed");
    } finally {
      setRunningEval(false);
    }
  }

  const chat = telemetry?.purposes?.["chat.stream"];
  const goldenCount = goldensData?.goldens?.length ?? 0;
  const latestEval = runsData?.runs?.find((r) => r.kind === "retrieval_eval");
  const evalChannels = latestEval?.summary?.channels as Record<string, EvalChannel> | undefined;

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Gauge className="w-5 h-5 mt-0.5 shrink-0 text-primary" />
          <div className="flex-1 space-y-4">
            <div>
              <h3 className="font-medium text-sm">Measurement Lab</h3>
              <p className="text-sm text-muted-foreground max-w-xl">
                Live speed telemetry and repeatable benchmarks — measure first, then tune.
              </p>
            </div>

            {/* Live chat telemetry */}
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
              <div>
                <div className="text-xs text-muted-foreground">Chat first response (median)</div>
                <div className="font-mono">{chat?.ttft_ms_p50 != null ? `${(chat.ttft_ms_p50 / 1000).toFixed(1)}s` : "—"}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Generation speed (median)</div>
                <div className="font-mono">{chat?.tok_per_s_median != null ? `${chat.tok_per_s_median} tok/s` : "—"}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Chat calls (24h)</div>
                <div className="font-mono">{chat?.calls ?? 0}</div>
              </div>
              <div>
                <div className="text-xs text-muted-foreground">Errors (24h)</div>
                <div className="font-mono">{chat?.errors ?? 0}</div>
              </div>
            </div>

            {/* Bench buttons */}
            <div className="flex flex-wrap gap-2">
              {(["ttft", "generation", "cache"] as const).map((kind) => (
                <Button
                  key={kind}
                  size="sm"
                  variant="outline"
                  disabled={runningBench !== null}
                  onClick={() => startBench(kind)}
                  data-testid={`button-bench-${kind}`}
                >
                  {runningBench === kind ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Zap className="w-3.5 h-3.5 mr-1.5" />}
                  {kind === "ttft" ? "First-token sweep" : kind === "generation" ? "Generation speed" : "Prefix cache check"}
                </Button>
              ))}
            </div>

            {/* Retrieval eval */}
            <div className="space-y-2 pt-1 border-t border-border/50">
              <div className="flex items-center justify-between gap-3 flex-wrap pt-2">
                <div className="text-sm">
                  <span className="font-medium">Retrieval quality</span>
                  <span className="text-muted-foreground ml-2 text-xs">{goldenCount} golden queries</span>
                </div>
                <div className="flex gap-2">
                  <Button size="sm" variant="outline" disabled={seeding} onClick={autoSeed} data-testid="button-goldens-seed">
                    {seeding ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Plus className="w-3.5 h-3.5 mr-1.5" />}
                    Seed from library
                  </Button>
                  <Button size="sm" disabled={runningEval || goldenCount === 0} onClick={runEval} data-testid="button-eval-run">
                    {runningEval ? <Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> : <Activity className="w-3.5 h-3.5 mr-1.5" />}
                    Score retrieval
                  </Button>
                </div>
              </div>
              {evalChannels && (
                <div className="grid grid-cols-3 gap-3 text-sm">
                  {(["fts", "semantic", "hybrid"] as const).map((ch) => {
                    const c = evalChannels[ch];
                    return (
                      <div key={ch}>
                        <div className="text-xs text-muted-foreground capitalize">{ch === "fts" ? "Keyword" : ch}</div>
                        <div className="font-mono text-xs">
                          {c?.ndcg != null ? `nDCG ${c.ndcg}` : c?.error ? "unavailable" : "—"}
                          {c?.recall != null && <span className="text-muted-foreground"> · R {c.recall}</span>}
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
              {latestEval && (
                <p className="text-[11px] text-muted-foreground">
                  Last scored {relativeTime(latestEval.ts)} · top-5 · higher is better (1.0 = perfect)
                </p>
              )}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

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
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Database className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--green-raw)' }} />
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
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start gap-3">
          <Eye className="w-5 h-5 mt-0.5 shrink-0" style={{ color: 'var(--green-raw)' }} />
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
                <div className="flex items-start gap-2 rounded-lg px-3 py-2 border"
                     style={{ background: 'var(--gilt-soft)', borderColor: 'var(--gilt-line)', color: 'var(--gilt)' }}>
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
              <div className="flex items-start gap-2 text-xs rounded-lg px-3 py-2 mt-1 border"
                   style={probeResult.ok
                     ? { background: 'var(--green-soft)', borderColor: 'var(--green-2)', color: 'var(--green-2)' }
                     : { background: 'var(--rust-soft)', borderColor: 'var(--rust)', color: 'var(--rust)' }}>
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


type ImageBackend = { name: string; url: string; online: boolean };
type ImageStatusResponse = { backends: ImageBackend[]; any_online: boolean };

function ImageGenUrlCard() {
  const qc = useQueryClient();

  // ── URL setting ─────────────────────────────────────────────────────────────
  const { data: urlData } = useQuery({
    queryKey: ["system", "image-gen-url"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/image-gen`);
      if (!r.ok) throw new Error();
      return r.json() as Promise<{ url: string }>;
    },
    staleTime: 60_000,
  });

  // ── Backend status ───────────────────────────────────────────────────────────
  const {
    data: statusData,
    isLoading: statusLoading,
    isFetching: statusFetching,
    refetch: refetchStatus,
  } = useQuery<ImageStatusResponse>({
    queryKey: ["studio", "image-status"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/studio/image-status`);
      if (!r.ok) throw new Error();
      return r.json();
    },
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  const [editing, setEditing] = useState(false);
  const [val, setVal] = useState("");
  const [saving, setSaving] = useState(false);

  function startEdit() { setVal(urlData?.url ?? ""); setEditing(true); }

  async function save() {
    setSaving(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/settings/image-gen`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: val.trim() }),
      });
      if (!r.ok) throw new Error();
      qc.invalidateQueries({ queryKey: ["system", "image-gen-url"] });
      // Re-probe immediately after URL change so the status list updates
      await refetchStatus();
      toast.success(val.trim() ? "Image generation URL saved" : "Reverted to auto-detect");
      setEditing(false);
    } catch {
      toast.error("Could not save image generation URL");
    } finally {
      setSaving(false);
    }
  }

  const backends = statusData?.backends ?? [];

  return (
    <Card className="vellum-card">
      <CardContent className="p-6 space-y-5">
        {/* ── Header ──────────────────────────────────────────────────────────── */}
        <div className="flex items-center gap-3">
          <ImageIcon className="w-5 h-5 shrink-0" style={{ color: 'var(--green-raw)' }} />
          <div className="flex-1 min-w-0">
            <h3 className="font-mono text-sm uppercase tracking-wider">Image Generation Backend</h3>
            <p className="text-xs text-muted-foreground mt-0.5">
              Orivellum auto-detects Automatic1111 (port 7860) and ComfyUI (port 8188).
              Set a custom URL to override — e.g. a remote SD server or any OpenAI-compatible{" "}
              <code className="bg-muted px-1 rounded text-[10px]">/images/generations</code>{" "}
              endpoint. Leave blank for auto-detection.
            </p>
          </div>
        </div>

        {/* ── Custom URL editor ────────────────────────────────────────────────── */}
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">Custom URL</p>
            {!editing && (
              <Button size="sm" variant="outline" className="h-7 text-xs px-2.5" onClick={startEdit}>
                {urlData?.url ? "Edit" : "Set URL"}
              </Button>
            )}
          </div>

          {!editing && urlData?.url && (
            <p className="text-xs font-mono bg-muted/40 rounded px-2.5 py-1.5 truncate border border-border/40">
              {urlData.url}
            </p>
          )}
          {!editing && !urlData?.url && (
            <p className="text-xs text-muted-foreground/60 font-mono italic">
              Auto-detect (Automatic1111 · ComfyUI)
            </p>
          )}
          {editing && (
            <div className="flex gap-2">
              <Input
                autoFocus
                value={val}
                onChange={e => setVal(e.target.value)}
                placeholder="http://localhost:7860 or leave blank for auto-detect"
                className="flex-1 text-xs font-mono h-8"
                onKeyDown={e => { if (e.key === "Enter") save(); if (e.key === "Escape") setEditing(false); }}
                disabled={saving}
              />
              <Button size="sm" className="h-8 px-3 text-xs gap-1.5" onClick={save} disabled={saving}>
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : null}
                Save
              </Button>
              <Button size="sm" variant="ghost" className="h-8 px-3 text-xs" onClick={() => setEditing(false)} disabled={saving}>
                Cancel
              </Button>
            </div>
          )}
        </div>

        {/* ── Backend status list ──────────────────────────────────────────────── */}
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-mono uppercase tracking-widest text-muted-foreground">
              Backend Status
            </p>
            <Button
              size="sm"
              variant="ghost"
              className="h-6 px-2 text-xs gap-1"
              onClick={() => refetchStatus()}
              disabled={statusFetching}
              title="Re-probe all backends"
            >
              {statusFetching
                ? <Loader2 className="w-3 h-3 animate-spin" />
                : <RotateCcw className="w-3 h-3" />}
              Refresh
            </Button>
          </div>

          {statusLoading ? (
            <div className="space-y-1.5">
              <Skeleton className="h-8 w-full" />
              <Skeleton className="h-8 w-full" />
            </div>
          ) : backends.length === 0 ? (
            <div className="flex items-center gap-2 text-xs text-muted-foreground px-3 py-2 rounded-lg border border-border/40 bg-muted/20">
              <AlertCircle className="w-3.5 h-3.5 shrink-0" />
              No backends reachable — start Automatic1111, ComfyUI, or set a custom URL above.
            </div>
          ) : (
            <div className="divide-y divide-border/30 rounded-lg border border-border/40 overflow-hidden">
              {backends.map((b, i) => (
                <div
                  key={i}
                  className={`flex items-center gap-3 px-3 py-2.5 text-xs ${
                    b.online ? "" : "opacity-50"
                  }`}
                >
                  {b.online ? (
                    <CheckCircle2 className="w-3.5 h-3.5 shrink-0" style={{ color: 'var(--green-2)' }} />
                  ) : (
                    <XCircle className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  )}
                  <span className="font-medium min-w-[120px] shrink-0">{b.name}</span>
                  <span className="font-mono text-muted-foreground truncate flex-1" title={b.url}>
                    {b.url}
                  </span>
                  <Badge
                    variant={b.online ? "default" : "secondary"}
                    className="text-[10px] shrink-0"
                    style={b.online ? { background: 'var(--green-soft)', color: 'var(--green-2)', borderColor: 'var(--green-2)' } : undefined}
                  >
                    {b.online ? "Online" : "Offline"}
                  </Badge>
                </div>
              ))}
            </div>
          )}

          {statusData && !statusData.any_online && (
            <p className="text-xs flex items-center gap-1.5" style={{ color: 'var(--gilt)' }}>
              <AlertTriangle className="w-3.5 h-3.5 shrink-0" />
              No image backend is reachable. Image generation will be unavailable until at least one is online.
            </p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function System() {
  const gdDark = useGdDark();
  const { data: health, isLoading: loadingHealth } = useGetSystemHealth({ query: { queryKey: getGetSystemHealthQueryKey(), refetchInterval: 10_000, staleTime: 8_000 } });
  const { data: capsResp, isLoading: loadingCaps } = useListCapabilities();
  const { data: aiExtraction, isLoading: loadingAiExt } = useAiExtractionSetting();
  const setAiExtraction = useSetAiExtractionSetting();
  const { data: aiReranking, isLoading: loadingAiRerank } = useAiRerankingSetting();
  const setAiReranking = useSetAiRerankingSetting();

  const aiStatus = (health?.services?.ai as Record<string, string> | undefined)?.status;
  const aiEndpoint = (health?.services?.ai as Record<string, string> | undefined)?.endpoint;
  const dbStatus = (health?.services?.database as Record<string, string> | undefined)?.status;
  const aiOnline = aiStatus === "ok";

  return (
    <div className={`space-y-8 animate-in fade-in duration-500 max-w-5xl mx-auto ${gdDark ? "dark text-foreground" : ""}`}>
      <div className="pb-4" style={{ borderBottom: '1px solid var(--line)' }}>
        <span className="eyebrow mb-1">Under the Hood</span>
        <h1 className="vellum-h1">The Engine</h1>
        <div className="gilt-rule w-32" />
        <p className="text-[13px] mt-1.5" style={{ color: 'var(--ink-soft)' }}>Infrastructure health and local AI capabilities.</p>
      </div>

      <ProfileCard />
      <PersonaCard />

      <div className="grid md:grid-cols-3 gap-4">
        {/* Overall */}
        <div className="vellum-card p-6" style={{ background: 'var(--green-soft)' }}>
          <div className="flex items-center justify-between mb-4">
            <h3 className="font-mono text-sm uppercase tracking-wider" style={{ color: 'var(--ink-soft)' }}>Overall Status</h3>
            <Activity className="w-5 h-5" style={{ color: 'var(--green-raw)' }} />
          </div>
          {loadingHealth ? (
            <Skeleton className="h-8 w-24 rounded-lg" />
          ) : (
            <div className="flex items-center gap-2">
              {health?.status === "ok" ? (
                <CheckCircle2 className="w-6 h-6" style={{ color: 'var(--green-2)' }} />
              ) : (
                <AlertCircle className="w-6 h-6" style={{ color: 'var(--gilt)' }} />
              )}
              <span className="text-2xl font-serif font-semibold capitalize">
                {health?.status || "Unknown"}
              </span>
            </div>
          )}
        </div>

        {/* Database */}
        <div className="vellum-card p-6">
          <div className="flex items-center justify-between mb-4" style={{ color: 'var(--ink-soft)' }}>
            <h3 className="font-mono text-sm uppercase tracking-wider">Database</h3>
            <Database className="w-5 h-5" />
          </div>
          {loadingHealth ? (
            <Skeleton className="h-8 w-24 rounded-lg" />
          ) : (
            <div className="flex items-center gap-2">
              {dbStatus === "ok" ? (
                <CheckCircle2 className="w-5 h-5" style={{ color: 'var(--green-2)' }} />
              ) : (
                <XCircle className="w-5 h-5" style={{ color: 'var(--rust)' }} />
              )}
              <span className="text-xl font-medium">
                {dbStatus === "ok" ? "Connected" : "Offline"}
              </span>
            </div>
          )}
        </div>

        {/* AI Engine */}
        <div className="vellum-card p-6" style={aiOnline ? {} : { borderColor: 'var(--gilt-line)', background: 'var(--gilt-soft)' }}>
          <div className="flex items-center justify-between mb-4" style={{ color: 'var(--ink-soft)' }}>
            <h3 className="font-mono text-sm uppercase tracking-wider">Local AI Engine</h3>
            <Cpu className="w-5 h-5" />
          </div>
          {loadingHealth ? (
            <Skeleton className="h-8 w-24 rounded-lg" />
          ) : (
            <div className="space-y-1.5">
              <div className="flex items-center gap-2">
                {aiOnline ? (
                  <CheckCircle2 className="w-5 h-5" style={{ color: 'var(--green-2)' }} />
                ) : (
                  <XCircle className="w-5 h-5" style={{ color: 'var(--gilt)' }} />
                )}
                <span className="text-xl font-medium">
                  {aiOnline ? "Connected" : "Unavailable"}
                </span>
              </div>
              {aiEndpoint && (
                <p className="text-[11px] font-mono truncate" style={{ color: 'var(--ink-faint)' }} title={aiEndpoint}>
                  {aiEndpoint}
                </p>
              )}
            </div>
          )}
        </div>
      </div>

      {/* Database statistics */}
      <DatabaseStatsCard />

      {/* Semantic / Embedding Search */}
      <SemanticSearchCard />

      {/* Cross-encoder Search Reranker */}
      <RerankerCard />

      {/* Measurement Lab — benchmarks, telemetry, retrieval eval */}
      <MeasurementLabCard />

      {/* Vision Model Setting */}
      <VisionModelCard />

      {/* AI Model Overrides (workhorse / reasoner / coder) */}
      <ModelPickerCard />

      {/* Image Generation URL Setting */}
      <ImageGenUrlCard />

      {/* Audio Enhancement (DeepFilterNet3) */}
      <AudioEnhancementCard />

      {/* Layout-aware PDF parsing (Docling) */}
      <DoclingCard />

      {/* Browser alerts (document / audiobook ready notifications) */}
      <BrowserAlertsCard />

      {/* Web Push — delivery even when the PWA is closed (iPhone) */}
      <WebPushCard />

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
                  <p className="text-xs mt-1" style={{ color: 'var(--gilt)' }}>
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

      {/* AI Re-ranking Setting */}
      <Card className="vellum-card">
        <CardContent className="p-6">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <Sparkles className="w-5 h-5 text-primary mt-0.5 shrink-0" />
              <div className="space-y-1">
                <h3 className="font-medium text-sm">AI-Powered Search Re-ranking</h3>
                <p className="text-sm text-muted-foreground max-w-xl">
                  When enabled, retrieved passages are re-scored by your local AI before being
                  injected into chat — so the most relevant evidence always reaches the model
                  first. BM25 re-ranking runs regardless; this adds a listwise AI pass on the
                  top&nbsp;10 candidates. Adds roughly 1–3&nbsp;s to first response time.
                </p>
                {!aiOnline && (
                  <p className="text-xs mt-1" style={{ color: 'var(--gilt)' }}>
                    Requires the local AI engine to be running. Enable it now and it will activate
                    automatically once the AI service is available.
                  </p>
                )}
              </div>
            </div>
            <div className="shrink-0 pt-0.5">
              {loadingAiRerank ? (
                <Skeleton className="h-6 w-11 rounded-full" />
              ) : (
                <Switch
                  checked={aiReranking?.enabled ?? false}
                  onCheckedChange={(checked) => setAiReranking.mutate(checked)}
                  disabled={setAiReranking.isPending || !aiOnline}
                  aria-label="Enable AI-powered search re-ranking"
                />
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* AI offline setup guide */}
      {!loadingHealth && !aiOnline && (
        <Card className="vellum-card" style={{ borderColor: 'var(--gilt-line)', background: 'var(--gilt-soft)' }}>
          <CardContent className="p-6 space-y-4">
            <div className="flex items-center gap-2">
              <Terminal className="w-4 h-4" style={{ color: 'var(--gilt)' }} />
              <h3 className="font-mono text-sm font-semibold uppercase tracking-wider" style={{ color: 'var(--gilt)' }}>
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
                  Lemonade is the recommended local AI engine for Orivellum. It listens on port 13305 by default.
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

      {/* Auto Deduplication */}
      <AutoDedupCard />

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

      {/* Lemonade Engine */}
      <LemonadeEngineCard />

      {/* MCP Server */}
      <McpCard />

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

// ─── Lemonade Engine card ─────────────────────────────────────────────────────
// Proxies /api/v1/health and /api/v1/system-info from the local Lemonade Server
// to show the true picture of what's loaded and running — nvidia-smi / rocm-smi
// are absent on Windows/Strix Halo, but Lemonade knows exactly what it's doing.

interface LemonadeHealth {
  status: string;
  version?: string;
  model_loaded?: boolean;
  all_models_loaded?: Array<{ model_name: string; device?: string; recipe?: string; ctx_size?: number }>;
}
interface LemonadeSysInfo {
  devices?: Record<string, unknown>;
  recipes?: Record<string, unknown>;
}
interface LemonadeStats {
  tokens_per_second?: number;
  total_tokens?: number;
  requests_total?: number;
}
interface LemonadeData {
  available: boolean;
  base_url?: string;
  health?: LemonadeHealth;
  system_info?: LemonadeSysInfo;
  stats?: LemonadeStats;
}

// ─── Model override settings ──────────────────────────────────────────────────

interface ModelSettings {
  workhorse: { stored: string; config: string; effective: string };
  reasoner:  { stored: string; config: string; effective: string };
  coder:     { stored: string; config: string; effective: string };
}

function useModelSettings() {
  return useQuery<ModelSettings>({
    queryKey: ["system", "models"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/models`);
      if (!r.ok) throw new Error("Failed to fetch model settings");
      return r.json();
    },
    staleTime: 30_000,
  });
}

function useSetModelSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: { workhorse: string; reasoner: string; coder: string }) => {
      const r = await apiFetch(`${API_BASE}/api/system/settings/models`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!r.ok) throw new Error("Failed to update model settings");
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["system", "models"] });
      toast.success("Model settings saved");
    },
    onError: () => toast.error("Failed to save model settings"),
  });
}

function ModelPickerCard() {
  const { data, isLoading } = useModelSettings();
  const setModels = useSetModelSettings();

  const [workhorse, setWorkhorse] = useState("");
  const [reasoner,  setReasoner]  = useState("");
  const [coder,     setCoder]     = useState("");
  const initialised = useRef(false);

  // Seed inputs from server data once
  useEffect(() => {
    if (data && !initialised.current) {
      initialised.current = true;
      setWorkhorse(data.workhorse.stored);
      setReasoner(data.reasoner.stored);
      setCoder(data.coder.stored);
    }
  }, [data]);

  const dirty =
    workhorse !== (data?.workhorse.stored ?? "") ||
    reasoner  !== (data?.reasoner.stored  ?? "") ||
    coder     !== (data?.coder.stored     ?? "");

  const handleSave = () => {
    setModels.mutate({ workhorse: workhorse.trim(), reasoner: reasoner.trim(), coder: coder.trim() });
  };

  const handleClear = () => {
    setWorkhorse("");
    setReasoner("");
    setCoder("");
    setModels.mutate({ workhorse: "", reasoner: "", coder: "" });
  };

  return (
    <Card className="vellum-card">
      <CardContent className="p-6">
        <div className="flex items-start gap-3 mb-5">
          <Brain className="w-5 h-5 mt-0.5 shrink-0" style={{ color: "var(--green-raw)" }} />
          <div className="space-y-1">
            <h3 className="font-medium text-sm">AI Model Overrides</h3>
            <p className="text-sm text-muted-foreground max-w-xl">
              Override the AI model used for each role. Leave blank to use the{" "}
              <span className="font-medium text-foreground">config.yaml</span> default.
              Changes take effect on the next conversation turn — no restart required.
            </p>
          </div>
        </div>

        {isLoading ? (
          <div className="space-y-3">
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
            <Skeleton className="h-9 w-full" />
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-[120px_1fr] items-center gap-3">
              <span className="text-sm font-medium">Workhorse</span>
              <div className="space-y-1">
                <Input
                  value={workhorse}
                  onChange={(e) => setWorkhorse(e.target.value)}
                  placeholder={data?.workhorse.config || "e.g. qwen3-30b-a3b"}
                  className="font-mono text-xs h-8"
                />
                {data?.workhorse.config && (
                  <p className="text-[11px] text-muted-foreground">
                    config default: <code className="font-mono">{data.workhorse.config}</code>
                  </p>
                )}
              </div>
            </div>

            <div className="grid grid-cols-[120px_1fr] items-center gap-3">
              <span className="text-sm font-medium">Reasoner</span>
              <div className="space-y-1">
                <Input
                  value={reasoner}
                  onChange={(e) => setReasoner(e.target.value)}
                  placeholder={data?.reasoner.config || "e.g. qwen3-30b-a3b:thinking"}
                  className="font-mono text-xs h-8"
                />
                {data?.reasoner.config && (
                  <p className="text-[11px] text-muted-foreground">
                    config default: <code className="font-mono">{data.reasoner.config}</code>
                  </p>
                )}
              </div>
            </div>

            <div className="grid grid-cols-[120px_1fr] items-center gap-3">
              <span className="text-sm font-medium">Coder</span>
              <div className="space-y-1">
                <Input
                  value={coder}
                  onChange={(e) => setCoder(e.target.value)}
                  placeholder={data?.coder.config || "e.g. Qwen3-Coder-30B-A3B-Instruct-GGUF"}
                  className="font-mono text-xs h-8"
                />
                {data?.coder.config && (
                  <p className="text-[11px] text-muted-foreground">
                    config default: <code className="font-mono">{data.coder.config}</code>
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-center gap-2 pt-1">
              <Button
                size="sm"
                onClick={handleSave}
                disabled={!dirty || setModels.isPending}
                className="h-7 text-xs"
              >
                {setModels.isPending ? (
                  <Loader2 className="w-3 h-3 animate-spin mr-1" />
                ) : (
                  <Save className="w-3 h-3 mr-1" />
                )}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={handleClear}
                disabled={setModels.isPending || (!workhorse && !reasoner && !coder)}
                className="h-7 text-xs text-muted-foreground"
              >
                Clear overrides
              </Button>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function LemonadeEngineCard() {
  const { data, isLoading, refetch, isFetching } = useQuery<LemonadeData>({
    queryKey: ["system", "lemonade"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/lemonade`);
      if (!r.ok) return { available: false };
      return r.json();
    },
    refetchInterval: 30_000,
    staleTime: 25_000,
  });

  const available   = data?.available ?? false;
  const health      = data?.health;
  const models      = health?.all_models_loaded ?? [];
  const stats       = data?.stats;
  const tps         = stats?.tokens_per_second;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Server className="w-5 h-5 text-muted-foreground" />
          Local AI Engine (Lemonade)
        </h2>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
        >
          {isFetching ? "refreshing…" : "refresh"}
        </button>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          {[1, 2].map(i => <Skeleton key={i} className="h-10 w-full" />)}
        </div>
      ) : !available ? (
        <div
          className="rounded-lg border border-dashed p-5 text-sm"
          style={{ borderColor: 'var(--gilt-line)', color: 'var(--ink-soft)' }}
        >
          <div className="flex items-center gap-2 mb-1">
            <AlertTriangle className="w-4 h-4" style={{ color: 'var(--gilt)' }} />
            <span className="font-medium">Lemonade not reachable</span>
          </div>
          <p className="text-xs" style={{ color: 'var(--ink-faint)' }}>
            Check that Lemonade Server is running and that{" "}
            <code className="font-mono">base_url</code> in{" "}
            <code className="font-mono">config.yaml</code> ends in{" "}
            <code className="font-mono">/api/v1</code>.
          </p>
        </div>
      ) : (
        <div className="space-y-3">
          {/* Status row */}
          <div className="flex items-center justify-between rounded-lg border border-border/50 px-4 py-3">
            <div className="flex items-center gap-2">
              <span
                className="w-2 h-2 rounded-full"
                style={{ background: health?.status === "ok" || health?.model_loaded ? 'var(--green-2)' : 'var(--gilt)' }}
              />
              <span className="text-sm font-medium capitalize">
                {health?.status ?? "running"}
              </span>
              {health?.version && (
                <span className="text-[11px] font-mono text-muted-foreground">v{health.version}</span>
              )}
            </div>
            {tps != null && (
              <span className="text-xs font-mono" style={{ color: 'var(--ink-soft)' }}>
                {Math.round(tps)} tok/s
              </span>
            )}
          </div>

          {/* Loaded models */}
          {models.length > 0 ? (
            <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30">
              {models.map((m, i) => (
                <div key={i} className="flex items-center gap-3 px-4 py-2.5">
                  <Cpu className="w-3.5 h-3.5 text-muted-foreground/60 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <span className="text-xs font-mono font-medium truncate block">{m.model_name}</span>
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {m.device && (
                      <span className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                            style={{ background: 'var(--green-soft)', color: 'var(--green)' }}>
                        {m.device}
                      </span>
                    )}
                    {m.ctx_size && (
                      <span className="text-[10px] font-mono text-muted-foreground/60">
                        {(m.ctx_size / 1024).toFixed(0)}K ctx
                      </span>
                    )}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <p className="text-xs text-muted-foreground px-1">
              No models currently loaded — Lemonade will load on first inference request.
            </p>
          )}

          {data?.base_url && (
            <p className="text-[11px] font-mono text-muted-foreground/50 px-1">
              {data.base_url}
            </p>
          )}
        </div>
      )}
    </div>
  );
}


// ─── MCP Server card ──────────────────────────────────────────────────────────
// Surfaces the MCP connect URL so the user can paste it into Claude Desktop,
// Cursor, or any MCP client. Currently invisible in the UI despite being live.

interface McpInfo {
  name: string;
  description: string;
  protocol: string;
  endpoint: string;
  tools: string[];
}

function McpCard() {
  const { data, isLoading } = useQuery<McpInfo>({
    queryKey: ["system", "mcp-info"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/mcp`);
      if (!r.ok) throw new Error("mcp info failed");
      return r.json();
    },
    staleTime: 5 * 60_000,
  });

  const [copied, setCopied] = useState(false);

  function copyEndpoint() {
    if (!data?.endpoint) return;
    navigator.clipboard?.writeText(data.endpoint).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    });
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 border-b border-border/50 pb-2">
        <Plug className="w-5 h-5 text-muted-foreground" />
        <h2 className="text-xl font-serif font-medium">MCP Server</h2>
      </div>

      {isLoading ? (
        <Skeleton className="h-24 w-full" />
      ) : data ? (
        <div className="space-y-3">
          <p className="text-sm" style={{ color: 'var(--ink-soft)' }}>
            {data.description ?? "Orivellum is an MCP server — any MCP client can query your knowledge base directly."}
          </p>

          {/* Connect URL */}
          <div className="rounded-lg border border-border/50 px-4 py-3 flex items-center gap-3">
            <Network className="w-4 h-4 shrink-0 text-muted-foreground" />
            <code className="flex-1 text-xs font-mono truncate" title={data.endpoint}>
              {data.endpoint}
            </code>
            <button
              onClick={copyEndpoint}
              className="text-[11px] font-mono px-2 py-1 rounded transition-colors shrink-0"
              style={{
                background: copied ? 'var(--green-soft)' : 'var(--paper-2)',
                color:      copied ? 'var(--green)'      : 'var(--ink-soft)',
              }}
            >
              {copied ? "copied ✓" : "copy"}
            </button>
          </div>

          {/* Available tools */}
          {data.tools?.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {data.tools.map(t => (
                <span key={t} className="text-[10px] font-mono px-2 py-0.5 rounded-full border border-border/50"
                      style={{ color: 'var(--ink-soft)' }}>
                  {t}
                </span>
              ))}
            </div>
          )}

          <p className="text-[11px] font-mono" style={{ color: 'var(--ink-faint)' }}>
            Protocol: {data.protocol} · Paste the URL above into Claude Desktop → Settings → MCP Servers.
          </p>
        </div>
      ) : (
        <div className="text-sm text-muted-foreground border border-dashed rounded-lg p-4 text-center">
          MCP endpoint not responding.
        </div>
      )}
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

// ─── Sparkline (pure SVG, no external deps) ────────────────────────────────────
// Renders a 60-second rolling window of metric percentages (0–100).

const SPARKLINE_CAP = 30; // keeps last 30 readings (covers 60 s at 2 s poll)

function Sparkline({
  data,
  color,
  w = 88,
  h = 28,
}: {
  data: number[];
  color: string;
  w?: number;
  h?: number;
}) {
  if (data.length < 2) return null;
  const pad = 2;
  const inner = h - pad * 2;
  const max = Math.max(...data, 1);
  const pts = data
    .map((v, i) => {
      const x = ((i / (data.length - 1)) * w).toFixed(1);
      const y = (pad + inner - (v / max) * inner).toFixed(1);
      return `${x},${y}`;
    })
    .join(" ");
  return (
    <svg width={w} height={h} style={{ display: "block", overflow: "visible" }} aria-hidden>
      {/* subtle fill */}
      <polyline
        points={`0,${h} ${pts} ${w},${h}`}
        fill={color}
        fillOpacity={0.08}
        stroke="none"
      />
      {/* line */}
      <polyline
        points={pts}
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        opacity={0.85}
      />
      {/* terminal dot */}
      {(() => {
        const last = pts.split(" ").at(-1)?.split(",");
        if (!last) return null;
        return <circle cx={last[0]} cy={last[1]} r={2.5} fill={color} opacity={0.9} />;
      })()}
    </svg>
  );
}

function HardwareCard() {
  const qc = useQueryClient();

  // ── Rolling history (one entry per successful poll) ─────────────────────────
  const [cpuHist,  setCpuHist]  = useState<number[]>([]);
  const [ramHist,  setRamHist]  = useState<number[]>([]);
  const [vramHist, setVramHist] = useState<number[]>([]);

  // ── Subscribe to the jobs cache (JobsCard already fetches it — no extra requests) ──
  const { data: jobsSnap } = useQuery<{ running: number } | null>({
    queryKey: ["system", "jobs"],
    enabled: false,  // read-only subscriber; JobsCard owns the fetch lifecycle
  });
  const isGenerating = (jobsSnap?.running ?? 0) > 0;

  const { data, isLoading, refetch, isFetching } = useQuery<HwData | null>({
    queryKey: ["system", "hardware"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/system/hardware`);
      if (!r.ok) return null;
      return r.json();
    },
    // Poll at 2 s while jobs are running (generation is happening);
    // drop back to 15 s when idle.
    refetchInterval: isGenerating ? 2_000 : 15_000,
    staleTime: isGenerating ? 1_500 : 13_000,
  });

  // Append each successful snapshot to the rolling history.
  useEffect(() => {
    if (!data) return;
    const push = (prev: number[], val: number | null | undefined) =>
      val == null ? prev : [...prev.slice(-(SPARKLINE_CAP - 1)), val];
    setCpuHist(prev => push(prev, data.cpu_percent));
    setRamHist(prev => push(prev, data.ram?.percent ?? null));
    const g0 = data.gpus?.[0];
    const vp =
      g0?.vram_used_mb != null && g0?.vram_total_mb != null && g0.vram_total_mb > 0
        ? (g0.vram_used_mb / g0.vram_total_mb) * 100
        : undefined;
    setVramHist(prev => push(prev, vp));
  }, [data]);

  function barColor(p: number) {
    return p > 90 ? "#ef4444" : p > 70 ? "#f59e0b" : "#22c55e";
  }

  function bar(pct: number | null | undefined) {
    const p = pct ?? 0;
    const color = p > 90 ? 'var(--rust)' : p > 70 ? 'var(--gilt)' : 'var(--green-2)';
    return (
      <div className="h-1.5 w-full bg-muted rounded-full overflow-hidden">
        <div className="h-full rounded-full transition-all duration-700"
             style={{ width: `${Math.min(p, 100)}%`, background: color }} />
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
          {/* LIVE badge — shown while generation is running and poll is accelerated */}
          {isGenerating && (
            <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-mono font-medium border"
                  style={{ background: 'var(--green-soft)', borderColor: 'var(--green-2)', color: 'var(--green-2)' }}>
              <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: 'var(--green-2)' }} />
              LIVE 2s
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
            {cpuHist.length >= 2 && (
              <div className="mt-2">
                <Sparkline data={cpuHist} color={barColor(data.cpu_percent ?? 0)} />
              </div>
            )}
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
            {ramHist.length >= 2 && data.ram && (
              <div className="mt-2">
                <Sparkline data={ramHist} color={barColor(data.ram.percent)} />
              </div>
            )}
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
              const vramPct =
                gpu.vram_used_mb != null && gpu.vram_total_mb != null && gpu.vram_total_mb > 0
                  ? (gpu.vram_used_mb / gpu.vram_total_mb) * 100
                  : null;
              const utilPct = gpu.utilization_percent;
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
                      {utilPct != null && (
                        <span className="text-muted-foreground">{utilPct}% util</span>
                      )}
                      {gpu.temp_c != null && (
                        <span style={{ color: gpu.temp_c > 85 ? 'var(--rust)' : gpu.temp_c > 70 ? 'var(--gilt)' : undefined }}>
                          {gpu.temp_c}°C
                        </span>
                      )}
                    </span>
                  </div>
                  {vramPct != null ? bar(vramPct) : utilPct != null ? bar(utilPct) : null}
                  {/* VRAM sparkline */}
                  {i === 0 && vramHist.length >= 2 && vramPct != null && (
                    <div className="mt-2">
                      <Sparkline data={vramHist} color={barColor(vramPct)} />
                    </div>
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
                <span className="w-1.5 h-1.5 rounded-full shrink-0"
                      style={{ background: j.state === "done" ? 'var(--green-2)' : j.state === "failed" ? 'var(--rust)' : 'var(--gilt)',
                               animation: j.state !== "done" && j.state !== "failed" ? "pulse 1.5s ease-in-out infinite" : undefined }} />

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
                  <span className="text-[10px] font-mono"
                        style={{ color: j.state === "done" ? 'var(--green-2)' : j.state === "failed" ? 'var(--rust)' : 'var(--gilt)' }}>
                    {j.state}
                  </span>

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

  const overallColor = data?.overall === "ok" ? 'var(--green-2)' : data?.overall === "degraded" ? 'var(--gilt)' : 'var(--rust)';

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between border-b border-border/50 pb-2">
        <h2 className="text-xl font-serif font-medium flex items-center gap-2">
          <Brain className="w-5 h-5 text-muted-foreground" />
          LLM Health
          {data && <span className="text-sm font-mono" style={{ color: overallColor }}>{data.overall}</span>}
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
              <span className="text-[10px] font-mono"
                    style={{ color: data.primary.ok ? 'var(--green-2)' : 'var(--rust)' }}>
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
                <span className="text-[10px] font-mono"
                      style={{ color: data.fallback.ok ? 'var(--green-2)' : 'var(--rust)' }}>
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
                  className="w-1.5 h-1.5 rounded-full shrink-0"
                  style={{ background: isOk ? 'var(--green-2)' : isErr ? 'var(--rust)' : 'var(--gilt)',
                           animation: !isOk && !isErr ? "pulse 1.5s ease-in-out infinite" : undefined }}
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
                  <span className="text-[10px] font-mono"
                        style={{ color: isOk ? 'var(--green-2)' : isErr ? 'var(--rust)' : 'var(--gilt)' }}>
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
                  <span className="text-[10px] font-mono"
                        style={{ color: isOk ? 'var(--green-2)' : 'var(--rust)' }}>
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

// ── Browser Alerts card ───────────────────────────────────────────────────────
// Opt-in browser notifications for "document ready" / "audiobook ready".
// Per-device toggle (localStorage) because notification permission itself is
// per-browser; enabling requests permission in the same user gesture.
function BrowserAlertsCard() {
  const supported = notificationsSupported();
  const [enabled, setEnabled] = useState(alertsEnabled());
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    supported ? Notification.permission : "unsupported",
  );

  const handleToggle = async (next: boolean) => {
    if (!next) {
      setAlertsEnabled(false);
      setEnabled(false);
      return;
    }
    const perm = await requestNotificationPermission();
    setPermission(perm);
    if (perm !== "granted") {
      toast.error("Notifications are blocked in your browser", {
        description: "Allow notifications for this site in your browser settings, then try again.",
      });
      return;
    }
    setAlertsEnabled(true);
    setEnabled(true);
    toast.success("Browser alerts on", {
      description: "You'll be notified when documents and audiobooks finish — even if this tab is in the background.",
    });
  };

  return (
    <Card data-testid="browser-alerts-card">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Bell className="w-5 h-5 text-primary mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Browser Alerts</p>
              <p className="text-sm text-muted-foreground mt-1">
                Get a notification when a document or audiobook finishes
                processing, even while this tab is in the background. Applies to
                this device only.
              </p>
              {!supported && (
                <p className="text-xs text-amber-500 mt-2">
                  This browser doesn't support notifications.
                </p>
              )}
              {supported && permission === "denied" && (
                <p className="text-xs text-amber-500 mt-2">
                  Notifications are blocked for this site — re-enable them in
                  your browser's site settings.
                </p>
              )}
            </div>
          </div>
          <Switch
            checked={enabled && permission === "granted"}
            onCheckedChange={handleToggle}
            disabled={!supported || permission === "denied"}
            data-testid="switch-browser-alerts"
          />
        </div>
      </CardContent>
    </Card>
  );
}

// ── Web Push card ─────────────────────────────────────────────────────────────
// True push delivery (VAPID) — notifications arrive even when the PWA is
// closed, which the polling fallback above cannot do on iPhone. Payloads are
// minimal (id + kind + deep link only); the in-app polling fallback always
// stays active regardless of this setting.
function webPushSupported(): boolean {
  return typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window &&
    "Notification" in window;
}

/** base64url → ArrayBuffer (applicationServerKey wants raw bytes on Safari). */
function b64uToBytes(b64u: string): ArrayBuffer {
  const pad = "=".repeat((4 - (b64u.length % 4)) % 4);
  const b64 = (b64u + pad).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(b64);
  const buf = new ArrayBuffer(raw.length);
  const out = new Uint8Array(buf);
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return buf;
}

function WebPushCard() {
  const supported = webPushSupported();
  const [subscribed, setSubscribed] = useState<boolean | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!supported) { setSubscribed(false); return; }
    let alive = true;
    (async () => {
      try {
        const reg = await navigator.serviceWorker.ready;
        const sub = await reg.pushManager.getSubscription();
        if (alive) setSubscribed(!!sub);
      } catch {
        if (alive) setSubscribed(false);
      }
    })();
    return () => { alive = false; };
  }, [supported]);

  const handleToggle = async (next: boolean) => {
    setBusy(true);
    try {
      const reg = await navigator.serviceWorker.ready;
      if (!next) {
        const sub = await reg.pushManager.getSubscription();
        if (sub) {
          await apiFetch(`${API_BASE}/api/system/push/unsubscribe`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ endpoint: sub.endpoint }),
          });
          await sub.unsubscribe();
        }
        setSubscribed(false);
        toast.success("Push notifications off for this device");
        return;
      }
      const perm = await Notification.requestPermission();
      if (perm !== "granted") {
        toast.error("Notifications are blocked in your browser", {
          description: "Allow notifications for this site, then try again.",
        });
        return;
      }
      const cfgResp = await apiFetch(`${API_BASE}/api/system/push/config`);
      if (!cfgResp.ok) throw new Error("push config unavailable");
      const cfg = await cfgResp.json();
      const sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: b64uToBytes(cfg.vapid_public_key as string),
      });
      const saveResp = await apiFetch(`${API_BASE}/api/system/push/subscribe`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(sub.toJSON()),
      });
      if (!saveResp.ok) throw new Error("subscribe failed");
      setSubscribed(true);
      toast.success("Push notifications on", {
        description: "You'll get alerts even when the app is closed. Payloads carry only a kind and link — no content.",
      });
    } catch (e) {
      toast.error("Could not update push subscription", {
        description: e instanceof Error ? e.message : undefined,
      });
    } finally {
      setBusy(false);
    }
  };

  const handleTest = async () => {
    setBusy(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/system/push/test`, { method: "POST" });
      if (r.status === 409) {
        toast.error("No push subscriptions on the server — enable push first.");
      } else if (!r.ok) {
        toast.error("Test push failed");
      } else {
        toast.success("Test push sent", { description: "It may take a few seconds to arrive." });
      }
    } catch {
      toast.error("Test push failed");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card data-testid="web-push-card">
      <CardContent className="p-6">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <Bell className="w-5 h-5 text-primary mt-0.5 shrink-0" />
            <div>
              <p className="font-medium">Push Notifications</p>
              <p className="text-sm text-muted-foreground mt-1">
                Deliver alerts even when the app is fully closed — needed on
                iPhone, where the browser-alert polling above only works while
                the app is open. Pushes carry only an event type and a link,
                never message content.
              </p>
              {!supported && (
                <p className="text-xs text-amber-500 mt-2">
                  This browser doesn't support Web Push. On iPhone, add the app
                  to your Home Screen first (Share → Add to Home Screen).
                </p>
              )}
              {supported && subscribed && (
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-3"
                  onClick={handleTest}
                  disabled={busy}
                  data-testid="button-push-test"
                >
                  Send test push
                </Button>
              )}
            </div>
          </div>
          {subscribed === null ? (
            <Skeleton className="h-6 w-11 rounded-full" />
          ) : (
            <Switch
              checked={!!subscribed}
              onCheckedChange={handleToggle}
              disabled={!supported || busy}
              data-testid="switch-web-push"
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}
