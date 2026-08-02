/**
 * Book Intelligence page — /works/:workId/intelligence
 *
 * The MONARCH "single-view" dashboard for a Work: completeness, gap analysis,
 * chapter structure, key knowledge items, and research suggestions — all
 * without navigating through files.
 */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorBoundary } from "@/components/error-boundary";
import {
  ArrowLeft, BarChart2, AlertTriangle, List, Lightbulb,
  TrendingUp, CheckCircle2, XCircle, RefreshCw, ChevronDown,
  ChevronRight, Layers, Brain,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ──────────────────────────────────────────────────────────────────────

interface ComplDimension {
  name: string; label: string; score: number;
  current: number | string; target: number | string; unit: string; rule: string;
}
interface ComplReport {
  overall: number; readiness: string; summary: string;
  dimensions: ComplDimension[];
}
interface GapItem { kind: string; title: string; description: string; severity: string; }
interface GapReport { coverage_pct: number; total_chapters: number; gaps: GapItem[]; suggested_queries: string[]; }
interface Chapter { id: string; seq: number; title: string; word_count: number; extraction_method: string; }

// ── Colour helpers ─────────────────────────────────────────────────────────────

const READINESS_RING: Record<string, string> = {
  "Ready":          "ring-emerald-400 text-emerald-700",
  "Near-Complete":  "ring-blue-400 text-blue-700",
  "Substantial":    "ring-violet-400 text-violet-700",
  "Developing":     "ring-amber-400 text-amber-700",
  "Draft":          "ring-muted text-muted-foreground",
};
const DIM_BAR: Record<string, string> = {
  structural: "bg-violet-500", content: "bg-blue-500",
  research:   "bg-emerald-500", editorial: "bg-amber-500", source: "bg-orange-400",
};
const GAP_DOT: Record<string, string> = {
  high: "bg-red-500", medium: "bg-amber-400", low: "bg-blue-400",
};

// ── Main page ──────────────────────────────────────────────────────────────────

export default function WorkIntelligence() {
  const { workId } = useParams<{ workId: string }>();
  const [, navigate] = useLocation();
  const [openSections, setOpenSections] = useState<Set<string>>(new Set(["completeness", "gaps"]));

  const toggle = (s: string) =>
    setOpenSections((prev) => {
      const next = new Set(prev);
      next.has(s) ? next.delete(s) : next.add(s);
      return next;
    });

  const { data: work } = useQuery<{ work: { id: string; title: string } }>({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}`).then((r) => r.json()),
    enabled: !!workId,
    staleTime: 60_000,
  });

  const { data: compl, isLoading: complLoading, refetch: refetchCompl } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/completeness`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: gaps, isLoading: gapsLoading, refetch: refetchGaps } = useQuery<GapReport>({
    queryKey: ["work-gaps", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/gaps`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: knData } = useQuery<{ knowledge: Array<{ id: string; kind: string; text: string; confidence?: number }> }>({
    queryKey: ["work-knowledge-top", workId],
    queryFn: () =>
      apiFetch(`${BASE}/works/${workId}/knowledge`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const refreshAll = () => {
    refetchCompl();
    refetchGaps();
  };

  const title = (work?.work as any)?.title ?? "Work Intelligence";
  const allReady = !complLoading && !gapsLoading;

  return (
    <div className="space-y-6 animate-in fade-in duration-300 max-w-4xl">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => navigate(`/works/${workId}`)} className="-ml-2">
            <ArrowLeft className="w-4 h-4 mr-1.5" /> {title}
          </Button>
        </div>
        <Button variant="outline" size="sm" onClick={refreshAll} disabled={!allReady}>
          <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Refresh all
        </Button>
      </div>

      <div className="border-b border-border/50 pb-3">
        <div className="flex items-center gap-3">
          <Brain className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-serif font-semibold tracking-tight">{title}</h1>
            <p className="text-muted-foreground text-sm font-serif mt-0.5">Knowledge Intelligence — what you have, what it means, what's missing, what's next.</p>
          </div>
        </div>
      </div>

      {/* ── Overview strip ───────────────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-4">
        <MetricCard
          label="Overall"
          value={compl ? `${compl.overall}%` : "—"}
          sub={compl?.readiness ?? "loading…"}
          loading={complLoading}
          color={compl ? (compl.overall >= 80 ? "text-emerald-700" : compl.overall >= 50 ? "text-amber-700" : "text-red-700") : "text-muted-foreground"}
        />
        <MetricCard
          label="Chapters"
          value={gaps ? String(gaps.total_chapters) : "—"}
          sub="sections extracted"
          loading={gapsLoading}
        />
        <MetricCard
          label="Coverage"
          value={gaps ? `${gaps.coverage_pct}%` : "—"}
          sub="research coverage"
          loading={gapsLoading}
          color={gaps ? (gaps.coverage_pct >= 80 ? "text-emerald-700" : gaps.coverage_pct >= 50 ? "text-amber-700" : "text-red-700") : "text-muted-foreground"}
        />
        <MetricCard
          label="Gaps"
          value={gaps ? String(gaps.gaps.length) : "—"}
          sub="issues to address"
          loading={gapsLoading}
          color={gaps && gaps.gaps.length > 0 ? "text-red-600" : "text-emerald-700"}
        />
      </div>

      {/* ── Completeness section ─────────────────────────────────────────── */}
      <Section
        id="completeness"
        label="Completeness"
        icon={BarChart2}
        open={openSections.has("completeness")}
        onToggle={() => toggle("completeness")}
        badge={compl ? `${compl.overall}%` : undefined}
      >
        {complLoading ? (
          <div className="space-y-3">{[1,2,3,4,5].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : compl ? (
          <div className="space-y-3">
            {compl.dimensions.map((d) => (
              <div key={d.name} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <div>
                    <span className="font-medium">{d.label}</span>
                    <span className="ml-2 text-[11px] font-mono text-muted-foreground">
                      {Number(d.current).toLocaleString()} / {Number(d.target).toLocaleString()} {d.unit}
                    </span>
                  </div>
                  <span className="font-mono font-semibold text-sm">{d.score}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${DIM_BAR[d.name] ?? "bg-primary"}`}
                    style={{ width: `${d.score}%` }}
                  />
                </div>
                <p className="text-[10px] font-mono text-muted-foreground/70">{d.rule}</p>
              </div>
            ))}
          </div>
        ) : (
          <Empty text="No completeness data yet — extract documents first." />
        )}
      </Section>

      {/* ── Gaps section ─────────────────────────────────────────────────── */}
      <Section
        id="gaps"
        label="Research Gaps"
        icon={AlertTriangle}
        open={openSections.has("gaps")}
        onToggle={() => toggle("gaps")}
        badge={gaps?.gaps.length ? String(gaps.gaps.length) : undefined}
        badgeVariant="destructive"
      >
        {gapsLoading ? (
          <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : gaps && gaps.gaps.length > 0 ? (
          <div className="space-y-2">
            {gaps.gaps.slice(0, 10).map((g, i) => (
              <div key={i} className={`flex items-start gap-3 p-3 rounded-lg border text-sm ${
                g.severity === "high" ? "border-red-200 bg-red-50/40" :
                g.severity === "medium" ? "border-amber-200 bg-amber-50/40" :
                "border-blue-200 bg-blue-50/40"}`}>
                <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${GAP_DOT[g.severity]}`} />
                <div>
                  <p className="font-medium leading-snug">{g.title}</p>
                  <p className="text-[11px] text-muted-foreground mt-0.5">{g.description}</p>
                </div>
              </div>
            ))}
            {gaps.gaps.length > 10 && (
              <p className="text-xs font-mono text-muted-foreground text-center">
                +{gaps.gaps.length - 10} more gaps — see the Gaps tab in the Work detail for the full list.
              </p>
            )}
          </div>
        ) : gaps ? (
          <div className="flex items-center gap-2 text-sm text-emerald-700 py-4">
            <CheckCircle2 className="w-4 h-4" />
            No research gaps detected — all chapters have sufficient coverage.
          </div>
        ) : (
          <Empty text="No gap analysis yet — extract documents first." />
        )}
      </Section>

      {/* ── Suggested queries section ─────────────────────────────────────── */}
      {gaps && gaps.suggested_queries.length > 0 && (
        <Section
          id="suggestions"
          label="Suggested Research"
          icon={Lightbulb}
          open={openSections.has("suggestions")}
          onToggle={() => toggle("suggestions")}
        >
          <div className="flex flex-wrap gap-2">
            {gaps.suggested_queries.map((q, i) => (
              <Badge key={i} variant="outline" className="font-mono text-xs cursor-default">
                {q}
              </Badge>
            ))}
          </div>
        </Section>
      )}

      {/* ── Top knowledge section ─────────────────────────────────────────── */}
      {knData && knData.knowledge.length > 0 && (
        <Section
          id="knowledge"
          label="Knowledge Highlights"
          icon={Layers}
          open={openSections.has("knowledge")}
          onToggle={() => toggle("knowledge")}
          badge={String(knData.knowledge.length)}
        >
          <div className="space-y-2">
            {knData.knowledge.slice(0, 8).map((item) => (
              <div key={item.id} className="flex items-start gap-3 p-3 rounded-lg border border-border/40 bg-muted/10">
                <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary shrink-0 mt-0.5">
                  {item.kind}
                </Badge>
                <p className="text-sm leading-snug">{item.text}</p>
              </div>
            ))}
            {knData.knowledge.length > 8 && (
              <p className="text-xs font-mono text-muted-foreground text-center">
                {knData.knowledge.length - 8} more items — see the Knowledge tab.
              </p>
            )}
          </div>
        </Section>
      )}

      {/* Footer */}
      <div className="pt-2 flex items-center justify-between text-[11px] font-mono text-muted-foreground/50">
        <span>Orivellum Knowledge Intelligence</span>
        <Button variant="link" size="sm" className="text-[11px] font-mono text-muted-foreground/50 h-auto p-0"
          onClick={() => navigate(`/works/${workId}`)}>
          Full Work detail →
        </Button>
      </div>
    </div>
  );
}

// ── Helper components ──────────────────────────────────────────────────────────

function MetricCard({ label, value, sub, loading, color = "text-foreground" }: {
  label: string; value: string; sub: string; loading?: boolean; color?: string;
}) {
  return (
    <Card className="border-border/50">
      <CardContent className="p-4 space-y-1">
        <p className="text-[11px] font-mono uppercase tracking-wider text-muted-foreground">{label}</p>
        {loading ? (
          <Skeleton className="h-8 w-20" />
        ) : (
          <p className={`text-2xl font-mono font-bold ${color}`}>{value}</p>
        )}
        <p className="text-[11px] font-mono text-muted-foreground">{sub}</p>
      </CardContent>
    </Card>
  );
}

function Section({
  id, label, icon: Icon, open, onToggle, badge, badgeVariant = "secondary", children,
}: {
  id: string; label: string; icon: React.ElementType; open: boolean;
  onToggle: () => void; badge?: string; badgeVariant?: "secondary" | "destructive"; children: React.ReactNode;
}) {
  return (
    <div className="border border-border/50 rounded-xl overflow-hidden">
      <button
        onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-muted/10 hover:bg-muted/20 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Icon className="w-4 h-4 text-primary/70" />
          <span className="font-mono text-sm font-semibold uppercase tracking-wider">{label}</span>
          {badge && (
            <Badge variant={badgeVariant} className="text-[10px] font-mono">
              {badge}
            </Badge>
          )}
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-5 py-4">{children}</div>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return (
    <p className="text-sm text-muted-foreground py-4 text-center">{text}</p>
  );
}
