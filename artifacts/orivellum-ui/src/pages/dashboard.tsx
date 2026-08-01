import {
  useGetDashboardSummary,
  useGetDashboardActivity,
  useGetBriefing,
  useListConversations,
  useCreateConversation,
  getGetDashboardSummaryQueryKey,
  getGetDashboardActivityQueryKey,
  getGetBriefingQueryKey,
  getListConversationsQueryKey,
} from "@workspace/api-client-react";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { format, formatDistanceToNow } from "date-fns";
import { BookOpen, Library, MessageSquare, Target, Activity, FileText, CheckCircle2, Clock, Plus, Upload, FolderPlus } from "lucide-react";
import { Link, useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

export default function Dashboard() {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const createConv = useCreateConversation();

  const handleNewChat = () => {
    createConv.mutate(
      { data: { title: "New Conversation" } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not create conversation"),
      }
    );
  };

  const { data: summary, isLoading: loadingSummary } = useGetDashboardSummary({
    query: { queryKey: getGetDashboardSummaryQueryKey(), refetchInterval: 30_000, staleTime: 20_000 },
  });
  const { data: activityResp, isLoading: loadingActivity } = useGetDashboardActivity(
    { limit: 10 },
    { query: { queryKey: getGetDashboardActivityQueryKey({ limit: 10 }), refetchInterval: 30_000, staleTime: 20_000 } },
  );
  const { data: briefing, isLoading: loadingBriefing } = useGetBriefing({
    query: { queryKey: getGetBriefingQueryKey(), staleTime: 300_000 },
  });
  const { data: convsResp, isLoading: loadingConvs } = useListConversations(
    { limit: 5 },
    { query: { queryKey: getListConversationsQueryKey({ limit: 5 }), refetchInterval: 30_000, staleTime: 20_000 } },
  );

  const docTotal = summary?.document_count ?? 0;
  const docReady = summary?.documents_ready ?? 0;
  const docSubtitle =
    docTotal > 0
      ? `${docReady} of ${docTotal} ready`
      : undefined;

  return (
    <div className="space-y-8 max-w-5xl animate-in fade-in slide-in-from-bottom-4 duration-500">

      {/* Briefing Header */}
      <div className="space-y-2">
        <div className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
          {briefing
            ? format(new Date(briefing.date || new Date()), "EEEE, MMMM do, yyyy")
            : <Skeleton className="h-4 w-40" />}
        </div>
        <h1 className="text-4xl font-serif font-semibold tracking-tight text-foreground">
          {loadingBriefing
            ? <Skeleton className="h-10 w-64" />
            : briefing?.greeting || "Good morning."}
        </h1>
        {loadingBriefing ? (
          <Skeleton className="h-6 w-3/4 mt-4" />
        ) : (
          <p className="text-xl text-muted-foreground font-serif italic mt-2 max-w-3xl leading-relaxed">
            {briefing?.summary?.work_count
              ? `You have ${briefing.summary.work_count} active work${briefing.summary.work_count !== 1 ? "s" : ""} and ${briefing.summary.pending_task_count ?? 0} pending task${briefing.summary.pending_task_count !== 1 ? "s" : ""} requiring attention.`
              : "Your workspace is ready."}
          </p>
        )}
      </div>

      {/* Quick Actions */}
      <div className="flex items-center gap-3 flex-wrap">
        <Button
          size="sm"
          className="gap-2 font-mono text-xs"
          onClick={handleNewChat}
          disabled={createConv.isPending}
        >
          <Plus className="w-3.5 h-3.5" /> New Conversation
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="gap-2 font-mono text-xs"
          onClick={() => setLocation("/library?import=1")}
        >
          <Upload className="w-3.5 h-3.5" /> Import Document
        </Button>
        <Button
          size="sm"
          variant="outline"
          className="gap-2 font-mono text-xs"
          onClick={() => setLocation("/works?create=1")}
        >
          <FolderPlus className="w-3.5 h-3.5" /> Create Work
        </Button>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard
          title="Works"
          value={summary?.work_count}
          icon={BookOpen}
          href="/works"
          loading={loadingSummary}
        />
        <StatCard
          title="Documents"
          value={docTotal}
          subtitle={docSubtitle}
          icon={Library}
          href="/library"
          loading={loadingSummary}
        />
        <StatCard
          title="Conversations"
          value={summary?.conversation_count}
          icon={MessageSquare}
          href="/chat"
          loading={loadingSummary}
        />
        <StatCard
          title="Knowledge Nodes"
          value={summary?.knowledge_count}
          icon={Target}
          href="/works"
          loading={loadingSummary}
        />
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
                      <div className="space-y-1 min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <h3 className="font-medium text-lg group-hover:text-primary transition-colors">{work.title}</h3>
                          <Badge variant="secondary" className="font-mono text-[10px] uppercase">{work.status}</Badge>
                          <Badge variant="outline" className="font-mono text-[10px] uppercase">{work.work_type}</Badge>
                        </div>
                        <p className="text-sm text-muted-foreground line-clamp-1">{work.description || "No description provided."}</p>
                      </div>
                      <div className="text-right shrink-0 ml-4 space-y-1">
                        <div className="text-xs font-mono text-muted-foreground">{work.pending_tasks} tasks</div>
                        <div className="text-xs font-mono text-muted-foreground">{work.knowledge_count} nodes</div>
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

          {/* Recent Documents */}
          {(loadingSummary || (summary?.recent_documents && summary.recent_documents.length > 0)) && (
            <div className="space-y-3 pt-2">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <FileText className="w-4 h-4 text-muted-foreground" />
                  <h2 className="text-lg font-serif font-semibold">Recent Documents</h2>
                </div>
                <Button asChild variant="ghost" size="sm" className="h-6 text-xs font-mono text-muted-foreground">
                  <Link href="/library">View all</Link>
                </Button>
              </div>
              {loadingSummary ? (
                <div className="space-y-2">
                  {[1, 2, 3].map(i => <Skeleton key={i} className="h-10 w-full" />)}
                </div>
              ) : (
                <div className="space-y-1">
                  {(summary?.recent_documents ?? []).map((doc) => (
                    <Link key={doc.id} href={`/library/${doc.id}`}>
                      <div className="flex items-center justify-between p-2.5 rounded-lg hover:bg-muted/30 transition-colors cursor-pointer group">
                        <div className="min-w-0 flex-1 flex items-center gap-2">
                          {doc.readiness === "ready"
                            ? <CheckCircle2 className="w-3.5 h-3.5 text-green-500 shrink-0" />
                            : <Clock className="w-3.5 h-3.5 text-amber-500 shrink-0" />}
                          <p className="text-sm font-medium truncate group-hover:text-primary transition-colors">{doc.title}</p>
                        </div>
                        <div className="flex items-center gap-2 ml-2 shrink-0">
                          {doc.kind && (
                            <Badge variant="outline" className="text-[9px] uppercase font-mono px-1 py-0 h-4">{doc.kind}</Badge>
                          )}
                          {doc.created_at && (
                            <span className="text-[10px] font-mono text-muted-foreground/50">
                              {formatDistanceToNow(new Date(doc.created_at), { addSuffix: false })}
                            </span>
                          )}
                        </div>
                      </div>
                    </Link>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Recent Conversations + Activity Feed */}
        <div className="space-y-6">
          {/* Recent Conversations */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <MessageSquare className="w-4 h-4 text-muted-foreground" />
                <h2 className="text-lg font-serif font-semibold">Conversations</h2>
              </div>
              <Button asChild variant="ghost" size="sm" className="h-6 text-xs font-mono text-muted-foreground">
                <Link href="/chat">View all</Link>
              </Button>
            </div>
            {loadingConvs ? (
              <div className="space-y-2">
                {[1, 2].map(i => <Skeleton key={i} className="h-12 w-full" />)}
              </div>
            ) : convsResp?.conversations?.length ? (
              <div className="space-y-1">
                {convsResp.conversations.slice(0, 4).map((c) => (
                  <Link key={c.id} href={`/chat?id=${c.id}`}>
                    <div className="flex items-center justify-between p-2.5 rounded-lg hover:bg-muted/30 transition-colors cursor-pointer group">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-medium truncate group-hover:text-primary transition-colors">{c.title || "Untitled"}</p>
                        {c.last_message && (
                          <p className="text-xs text-muted-foreground truncate">{c.last_message.slice(0, 55)}</p>
                        )}
                      </div>
                      {c.updated_at && (
                        <span className="text-[10px] font-mono text-muted-foreground/50 ml-2 shrink-0">
                          {formatDistanceToNow(new Date(c.updated_at), { addSuffix: false })}
                        </span>
                      )}
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground italic px-1">No conversations yet.</p>
            )}
          </div>

          {/* Activity Feed */}
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <Activity className="w-4 h-4 text-muted-foreground" />
              <h2 className="text-lg font-serif font-semibold">Activity</h2>
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
                    {activityResp.activity.map((item, i) => {
                      const href =
                        item.kind === "work"
                          ? `/works/${item.id}`
                          : item.kind === "document"
                          ? `/library/${item.id}`
                          : null;
                      const Inner = (
                        <div className={`p-4 flex gap-3 transition-colors ${href ? "hover:bg-muted/30 cursor-pointer" : ""}`}>
                          <div className="w-2 h-2 mt-1.5 rounded-full bg-primary/40 shrink-0" />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium truncate">{item.label}</p>
                            <div className="flex items-center gap-2 mt-1">
                              <Badge variant="outline" className="text-[9px] uppercase font-mono px-1 py-0 h-4">{item.kind}</Badge>
                              <span className="text-xs text-muted-foreground font-mono">
                                {item.created_at ? formatDistanceToNow(new Date(item.created_at), { addSuffix: true }) : ""}
                              </span>
                            </div>
                          </div>
                        </div>
                      );
                      return href ? (
                        <Link key={`${item.id}-${i}`} href={href}>{Inner}</Link>
                      ) : (
                        <div key={`${item.id}-${i}`}>{Inner}</div>
                      );
                    })}
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
    </div>
  );
}

function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  href,
  loading,
}: {
  title: string;
  value?: number;
  subtitle?: string;
  icon: React.ElementType;
  href: string;
  loading: boolean;
}) {
  return (
    <Link href={href}>
      <Card className="hover-elevate cursor-pointer transition-colors group">
        <CardContent className="p-5 space-y-2">
          <div className="flex items-center justify-between text-muted-foreground group-hover:text-primary transition-colors">
            <span className="font-mono text-xs uppercase tracking-wider">{title}</span>
            <Icon className="w-4 h-4" />
          </div>
          <div className="text-3xl font-serif font-semibold">
            {loading ? <Skeleton className="h-8 w-16" /> : value ?? 0}
          </div>
          {!loading && subtitle && (
            <div className="text-xs font-mono text-muted-foreground">{subtitle}</div>
          )}
          {loading && subtitle !== undefined && (
            <Skeleton className="h-3 w-24" />
          )}
        </CardContent>
      </Card>
    </Link>
  );
}
