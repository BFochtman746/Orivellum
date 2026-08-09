/**
 * Command hub — entry screen of the Command app (GD-industrial primitives).
 *
 * The bridge: a status board of live ambient tiles (connectivity, semantic
 * search breaker, nightshift, review inbox, governance approvals) over a
 * section list covering everything operational — System, Actions, Review,
 * Governance, Calibration, Backups, Mail steward. Reorganization only: every
 * tile links into an existing page; all data comes from existing endpoints
 * and polling patterns.
 */
import { Link, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { useConnectivity } from "@/lib/useConnectivity";
import {
  Activity,
  AlertTriangle,
  Archive,
  CheckCircle2,
  ChevronRight,
  Gauge,
  Inbox,
  Loader2,
  Mail,
  Moon,
  Settings2,
  Shield,
  Sparkles,
  Terminal,
  XCircle,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Relative time (compact) ──────────────────────────────────────────────────

function relTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const min = Math.round((Date.now() - then) / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.round(hr / 24)}d ago`;
}

// ── Ambient status tile ──────────────────────────────────────────────────────

type Tone = "ok" | "warn" | "bad" | "idle";

const TONE_COLOR: Record<Tone, string> = {
  ok: "var(--gd-success)",
  warn: "var(--gd-caution)",
  bad: "var(--gd-danger)",
  idle: "var(--gd-dim)",
};

function StatusTile({
  href,
  icon: Icon,
  label,
  value,
  detail,
  tone,
  loading,
  testid,
}: {
  href: string;
  icon: typeof Activity;
  label: string;
  value: string;
  detail?: string;
  tone: Tone;
  loading?: boolean;
  testid: string;
}) {
  return (
    <Link href={href} className="gd-tile" data-testid={testid}>
      <div className="flex items-start gap-2.5">
        <Icon className="w-4 h-4 shrink-0 mt-0.5" style={{ color: TONE_COLOR[tone] }} aria-hidden />
        <div className="min-w-0 flex-1">
          <p className="gd-eyebrow">{label}</p>
          {loading ? (
            <Loader2 className="w-4 h-4 mt-1.5 animate-spin" style={{ color: "var(--gd-dim)" }} aria-hidden />
          ) : (
            <>
              <div
                className="mt-1 truncate"
                style={{ fontFamily: "var(--gd-display)", fontSize: 15, fontWeight: 600, letterSpacing: "0.02em", color: TONE_COLOR[tone] }}
              >
                {value}
              </div>
              {detail && (
                <p className="text-[11px] mt-0.5 truncate" style={{ color: "var(--gd-muted)" }}>
                  {detail}
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </Link>
  );
}

// ── Sections ─────────────────────────────────────────────────────────────────

const SECTIONS = [
  { href: "/system",     icon: Settings2, title: "System",      desc: "Health, models, audio, search, maintenance & diagnostics" },
  { href: "/review",     icon: Inbox,     title: "Review inbox", desc: "Approve, reject or defer everything awaiting a decision" },
  { href: "/governance", icon: Shield,    title: "Governance",  desc: "Knowledge approvals, conflicts, regressions & audit chain" },
  { href: "/mcos",       icon: Gauge,     title: "Calibration", desc: "Benchmark suites, runs, prompt lab & LLM telemetry" },
  { href: "/actions",    icon: Terminal,  title: "Actions",     desc: "Run catalogued operations against your Works" },
  { href: "/backups",    icon: Archive,   title: "Backups",     desc: "Snapshots of your database — browse & restore" },
  { href: "/mail",       icon: Mail,      title: "Mail steward", desc: "Inbox triage, drafts & account connection" },
] as const;

// ── Hub ──────────────────────────────────────────────────────────────────────

export default function CommandHub() {
  const [, setLocation] = useLocation();

  // Connectivity — single source of truth hook (health poll already inside)
  const conn = useConnectivity();

  // Semantic search circuit breaker — status endpoint makes no network call
  const { data: embed, isLoading: embedLoading } = useQuery<{ circuit_open: boolean; available_at: number | null }>({
    queryKey: ["command", "embeddings-status"],
    queryFn: () => apiFetch(`${BASE}/system/embeddings/status`).then((r) => r.json()),
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  // Nightshift
  const { data: night, isLoading: nightLoading } = useQuery<{
    running: boolean;
    last_run: { ran_at: string; docs_processed: number; items_added: number } | null;
  }>({
    queryKey: ["command", "nightshift-status"],
    queryFn: () => apiFetch(`${BASE}/system/nightshift/status`).then((r) => r.json()),
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  // Review inbox pending count
  const { data: queue, isLoading: queueLoading } = useQuery<{ count: number }>({
    queryKey: ["command", "review-count"],
    queryFn: () => apiFetch(`${BASE}/review/queue?limit=1`).then((r) => r.json()),
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  // Governance pending approvals
  const { data: gov, isLoading: govLoading } = useQuery<{ pending: number }>({
    queryKey: ["command", "governance-stats"],
    queryFn: () => apiFetch(`${BASE}/governance/stats`).then((r) => r.json()),
    staleTime: 20_000,
    refetchInterval: 30_000,
  });

  const connTone: Tone = !conn.apiReachable ? "bad" : conn.aiReachable ? "ok" : "warn";
  const connValue = !conn.apiReachable ? "Offline" : conn.aiReachable ? "All systems go" : "AI offline";
  const connDetail = !conn.apiReachable
    ? "Server unreachable"
    : conn.aiReachable
      ? "Server & AI reachable"
      : "Server up — AI engine down";

  const embedTone: Tone = embed ? (embed.circuit_open ? "warn" : "ok") : "idle";
  const reviewCount = queue?.count ?? 0;
  const govPending = gov?.pending ?? 0;

  return (
    <div className="pb-10">
      {/* Section header */}
      <div className="flex items-end justify-between gap-3 pt-2 pb-4">
        <div>
          <p className="gd-eyebrow">The bridge</p>
          <h2
            className="mt-1"
            style={{
              fontFamily: "var(--gd-display)",
              fontSize: 24,
              fontWeight: 600,
              letterSpacing: "0.04em",
              textTransform: "uppercase",
              color: "var(--gd-text)",
            }}
          >
            Command
          </h2>
        </div>
        <button className="gd-chip" onClick={() => conn.recheckNow()} data-testid="chip-recheck">
          {conn.isFetching ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : <Activity className="w-3.5 h-3.5" aria-hidden />}
          Recheck
        </button>
      </div>

      {/* Status board */}
      <div className="grid gap-3 grid-cols-2 lg:grid-cols-3">
        <StatusTile
          href="/system"
          icon={connTone === "bad" ? XCircle : connTone === "warn" ? AlertTriangle : CheckCircle2}
          label="Connectivity"
          value={connValue}
          detail={connDetail}
          tone={connTone}
          loading={conn.isFetching && !conn.data && !conn.isError}
          testid="tile-status-connectivity"
        />
        <StatusTile
          href="/system"
          icon={Sparkles}
          label="Semantic search"
          value={embed ? (embed.circuit_open ? "Paused" : "Active") : "Unknown"}
          detail={embed?.circuit_open ? "Breaker open — using keyword search" : "Embeddings healthy"}
          tone={embedTone}
          loading={embedLoading}
          testid="tile-status-embeddings"
        />
        <StatusTile
          href="/system"
          icon={Moon}
          label="Nightshift"
          value={night?.running ? "Running now" : night?.last_run ? relTime(night.last_run.ran_at) : "No runs yet"}
          detail={
            night?.running
              ? "Maintenance in progress"
              : night?.last_run
                ? `${night.last_run.docs_processed} docs · ${night.last_run.items_added} items`
                : "Fires nightly at 3:00 AM"
          }
          tone={night?.running ? "warn" : night?.last_run ? "ok" : "idle"}
          loading={nightLoading}
          testid="tile-status-nightshift"
        />
        <StatusTile
          href="/review"
          icon={Inbox}
          label="Review inbox"
          value={reviewCount > 0 ? `${reviewCount} waiting` : "Clear"}
          detail={reviewCount > 0 ? "Decisions awaiting you" : "Nothing needs a decision"}
          tone={reviewCount > 0 ? "warn" : "ok"}
          loading={queueLoading}
          testid="tile-status-review"
        />
        <StatusTile
          href="/governance"
          icon={Shield}
          label="Governance"
          value={govPending > 0 ? `${govPending} pending` : "Clear"}
          detail={govPending > 0 ? "AI knowledge awaiting approval" : "No pending approvals"}
          tone={govPending > 0 ? "warn" : "ok"}
          loading={govLoading}
          testid="tile-status-governance"
        />
      </div>

      {/* Sections */}
      <p className="gd-eyebrow mt-8 pb-3">Stations</p>
      <div className="grid gap-2">
        {SECTIONS.map(({ href, icon: Icon, title, desc }) => (
          <button
            key={href}
            className="gd-row w-full"
            onClick={() => setLocation(href)}
            data-testid={`row-section-${href.slice(1)}`}
          >
            <Icon className="w-4 h-4 shrink-0" style={{ color: "var(--gd-accent)" }} aria-hidden />
            <span className="flex-1 min-w-0 text-left">
              <span className="block text-[14px] font-medium">{title}</span>
              <span className="block text-[12px] truncate" style={{ color: "var(--gd-muted)" }}>
                {desc}
              </span>
            </span>
            <ChevronRight className="w-4 h-4 shrink-0" style={{ color: "var(--gd-dim)" }} aria-hidden />
          </button>
        ))}
      </div>
    </div>
  );
}
