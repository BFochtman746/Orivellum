/**
 * Writing Architect — /architect
 *
 * The decomposition bench for the WRITING_ARCHITECT archive: upload the
 * zip, run the decomposer, and inspect what it produced — the coverage
 * inventory (every file explicitly extracted / deduped / deferred), the
 * machine-readable doctrine records (engine contracts, policies, voice
 * envelope, POSITION spec…), and the canon-fact proposal queue.
 *
 * Authority rule (M0): nothing here writes canon. Every proposal stays
 * 'proposed' until it is explicitly approved or rejected — and approval
 * here is a disposition, not a canon insert; ratified facts are minted
 * through the Canon/Chancery paths.
 */
import { useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { useGdDark } from "@/lib/useGdDark";
import { toast } from "sonner";
import {
  DraftingCompass, Loader2, Upload, Play, FileArchive, ScrollText,
  ThumbsUp, ThumbsDown, RotateCcw, ChevronDown, ChevronRight,
  CheckCircle2, AlertTriangle, Landmark, GitBranch, Sparkles,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

/* ── types ─────────────────────────────────────────────────────────────── */

interface Coverage {
  total_docs: number;
  by_status: Record<string, number>;
  by_layer: { layer: string; status: string; n: number }[];
  records_by_type: Record<string, number>;
  proposals: Record<string, number>;
  fully_accounted: boolean;
}

interface InventoryItem {
  id: string;
  rel_path: string;
  filename: string;
  layer: string;
  size_bytes: number;
  duplicate_of: string | null;
  status: string;
  reason: string | null;
}

interface RecordRow {
  id: string;
  record_type: string;
  name: string;
  source_path: string;
  source_note: string | null;
  created_at: string;
}

interface Proposal {
  id: string;
  fact_title: string;
  fact_text: string;
  classification: string;
  scope: string;
  source_path: string;
  source_location: string;
  status: string;
  decided_at: string | null;
}

type Tab = "archive" | "doctrine" | "proposals";

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  extracted: { color: "var(--green-2)", borderColor: "var(--green-2)", background: "var(--green-soft)" },
  deduped: { color: "var(--ink-soft)", borderColor: "var(--line-2)" },
  deferred: { color: "var(--rust)", borderColor: "var(--rust)", background: "var(--rust-soft)" },
};

const CLASS_ICON: Record<string, typeof Landmark> = {
  HISTORICAL: Landmark, INFERRED: GitBranch, INVENTED: Sparkles,
};
const CLASS_STYLE: Record<string, React.CSSProperties> = {
  HISTORICAL: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
  INFERRED: { color: "var(--green-2)", borderColor: "var(--green-2)", background: "var(--green-soft)" },
  INVENTED: { color: "var(--rust)", borderColor: "var(--rust)", background: "var(--rust-soft)" },
};

function prettyType(t: string) {
  return t.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function fmtBytes(n: number) {
  if (n >= 1024 * 1024) return `${(n / (1024 * 1024)).toFixed(1)} MB`;
  if (n >= 1024) return `${Math.round(n / 1024)} KB`;
  return `${n} B`;
}

/* ── page ──────────────────────────────────────────────────────────────── */

export default function WritingArchitectPage() {
  useGdDark();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("archive");

  const { data: coverage } = useQuery<Coverage>({
    queryKey: ["wa-coverage"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/wa/coverage`);
      if (!r.ok) throw new Error("Failed to load coverage");
      return r.json();
    },
  });

  const hasRun = (coverage?.total_docs ?? 0) > 0;
  const proposedCount = Object.entries(coverage?.proposals ?? {})
    .filter(([k]) => k.endsWith("/proposed"))
    .reduce((n, [, v]) => n + v, 0);

  const invalidateAll = () => {
    qc.invalidateQueries({ queryKey: ["wa-coverage"] });
    qc.invalidateQueries({ queryKey: ["wa-inventory"] });
    qc.invalidateQueries({ queryKey: ["wa-records"] });
    qc.invalidateQueries({ queryKey: ["wa-proposals"] });
  };

  const TABS: { id: Tab; label: string; badge?: number }[] = [
    { id: "archive", label: "Archive & coverage" },
    { id: "doctrine", label: "Doctrine" },
    { id: "proposals", label: "Canon proposals", badge: proposedCount || undefined },
  ];

  return (
    <div className="mx-auto max-w-5xl px-4 py-8 space-y-6">
      <div className="space-y-1">
        <div className="text-xs uppercase tracking-[0.2em] text-muted-foreground flex items-center gap-2">
          <DraftingCompass className="w-3.5 h-3.5" /> Doctrine
        </div>
        <h1 className="text-3xl font-serif">Writing Architect</h1>
        <p className="text-sm text-muted-foreground max-w-2xl">
          The decomposition bench for the Writing Architect archive. Every file is
          explicitly accounted for, doctrine becomes machine-readable records, and
          bible facts wait here as proposals — nothing writes canon without you.
        </p>
      </div>

      {/* Tab strip */}
      <div className="flex items-center gap-1 border-b" style={{ borderColor: "var(--line-2)" }}>
        {TABS.map((t) => (
          <button
            key={t.id}
            onClick={() => setTab(t.id)}
            className={`px-3 py-2 text-sm border-b-2 -mb-px transition-colors ${
              tab === t.id ? "font-medium" : "text-muted-foreground"
            }`}
            style={tab === t.id
              ? { borderBottomColor: "var(--gilt)", color: "var(--gilt)" }
              : { borderBottomColor: "transparent" }}
            data-testid={`wa-tab-${t.id}`}
          >
            {t.label}
            {t.badge ? (
              <span className="ml-1.5 px-1.5 py-0.5 rounded-full text-[10px] border"
                    style={{ borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }}>
                {t.badge}
              </span>
            ) : null}
          </button>
        ))}
      </div>

      {tab === "archive" && (
        <ArchiveTab coverage={coverage} hasRun={hasRun} onChanged={invalidateAll} />
      )}
      {tab === "doctrine" && <DoctrineTab hasRun={hasRun} />}
      {tab === "proposals" && <ProposalsTab hasRun={hasRun} onDecided={invalidateAll} />}
    </div>
  );
}

/* ── Archive & coverage ────────────────────────────────────────────────── */

function ArchiveTab({
  coverage, hasRun, onChanged,
}: { coverage: Coverage | undefined; hasRun: boolean; onChanged: () => void }) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState<"upload" | "decompose" | null>(null);
  const [statusFilter, setStatusFilter] = useState<string | "all">("all");

  const decompose = async (archivePath?: string) => {
    setBusy("decompose");
    try {
      const r = await apiFetch(`${BASE}/wa/decompose`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(archivePath ? { archive_path: archivePath } : {}),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `Decompose failed (${r.status})`);
      }
      const j = await r.json();
      toast.success(
        `Decomposition complete — ${j.extracted ?? j.records ?? "all"} files accounted for`,
      );
      onChanged();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Decompose failed");
    } finally {
      setBusy(null);
    }
  };

  const onPickFile = async (f: File | null) => {
    if (!f) return;
    setBusy("upload");
    try {
      const fd = new FormData();
      fd.append("file", f);
      const r = await apiFetch(`${BASE}/wa/upload`, { method: "POST", body: fd });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `Upload failed (${r.status})`);
      }
      const j = await r.json();
      toast.success(`Archive uploaded (${fmtBytes(j.size_bytes)}) — decomposing…`);
      await decompose(j.path);
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Upload failed");
      setBusy(null);
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const { data: inv, isLoading: invLoading } = useQuery<{ items: InventoryItem[] }>({
    queryKey: ["wa-inventory", statusFilter],
    queryFn: async () => {
      const p = statusFilter === "all" ? "" : `?status=${statusFilter}`;
      const r = await apiFetch(`${BASE}/wa/inventory${p}`);
      if (!r.ok) throw new Error("Failed to load inventory");
      return r.json();
    },
    enabled: hasRun,
  });

  const byStatus = coverage?.by_status ?? {};

  return (
    <div className="space-y-5">
      {/* Actions */}
      <div className="border rounded-xl bg-card p-4 flex flex-wrap items-center gap-3"
           style={{ borderColor: "var(--line-2)" }}>
        <FileArchive className="w-5 h-5 text-muted-foreground" />
        <div className="text-sm flex-1 min-w-[200px]">
          {hasRun
            ? <>Last run covered <span className="font-medium">{coverage!.total_docs}</span> files
                {coverage!.fully_accounted
                  ? <span className="inline-flex items-center gap-1 ml-2 text-xs" style={{ color: "var(--green-2)" }}>
                      <CheckCircle2 className="w-3.5 h-3.5" /> fully accounted
                    </span>
                  : <span className="inline-flex items-center gap-1 ml-2 text-xs" style={{ color: "var(--rust)" }}>
                      <AlertTriangle className="w-3.5 h-3.5" /> coverage gap
                    </span>}
              </>
            : "No decomposition run yet — upload the archive zip or run against the bundled one."}
        </div>
        <input ref={fileRef} type="file" accept=".zip" className="hidden"
               onChange={(e) => onPickFile(e.target.files?.[0] ?? null)}
               data-testid="wa-file-input" />
        <Button size="sm" variant="outline" className="h-8 gap-1.5"
                style={{ borderColor: "var(--line-2)" }}
                disabled={busy !== null}
                onClick={() => fileRef.current?.click()}
                data-testid="wa-upload">
          {busy === "upload" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Upload className="w-3 h-3" />}
          Upload archive
        </Button>
        <Button size="sm" className="h-8 gap-1.5"
                disabled={busy !== null}
                onClick={() => decompose()}
                data-testid="wa-decompose">
          {busy === "decompose" ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />}
          {hasRun ? "Re-run decomposition" : "Run decomposition"}
        </Button>
      </div>

      {/* Coverage summary */}
      {hasRun && (
        <div className="grid grid-cols-3 gap-3">
          {(["extracted", "deduped", "deferred"] as const).map((s) => (
            <button key={s}
                    onClick={() => setStatusFilter(statusFilter === s ? "all" : s)}
                    className="border rounded-xl bg-card p-3 text-left transition-colors"
                    style={statusFilter === s
                      ? { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }
                      : { borderColor: "var(--line-2)" }}
                    data-testid={`wa-coverage-${s}`}>
              <div className="text-2xl font-serif">{byStatus[s] ?? 0}</div>
              <div className="text-xs text-muted-foreground capitalize">{s}</div>
            </button>
          ))}
        </div>
      )}

      {/* Inventory */}
      {hasRun && (
        invLoading ? (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-10 w-full rounded-lg" />)}</div>
        ) : (
          <div className="border rounded-xl bg-card divide-y overflow-hidden"
               style={{ borderColor: "var(--line-2)" }}>
            {(inv?.items ?? []).map((it) => (
              <div key={it.id} className="px-3 py-2 flex items-center gap-3 text-sm"
                   style={{ borderColor: "var(--line-2)" }}
                   data-testid={`wa-inv-${it.id}`}>
                <Badge variant="outline" className="text-[10px] shrink-0 border"
                       style={STATUS_STYLE[it.status] ?? { borderColor: "var(--line-2)" }}>
                  {it.status}
                </Badge>
                <span className="font-mono text-xs truncate flex-1" title={it.rel_path}>
                  {it.rel_path}
                </span>
                <span className="text-[11px] text-muted-foreground shrink-0">{fmtBytes(it.size_bytes)}</span>
                {it.reason && (
                  <span className="text-[11px] truncate max-w-[240px]"
                        style={{ color: "var(--rust)" }} title={it.reason}>
                    {it.reason}
                  </span>
                )}
              </div>
            ))}
            {(inv?.items ?? []).length === 0 && (
              <div className="px-3 py-6 text-center text-sm text-muted-foreground">
                No files in this view.
              </div>
            )}
          </div>
        )
      )}
    </div>
  );
}

/* ── Doctrine records ──────────────────────────────────────────────────── */

function DoctrineTab({ hasRun }: { hasRun: boolean }) {
  const [typeFilter, setTypeFilter] = useState<string | "all">("all");
  const [openId, setOpenId] = useState<string | null>(null);

  const { data, isLoading } = useQuery<{ items: RecordRow[] }>({
    queryKey: ["wa-records"],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/wa/records`);
      if (!r.ok) throw new Error("Failed to load records");
      return r.json();
    },
    enabled: hasRun,
  });

  const items = data?.items ?? [];
  const types = useMemo(
    () => Array.from(new Set(items.map((i) => i.record_type))).sort(),
    [items],
  );
  const visible = typeFilter === "all" ? items : items.filter((i) => i.record_type === typeFilter);

  if (!hasRun) {
    return <EmptyRunHint text="Doctrine records appear here after a decomposition run." />;
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5">
        {(["all", ...types] as const).map((t) => (
          <button key={t}
                  onClick={() => setTypeFilter(t)}
                  className={`px-2.5 py-1 rounded-md text-xs border transition-colors ${
                    typeFilter === t ? "font-medium" : "text-muted-foreground"
                  }`}
                  style={typeFilter === t
                    ? { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }
                    : { borderColor: "var(--line-2)" }}
                  data-testid={`wa-doctrine-type-${t}`}>
            {t === "all" ? `All (${items.length})` : prettyType(t)}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-12 w-full rounded-lg" />)}</div>
      ) : visible.length === 0 ? (
        <EmptyRunHint text="No doctrine records in this view." />
      ) : (
        <div className="space-y-2">
          {visible.map((rec) => (
            <DoctrineCard key={rec.id} rec={rec}
                          open={openId === rec.id}
                          onToggle={() => setOpenId(openId === rec.id ? null : rec.id)} />
          ))}
        </div>
      )}
    </div>
  );
}

function DoctrineCard({
  rec, open, onToggle,
}: { rec: RecordRow; open: boolean; onToggle: () => void }) {
  const { data: detail, isLoading } = useQuery<RecordRow & { payload: unknown }>({
    queryKey: ["wa-record", rec.id],
    queryFn: async () => {
      const r = await apiFetch(`${BASE}/wa/records/${rec.id}`);
      if (!r.ok) throw new Error("Failed to load record");
      return r.json();
    },
    enabled: open,
  });

  return (
    <div className="border rounded-xl bg-card overflow-hidden" style={{ borderColor: "var(--line-2)" }}>
      <button onClick={onToggle}
              className="w-full px-4 py-3 flex items-center gap-3 text-left"
              data-testid={`wa-record-${rec.id}`}>
        {open ? <ChevronDown className="w-4 h-4 shrink-0 text-muted-foreground" />
              : <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" />}
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{rec.name}</div>
          <div className="text-[11px] text-muted-foreground font-mono truncate">{rec.source_path}</div>
        </div>
        <Badge variant="outline" className="text-[10px] shrink-0"
               style={{ borderColor: "var(--line-2)", color: "var(--ink-soft)" }}>
          {prettyType(rec.record_type)}
        </Badge>
      </button>
      {open && (
        <div className="px-4 pb-4 border-t" style={{ borderColor: "var(--line-2)" }}>
          {rec.source_note && (
            <p className="text-xs text-muted-foreground mt-3">{rec.source_note}</p>
          )}
          {isLoading ? (
            <Skeleton className="h-24 w-full rounded-lg mt-3" />
          ) : (
            <pre className="mt-3 p-3 rounded-lg text-[11px] leading-relaxed overflow-x-auto max-h-96 overflow-y-auto"
                 style={{ background: "var(--paper-2, rgba(0,0,0,0.04))", border: "1px solid var(--line-2)" }}
                 data-testid={`wa-record-payload-${rec.id}`}>
              {JSON.stringify(detail?.payload ?? {}, null, 2)}
            </pre>
          )}
        </div>
      )}
    </div>
  );
}

/* ── Canon proposals ───────────────────────────────────────────────────── */

function ProposalsTab({ hasRun, onDecided }: { hasRun: boolean; onDecided: () => void }) {
  const [statusFilter, setStatusFilter] = useState<string>("proposed");
  const [deciding, setDeciding] = useState<string | null>(null);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<{ items: Proposal[] }>({
    queryKey: ["wa-proposals", statusFilter],
    queryFn: async () => {
      const p = statusFilter === "all" ? "" : `?status=${statusFilter}`;
      const r = await apiFetch(`${BASE}/wa/canon-proposals${p}`);
      if (!r.ok) throw new Error("Failed to load proposals");
      return r.json();
    },
    enabled: hasRun,
  });

  const decide = async (id: string, status: "approved" | "rejected" | "proposed") => {
    setDeciding(id);
    try {
      const r = await apiFetch(`${BASE}/wa/canon-proposals/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ status }),
      });
      if (!r.ok) {
        const j = await r.json().catch(() => null);
        throw new Error(j?.detail || `Decision failed (${r.status})`);
      }
      toast.success(status === "proposed" ? "Proposal re-opened" : `Proposal ${status}`);
      qc.invalidateQueries({ queryKey: ["wa-proposals"] });
      onDecided();
    } catch (e) {
      toast.error(e instanceof Error ? e.message : "Decision failed");
    } finally {
      setDeciding(null);
    }
  };

  if (!hasRun) {
    return <EmptyRunHint text="Canon proposals appear here after a decomposition run." />;
  }

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-1.5">
        {["proposed", "approved", "rejected", "all"].map((s) => (
          <button key={s}
                  onClick={() => setStatusFilter(s)}
                  className={`px-2.5 py-1 rounded-md text-xs border capitalize transition-colors ${
                    statusFilter === s ? "font-medium" : "text-muted-foreground"
                  }`}
                  style={statusFilter === s
                    ? { borderColor: "var(--gilt-line)", background: "var(--gilt-soft)", color: "var(--gilt)" }
                    : { borderColor: "var(--line-2)" }}
                  data-testid={`wa-prop-filter-${s}`}>
            {s}
          </button>
        ))}
        <p className="ml-auto text-[11px] text-muted-foreground">
          Approving is a disposition — facts still enter canon only through ratification.
        </p>
      </div>

      {isLoading ? (
        <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 w-full rounded-xl" />)}</div>
      ) : items.length === 0 ? (
        <EmptyRunHint text={`No ${statusFilter === "all" ? "" : statusFilter + " "}proposals.`} />
      ) : (
        <div className="space-y-2">
          {items.map((p) => {
            const Icon = CLASS_ICON[p.classification] ?? ScrollText;
            const dimmed = p.status === "rejected";
            return (
              <div key={p.id}
                   className="border border-l-4 rounded-xl bg-card p-4 space-y-2"
                   style={{
                     borderLeftColor: (CLASS_STYLE[p.classification]?.color as string) ?? "var(--line-2)",
                     opacity: dimmed ? 0.55 : 1,
                   }}
                   data-testid={`wa-proposal-${p.id}`}>
                <div className="flex items-start justify-between gap-3">
                  <div className="flex items-center gap-2 flex-wrap">
                    <Badge variant="outline" className="gap-1 border"
                           style={CLASS_STYLE[p.classification] ?? { borderColor: "var(--line-2)" }}>
                      <Icon className="w-3 h-3" />{p.classification}
                    </Badge>
                    <Badge variant="outline" className="text-[10px]"
                           style={{ borderColor: "var(--line-2)", color: "var(--ink-soft)" }}>
                      {p.scope}
                    </Badge>
                    {p.status !== "proposed" && (
                      <Badge variant="outline" className="text-[10px] capitalize"
                             style={{ borderColor: "var(--line-2)", color: "var(--ink-soft)" }}>
                        {p.status}
                      </Badge>
                    )}
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    {p.status === "proposed" ? (
                      <>
                        <Button size="sm" variant="outline" className="h-7 px-2 gap-1"
                                style={{ borderColor: "var(--green-2)", color: "var(--green-2)" }}
                                disabled={deciding === p.id}
                                onClick={() => decide(p.id, "approved")}
                                data-testid={`wa-approve-${p.id}`}>
                          {deciding === p.id ? <Loader2 className="w-3 h-3 animate-spin" /> : <ThumbsUp className="w-3 h-3" />}
                          Approve
                        </Button>
                        <Button size="sm" variant="outline" className="h-7 px-2 gap-1"
                                style={{ borderColor: "var(--rust)", color: "var(--rust)" }}
                                disabled={deciding === p.id}
                                onClick={() => decide(p.id, "rejected")}
                                data-testid={`wa-reject-${p.id}`}>
                          <ThumbsDown className="w-3 h-3" />
                          Reject
                        </Button>
                      </>
                    ) : (
                      <Button size="sm" variant="ghost" className="h-7 px-2 gap-1 text-muted-foreground"
                              disabled={deciding === p.id}
                              onClick={() => decide(p.id, "proposed")}
                              data-testid={`wa-reopen-${p.id}`}>
                        <RotateCcw className="w-3 h-3" />
                        Re-open
                      </Button>
                    )}
                  </div>
                </div>
                <div className="text-sm font-medium">{p.fact_title}</div>
                <p className="text-sm text-foreground whitespace-pre-wrap break-words">{p.fact_text}</p>
                <div className="text-[11px] text-muted-foreground font-mono truncate">
                  {p.source_path}{p.source_location ? ` · ${p.source_location}` : ""}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function EmptyRunHint({ text }: { text: string }) {
  return (
    <div className="text-center py-16 text-muted-foreground">
      <ScrollText className="w-8 h-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">{text}</p>
    </div>
  );
}
