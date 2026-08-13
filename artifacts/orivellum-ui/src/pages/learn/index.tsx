import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { Page, EmptyState, ErrorState } from "@/components/primitives";
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
  const color = pct >= 80 ? "var(--gd-success)" : pct >= 50 ? "var(--gd-bronze)" : "var(--gd-muted)";
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
  const masteryStyle = pct >= 80 ? { color: 'var(--gd-success)' } : pct >= 50 ? { color: 'var(--gd-bronze)' } : { color: 'var(--gd-muted)' };

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
                <p className="text-xs font-mono mt-0.5" style={masteryStyle}>{masteryLabel}</p>
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
  const { data, isLoading, isError, refetch } = useQuery<{ works: LearnWork[] }>({
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
    <Page eyebrow="The Tutor" title="Learn" wide>
      <p className="text-[13px] -mt-2 text-muted-foreground">
        Socratic study sessions grounded in your own sources.
      </p>

      {/* Overall scorecard */}
      {!isLoading && !isError && totalConcepts > 0 && (
        <div className="grid grid-cols-3 gap-4">
          {[
            { label: "Total Concepts",  value: totalConcepts,       icon: Target,       tokenColor: 'var(--gd-olive)',  tokenBg: 'var(--gd-olive-soft)' },
            { label: "Graduated",       value: totalGraduated,      icon: GraduationCap, tokenColor: 'var(--gd-bronze)', tokenBg: 'var(--gd-bronze-soft)' },
            { label: "Overall Mastery", value: `${overallPct}%`,    icon: TrendingUp,   tokenColor: 'var(--gd-success)', tokenBg: 'var(--gd-olive-soft)' },
          ].map(({ label, value, icon: Icon, tokenColor, tokenBg }) => (
            <div key={label} className="rounded-lg border border-card-border p-4 flex items-center gap-3"
                 style={{ background: tokenBg }}>
              <Icon className="w-5 h-5 shrink-0" style={{ color: tokenColor }} />
              <div className="min-w-0">
                <div className="text-2xl font-serif font-semibold truncate" style={{ color: tokenColor }}>{value}</div>
                <div className="text-[10px] font-mono uppercase truncate" style={{ color: tokenColor, opacity: 0.7 }}>{label}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Works with active learning */}
      {isLoading ? (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-40 w-full rounded-xl" />)}
        </div>
      ) : isError ? (
        <ErrorState
          title="Couldn't load your study progress"
          detail="The tutor data didn't come back. Check your connection and try again."
          onRetry={() => refetch()}
        />
      ) : works.length === 0 ? (
        <EmptyState
          icon={<GraduationCap />}
          title="No Works yet"
          description="Create a Work, import documents, and seed learning concepts to start."
          action={<Button asChild size="sm"><Link href="/works">Go to Works</Link></Button>}
        />
      ) : (
        <div className="space-y-6">
          {worksWithConcepts.length > 0 && (
            <div className="space-y-3">
              <h2 className="text-lg font-serif font-semibold flex items-center gap-2 text-balance">
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
    </Page>
  );
}
