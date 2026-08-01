import { useParams, useLocation } from "wouter";
import { useGetProject } from "@workspace/api-client-react";
import { useQuery } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Button } from "@/components/ui/button";
import { ArrowLeft, Target, BookOpen, TrendingUp, Network } from "lucide-react";
import { format } from "date-fns";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface Concept {
  id: string;
  name: string;
  description?: string | null;
  mastery: number;
  last_review?: string | null;
  prereq_count?: number;
  created_at?: string;
  work_id?: string | null;
}

function masteryLabel(m: number): string {
  if (m >= 0.9) return "Mastered";
  if (m >= 0.7) return "Proficient";
  if (m >= 0.4) return "Developing";
  if (m > 0) return "Beginner";
  return "Not started";
}

function masteryColor(m: number): string {
  if (m >= 0.9) return "text-emerald-700 bg-emerald-50 border-emerald-200";
  if (m >= 0.7) return "text-blue-700 bg-blue-50 border-blue-200";
  if (m >= 0.4) return "text-amber-700 bg-amber-50 border-amber-200";
  if (m > 0) return "text-orange-700 bg-orange-50 border-orange-200";
  return "text-muted-foreground bg-muted border-border";
}

export default function ProjectDetail() {
  const { projectId } = useParams<{ projectId: string }>();
  const [, navigate] = useLocation();

  const { data: projData, isLoading: projLoading } = useGetProject(projectId ?? "", {
    query: { enabled: !!projectId },
  });

  const { data: conceptsData, isLoading: conceptsLoading } = useQuery<{ concepts: Concept[] }>({
    queryKey: ["project-concepts", projectId],
    queryFn: () => fetch(`${BASE}/projects/${projectId}/concepts`).then((r) => r.json()),
    enabled: !!projectId,
    staleTime: 30_000,
  });

  const project = projData?.project as any;
  const concepts = conceptsData?.concepts ?? [];

  const avgMastery = concepts.length
    ? concepts.reduce((s, c) => s + (c.mastery ?? 0), 0) / concepts.length
    : 0;

  if (projLoading) {
    return (
      <div className="space-y-4 max-w-4xl animate-in fade-in duration-500">
        <Skeleton className="h-8 w-64" />
        <Skeleton className="h-4 w-48" />
        <Skeleton className="h-48 w-full" />
      </div>
    );
  }

  if (!project) {
    return (
      <div className="text-center py-20">
        <p className="text-muted-foreground">Project not found.</p>
        <Button variant="ghost" className="mt-4 gap-2" onClick={() => navigate("/projects")}>
          <ArrowLeft className="w-4 h-4" /> Back to Projects
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-6 max-w-4xl animate-in fade-in duration-500">
      {/* Header */}
      <div className="border-b border-border/50 pb-4">
        <button
          onClick={() => navigate("/projects")}
          className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground hover:text-foreground transition-colors mb-4 uppercase tracking-wide"
        >
          <ArrowLeft className="w-3.5 h-3.5" /> Projects
        </button>
        <div className="flex items-start gap-4">
          <div className="flex-1 min-w-0">
            <h1 className="text-3xl font-serif font-semibold tracking-tight">{project.name}</h1>
            {project.description && (
              <p className="text-muted-foreground mt-1 font-serif">{project.description}</p>
            )}
          </div>
          <div className="shrink-0 text-right space-y-1">
            <div className="text-2xl font-mono font-bold">{Math.round(avgMastery * 100)}%</div>
            <div className="text-xs font-mono text-muted-foreground uppercase">avg mastery</div>
          </div>
        </div>

        <div className="mt-4 space-y-1.5">
          <div className="flex justify-between text-xs font-mono text-muted-foreground uppercase">
            <span>Overall Progress</span>
            <span>{masteryLabel(avgMastery)}</span>
          </div>
          <Progress value={avgMastery * 100} className="h-2" />
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { icon: Network, label: "Concepts", value: concepts.length },
          { icon: TrendingUp, label: "Mastered", value: concepts.filter((c) => c.mastery >= 0.9).length },
          { icon: BookOpen, label: "In Progress", value: concepts.filter((c) => c.mastery > 0 && c.mastery < 0.9).length },
        ].map(({ icon: Icon, label, value }) => (
          <Card key={label} className="bg-muted/5">
            <CardContent className="p-4 flex items-center gap-3">
              <Icon className="w-5 h-5 text-muted-foreground" />
              <div>
                <div className="text-2xl font-mono font-semibold">{value}</div>
                <div className="text-xs font-mono text-muted-foreground uppercase">{label}</div>
              </div>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* Concepts list */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Target className="w-5 h-5 text-muted-foreground" />
          <h2 className="text-xl font-serif font-semibold">Concepts</h2>
          <span className="text-xs font-mono text-muted-foreground">({concepts.length})</span>
        </div>

        {conceptsLoading ? (
          <div className="space-y-3">
            {[1, 2, 3].map((i) => <Skeleton key={i} className="h-24 w-full" />)}
          </div>
        ) : concepts.length === 0 ? (
          <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg">
            <Target className="w-8 h-8 mx-auto mb-3 opacity-20" />
            <p className="text-muted-foreground">No concepts defined yet.</p>
          </div>
        ) : (
          <div className="grid gap-3">
            {concepts.map((c) => (
              <Card key={c.id} className="hover:border-border transition-colors">
                <CardContent className="p-4">
                  <div className="flex items-start gap-4">
                    <div className="flex-1 min-w-0 space-y-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <h3 className="font-medium">{c.name}</h3>
                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[10px] font-mono font-semibold border ${masteryColor(c.mastery)}`}>
                          {masteryLabel(c.mastery)}
                        </span>
                        {c.prereq_count && c.prereq_count > 0 ? (
                          <span className="text-[10px] font-mono text-muted-foreground">{c.prereq_count} prereq{c.prereq_count !== 1 ? "s" : ""}</span>
                        ) : null}
                      </div>
                      {c.description && (
                        <p className="text-sm text-muted-foreground line-clamp-2">{c.description}</p>
                      )}
                      {c.last_review && (
                        <p className="text-[10px] font-mono text-muted-foreground">
                          Last reviewed: {format(new Date(c.last_review), "MMM d, yyyy")}
                        </p>
                      )}
                    </div>
                    <div className="shrink-0 w-32 space-y-1">
                      <Progress value={(c.mastery ?? 0) * 100} className="h-1.5" />
                      <div className="text-right text-xs font-mono text-muted-foreground">
                        {Math.round((c.mastery ?? 0) * 100)}%
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            ))}
          </div>
        )}
      </div>

      {project.created_at && (
        <p className="text-xs font-mono text-muted-foreground">
          Created {format(new Date(project.created_at), "MMMM d, yyyy")}
        </p>
      )}
    </div>
  );
}
