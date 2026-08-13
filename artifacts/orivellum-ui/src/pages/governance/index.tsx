/**
 * Governance Review Queue — /governance
 *
 * Lists all AI-extracted knowledge items awaiting human approval.
 * Users can approve or dismiss items in bulk or one at a time.
 *
 * Keyboard shortcuts:
 *   j / ↓  move focus down
 *   k / ↑  move focus up
 *   a       approve focused item
 *   r       reject focused item
 *   Escape  clear focus
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import {
  Shield, ThumbsUp, ThumbsDown, RefreshCw, CheckCircle2,
  Sparkles, Link, Keyboard, AlertTriangle, Gauge, ArrowRight,
  TrendingDown, Loader2, Link2, Inbox, ShieldCheck, ShieldAlert,
} from "lucide-react";
import { useLocation } from "wouter";
import { Page, EmptyState, ErrorState, LoadingState, Status, ConfirmAction } from "@/components/primitives";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

interface PendingItem {
  id: string; work_id: string | null; kind: string; text: string;
  subject: string | null; predicate: string | null; object: string | null;
  confidence: number | null; review_status: string;
  work_title: string | null; doc_title: string | null; created_at: string;
}

interface GovernanceStats {
  pending: number; approved: number; rejected: number; auto: number; total: number;
}

// ── Confidence tier ───────────────────────────────────────────────────────────

type ConfidenceTier = "all" | "high" | "medium" | "low";

function getConfidenceTier(c: number | null): "high" | "medium" | "low" {
  if (c == null || c < 0.5) return "low";
  if (c < 0.8) return "medium";
  return "high";
}

const TIER_BADGE_STYLE: Record<string, React.CSSProperties> = {
  high:   { borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", color: "var(--gd-success)", background: "color-mix(in srgb, var(--gd-success) 14%, transparent)" },
  medium: { borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" },
  low:    { borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", color: "var(--gd-danger)", background: "var(--gd-danger-soft)" },
};
const TIER_LABEL: Record<string, string> = { high: "High", medium: "Med", low: "Low" };

// ── Filter constants ──────────────────────────────────────────────────────────

type KindFilter = "all" | "entity" | "claim" | "relationship";

const KIND_FILTERS: { key: KindFilter; label: string }[] = [
  { key: "all",          label: "All" },
  { key: "entity",       label: "Entities" },
  { key: "claim",        label: "Claims" },
  { key: "relationship", label: "Relationships" },
];

const CONF_FILTERS: { key: ConfidenceTier; label: string }[] = [
  { key: "all",    label: "Any" },
  { key: "high",   label: "≥80%" },
  { key: "medium", label: "50–79%" },
  { key: "low",    label: "<50%" },
];

// ── API helpers ───────────────────────────────────────────────────────────────

async function reviewItem(itemId: string, status: "approved" | "rejected") {
  const r = await apiFetch(`${BASE}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status }),
  });
  if (!r.ok) throw new Error("Review failed");
}

async function batchReview(itemIds: string[], status: "approved" | "rejected"): Promise<number> {
  const r = await apiFetch(`${BASE}/governance/batch-review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ item_ids: itemIds, status }),
  });
  if (!r.ok) throw new Error("Batch review failed");
  const data = await r.json();
  return data.updated ?? itemIds.length;
}

// ── Spinner ───────────────────────────────────────────────────────────────────

function Spin({ className }: { className?: string }) {
  return (
    <svg className={`animate-spin ${className ?? "w-3 h-3"}`} viewBox="0 0 24 24" fill="none"
         stroke="currentColor" strokeWidth="2"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>
  );
}

// ── FilterBar ─────────────────────────────────────────────────────────────────

function FilterBar<K extends string>({
  filters, active, counts, onChange,
}: {
  filters: { key: K; label: string }[];
  active: K;
  counts?: Partial<Record<K, number>>;
  onChange: (k: K) => void;
}) {
  return (
    <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg shrink-0">
      {filters.map(({ key, label }) => (
        <button key={key} onClick={() => onChange(key)}
          className={`px-2.5 py-1.5 rounded text-xs font-mono transition-colors ${
            active === key
              ? "bg-background text-foreground shadow-sm font-semibold"
              : "text-muted-foreground hover:text-foreground"
          }`}>
          {label}
          {counts && counts[key] != null && key !== ("all" as unknown as K) && (
            <span className="ml-1 opacity-60">({counts[key]})</span>
          )}
        </button>
      ))}
    </div>
  );
}

// ── Conflicts section ─────────────────────────────────────────────────────────

interface Conflict {
  id: string; conflict_type: string; created_at: string;
  a_id: string; a_text: string; a_subject: string | null; a_confidence: number | null;
  b_id: string; b_text: string; b_subject: string | null; b_confidence: number | null;
  work_id: string | null; work_title: string | null;
}

function ConflictsSection() {
  const qc = useQueryClient();
  const [resolving, setResolving] = useState<Set<string>>(new Set());

  const { data } = useQuery<{ conflicts: Conflict[]; count: number }>({
    queryKey: ["governance", "conflicts"],
    queryFn: () => apiFetch(`${BASE}/governance/conflicts`).then((r) => r.json()),
    staleTime: 30_000,
  });

  const conflicts = data?.conflicts ?? [];
  if (conflicts.length === 0) return null;

  const resolve = async (id: string, resolution: "keep_a" | "keep_b" | "keep_both") => {
    setResolving((s) => new Set([...s, id]));
    try {
      const r = await apiFetch(`${BASE}/governance/conflicts/${id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resolution }),
      });
      if (!r.ok) throw new Error();
      toast.success(resolution === "keep_both" ? "Kept both claims" : "Conflict resolved");
      qc.invalidateQueries({ queryKey: ["governance"] });
    } catch {
      toast.error("Could not resolve conflict");
    } finally {
      setResolving((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4" style={{ color: "var(--gd-bronze)" }} />
        <h2 className="text-sm font-mono font-semibold" style={{ color: "var(--gd-bronze)" }}>
          Contradicting claims ({conflicts.length})
        </h2>
      </div>
      <div className="space-y-2">
        {conflicts.map((c) => {
          const busy = resolving.has(c.id);
          return (
            <div key={c.id} className="rounded-lg border p-3 space-y-2" style={{ borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", background: "var(--gd-bronze-soft)" }}>
              <div className="flex items-center gap-2 text-[11px] font-mono text-muted-foreground">
                <Badge variant="outline" className="text-[10px]" style={{ borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", color: "var(--gd-bronze)" }}>
                  {c.conflict_type === "negation" ? "Negation" : "Conflicting values"}
                </Badge>
                {c.work_title && <span>{c.work_title}</span>}
              </div>
              <div className="grid sm:grid-cols-2 gap-2">
                {[
                  { label: "A", text: c.a_text, conf: c.a_confidence, res: "keep_a" as const },
                  { label: "B", text: c.b_text, conf: c.b_confidence, res: "keep_b" as const },
                ].map(({ label, text, conf, res }) => (
                  <div key={label} className="rounded border border-border/50 bg-background p-2.5 flex flex-col gap-2">
                    <p className="text-xs font-serif leading-relaxed flex-1">{text}</p>
                    <div className="flex items-center justify-between">
                      <span className="text-[10px] font-mono text-muted-foreground">
                        {conf != null ? `${Math.round(conf * 100)}% conf` : "—"}
                      </span>
                      <Button size="sm" variant="outline" disabled={busy}
                        onClick={() => resolve(c.id, res)}
                        className="h-6 text-[11px] gap-1 text-[var(--gd-success)] hover:text-[var(--gd-success)] hover:bg-[color-mix(in_srgb,var(--gd-success)_14%,transparent)]"
                        style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}>
                        {busy ? <Spin /> : <ThumbsUp className="w-3 h-3" />} Keep {label}
                      </Button>
                    </div>
                  </div>
                ))}
              </div>
              <div className="flex justify-end">
                <Button size="sm" variant="ghost" disabled={busy}
                  onClick={() => resolve(c.id, "keep_both")}
                  className="h-6 text-[11px] text-muted-foreground">
                  Keep both (not a real conflict)
                </Button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Benchmark regressions ───────────────────────────────────────────────────────

interface Regression {
  run_id: string;
  benchmark_id: string;
  benchmark_name: string;
  finished_at: string | null;
  avg_score: number | null;
  delta: number | null;
  acknowledged: boolean;
  kind?: "benchmark" | "prompt";
  prompt_name?: string | null;
  prompt_version?: number | null;
}

function fmtRegTime(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function RegressionsSection() {
  const qc = useQueryClient();
  const [, navigate] = useLocation();
  const [showAck, setShowAck] = useState(false);
  const [acking, setAcking] = useState<Set<string>>(new Set());

  const { data, isLoading, isError, refetch } = useQuery<{ regressions: Regression[] }>({
    queryKey: ["mcos", "regressions"],
    queryFn: () => apiFetch(`${BASE}/mcos/regressions?limit=20`).then((r) => {
      if (!r.ok) throw new Error("Failed to load regressions");
      return r.json();
    }),
    staleTime: 30_000,
  });

  const all = data?.regressions ?? [];
  const visible = showAck ? all : all.filter((r) => !r.acknowledged);
  const ackCount = all.filter((r) => r.acknowledged).length;

  const acknowledge = async (runId: string) => {
    setAcking((s) => new Set([...s, runId]));
    try {
      const r = await apiFetch(`${BASE}/mcos/regressions/${runId}/ack`, { method: "POST" });
      if (!r.ok) throw new Error();
      toast.success("Regression acknowledged");
      qc.invalidateQueries({ queryKey: ["mcos", "regressions"] });
    } catch {
      toast.error("Could not acknowledge regression");
    } finally {
      setAcking((s) => { const n = new Set(s); n.delete(runId); return n; });
    }
  };

  return (
    <div className="space-y-2.5">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <TrendingDown className="w-4 h-4" style={{ color: "var(--gd-danger)" }} />
          <h2 className="text-sm font-mono font-semibold" style={{ color: "var(--gd-danger)" }}>
            Benchmark Regressions{visible.length > 0 ? ` (${visible.length})` : ""}
          </h2>
        </div>
        <div className="flex items-center gap-2">
          {ackCount > 0 && (
            <Button size="sm" variant="ghost" className="h-6 text-[11px] text-muted-foreground"
              onClick={() => setShowAck((v) => !v)}>
              {showAck ? "Hide acknowledged" : `Show acknowledged (${ackCount})`}
            </Button>
          )}
          <Button size="sm" variant="ghost" className="h-6 text-[11px] text-muted-foreground gap-1"
            onClick={() => navigate("/mcos")}>
            <Gauge className="w-3 h-3" /> Calibration <ArrowRight className="w-3 h-3" />
          </Button>
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-2">
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : isError ? (
        <div className="rounded-lg border border-dashed border-border/60 p-4 flex items-center justify-between gap-3">
          <p className="text-xs text-muted-foreground">Could not load benchmark regressions.</p>
          <Button size="sm" variant="outline" className="h-7 text-[11px] gap-1" onClick={() => refetch()}>
            <RefreshCw className="w-3 h-3" /> Retry
          </Button>
        </div>
      ) : visible.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border/50 p-6 text-center text-muted-foreground">
          <CheckCircle2 className="w-6 h-6 mx-auto mb-2 opacity-40" style={{ color: "var(--gd-success)" }} />
          <p className="text-sm">No benchmark regressions</p>
        </div>
      ) : (
        <div className="space-y-2">
          {visible.map((reg) => {
            const busy = acking.has(reg.run_id);
            const deltaPts = reg.delta != null ? Math.round(reg.delta * 100) : null;
            return (
              <div key={reg.run_id}
                className={`rounded-lg border p-3 flex items-center gap-3 ${
                  reg.acknowledged ? "border-border/50 bg-muted/10 opacity-60" : ""
                }`} style={!reg.acknowledged ? { borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", background: "var(--gd-danger-soft)" } : undefined}>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    {reg.acknowledged && <CheckCircle2 className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-success)" }} />}
                    <Badge variant="outline"
                      className="text-[10px] shrink-0"
                      style={reg.kind === "prompt" ? { borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", color: "var(--gd-bronze)" } : undefined}
                    >
                      {reg.kind === "prompt" ? "Prompt" : "Benchmark"}
                    </Badge>
                    <button
                      onClick={() => navigate("/mcos")}
                      className="text-sm font-medium truncate hover:underline text-left">
                      {reg.kind === "prompt" && reg.prompt_name
                        ? `${reg.prompt_name}${reg.prompt_version != null ? ` v${reg.prompt_version}` : ""}`
                        : reg.benchmark_name}
                    </button>
                  </div>
                  <div className="flex items-center gap-3 mt-1 text-[11px] font-mono text-muted-foreground">
                    <span>{fmtRegTime(reg.finished_at)}</span>
                    <span>{reg.avg_score != null ? `${Math.round(reg.avg_score * 100)}%` : "—"}</span>
                    {deltaPts != null && (
                      <span className="font-semibold" style={{ color: "var(--gd-danger)" }}>
                        {deltaPts > 0 ? "+" : ""}{deltaPts} pts
                      </span>
                    )}
                  </div>
                </div>
                {!reg.acknowledged && (
                  <Button size="sm" variant="outline" disabled={busy}
                    onClick={() => acknowledge(reg.run_id)}
                    className="h-7 text-[11px] gap-1 shrink-0">
                    {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                    Acknowledge
                  </Button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Audit-chain integrity ──────────────────────────────────────────────────────

interface AuditChainStatus {
  ok: boolean;
  checked_rows: number;
  status: "intact" | "broken";
  reason?: string;
}

function AuditChainSection() {
  const { data, isLoading, isError, refetch, isFetching } =
    useQuery<AuditChainStatus>({
      queryKey: ["governance", "audit-chain"],
      queryFn: () =>
        apiFetch(`${BASE}/governance/audit-chain`).then((r) => r.json()),
      staleTime: 60_000,
      refetchInterval: 120_000,
    });

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-2">
          <Link2 className="w-4 h-4 text-muted-foreground" />
          <h2 className="text-sm font-mono font-semibold text-foreground/80">
            Audit Chain Integrity
          </h2>
        </div>
        <button
          onClick={() => refetch()}
          disabled={isFetching}
          className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors"
        >
          <RefreshCw className={`w-3 h-3 ${isFetching ? "animate-spin" : ""}`} />
          Check now
        </button>
      </div>

      {isLoading ? (
        <div className="h-12 rounded-lg bg-muted/20 animate-pulse" />
      ) : isError ? (
        <div className="rounded-lg border border-dashed border-border/60 p-3 flex items-center gap-2 text-xs text-muted-foreground">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-bronze)" }} />
          Could not reach audit-chain endpoint.
        </div>
      ) : data?.ok ? (
        <div className="rounded-lg border p-3 flex items-center gap-2.5" style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)", background: "color-mix(in srgb, var(--gd-success) 14%, transparent)" }}>
          <ShieldCheck className="w-4 h-4 shrink-0" style={{ color: "var(--gd-success)" }} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-medium" style={{ color: "var(--gd-success)" }}>
              Chain intact
            </p>
            <p className="text-[11px] font-mono mt-0.5" style={{ color: "color-mix(in srgb, var(--gd-success) 70%, transparent)" }}>
              {data.checked_rows.toLocaleString()} row{data.checked_rows !== 1 ? "s" : ""} verified — no tampering detected
            </p>
          </div>
        </div>
      ) : (
        <div className="rounded-lg border p-3 flex items-start gap-2.5" style={{ borderColor: "color-mix(in srgb, var(--gd-danger) 40%, transparent)", background: "var(--gd-danger-soft)" }}>
          <ShieldAlert className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--gd-danger)" }} />
          <div className="flex-1 min-w-0">
            <p className="text-sm font-semibold" style={{ color: "var(--gd-danger)" }}>
              Chain integrity broken
            </p>
            <p className="text-[11px] font-mono mt-0.5 break-words" style={{ color: "color-mix(in srgb, var(--gd-danger) 80%, transparent)" }}>
              {data?.reason ?? "Unknown reason"}
            </p>
            <p className="text-[10px] font-mono mt-1" style={{ color: "color-mix(in srgb, var(--gd-danger) 60%, transparent)" }}>
              {(data?.checked_rows ?? 0).toLocaleString()} rows checked
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Outbox backlog ─────────────────────────────────────────────────────────────

interface OutboxEvent {
  id: string;
  event_type: string;
  object_id: string | null;
  object_type: string | null;
  created_at: string;
  dispatched_at: string | null;
}

function OutboxSection() {
  const { data } = useQuery<{ events: OutboxEvent[]; count: number }>({
    queryKey: ["governance", "outbox"],
    queryFn: () =>
      apiFetch(`${BASE}/governance/outbox?pending_only=true&limit=20`).then(
        (r) => r.json()
      ),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const events = data?.events ?? [];
  // Only surface the section when there's a backlog — a drained outbox is expected.
  if (events.length === 0) return null;

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <Inbox className="w-4 h-4" style={{ color: "var(--gd-bronze)" }} />
        <h2 className="text-sm font-mono font-semibold" style={{ color: "var(--gd-bronze)" }}>
          Outbox Backlog ({data?.count ?? events.length})
        </h2>
      </div>

      <div className="rounded-lg border divide-y divide-border/30" style={{ borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", background: "var(--gd-bronze-soft)" }}>
        {events.slice(0, 10).map((ev) => (
          <div key={ev.id} className="px-3 py-2 flex items-center gap-3 text-xs font-mono">
            <span className="shrink-0 px-1.5 py-0.5 rounded text-[10px]" style={{ background: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}>
              {ev.event_type}
            </span>
            <span className="flex-1 truncate text-muted-foreground">
              {ev.object_type ?? "—"} {ev.object_id ? `· ${ev.object_id.slice(0, 12)}…` : ""}
            </span>
            <span className="shrink-0 text-muted-foreground/60 text-[10px]">
              {new Date(ev.created_at).toLocaleTimeString(undefined, {
                hour: "2-digit", minute: "2-digit",
              })}
            </span>
          </div>
        ))}
        {(data?.count ?? 0) > 10 && (
          <p className="px-3 py-2 text-[11px] font-mono text-muted-foreground/60">
            … and {(data!.count - 10).toLocaleString()} more
          </p>
        )}
      </div>

      <p className="text-[10px] font-mono text-muted-foreground/50">
        The Night Scriptorium drains the outbox automatically on its next run.
      </p>
    </div>
  );
}

// ── Open findings (M0.2 blockers) ─────────────────────────────────────────────

interface Finding {
  id: string;
  object_id: string;
  object_type: string;
  kind: string;
  description: string;
  severity: "info" | "warning" | "high" | "critical";
  state: "open" | "resolved";
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
}

const SEVERITY_BADGE_STYLE: Record<string, React.CSSProperties> = {
  critical: { borderColor: "color-mix(in srgb, var(--gd-danger) 60%, transparent)", color: "var(--gd-danger)", background: "var(--gd-danger-soft)" },
  high:     { borderColor: "color-mix(in srgb, var(--gd-danger) 40%, transparent)", color: "var(--gd-danger)", background: "var(--gd-danger-soft)" },
  warning:  { borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" },
  info:     {},
};

function FindingsSection() {
  const qc = useQueryClient();
  const [resolving, setResolving] = useState<Set<string>>(new Set());

  const { data, refetch } = useQuery<{ findings: Finding[]; count: number }>({
    queryKey: ["governance", "findings", "open"],
    queryFn: () =>
      apiFetch(`${BASE}/governance/findings?state=open&limit=50`).then(
        (r) => r.json()
      ),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const findings = data?.findings ?? [];
  // Only show when there are open findings
  if (findings.length === 0) return null;

  const blocking = findings.filter(
    (f) => f.severity === "high" || f.severity === "critical"
  );
  const advisory = findings.filter(
    (f) => f.severity === "warning" || f.severity === "info"
  );

  const resolve = async (fid: string) => {
    setResolving((s) => new Set([...s, fid]));
    try {
      const r = await apiFetch(`${BASE}/governance/findings/${fid}/resolve`, {
        method: "PATCH",
      });
      if (!r.ok) throw new Error("Resolve failed");
      toast.success("Finding resolved");
      qc.invalidateQueries({ queryKey: ["governance", "findings"] });
      refetch();
    } catch {
      toast.error("Could not resolve finding");
    } finally {
      setResolving((s) => { const n = new Set(s); n.delete(fid); return n; });
    }
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <AlertTriangle className="w-4 h-4" style={{ color: "var(--gd-bronze)" }} />
        <h2 className="text-sm font-mono font-semibold" style={{ color: "var(--gd-bronze)" }}>
          Open Findings ({findings.length})
          {blocking.length > 0 && (
            <span className="ml-2 text-[10px] font-normal" style={{ color: "var(--gd-danger)" }}>
              · {blocking.length} blocking
            </span>
          )}
        </h2>
      </div>

      <div className="space-y-1.5">
        {findings.map((f) => {
          const busy = resolving.has(f.id);
          return (
            <div
              key={f.id}
              className="rounded-lg border p-3 flex items-start gap-3"
              style={(f.severity === "critical" || f.severity === "high")
                ? { borderColor: "color-mix(in srgb, var(--gd-danger) 28%, transparent)", background: "var(--gd-danger-soft)" }
                : { borderColor: undefined, background: undefined }}
            >
              <div className="flex-1 min-w-0 space-y-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono border" style={SEVERITY_BADGE_STYLE[f.severity] ?? SEVERITY_BADGE_STYLE.info}>
                    {f.severity}
                  </span>
                  <span className="text-[10px] font-mono text-muted-foreground/70 bg-muted/40 px-1.5 py-0.5 rounded border border-border/30">
                    {f.object_type}
                  </span>
                  {(f.severity === "high" || f.severity === "critical") && (
                    <span className="text-[10px] font-mono" style={{ color: "var(--gd-danger)" }}>
                      blocks transitions
                    </span>
                  )}
                </div>
                <p className="text-sm text-foreground/80 leading-snug">{f.description}</p>
                <p className="text-[10px] font-mono text-muted-foreground/50">
                  {f.object_id.slice(0, 12)}… · {new Date(f.created_at).toLocaleDateString()}
                </p>
              </div>
              <Button
                size="sm"
                variant="outline"
                disabled={busy}
                onClick={() => resolve(f.id)}
                className="h-7 text-[11px] gap-1 shrink-0 text-[var(--gd-success)] hover:text-[var(--gd-success)] hover:bg-[color-mix(in_srgb,var(--gd-success)_14%,transparent)]"
                style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}
              >
                {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                Resolve
              </Button>
            </div>
          );
        })}
      </div>

      {advisory.length > 0 && blocking.length > 0 && (
        <p className="text-[10px] font-mono text-muted-foreground/50">
          {advisory.length} advisory finding{advisory.length !== 1 ? "s" : ""} do not block transitions.
        </p>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function GovernancePage() {
  const [, navigate] = useLocation();
  const qc = useQueryClient();

  const [reviewing, setReviewing]   = useState<Set<string>>(new Set());
  const [kindFilter, setKindFilter] = useState<KindFilter>("all");
  const [confFilter, setConfFilter] = useState<ConfidenceTier>("all");
  const [focusedIdx, setFocusedIdx] = useState<number | null>(null);
  const [bulkPending, setBulkPending] = useState(false);

  const itemRefs = useRef<(HTMLDivElement | null)[]>([]);

  // ── Data ─────────────────────────────────────────────────────────────────────

  const { data: statsData } = useQuery<GovernanceStats>({
    queryKey: ["governance", "stats"],
    queryFn: () => apiFetch(`${BASE}/governance/stats`).then((r) => r.json()),
    staleTime: 30_000, refetchInterval: 60_000,
  });

  const { data, isLoading, isError, refetch, isFetching } = useQuery<{ items: PendingItem[]; count: number }>({
    queryKey: ["governance", "pending"],
    queryFn: () => apiFetch(`${BASE}/governance/pending?limit=500`).then((r) => {
      if (!r.ok) throw new Error("Failed to load pending items");
      return r.json();
    }),
    staleTime: 30_000,
  });

  const invalidate = useCallback(
    () => qc.invalidateQueries({ queryKey: ["governance"] }),
    [qc],
  );

  // ── Filtering ─────────────────────────────────────────────────────────────

  const allItems = data?.items ?? [];

  const filtered = allItems.filter((i) => {
    if (kindFilter !== "all" && i.kind !== kindFilter) return false;
    if (confFilter !== "all" && getConfidenceTier(i.confidence) !== confFilter) return false;
    return true;
  });

  const kindCounts = Object.fromEntries(
    KIND_FILTERS.filter(f => f.key !== "all").map(f => [f.key, allItems.filter(i => i.kind === f.key).length])
  ) as Partial<Record<KindFilter, number>>;

  // Group by work for display
  const byWork = filtered.reduce<Record<string, { title: string; items: PendingItem[] }>>(
    (acc, item) => {
      const key = item.work_id ?? "__unlinked__";
      if (!acc[key]) acc[key] = { title: item.work_title ?? "Unlinked", items: [] };
      acc[key].items.push(item);
      return acc;
    },
    {},
  );

  // ── Actions ───────────────────────────────────────────────────────────────

  const handleReview = useCallback(async (id: string, status: "approved" | "rejected") => {
    setReviewing((s) => new Set([...s, id]));
    try {
      await reviewItem(id, status);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      invalidate();
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing((s) => { const n = new Set(s); n.delete(id); return n; });
    }
  }, [invalidate]);

  const handleBatchApprove = useCallback(async (items: PendingItem[]) => {
    if (items.length === 0) return;
    setBulkPending(true);
    try {
      const updated = await batchReview(items.map((i) => i.id), "approved");
      toast.success(`Approved ${updated} item${updated !== 1 ? "s" : ""}`);
      setFocusedIdx(null);
      invalidate();
    } catch {
      toast.error("Batch approval failed");
    } finally {
      setBulkPending(false);
    }
  }, [invalidate]);

  // ── Keyboard navigation ───────────────────────────────────────────────────

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;

      if (e.key === "j" || e.key === "ArrowDown") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = i == null ? 0 : Math.min(i + 1, filtered.length - 1);
          itemRefs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return next;
        });
      } else if (e.key === "k" || e.key === "ArrowUp") {
        e.preventDefault();
        setFocusedIdx((i) => {
          const next = i == null ? 0 : Math.max(i - 1, 0);
          itemRefs.current[next]?.scrollIntoView({ block: "nearest", behavior: "smooth" });
          return next;
        });
      } else if (e.key === "a" && focusedIdx != null) {
        e.preventDefault();
        const item = filtered[focusedIdx];
        if (item) { handleReview(item.id, "approved"); setFocusedIdx((i) => (i != null && i < filtered.length - 1 ? i : i)); }
      } else if (e.key === "r" && focusedIdx != null) {
        e.preventDefault();
        const item = filtered[focusedIdx];
        if (item) handleReview(item.id, "rejected");
      } else if (e.key === "Escape") {
        setFocusedIdx(null);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [filtered, focusedIdx, handleReview]);

  // Reset focus when filters change
  useEffect(() => { setFocusedIdx(null); }, [kindFilter, confFilter]);

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <Page
      wide
      eyebrow="Nothing unverified becomes true"
      title="Review"
      actions={
        <>
          <span className="hidden sm:flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground/50 border border-border/30 rounded px-2 py-1">
            <Keyboard className="w-3 h-3" /> j/k navigate · a approve · r reject
          </span>
          <button onClick={() => refetch()} disabled={isFetching}
            className="flex items-center gap-1.5 min-h-11 text-xs font-mono text-muted-foreground hover:text-foreground transition-colors">
            <RefreshCw className={`w-3.5 h-3.5 ${isFetching ? "animate-spin" : ""}`} />
            Refresh
          </button>
        </>
      }
    >
      <p className="text-[13px] -mt-2 text-muted-foreground">
        Approve what becomes canon.
      </p>

      {/* Stats strip */}
      {statsData && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          {[
            { label: "Pending",  value: statsData.pending,  cls: statsData.pending > 0 ? "" : "text-muted-foreground", style: statsData.pending > 0 ? { color: "var(--gd-bronze)" } : undefined },
            { label: "Approved", value: statsData.approved, cls: "", style: { color: "var(--gd-success)" } as React.CSSProperties },
            { label: "Rejected", value: statsData.rejected, cls: "", style: { color: "var(--gd-danger)" } as React.CSSProperties },
            { label: "Total",    value: statsData.total,    cls: "text-muted-foreground", style: undefined },
          ].map(({ label, value, cls, style }) => (
            <div key={label} className="p-3 rounded-lg border border-border/50 bg-muted/10 text-center">
              <p className={`text-2xl font-mono font-bold ${cls}`} style={style}>{value}</p>
              <p className="text-[11px] font-mono text-muted-foreground uppercase tracking-wide mt-0.5">{label}</p>
            </div>
          ))}
        </div>
      )}

      {/* Audit-chain integrity */}
      <AuditChainSection />

      {/* Outbox backlog — only shown when events are pending */}
      <OutboxSection />

      {/* Contradicting claims */}
      <ConflictsSection />

      <RegressionsSection />

      {/* Open findings — only shown when blockers exist */}
      <FindingsSection />

      {/* Filters + bulk approve */}
      {allItems.length > 0 && (
        <div className="space-y-2.5">
          <div className="flex items-center gap-2 flex-wrap">
            <FilterBar filters={KIND_FILTERS} active={kindFilter} counts={kindCounts} onChange={setKindFilter} />
            <FilterBar filters={CONF_FILTERS} active={confFilter} onChange={(k) => setConfFilter(k as ConfidenceTier)} />
          </div>

          {filtered.length > 0 && (
            <div className="flex items-center justify-between">
              <p className="text-xs font-mono text-muted-foreground">
                {filtered.length} item{filtered.length !== 1 ? "s" : ""}
                {focusedIdx != null && (
                  <span className="text-primary/60"> · {focusedIdx + 1}/{filtered.length} focused</span>
                )}
              </p>
              <ConfirmAction
                title={`Approve all ${filtered.length} item${filtered.length !== 1 ? "s" : ""}?`}
                consequence="Approved items become canon and feed downstream knowledge. You can still reject them individually later."
                confirmLabel="Approve all"
                onConfirm={() => handleBatchApprove(filtered)}
                trigger={
                  <Button size="sm" variant="outline"
                    disabled={bulkPending}
                    className="gap-1.5 min-h-11 text-xs text-[var(--gd-success)] hover:text-[var(--gd-success)] hover:bg-[color-mix(in_srgb,var(--gd-success)_14%,transparent)]"
                    style={{ borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}>
                    {bulkPending
                      ? <><Spin /> Approving…</>
                      : <><CheckCircle2 className="w-3.5 h-3.5" /> Approve all ({filtered.length})</>}
                  </Button>
                }
              />
            </div>
          )}
        </div>
      )}

      {/* Content */}
      {isLoading ? (
        <LoadingState rows={5} label="Loading pending items" />
      ) : isError ? (
        <ErrorState
          title="Could not load pending items"
          detail="The review queue is unavailable right now."
          onRetry={() => refetch()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={<CheckCircle2 />}
          title={allItems.length > 0 ? "No items match the current filters" : "No items pending review"}
          description={
            allItems.length > 0
              ? "Try a different filter combination."
              : "AI-extracted knowledge will appear here when documents are processed with AI extraction enabled."
          }
          action={
            allItems.length === 0 ? (
              <Button variant="link" size="sm" className="text-muted-foreground" onClick={() => navigate("/system")}>
                Configure AI extraction →
              </Button>
            ) : (
              <Button variant="link" size="sm" className="text-muted-foreground"
                onClick={() => { setKindFilter("all"); setConfFilter("all"); }}>
                Clear filters
              </Button>
            )
          }
        />
      ) : (
        <div className="space-y-6">
          {Object.entries(byWork).map(([workId, { title, items: workItems }]) => (
            <div key={workId} className="space-y-2">

              {/* Work header */}
              <div className="flex items-center gap-2 pb-1 border-b border-border/30">
                <h3 className="text-sm font-mono font-semibold text-muted-foreground truncate">{title}</h3>
                <Badge variant="outline" className="text-[10px] font-mono shrink-0">{workItems.length}</Badge>
                {workId !== "__unlinked__" && (
                  <button onClick={() => navigate(`/works/${workId}`)}
                    className="text-[11px] font-mono text-muted-foreground/60 hover:text-primary transition-colors flex items-center gap-1 shrink-0">
                    <Link className="w-3 h-3" /> Open Work
                  </button>
                )}
                <div className="ml-auto shrink-0">
                  <Button size="sm" variant="ghost"
                    onClick={() => handleBatchApprove(workItems)}
                    disabled={bulkPending}
                    className="h-6 px-2 text-[10px] font-mono gap-1 text-[var(--gd-success)] hover:text-[var(--gd-success)] hover:bg-[color-mix(in_srgb,var(--gd-success)_14%,transparent)]">
                    <CheckCircle2 className="w-3 h-3" /> Approve {workItems.length}
                  </Button>
                </div>
              </div>

              {/* Items */}
              {workItems.map((item) => {
                const globalIdx = filtered.indexOf(item);
                const isFocused = focusedIdx === globalIdx;
                const isReviewing = reviewing.has(item.id);
                const tier = getConfidenceTier(item.confidence);

                return (
                  <div key={item.id}
                    ref={(el) => { itemRefs.current[globalIdx] = el; }}
                    onClick={() => setFocusedIdx(isFocused ? null : globalIdx)}
                    className={`flex items-start gap-3 p-3.5 rounded-lg border transition-all cursor-pointer select-none ${
                      isFocused ? "border-primary/40 bg-primary/5 ring-1 ring-primary/20 shadow-sm" : ""
                    }`}
                    style={!isFocused ? { borderColor: "color-mix(in srgb, var(--gd-bronze) 40%, transparent)", background: "color-mix(in srgb, var(--gd-bronze-soft) 50%, transparent)" } : undefined}
                  >
                    <Sparkles className="w-3.5 h-3.5 mt-0.5 shrink-0" style={{ color: "var(--gd-bronze)" }} />
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1.5 flex-wrap">
                        <Badge variant="outline"
                          className="text-[10px] uppercase font-mono border-primary/30 text-primary shrink-0">
                          {item.kind}
                        </Badge>
                        {item.confidence != null && (
                          <Badge variant="outline"
                            className="text-[10px] font-mono shrink-0" style={TIER_BADGE_STYLE[tier]}>
                            {TIER_LABEL[tier]} {Math.round(item.confidence * 100)}%
                          </Badge>
                        )}
                        {item.doc_title && (
                          <span className="text-[10px] font-mono text-muted-foreground/60 truncate">
                            {item.doc_title}
                          </span>
                        )}
                        {isFocused && (
                          <span className="text-[10px] font-mono text-primary/50 ml-auto hidden sm:block shrink-0">
                            a · r
                          </span>
                        )}
                      </div>
                      {item.kind === "relationship" && item.subject && item.predicate && item.object ? (
                        <p className="text-sm font-mono leading-relaxed">
                          <span className="font-semibold text-primary">{item.subject}</span>
                          {" "}<span className="text-muted-foreground italic">{item.predicate}</span>{" "}
                          <span className="font-semibold">{item.object}</span>
                        </p>
                      ) : (
                        <p className="text-sm leading-snug">{item.text}</p>
                      )}
                    </div>

                    {/* Action buttons */}
                    <div className="flex items-center gap-1 shrink-0">
                      <button
                        disabled={isReviewing}
                        onClick={(e) => { e.stopPropagation(); handleReview(item.id, "approved"); }}
                        title="Approve (a)"
                        className="p-1.5 rounded transition-colors text-muted-foreground hover:text-[var(--gd-success)] hover:bg-[color-mix(in_srgb,var(--gd-success)_14%,transparent)] disabled:opacity-40"
                      >
                        {isReviewing ? <Spin /> : <ThumbsUp className="w-3.5 h-3.5" />}
                      </button>
                      <button
                        disabled={isReviewing}
                        onClick={(e) => { e.stopPropagation(); handleReview(item.id, "rejected"); }}
                        title="Reject (r)"
                        className="p-1.5 rounded transition-colors text-muted-foreground hover:text-[var(--gd-danger)] hover:bg-[var(--gd-danger-soft)] disabled:opacity-40"
                      >
                        <ThumbsDown className="w-3.5 h-3.5" />
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      )}

    </Page>
  );
}
