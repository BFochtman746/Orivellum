import { useGetDashboardSummary, useGetDashboardActivity, useGetBriefing } from "@workspace/api-client-react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { format } from "date-fns";
import { BookOpen, Library, MessageSquare, Target, Activity } from "lucide-react";
import { Link } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";

export default function Dashboard() {
  const { data: summary, isLoading: loadingSummary } = useGetDashboardSummary();
  const { data: activityResp, isLoading: loadingActivity } = useGetDashboardActivity({ limit: 10 });
  const { data: briefing, isLoading: loadingBriefing } = useGetBriefing();

  return (
    <div className="space-y-8 max-w-5xl animate-in fade-in slide-in-from-bottom-4 duration-500">
      
      {/* Briefing Header */}
      <div className="space-y-2">
        <div className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
          {briefing ? format(new Date(briefing.date || new Date()), 'EEEE, MMMM do, yyyy') : <Skeleton className="h-4 w-40" />}
        </div>
        <h1 className="text-4xl font-serif font-semibold tracking-tight text-foreground">
          {loadingBriefing ? <Skeleton className="h-10 w-64" /> : briefing?.greeting || "Good morning."}
        </h1>
        {loadingBriefing ? (
          <Skeleton className="h-6 w-3/4 mt-4" />
        ) : (
          <p className="text-xl text-muted-foreground font-serif italic mt-2 max-w-3xl leading-relaxed">
            {briefing?.summary?.work_count ? `You have ${briefing.summary.work_count} active works and ${briefing.summary.pending_task_count} pending tasks requiring attention.` : "Your workspace is ready."}
          </p>
        )}
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard title="Works" value={summary?.work_count} icon={BookOpen} href="/works" loading={loadingSummary} />
        <StatCard title="Documents" value={summary?.document_count} icon={Library} href="/library" loading={loadingSummary} />
        <StatCard title="Conversations" value={summary?.conversation_count} icon={MessageSquare} href="/chat" loading={loadingSummary} />
        <StatCard title="Knowledge Nodes" value={summary?.knowledge_count} icon={Target} href="/works" loading={loadingSummary} />
      </div>

      <div className="grid md:grid-cols-3 gap-8">
        {/* Recent Works */}
        <div className="md:col-span-2 space-y-4">
          <div className="flex items-center justify-between">
            <h2 className="text-2xl font-serif font-semibold">Active Works</h2>
            <Button asChild variant="outline" size="sm" className="font-mono text-xs uppercase tracking-wider">
              <Link href="/works">View All</Link>
            </Button>
          </div>
          
          {loadingSummary ? (
            <div className="space-y-3">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          ) : summary?.recent_works && summary.recent_works.length > 0 ? (
            <div className="grid gap-3">
              {summary.recent_works.map((work) => (
                <Link key={work.id} href={`/works/${work.id}`}>
                  <Card className="hover-elevate cursor-pointer transition-colors hover:border-primary/50 group">
                    <CardContent className="p-4 flex items-center justify-between">
                      <div className="space-y-1">
                        <div className="flex items-center gap-2">
                          <h3 className="font-medium text-lg group-hover:text-primary transition-colors">{work.title}</h3>
                          <Badge variant="secondary" className="font-mono text-[10px] uppercase">{work.status}</Badge>
                          <Badge variant="outline" className="font-mono text-[10px] uppercase">{work.work_type}</Badge>
                        </div>
                        <p className="text-sm text-muted-foreground line-clamp-1">{work.description || 'No description provided.'}</p>
                      </div>
                      <div className="text-right shrink-0 ml-4 space-y-1">
                        <div className="text-xs font-mono text-muted-foreground">
                          {work.pending_tasks} tasks
                        </div>
                        <div className="text-xs font-mono text-muted-foreground">
                          {work.knowledge_count} nodes
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                </Link>
              ))}
            </div>
          ) : (
            <Card className="border-dashed bg-muted/30">
              <CardContent className="p-8 text-center space-y-3">
                <BookOpen className="w-8 h-8 text-muted-foreground mx-auto" />
                <p className="text-muted-foreground font-medium">No active works</p>
                <Button asChild variant="outline" size="sm">
                  <Link href="/works">Create your first work</Link>
                </Button>
              </CardContent>
            </Card>
          )}
        </div>

        {/* Activity Feed */}
        <div className="space-y-4">
          <div className="flex items-center gap-2">
            <Activity className="w-5 h-5 text-muted-foreground" />
            <h2 className="text-xl font-serif font-semibold">Activity</h2>
          </div>
          
          <Card className="bg-muted/10">
            <CardContent className="p-0">
              {loadingActivity ? (
                <div className="p-4 space-y-4">
                  {[1, 2, 3, 4].map(i => (
                    <div key={i} className="flex gap-3">
                      <Skeleton className="h-8 w-8 rounded-full" />
                      <div className="space-y-2 flex-1">
                        <Skeleton className="h-4 w-full" />
                        <Skeleton className="h-3 w-20" />
                      </div>
                    </div>
                  ))}
                </div>
              ) : activityResp?.activity && activityResp.activity.length > 0 ? (
                <div className="divide-y divide-border/50">
                  {activityResp.activity.map((item, i) => (
                    <div key={`${item.id}-${i}`} className="p-4 flex gap-3 hover:bg-muted/30 transition-colors">
                      <div className="w-2 h-2 mt-1.5 rounded-full bg-primary/40 shrink-0" />
                      <div>
                        <p className="text-sm font-medium">{item.label}</p>
                        <div className="flex items-center gap-2 mt-1">
                          <Badge variant="outline" className="text-[9px] uppercase font-mono px-1 py-0 h-4">{item.kind}</Badge>
                          <span className="text-xs text-muted-foreground font-mono">
                            {item.created_at ? format(new Date(item.created_at), 'MMM d, HH:mm') : ''}
                          </span>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="p-8 text-center text-sm text-muted-foreground">
                  No recent activity to show.
                </div>
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </div>
  );
}

function StatCard({ title, value, icon: Icon, href, loading }: { title: string, value?: number, icon: any, href: string, loading: boolean }) {
  return (
    <Link href={href}>
      <Card className="hover-elevate cursor-pointer transition-colors group">
        <CardContent className="p-5 space-y-2">
          <div className="flex items-center justify-between text-muted-foreground group-hover:text-primary transition-colors">
            <span className="font-mono text-xs uppercase tracking-wider">{title}</span>
            <Icon className="w-4 h-4" />
          </div>
          <div className="text-3xl font-serif font-semibold">
            {loading ? <Skeleton className="h-8 w-16" /> : value || 0}
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}
