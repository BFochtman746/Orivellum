/**
 * Instrument Certification (PROMOTION) — /assay
 *
 * The parity dashboard for quality instruments: every detector's
 * certification status (shadow / certified / advisory), its rolling
 * precision against the author's ratified dispositions over time, and
 * shadow/baseline parity. Promotion to certified requires meeting the
 * declared precision bar plus the author's signature; demotion returns a
 * degraded instrument to shadow. Every transition is ledgered.
 *
 * Data fetching mirrors the direct-fetch pattern used by the MCOS page
 * (apiFetch + BASE, react-query wrappers).
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogHeader, DialogFooter, DialogTitle,
  DialogDescription, DialogClose,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  ShieldCheck, Eye, FlaskConical, Loader2, RefreshCw, AlertCircle,
  TrendingDown, ArrowUpCircle, ArrowDownCircle, ScrollText,
} from "lucide-react";
import { toast } from "sonner";
import { useGdDark } from "@/lib/useGdDark";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types (mirror /api/assay/promotion/dashboard) ────────────────────────────

interface PrecisionPoint { at: string; precision: number; true_positives: number; false_positives: number }
interface PrecisionReport {
  true_positives: number; false_positives: number; sample_size: number;
  precision: number | null; series: PrecisionPoint[];
  bar: { min_precision: number; min_dispositions: number };
  meets_bar: boolean;
}
interface ParityPair { shadow_run_id: string; baseline_run_id: string; agreement: number }
interface ParityReport { baseline: string | null; pairs: ParityPair[]; mean_agreement: number | null }
interface InstrumentRow {
  key: string; name: string; tier: number; certification: string;
  shadow_of: string | null; precision: PrecisionReport; parity: ParityReport;
  promotable: boolean; degraded: boolean;
}
interface CertEvent {
  id: string; instrument_id: string; from_status: string; to_status: string;
  actor: string; precision_val: number | null; sample_size: number | null;
  note: string; created_at: string;
}

// ── Status badge ─────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, React.CSSProperties> = {
  certified: { borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", color: "var(--green-2)", background: "var(--green-soft)" },
  shadow:    { borderColor: "var(--gilt-line)", color: "var(--gilt)", background: "var(--gilt-soft)" },
  advisory:  { borderColor: "var(--line)", color: "var(--ink-3)", background: "transparent" },
};

function StatusBadge({ status }: { status: string }) {
  const icon = status === "certified" ? <ShieldCheck className="h-3 w-3" />
    : status === "shadow" ? <Eye className="h-3 w-3" />
    : <FlaskConical className="h-3 w-3" />;
  return (
    <Badge variant="outline" className="gap-1 capitalize" style={STATUS_STYLE[status] ?? STATUS_STYLE.advisory}>
      {icon}{status}
    </Badge>
  );
}

// ── Precision sparkline (pure SVG, no chart dep) ─────────────────────────────

function Sparkline({ series, bar }: { series: PrecisionPoint[]; bar: number }) {
  if (series.length < 2) return <span className="text-xs" style={{ color: "var(--ink-3)" }}>—</span>;
  const w = 120, h = 28, pad = 2;
  const xs = series.map((_, i) => pad + (i * (w - 2 * pad)) / (series.length - 1));
  const ys = series.map((p) => h - pad - p.precision * (h - 2 * pad));
  const barY = h - pad - bar * (h - 2 * pad);
  const last = series[series.length - 1].precision;
  const color = last >= bar ? "var(--green-2)" : "var(--rust)";
  return (
    <svg width={w} height={h} aria-label="precision over time" role="img">
      <line x1={pad} x2={w - pad} y1={barY} y2={barY} stroke="var(--line)" strokeDasharray="3 3" />
      <polyline
        points={xs.map((x, i) => `${x},${ys[i]}`).join(" ")}
        fill="none" stroke={color} strokeWidth={1.5}
      />
      <circle cx={xs[xs.length - 1]} cy={ys[ys.length - 1]} r={2.5} fill={color} />
    </svg>
  );
}

// ── Certification action dialog ──────────────────────────────────────────────

type Action = { kind: "promote" | "demote" | "shadow"; row: InstrumentRow } | null;

const ACTION_COPY = {
  promote: {
    title: "Promote to certified",
    desc: "This instrument will gain blocking authority at its tier. Your signature is recorded on the certification ledger with the precision evidence.",
    verb: "Sign & promote",
  },
  demote: {
    title: "Demote to shadow",
    desc: "The instrument loses blocking authority immediately and returns to shadow observation. The precision at this moment is recorded as evidence.",
    verb: "Demote",
  },
  shadow: {
    title: "Enter shadow mode",
    desc: "The instrument starts running alongside its baseline. Findings are recorded and labeled but never block; a precision record accumulates.",
    verb: "Enter shadow",
  },
} as const;

export default function AssayPromotion() {
  const gdDark = useGdDark();
  const qc = useQueryClient();
  const [action, setAction] = useState<Action>(null);
  const [note, setNote] = useState("");

  const dashQuery = useQuery<{ instruments: InstrumentRow[]; events: CertEvent[] }>({
    queryKey: ["assay", "promotion"],
    queryFn: () => apiFetch(`${BASE}/assay/promotion/dashboard`).then((r) => {
      if (!r.ok) throw new Error(`The server returned HTTP ${r.status}.`);
      return r.json();
    }),
    staleTime: 10_000,
  });

  const mutate = useMutation({
    mutationFn: async ({ kind, key, note }: { kind: "promote" | "demote" | "shadow"; key: string; note: string }) => {
      const r = await apiFetch(`${BASE}/assay/instruments/${encodeURIComponent(key)}/${kind}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ note }),
      });
      if (!r.ok) {
        let detail = `HTTP ${r.status}`;
        try { const b = await r.json(); if (b?.detail) detail = String(b.detail); } catch { /* keep status */ }
        throw new Error(detail);
      }
      return r.json();
    },
    onSuccess: (_d, v) => {
      toast.success(
        v.kind === "promote" ? "Instrument certified" :
        v.kind === "demote" ? "Instrument demoted to shadow" : "Instrument entered shadow mode",
      );
      qc.invalidateQueries({ queryKey: ["assay", "promotion"] });
      setAction(null); setNote("");
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const rows = dashQuery.data?.instruments ?? [];
  const events = dashQuery.data?.events ?? [];

  return (
    <div className={gdDark ? "gd-dark" : undefined} style={{ background: "var(--paper)", minHeight: "100%" }}>
      <div className="mx-auto max-w-5xl px-4 py-8 space-y-8">
        <header className="flex items-start justify-between gap-4">
          <div>
            <p className="eyebrow">Scriptorium · Quality</p>
            <h1 className="font-display text-2xl" style={{ color: "var(--ink)" }}>
              Instrument Certification
            </h1>
            <p className="mt-1 text-sm max-w-2xl" style={{ color: "var(--ink-2)" }}>
              No instrument may block your book until it earns that right. Shadow
              candidates run alongside certified checks; your true/false-positive
              verdicts build the precision record that promotion requires.
            </p>
          </div>
          <Button variant="outline" size="sm" onClick={() => dashQuery.refetch()} disabled={dashQuery.isFetching}>
            {dashQuery.isFetching ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          </Button>
        </header>

        {dashQuery.isLoading && (
          <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 w-full" />)}</div>
        )}
        {dashQuery.isError && (
          <Card><CardContent className="flex items-center gap-2 py-6 text-sm" style={{ color: "var(--rust)" }}>
            <AlertCircle className="h-4 w-4 shrink-0" />{(dashQuery.error as Error).message}
          </CardContent></Card>
        )}

        {!dashQuery.isLoading && !dashQuery.isError && (
          <div className="space-y-3">
            {rows.map((row) => {
              const p = row.precision;
              const barPct = Math.round(p.bar.min_precision * 100);
              return (
                <Card key={row.key} data-testid={`card-instrument-${row.key}`}>
                  <CardContent className="py-4">
                    <div className="flex flex-wrap items-center gap-3">
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium truncate" style={{ color: "var(--ink)" }}>{row.name}</span>
                          <StatusBadge status={row.certification} />
                          <Badge variant="outline" style={{ borderColor: "var(--line)", color: "var(--ink-3)" }}>
                            Tier {row.tier}
                          </Badge>
                          {row.shadow_of && (
                            <Badge variant="outline" className="gap-1" style={STATUS_STYLE.shadow}>
                              <Eye className="h-3 w-3" />shadows {row.shadow_of}
                            </Badge>
                          )}
                          {row.degraded && (
                            <Badge variant="outline" className="gap-1"
                              style={{ borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", color: "var(--rust)", background: "var(--rust-soft)" }}>
                              <TrendingDown className="h-3 w-3" />degraded
                            </Badge>
                          )}
                        </div>
                        <p className="mt-1 text-xs" style={{ color: "var(--ink-3)" }}>
                          {p.sample_size > 0
                            ? <>Precision {p.precision != null ? `${Math.round(p.precision * 100)}%` : "—"} over {p.sample_size} verdict{p.sample_size === 1 ? "" : "s"} (bar: {barPct}% over ≥{p.bar.min_dispositions})</>
                            : <>No author verdicts yet — disposition this instrument's findings to build its record (bar: {barPct}% over ≥{p.bar.min_dispositions}).</>}
                          {row.parity.mean_agreement != null && (
                            <> · Parity with {row.parity.baseline}: {Math.round(row.parity.mean_agreement * 100)}% over {row.parity.pairs.length} paired run{row.parity.pairs.length === 1 ? "" : "s"}</>
                          )}
                        </p>
                      </div>
                      <Sparkline series={p.series} bar={p.bar.min_precision} />
                      <div className="flex items-center gap-2">
                        {row.certification === "advisory" && (
                          <Button size="sm" variant="outline" data-testid={`button-shadow-${row.key}`}
                            onClick={() => setAction({ kind: "shadow", row })}>
                            <Eye className="mr-1 h-3.5 w-3.5" />Shadow
                          </Button>
                        )}
                        {row.certification === "shadow" && (
                          <Button size="sm" disabled={!row.promotable} data-testid={`button-promote-${row.key}`}
                            title={row.promotable ? undefined : "Precision bar not met yet"}
                            onClick={() => setAction({ kind: "promote", row })}>
                            <ArrowUpCircle className="mr-1 h-3.5 w-3.5" />Promote
                          </Button>
                        )}
                        {row.certification === "certified" && (
                          <Button size="sm" variant="outline" data-testid={`button-demote-${row.key}`}
                            onClick={() => setAction({ kind: "demote", row })}>
                            <ArrowDownCircle className="mr-1 h-3.5 w-3.5" />Demote
                          </Button>
                        )}
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        )}

        {events.length > 0 && (
          <section>
            <h2 className="mb-2 flex items-center gap-2 font-display text-lg" style={{ color: "var(--ink)" }}>
              <ScrollText className="h-4 w-4" />Certification ledger
            </h2>
            <Card><CardContent className="divide-y py-1" style={{ borderColor: "var(--line)" }}>
              {events.map((e) => (
                <div key={e.id} className="flex flex-wrap items-center gap-2 py-2 text-sm">
                  <span style={{ color: "var(--ink-2)" }}>{format(new Date(e.created_at), "d MMM yyyy HH:mm")}</span>
                  <span className="capitalize" style={{ color: "var(--ink)" }}>{e.from_status} → {e.to_status}</span>
                  <span style={{ color: "var(--ink-3)" }}>by {e.actor}</span>
                  {e.precision_val != null && (
                    <span style={{ color: "var(--ink-3)" }}>
                      · precision {Math.round(e.precision_val * 100)}%{e.sample_size != null ? ` over ${e.sample_size}` : ""}
                    </span>
                  )}
                  {e.note && <span className="italic" style={{ color: "var(--ink-3)" }}>“{e.note}”</span>}
                </div>
              ))}
            </CardContent></Card>
          </section>
        )}

        <Dialog open={action != null} onOpenChange={(open) => { if (!open) { setAction(null); setNote(""); } }}>
          <DialogContent>
            {action && (
              <>
                <DialogHeader>
                  <DialogTitle>{ACTION_COPY[action.kind].title}</DialogTitle>
                  <DialogDescription>
                    {action.row.name} — {ACTION_COPY[action.kind].desc}
                  </DialogDescription>
                </DialogHeader>
                <div className="space-y-2">
                  <Label htmlFor="cert-note">Note (optional)</Label>
                  <Textarea id="cert-note" value={note} onChange={(e) => setNote(e.target.value)}
                    placeholder="Why this decision — kept on the ledger" rows={2} data-testid="input-cert-note" />
                </div>
                <DialogFooter>
                  <DialogClose asChild><Button variant="outline">Cancel</Button></DialogClose>
                  <Button data-testid="button-confirm-cert"
                    disabled={mutate.isPending}
                    onClick={() => mutate.mutate({ kind: action.kind, key: action.row.key, note })}>
                    {mutate.isPending && <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />}
                    {ACTION_COPY[action.kind].verb}
                  </Button>
                </DialogFooter>
              </>
            )}
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}
