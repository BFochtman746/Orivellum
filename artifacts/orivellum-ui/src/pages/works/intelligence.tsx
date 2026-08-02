/**
 * Book Intelligence page — /works/:workId/intelligence
 *
 * The MONARCH "single-view" dashboard for a Work: completeness, gap analysis,
 * chapter structure, key knowledge items, and research suggestions — all
 * without navigating through individual files.
 */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  ArrowLeft, BarChart2, AlertTriangle, Lightbulb, CheckCircle2,
  RefreshCw, ChevronDown, ChevronRight, Layers, Brain,
  BookOpen, FileText,
} from "lucide-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ──────────────────────────────────────────────────────────────────────

interface ComplDimension {
  name: string; label: string; score: number;
  current: number | string; target: number | string; unit: string; rule: string;
  evidence?: string[];
}
interface ComplReport {
  overall: number; readiness: string; summary: string; evaluated_at?: string;
  dimensions: ComplDimension[];
}
interface GapItem {
  kind: string; title: string; description: string; severity: string;
  metadata?: Record<string, string>;
}
interface GapReport {
  coverage_pct: number; total_chapters: number;
  gaps: GapItem[]; suggested_queries: string[];
}
interface Chapter {
  id: string; seq: number; level: number; title: string;
  word_count: number; status: string; extraction_method: string; source_doc_id: string;
}
interface ChapterDoc { doc_title: string; doc_id: string; chapters: Chapter[]; }
interface ChaptersResponse { work_id: string; total_chapters: number; documents: ChapterDoc[]; }
interface WorkStats {
  documents_by_kind: Record<string, number>;
  documents_by_readiness: Record<string, number>;
  knowledge_by_kind: Record<string, number>;
  tasks_by_status: Record<string, number>;
  pending_task_count: number;
  conversation_count: number;
  avg_mastery_pct: number;
  concept_count: number;
}

// ── Colour helpers ─────────────────────────────────────────────────────────────

function scoreColor(score: number) {
  if (score >= 80) return "text-emerald-700";
  if (score >= 50) return "text-amber-700";
  return "text-red-700";
}

const DIM_BAR: Record<string, string> = {
  structural: "bg-violet-500", content: "bg-blue-500",
  research:   "bg-emerald-500", editorial: "bg-amber-500", source: "bg-orange-400",
};
const GAP_ROW: Record<string, string> = {
  high:   "border-red-200   bg-red-50/40",
  medium: "border-amber-200 bg-amber-50/40",
  low:    "border-blue-200  bg-blue-50/40",
};
const GAP_DOT: Record<string, string> = {
  high: "bg-red-500", medium: "bg-amber-400", low: "bg-blue-400",
};

// ── Main page ──────────────────────────────────────────────────────────────────

