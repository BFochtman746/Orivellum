import { useState, useEffect } from "react";
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
import {
  BookOpen, Library, MessageSquare, Target, Activity, FileText, CheckCircle2,
  Clock, Plus, Upload, FolderPlus, Sparkles, RefreshCw, ArrowRight, Lightbulb,
  Telescope, Zap, GitMerge, AlertTriangle, BookMarked, GraduationCap, Award,
  Star, BarChart3,
} from "lucide-react";
import { Link, useLocation } from "wouter";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

const KIND_ICON: Record<string, React.ElementType> = {
  explore:    Telescope,
  deep_dive:  BookOpen,
  practice:   Zap,
  connect:    GitMerge,
  gap:        Lightbulb,
};
const KIND_COLOR: Record<string, string> = {
  explore:   "text-blue-500",
  deep_dive: "text-violet-500",
  practice:  "text-emerald-500",
  connect:   "text-amber-500",
  gap:       "text-rose-500",
};

function SuggestionsWidget() {
  const [suggestions, setSuggestions] = useState<any[]>([]);
  const [loading, setLoading]   = useState(false);
  const [fetched, setFetched]   = useState(false);
  const [fetchError, setFetchError] = useState(false);
  const [, setLocation] = useLocation();

  async function fetchSuggestions() {
    setFetchError(false);
    try {
      const resp = await apiFetch(`${BASE}/suggestions?limit=6`);
      if (resp.ok) {
        const data = await resp.json();
        setSuggestions(data.suggestions ?? []);
        setFetched(true);
      } else {
        setFetchError(true);
      }
    } catch { setFetchError(true); }
  }

  async function handleGenerate() {
    setLoading(true);
    try {
      const resp = await apiFetch(`${BASE}/suggestions/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ limit: 6 }),
      });
      if (!resp.ok) throw new Error("generate failed");
      const data = await resp.json();
      setSuggestions(data.suggestions ?? []);
      setFetched(true);
      if ((data.suggestions ?? []).length === 0) {
        toast.info("No suggestions yet — add more documents to your library first.");
      }
    } catch {
      toast.error("Could not generate suggestions. Check that your library has processed documents.");
    } finally {
      setLoading(false);
    }
  }

  // Auto-fetch existing suggestions on mount
  useEffect(() => { fetchSuggestions(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-2xl font-serif font-semibold flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-primary" />
          What to Explore Next
        </h2>
        <Button
          size="sm" variant="outline"
          className="gap-1.5 font-mono text-xs uppercase tracking-wider"
          onClick={handleGenerate}
          disabled={loading}
        >
          {loading
            ? <><Loader2Dash className="w-3 h-3 animate-spin" />Thinking…</>
            : <><RefreshCw className="w-3 h-3" />Refresh</>}
        </Button>
      </div>

      {fetchError && !fetched ? (
        <div className="rounded-xl border border-dashed border-red-200/60 bg-red-50/30 p-6 text-center space-y-3">
          <p className="text-sm text-red-600">Could not load suggestions — server may be unreachable.</p>
          <Button size="sm" variant="outline" className="gap-1.5 border-red-200 text-red-600 hover:bg-red-50" onClick={fetchSuggestions}>
            <RefreshCw className="w-3 h-3" />Retry
          </Button>
        </div>
      ) : !fetched && !loading ? (
        <div className="rounded-xl border border-dashed border-border/50 bg-muted/10 p-8 text-center space-y-3">
          <Sparkles className="w-8 h-8 mx-auto text-muted-foreground opacity-40" />
          <p className="text-sm text-muted-foreground">
            Tap <strong>Refresh</strong> to get personalised study suggestions based on your knowledge base.
          </p>
          <Button size="sm" className="gap-2" onClick={handleGenerate} disabled={loading}>
            {loading ? <><Loader2Dash className="w-3.5 h-3.5 animate-spin" />Thinking…</> : <><Sparkles className="w-3.5 h-3.5" />Generate Suggestions</>}
          </Button>
        </div>
      ) : suggestions.length === 0 ? (
        <div className="rounded-xl border border-dashed border-border/50 bg-muted/10 p-6 text-center">
          <p className="text-sm text-muted-foreground">
            {loading ? "Analysing your knowledge base…" : "No suggestions yet — upload and process some documents, then tap Refresh."}
          </p>
        </div>
      ) : (
        <div className="grid sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {suggestions.map((s: any) => {
            const meta   = typeof s.meta === "string" ? JSON.parse(s.meta || "{}") : (s.meta ?? {});
            const kind   = meta.kind ?? s.kind ?? "explore";
            const KIcon  = KIND_ICON[kind] ?? Lightbulb;
            const kColor = KIND_COLOR[kind] ?? "text-primary";
            return (
              <div key={s.id}
                className="flex flex-col gap-2 p-4 rounded-xl border border-border/50 bg-card hover:border-primary/40 transition-colors group">
                <div className="flex items-start gap-2">
                  <KIcon className={`w-4 h-4 mt-0.5 shrink-0 ${kColor}`} />
                  <p className="text-sm font-medium leading-snug line-clamp-2 flex-1">{s.text}</p>
                </div>
                {meta.rationale && (
                  <p className="text-xs text-muted-foreground leading-relaxed line-clamp-2">
                    {meta.rationale}
                  </p>
                )}
                <div className="flex items-center justify-between mt-auto pt-1">
                  {meta.effort && (
                    <span className="text-[10px] font-mono text-muted-foreground bg-muted/40 px-2 py-0.5 rounded-full">
                      {meta.effort}
                    </span>
                  )}
                  <Button
                    size="sm" variant="ghost"
                    className="gap-1 text-xs h-7 ml-auto opacity-0 group-hover:opacity-100 [@media(hover:none)]:opacity-100 transition-opacity"
                    onClick={() => setLocation("/chat")}
                  >
                    Explore <ArrowRight className="w-3 h-3" />
                  </Button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Inline loader to avoid naming conflict
function Loader2Dash({ className }: { className?: string }) {
  return <svg className={className} xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M21 12a9 9 0 1 1-6.219-8.56"/></svg>;
}

interface TopGapEntry {
  work_id: string; work_title: string;
  kind: string; title: string; description: string; severity: string;
}

const SEV_DOT: Record<string, string>    = { high: "bg-red-500", medium: "bg-amber-400", low: "bg-blue-400" };
const SEV_BADGE: Record<string, string>  = {
  high:   "border-red-200 text-red-700 bg-red-50/70",
  medium: "border-amber-200 text-amber-700 bg-amber-50/70",
  low:    "border-blue-200 text-blue-700 bg-blue-50/70",
};

function TopGapsWidget() {
  const [gaps, setGaps]       = useState<TopGapEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);

  const loadGaps = async (forceRefresh = false) => {
    const url = `${BASE}/gaps/top?limit=3${forceRefresh ? "&refresh=true" : ""}`;
    try {
      const r = await apiFetch(url);
      const d = r.ok ? await r.json() : { gaps: [] };
      setGaps(d.gaps ?? []);
    } catch { /* silent */ } finally {
      setLoading(false);
      setRefreshing(false);
    }
  };

  useEffect(() => { loadGaps(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  if (loading || gaps.length === 0) return null;

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-serif font-semibold flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-amber-500" />
          Research Gaps
        </h2>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="font-mono text-[10px] uppercase text-amber-700 border-amber-200 bg-amber-50/70">
            {gaps.filter(g => g.severity === "high").length > 0
              ? `${gaps.filter(g => g.severity === "high").length} high`
              : `${gaps.length} found`}
          </Badge>
          <button
            onClick={() => { setRefreshing(true); loadGaps(true); }}
            disabled={refreshing}
            className="p-1 rounded hover:bg-muted/40 transition-colors text-muted-foreground hover:text-foreground disabled:opacity-40"
            title="Refresh gap analysis"
          >
            <RefreshCw className={`w-3 h-3 ${refreshing ? "animate-spin" : ""}`} />
          </button>
        </div>
      </div>
      <div className="space-y-1.5">
        {gaps.map((gap, i) => (
          <Link key={i} href={`/works/${gap.work_id}`}>
            <div className="flex items-start gap-3 p-3 rounded-lg border border-border/50 hover:border-primary/40 hover:bg-muted/20 transition-colors cursor-pointer group">
              <span className={`w-2 h-2 rounded-full mt-1.5 shrink-0 ${SEV_DOT[gap.severity] ?? "bg-muted"}`} />
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium leading-snug line-clamp-1 group-hover:text-primary transition-colors">
                  {gap.title}
                </p>
                <p className="text-[10px] font-mono text-muted-foreground mt-0.5">{gap.work_title}</p>
              </div>
              <Badge variant="outline" className={`text-[9px] font-mono uppercase shrink-0 ${SEV_BADGE[gap.severity] ?? ""}`}>
                {gap.severity}
              </Badge>
            </div>
          </Link>
        ))}
      </div>
    </div>
  );
}

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
  const { data: briefing, isLoading: loadingBriefing, isError: briefingError, refetch: refetchBriefing } = useGetBriefing({
    query: { queryKey: getGetBriefingQueryKey(), staleTime: 300_000, retry: 1 },
  });
  const { data: convsResp, isLoading: loadingConvs } = useListConversations(
    { limit: 5 },
    { query: { queryKey: getListConversationsQueryKey({ limit: 5 }), refetchInterval: 30_000, staleTime: 20_000 } },
  );

  const docTotal = summary?.document_count ?? 0;
  const docReady = summary?.documents_ready ?? 0;
  const tierCounts: Record<string, number> = (summary as any)?.document_tier_counts ?? {};
  const booksInProgress: number = (summary as any)?.books_in_progress ?? 0;
  const conceptsMastered: number = (summary as any)?.concepts_mastered ?? 0;

  return (
    <div className="space-y-8 max-w-5xl animate-in fade-in slide-in-from-bottom-4 duration-500">

      {/* Briefing Header */}
      <div className="space-y-2">
        <div className="text-sm font-mono text-muted-foreground uppercase tracking-wider">
          {briefing
            ? format(new Date(briefing.date || new Date()), "EEEE, MMMM do, yyyy")
            : briefingError
            ? format(new Date(), "EEEE, MMMM do, yyyy")
            : <Skeleton className="h-4 w-40" />}
        </div>
        <h1 className="text-4xl font-serif font-semibold tracking-tight text-foreground">
          {loadingBriefing
            ? <Skeleton className="h-10 w-64" />
            : briefing?.greeting || "Good morning."}
        </h1>
        {loadingBriefing ? (
          <Skeleton className="h-6 w-3/4 mt-4" />
        ) : briefingError ? (
          <p className="text-sm text-muted-foreground font-mono mt-2 flex items-center gap-2">
            <span className="text-destructive/70">⚠ Could not load workspace briefing.</span>
            <button
              onClick={() => refetchBriefing()}
              className="underline underline-offset-2 hover:text-foreground transition-colors"
            >
              Retry
            </button>
          </p>
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

      {/* ── Scorecard ─────────────────────────────────────────────────────────── */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-muted-foreground" />
          <h2 className="text-sm font-mono uppercase tracking-wider text-muted-foreground">Scorecard</h2>
        </div>

        {/* Tier breakdown — real canonical vs source vs artifact */}
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {loadingSummary ? (
            <>
              {[1,2,3,4,5,6].map(i => <Skeleton key={i} className="h-20 rounded-xl" />)}
            </>
          ) : (
            <>
              {/* CANON */}
              <Link href="/library?tier=canon">
                <div className="group p-4 rounded-xl border border-violet-200/60 bg-violet-50/50 hover:border-violet-300 hover:bg-violet-50 transition-colors cursor-pointer space-y-1 text-center">
                  <Star className="w-4 h-4 text-violet-500 mx-auto" />
                  <div className="text-2xl font-serif font-bold text-violet-700">{tierCounts.canon ?? 0}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-violet-600">Canon</div>
                </div>
              </Link>
              {/* SOURCE */}
              <Link href="/library?tier=source">
                <div className="group p-4 rounded-xl border border-blue-200/60 bg-blue-50/50 hover:border-blue-300 hover:bg-blue-50 transition-colors cursor-pointer space-y-1 text-center">
                  <Library className="w-4 h-4 text-blue-500 mx-auto" />
                  <div className="text-2xl font-serif font-bold text-blue-700">{tierCounts.source ?? 0}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-blue-600">Source</div>
                </div>
              </Link>
              {/* ARTIFACT */}
              <Link href="/library?tier=artifact">
                <div className="group p-4 rounded-xl border border-amber-200/60 bg-amber-50/50 hover:border-amber-300 hover:bg-amber-50 transition-colors cursor-pointer space-y-1 text-center">
                  <FileText className="w-4 h-4 text-amber-500 mx-auto" />
                  <div className="text-2xl font-serif font-bold text-amber-700">{(tierCounts.artifact ?? 0) + (tierCounts.system ?? 0)}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-amber-600">Artifact</div>
                </div>
              </Link>
              {/* Books in progress */}
              <Link href="/books">
                <div className="group p-4 rounded-xl border border-emerald-200/60 bg-emerald-50/50 hover:border-emerald-300 hover:bg-emerald-50 transition-colors cursor-pointer space-y-1 text-center">
                  <BookMarked className="w-4 h-4 text-emerald-500 mx-auto" />
                  <div className="text-2xl font-serif font-bold text-emerald-700">{booksInProgress}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-emerald-600">Books</div>
                </div>
              </Link>
              {/* Concepts mastered */}
              <Link href="/learn">
                <div className="group p-4 rounded-xl border border-rose-200/60 bg-rose-50/50 hover:border-rose-300 hover:bg-rose-50 transition-colors cursor-pointer space-y-1 text-center">
                  <GraduationCap className="w-4 h-4 text-rose-500 mx-auto" />
                  <div className="text-2xl font-serif font-bold text-rose-700">{conceptsMastered}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-rose-600">Mastered</div>
                </div>
              </Link>
              {/* Knowledge */}
              <Link href="/works">
                <div className="group p-4 rounded-xl border border-border/50 bg-card hover:border-primary/30 transition-colors cursor-pointer space-y-1 text-center">
                  <Target className="w-4 h-4 text-primary mx-auto" />
                  <div className="text-2xl font-serif font-bold">{summary?.knowledge_count ?? 0}</div>
                  <div className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground">Knowledge</div>
                </div>
              </Link>
            </>
          )}
        </div>
      </div>

      {/* Research Gaps — only shown when there are active critical gaps */}
      <TopGapsWidget />

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

        {/* Suggestions */}
        <div className="md:col-span-3">
          <SuggestionsWidget />
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
