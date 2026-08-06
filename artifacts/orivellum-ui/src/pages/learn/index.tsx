import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  GraduationCap, BookOpen, Brain, Target, Zap,
  ChevronRight, Lightbulb, TrendingUp,
} from "lucide-react";
import type { Work } from "@workspace/api-client-react";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface LearnWork extends Work {
  concept_count: number;
  graduated_count: number;
  mastery_pct: number;
}

// ─── Mastery ring ─────────────────────────────────────────────────────────────

function MasteryRing({ pct, size = 48 }: { pct: number; size?: number }) {
  const r = (size - 8) / 2;
  const circ = 2 * Math.PI * r;
  const color = pct >= 80 ? "#22c55e" : pct >= 50 ? "#f59e0b" : "#6366f1";
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="shrink-0 -rotate-90">
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={6}
        className="stroke-muted" />
      <circle cx={size / 2} cy={size / 2} r={r} fill="none" strokeWidth={6}
        stroke={color}
        strokeDasharray={circ}
        strokeDashoffset={circ * (1 - pct / 100)}
        strokeLinecap="round"
        style={{ transition: "stroke-dashoffset 0.6s ease" }}
      />
    </svg>
  );
}

// ─── Work card ────────────────────────────────────────────────────────────────

function LearnWorkCard({ work }: { work: LearnWork }) {
  const hasConcepts = work.concept_count > 0;
  const pct = work.mastery_pct;
  const masteryLabel = pct >= 100 ? "Mastered"
    : pct >= 80 ? "Near complete"
    : pct >= 50 ? "In progress"
    : pct > 0   ? "Getting started"
    : "Not started";
  const masteryColor = pct >= 80 ? "text-green-600" : pct >= 50 ? "text-amber-600" : "text-violet-600";

  return (
    <Link href={`/works/${work.id}`}>
      <Card className="hover-elevate cursor-pointer transition-all hover:border-primary/50 group h-full">
        <CardContent className="p-5 flex gap-4 h-full">
          {/* Mastery ring / placeholder */}
          <div className="flex flex-col items-center gap-1 shrink-0 pt-1">
            {hasConcepts ? (
              <>
                <div className="relative">
                  <MasteryRing pct={pct} />
                  <span className="absolute inset-0 flex items-center justify-center text-[11px] font-mono font-bold">
                    {pct}%
                  </span>
                </div>
              </>
            ) : (
              <div className="w-12 h-12 rounded-full border-4 border-dashed border-muted flex items-center justify-center">
                <Brain className="w-4 h-4 text-muted-foreground/40" />
              </div>
            )}
          </div>

          {/* Content */}
          <div className="flex-1 min-w-0 space-y-2">
            <div>
              <h3 className="font-serif font-semibold leading-snug group-hover:text-primary transition-colors line-clamp-1">
                {work.title}
              </h3>
              {hasConcepts ? (
                <p className={`text-xs font-mono mt-0.5 ${masteryColor}`}>{masteryLabel}</p>
              ) : (
                <p className="text-xs font-mono text-muted-foreground mt-0.5">No concepts seeded</p>
              )}
            </div>

            <div className="flex items-center gap-3 text-xs text-muted-foreground font-mono flex-wrap">
              {hasConcepts && (
                <>
                  <span className="flex items-center gap-1">
                    <Target className="w-3 h-3" />
                    {work.graduated_count}/{work.concept_count}
                  </span>
                  <span className="flex items-center gap-1">
                    <Zap className="w-3 h-3" />
                    {work.concept_count - work.graduated_count} to go
                  </span>
                </>
              )}
              {(work as any).knowledge_count > 0 && (
                <span className="flex items-center gap-1">
                  <Lightbulb className="w-3 h-3" />
                  {(work as any).knowledge_count} nodes
                </span>
              )}
            </div>

            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant="outline" className="text-[9px] font-mono uppercase px-1.5 h-4">
                {work.work_type}
              </Badge>
              <Button
                asChild
                size="sm"
                variant="ghost"
                className="gap-1 text-xs h-6 ml-auto opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity p-0"
                onClick={e => e.stopPropagation()}
              >
                <Link href={`/works/${work.id}`}>
                  Study <ChevronRight className="w-3 h-3" />
                </Link>
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

// ─── Page ─────────────────────────────────────────────────────────────────────

export default function LearnPage() {
  const { data, isLoading } = useQuery<{ works: LearnWork[] }>({
    queryKey: ["learn"],
    queryFn: () => apiFetch(`${BASE}/learn`).then(r => r.json()),
    staleTime: 30_000,
    refetchInterval: 60_000,
  });

  const works = data?.works ?? [];
  const worksWithConcepts = works.filter(w => w.concept_count > 0);
  const worksNeedingSeed = works.filter(w => w.concept_count === 0 && (w as any).knowledge_count > 0);

  const totalConcepts = works.reduce((a, w) => a + w.concept_count, 0);
  const totalGraduated = works.reduce((a, w) => a + w.graduated_count, 0);
  const overallPct = totalConcepts > 0 ? Math.round(totalGraduated / totalConcepts * 100) : 0;

  return (
    <div className="space-y-8 max-w-5xl animate-in fade-in slide-in-from-bottom-4 duration-500">
      {/* Header */}
      <div>
        <span className="eyebrow mb-1">The Tutor</span>
        <h1 className="vellum-h1">Learn</h1>
        <div className="gilt-rule w-28" />
        <p className="text-[13px] mt-1.5" style={{ color: 'var(--ink-soft)' }}>
          Socratic study sessions grounded in your own sources.
        </p>
      </div>

      {/* Overall scorecard */}
      {!isLoading && totalConcepts > 0 && (
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: "Total Concepts", value: totalConcepts, icon: Target, color: "text-violet-500" },
            { label: "Graduated", value: totalGraduated, icon: GraduationCap, color: "text-green-500" },
            { label: "Overall Mastery", value: `${overallPct}%`, icon: TrendingUp, color: "text-primary" },
          ].map(({ label, value, icon: Icon, color }) => (
            <Card key={label} className="bg-card">
              <CardContent className="p-4 flex items-center gap-3">
                <Icon className={`w-5 h-5 shrink-0 ${color}`} />
                <div>
                  <div className="text-2xl font-serif font-semibold">{value}</div>
                  <div className="text-[10px] font-mono uppercase text-muted-foreground">{label}</div>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Works with active learning */}
      {isLoading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-40 w-full rounded-xl" />)}
        </div>
      ) : works.length === 0 ? (
        <Card className="border-dashed bg-muted/20">
          <CardContent className="p-12 text-center space-y-4">
            <GraduationCap className="w-10 h-10 text-muted-foreground mx-auto opacity-40" />
            <div className="space-y-1">
              <p className="font-medium">No Works yet</p>
              <p className="text-sm text-muted-foreground">
                Create a Work, import documents, and seed learning concepts to start.
              </p>
            </div>
            <Button asChild size="sm">
              <Link href="/works">Go to Works</Link>
            </Button>
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-6">
          {worksWithConcepts.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
                <BookOpen className="w-4 h-4 text-primary" /> Active Study
              </h2>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {worksWithConcepts.map(w => <LearnWorkCard key={w.id} work={w} />)}
              </div>
            </div>
          )}

          {worksNeedingSeed.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-base font-serif font-medium text-muted-foreground">
                Ready to seed — click a Work to start
              </h2>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
                {worksNeedingSeed.map(w => <LearnWorkCard key={w.id} work={w} />)}
              </div>
            </div>
          )}

          {works.filter(w => w.concept_count === 0 && !(w as any).knowledge_count).length > 0 && (
            <div className="space-y-3">
              <h2 className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
                No knowledge yet
              </h2>
              <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
                {works.filter(w => w.concept_count === 0 && !(w as any).knowledge_count).map(w => (
                  <Link key={w.id} href={`/works/${w.id}`}>
                    <div className="p-4 rounded-xl border border-border/50 hover:border-primary/30 bg-muted/10 cursor-pointer group transition-all">
                      <p className="text-sm font-medium line-clamp-1 group-hover:text-primary transition-colors">{w.title}</p>
                      <p className="text-[10px] font-mono text-muted-foreground mt-1">Import documents to begin</p>
                    </div>
                  </Link>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