export default function WorkIntelligence() {
  const { workId } = useParams<{ workId: string }>();
  const [, navigate]    = useLocation();
  const [open, setOpen] = useState<Set<string>>(new Set(["completeness", "gaps"]));

  const toggle = (s: string) =>
    setOpen((prev) => { const n = new Set(prev); n.has(s) ? n.delete(s) : n.add(s); return n; });

  const { data: work } = useQuery<{ work: { id: string; title: string } }>({
    queryKey: ["work", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}`).then((r) => r.json()),
    enabled: !!workId, staleTime: 60_000,
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
    queryFn: () => apiFetch(`${BASE}/works/${workId}/knowledge`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: statsData } = useQuery<WorkStats>({
    queryKey: ["work-stats", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/stats`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const { data: chaptersData } = useQuery<ChaptersResponse>({
    queryKey: ["work-chapters", workId],
    queryFn: () => apiFetch(`${BASE}/works/${workId}/chapters`).then((r) => r.json()),
    enabled: !!workId, staleTime: 120_000,
  });

  const title = (work?.work as any)?.title ?? "Work Intelligence";

  // Derived counts
  const totalDocs = Object.values(statsData?.documents_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const totalKn   = Object.values(statsData?.knowledge_by_kind   ?? {}).reduce((a, b) => a + b, 0);
  const readyDocs = statsData?.documents_by_readiness?.["ready"] ?? 0;

  // Gap groups
  const highGaps  = gaps?.gaps.filter(g => g.severity === "high")   ?? [];
  const medGaps   = gaps?.gaps.filter(g => g.severity === "medium")  ?? [];
  const lowGaps   = gaps?.gaps.filter(g => g.severity === "low")     ?? [];
  const totalGaps = gaps?.gaps.length ?? 0;

  const allReady = !complLoading && !gapsLoading;

  return (
    <div className="space-y-6 animate-in fade-in duration-300 max-w-4xl">

      {/* Header */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate(`/works/${workId}`)} className="-ml-2">
          <ArrowLeft className="w-4 h-4 mr-1.5" /> {title}
        </Button>
        <Button variant="outline" size="sm"
          onClick={() => { refetchCompl(); refetchGaps(); }}
          disabled={!allReady}>
          <RefreshCw className="w-3.5 h-3.5 mr-1.5" /> Refresh all
        </Button>
      </div>

      <div className="border-b border-border/50 pb-3">
        <div className="flex items-center gap-3">
          <Brain className="w-6 h-6 text-primary" />
          <div>
            <h1 className="text-2xl font-serif font-semibold tracking-tight">{title}</h1>
            <p className="text-muted-foreground text-sm font-serif mt-0.5">
              Knowledge Intelligence — what you have, what it means, what's missing, what's next.
            </p>
          </div>
        </div>
      </div>

      {/* ── Completeness + gaps metrics ───────────────────────────────────────── */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <MetricCard
          label="Overall"
          value={compl ? `${compl.overall}%` : "—"}
          sub={compl?.readiness ?? "loading…"}
          loading={complLoading}
          color={compl ? scoreColor(compl.overall) : "text-muted-foreground"}
        />
        <MetricCard
          label="Coverage"
          value={gaps ? `${gaps.coverage_pct}%` : "—"}
          sub="research coverage"
          loading={gapsLoading}
          color={gaps ? scoreColor(gaps.coverage_pct) : "text-muted-foreground"}
        />
        <MetricCard
          label="Gaps"
          value={totalGaps ? String(totalGaps) : (gaps ? "0" : "—")}
          sub={totalGaps > 0 ? `${highGaps.length} high · ${medGaps.length} med` : "none detected"}
          loading={gapsLoading}
          color={totalGaps > 0 ? "text-red-600" : (gaps ? "text-emerald-700" : "text-muted-foreground")}
        />
        <MetricCard
          label="Chapters"
          value={gaps ? String(gaps.total_chapters) : "—"}
          sub="sections extracted"
          loading={gapsLoading}
        />
      </div>

      {/* ── Work stats strip ─────────────────────────────────────────────────── */}
      {statsData && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <MetricCard
            label="Documents"
            value={String(totalDocs)}
            sub={`${readyDocs} ready`}
          />
          <MetricCard
            label="Knowledge"
            value={String(totalKn)}
            sub={`${Object.keys(statsData.knowledge_by_kind).length} kind${Object.keys(statsData.knowledge_by_kind).length !== 1 ? "s" : ""}`}
          />
          <MetricCard
            label="Tasks"
            value={String(statsData.pending_task_count)}
            sub="pending"
            color={statsData.pending_task_count > 0 ? "text-amber-600" : "text-muted-foreground"}
          />
          <MetricCard
            label="Chats"
            value={String(statsData.conversation_count)}
            sub="conversations"
          />
        </div>
      )}

      {/* ── Completeness ─────────────────────────────────────────────────────── */}
      <Section id="completeness" label="Completeness" icon={BarChart2}
        open={open.has("completeness")} onToggle={() => toggle("completeness")}
        badge={compl ? `${compl.overall}%` : undefined}>
        {complLoading ? (
          <div className="space-y-3">{[1,2,3,4,5].map(i => <Skeleton key={i} className="h-10 w-full" />)}</div>
        ) : compl ? (
          <div className="space-y-4">
            {compl.summary && (
              <p className="text-sm text-muted-foreground border-l-2 border-primary/30 pl-3 italic leading-relaxed">
                {compl.summary}
              </p>
            )}
            {compl.dimensions.map((d) => (
              <div key={d.name} className="space-y-1.5">
                <div className="flex items-center justify-between text-sm">
                  <div>
                    <span className="font-medium">{d.label}</span>
                    <span className="ml-2 text-[11px] font-mono text-muted-foreground">
                      {Number(d.current).toLocaleString()} / {Number(d.target).toLocaleString()} {d.unit}
                    </span>
                  </div>
                  <span className={`font-mono font-semibold text-sm ${scoreColor(d.score)}`}>{d.score}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className={`h-full rounded-full transition-all duration-500 ${DIM_BAR[d.name] ?? "bg-primary"}`}
                    style={{ width: `${d.score}%` }}
                  />
                </div>
                <p className="text-[10px] font-mono text-muted-foreground/70">{d.rule}</p>
                {d.evidence && d.evidence.length > 0 && (
                  <ul className="space-y-0.5 mt-0.5">
                    {d.evidence.map((ev, i) => (
                      <li key={i} className="flex items-start gap-1.5 text-[10px] font-mono text-muted-foreground/60">
                        <span className="shrink-0">·</span>
                        <span>{ev}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
            {compl.evaluated_at && (
              <p className="text-[10px] font-mono text-muted-foreground/40 text-right pt-1">
                evaluated {new Date(compl.evaluated_at).toLocaleString()}
              </p>
            )}
          </div>
        ) : (
          <Empty text="No completeness data yet — extract documents first." />
        )}
      </Section>

      {/* ── Research Gaps ────────────────────────────────────────────────────── */}
      <Section id="gaps" label="Research Gaps" icon={AlertTriangle}
        open={open.has("gaps")} onToggle={() => toggle("gaps")}
        badge={totalGaps ? String(totalGaps) : undefined}
        badgeVariant="destructive">
        {gapsLoading ? (
          <div className="space-y-2">{[1,2,3].map(i => <Skeleton key={i} className="h-14 w-full" />)}</div>
        ) : totalGaps > 0 ? (
          <div className="space-y-5">
            {[
              { severity: "high",   label: "High priority",   items: highGaps },
              { severity: "medium", label: "Medium priority", items: medGaps  },
              { severity: "low",    label: "Low priority",    items: lowGaps  },
            ].filter(g => g.items.length > 0).map(({ severity, label, items }) => (
              <div key={severity} className="space-y-2">
                <div className="flex items-center gap-2">
                  <span className={`w-2 h-2 rounded-full shrink-0 ${GAP_DOT[severity]}`} />
                  <span className="text-[11px] font-mono font-semibold uppercase tracking-wider text-muted-foreground">
                    {label} ({items.length})
                  </span>
                </div>
                {items.map((g, i) => (
                  <div key={i} className={`flex items-start gap-3 p-3 rounded-lg border text-sm ${GAP_ROW[g.severity] ?? ""}`}>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium leading-snug">{g.title}</p>
                      <p className="text-[11px] text-muted-foreground mt-0.5">{g.description}</p>
                      {g.metadata?.chapter_title && (
                        <p className="text-[10px] font-mono text-muted-foreground/60 mt-1">
                          chapter: {g.metadata.chapter_title}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            ))}
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

      {/* ── Chapter Structure ────────────────────────────────────────────────── */}
      {chaptersData && chaptersData.total_chapters > 0 && (
        <Section id="chapters" label="Chapter Structure" icon={BookOpen}
          open={open.has("chapters")} onToggle={() => toggle("chapters")}
          badge={String(chaptersData.total_chapters)}>
          <div className="space-y-4">
            {chaptersData.documents.map((docGroup) => (
              <div key={docGroup.doc_id}>
                <div className="flex items-center gap-2 py-1.5 mb-1.5 border-b border-border/30">
                  <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                  <span className="text-xs font-mono font-semibold text-muted-foreground truncate">{docGroup.doc_title}</span>
                  <Badge variant="outline" className="text-[9px] font-mono ml-auto shrink-0">
                    {docGroup.chapters.length} ch
                  </Badge>
                </div>
                <div className="pl-5 space-y-1">
                  {docGroup.chapters.map((ch) => (
                    <div key={ch.id} className="flex items-center gap-2 text-xs py-0.5 text-muted-foreground">
                      <span className="font-mono w-5 text-right shrink-0 opacity-50">{ch.seq}.</span>
                      <span className={`truncate flex-1 ${ch.level > 1 ? "pl-" + ((ch.level - 1) * 2) : ""}`}>
                        {ch.title || "(untitled)"}
                      </span>
                      {ch.word_count > 0 && (
                        <span className="font-mono opacity-40 shrink-0">{ch.word_count.toLocaleString()}w</span>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* ── Suggested Research ───────────────────────────────────────────────── */}
      {gaps && gaps.suggested_queries.length > 0 && (
        <Section id="suggestions" label="Suggested Research" icon={Lightbulb}
          open={open.has("suggestions")} onToggle={() => toggle("suggestions")}>
          <div className="flex flex-wrap gap-2">
            {gaps.suggested_queries.map((q, i) => (
              <Badge key={i} variant="outline" className="font-mono text-xs cursor-default">{q}</Badge>
            ))}
          </div>
        </Section>
      )}

      {/* ── Knowledge Highlights ─────────────────────────────────────────────── */}
      {knData && knData.knowledge.length > 0 && (
        <Section id="knowledge" label="Knowledge Highlights" icon={Layers}
          open={open.has("knowledge")} onToggle={() => toggle("knowledge")}
          badge={String(knData.knowledge.length)}>
          <div className="space-y-2">
            {knData.knowledge.slice(0, 10).map((item) => (
              <div key={item.id} className="flex items-start gap-3 p-3 rounded-lg border border-border/40 bg-muted/10">
                <Badge variant="outline"
                  className="text-[10px] uppercase font-mono border-primary/30 text-primary shrink-0 mt-0.5">
                  {item.kind}
                </Badge>
                <p className="text-sm leading-snug flex-1">{item.text}</p>
                {item.confidence != null && (
                  <span className="text-[10px] font-mono text-muted-foreground/50 shrink-0 mt-0.5">
                    {Math.round(item.confidence * 100)}%
                  </span>
                )}
              </div>
            ))}
            {knData.knowledge.length > 10 && (
              <p className="text-xs font-mono text-muted-foreground text-center">
                {knData.knowledge.length - 10} more items — see the Knowledge tab.
              </p>
            )}
          </div>
        </Section>
      )}

      {/* Footer */}
      <div className="pt-2 flex items-center justify-between text-[11px] font-mono text-muted-foreground/50">
        <span>Orivellum Knowledge Intelligence</span>
        <Button variant="link" size="sm"
          className="text-[11px] font-mono text-muted-foreground/50 h-auto p-0"
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
        {loading ? <Skeleton className="h-8 w-20" /> : (
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
  onToggle: () => void; badge?: string; badgeVariant?: "secondary" | "destructive";
  children: React.ReactNode;
}) {
  return (
    <div className="border border-border/50 rounded-xl overflow-hidden">
      <button onClick={onToggle}
        className="w-full flex items-center justify-between px-5 py-3.5 bg-muted/10 hover:bg-muted/20 transition-colors">
        <div className="flex items-center gap-2.5">
          <Icon className="w-4 h-4 text-primary/70" />
          <span className="font-mono text-sm font-semibold uppercase tracking-wider">{label}</span>
          {badge && (
            <Badge variant={badgeVariant} className="text-[10px] font-mono">{badge}</Badge>
          )}
        </div>
        {open ? <ChevronDown className="w-4 h-4 text-muted-foreground" /> : <ChevronRight className="w-4 h-4 text-muted-foreground" />}
      </button>
      {open && <div className="px-5 py-4">{children}</div>}
    </div>
  );
}

function Empty({ text }: { text: string }) {
  return <p className="text-sm text-muted-foreground py-4 text-center">{text}</p>;
}
