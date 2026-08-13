import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Lightbulb,
  Sparkles,
  Loader2,
  ThumbsUp,
  ChevronDown,
  ChevronUp,
  Star,
  Zap,
  BookOpen,
  Clock,
  AlertCircle,
} from "lucide-react";

import { Button }   from "@/components/ui/button";
import { Badge }    from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { EmptyState, ErrorState } from "@/components/primitives";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
import { toast } from "sonner";

// ── Types ────────────────────────────────────────────────────────────────────

interface BrainstormIdea {
  id:               string;
  domain:           string;
  text:             string;
  originality:      number;   // 0-1
  usefulness:       number;   // 1-5
  on_pareto_front:  boolean;
  knowledge_item_id: string | null;
}

interface BrainstormSession {
  id:            string;
  work_id:       string;
  seed_prompt:   string;
  context_type:  string;
  status:        "running" | "done" | "failed";
  ideas:         BrainstormIdea[];
  domain_count:  number;
  created_at:    string;
  completed_at:  string | null;
}

// ── Constants ────────────────────────────────────────────────────────────────

const CONTEXT_TYPES = [
  { value: "general",              label: "General exploration" },
  { value: "narrative_structure",  label: "Narrative structure" },
  { value: "chapter_architecture", label: "Chapter architecture" },
  { value: "knowledge_organization", label: "Knowledge organization" },
  { value: "research_planning",    label: "Research planning" },
] as const;

const N_DOMAINS_OPTIONS = [3, 5, 7, 10] as const;

// ── Helpers ───────────────────────────────────────────────────────────────────

function OriginalityBar({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const barStyle: React.CSSProperties =
    pct >= 70 ? { background: "var(--gd-success)" } :
    pct >= 45 ? { background: "var(--gd-caution)" }    :
                { background: "var(--gd-dim)", opacity: 0.6 };
  return (
    <div className="flex items-center gap-1.5 min-w-0">
      <div className="flex-1 h-1 bg-muted rounded-full overflow-hidden">
        <div className="h-full rounded-full" style={{ width: `${pct}%`, ...barStyle }} />
      </div>
      <span className="text-[10px] font-mono text-muted-foreground tabular-nums w-6 text-right">
        {pct}%
      </span>
    </div>
  );
}

function UsefulnessStars({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-0.5">
      {[1,2,3,4,5].map(n => (
        <Star
          key={n}
          className="w-2.5 h-2.5"
          style={n <= value ? { color: "var(--gd-bronze)", fill: "var(--gd-bronze)" } : undefined}
        />
      ))}
    </div>
  );
}

function DomainPill({ domain }: { domain: string }) {
  // Derive a stable colour from the domain string
  const hue = domain.split("").reduce((acc, c) => acc + c.charCodeAt(0), 0) % 360;
  const style = {
    backgroundColor: `hsl(${hue} 40% 92%)`,
    color:           `hsl(${hue} 50% 35%)`,
    borderColor:     `hsl(${hue} 30% 80%)`,
  };
  return (
    <span
      className="inline-block px-1.5 py-0.5 rounded text-[9px] font-mono border leading-none"
      style={style}
    >
      {domain.split(" ").slice(0,2).join(" ")}
    </span>
  );
}

// ── Idea card ─────────────────────────────────────────────────────────────────

function IdeaCard({
  idea,
  workId,
  sessionId,
  onApproved,
}: {
  idea: BrainstormIdea;
  workId: string;
  sessionId: string;
  onApproved: (ideaId: string, knowledgeItemId: string) => void;
}) {
  const approveMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(
        `${BASE}/works/${workId}/brainstorm/${sessionId}/ideas/${idea.id}/approve`,
        { method: "POST" },
      );
      if (!r.ok) throw new Error((await r.json()).detail ?? "Failed");
      return r.json();
    },
    onSuccess: (data) => {
      toast.success("Idea added to knowledge");
      onApproved(idea.id, data.knowledge_item_id);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  const approved = !!idea.knowledge_item_id;

  return (
    <div className={`relative rounded-xl border p-4 space-y-3 transition-all ${
      idea.on_pareto_front
        ? "border-primary/30 bg-primary/[0.02] shadow-sm"
        : "border-border/50 bg-muted/10"
    } ${approved ? "opacity-60" : ""}`}>
      {/* Pareto badge */}
      {idea.on_pareto_front && (
        <div className="absolute -top-2.5 left-3">
          <Badge className="h-4 text-[9px] px-1.5 gap-0.5 bg-primary text-primary-foreground">
            <Zap className="w-2 h-2" /> Best
          </Badge>
        </div>
      )}

      {/* Domain + scores row */}
      <div className="flex items-center gap-2 flex-wrap">
        <DomainPill domain={idea.domain} />
        <UsefulnessStars value={idea.usefulness} />
        <div className="flex-1 min-w-[60px]">
          <OriginalityBar value={idea.originality} />
        </div>
      </div>

      {/* Idea text */}
      <p className="text-sm leading-relaxed">{idea.text}</p>

      {/* Footer */}
      <div className="flex items-center justify-between">
        <span className="text-[10px] text-muted-foreground font-mono">
          orig {Math.round(idea.originality * 100)}% · useful {idea.usefulness}/5
        </span>
        {approved ? (
          <Badge
            variant="outline"
            className="h-5 text-[10px]"
            style={{ color: "var(--gd-success)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" }}
          >
            ✓ In knowledge
          </Badge>
        ) : (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 px-2 text-[11px] gap-1 text-primary hover:bg-primary/5"
            onClick={() => approveMutation.mutate()}
            disabled={approveMutation.isPending}
          >
            {approveMutation.isPending
              ? <Loader2 className="w-3 h-3 animate-spin" />
              : <ThumbsUp className="w-3 h-3" />}
            Use this idea
          </Button>
        )}
      </div>
    </div>
  );
}

// ── Session results view ──────────────────────────────────────────────────────

function SessionResults({
  session,
  workId,
}: {
  session: BrainstormSession;
  workId: string;
}) {
  const [ideas, setIdeas] = useState(session.ideas);
  const pareto   = ideas.filter(i => i.on_pareto_front);
  const others   = ideas.filter(i => !i.on_pareto_front);
  const [showOthers, setShowOthers] = useState(false);

  const handleApproved = (ideaId: string, knowledgeItemId: string) => {
    setIdeas(prev =>
      prev.map(i => i.id === ideaId ? { ...i, knowledge_item_id: knowledgeItemId } : i)
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="space-y-0.5">
          <p className="text-xs text-muted-foreground font-mono">
            "{session.seed_prompt.slice(0, 80)}{session.seed_prompt.length > 80 ? "…" : ""}"
          </p>
          <p className="text-[10px] text-muted-foreground">
            {session.domain_count} domains · {ideas.length} ideas ·{" "}
            {session.completed_at
              ? new Date(session.completed_at).toLocaleString([], { dateStyle: "short", timeStyle: "short" })
              : "running"}
          </p>
        </div>
      </div>

      {/* Pareto front */}
      {pareto.length > 0 && (
        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Zap className="w-3.5 h-3.5 text-primary" />
            <span className="text-xs font-medium">Best ideas (originality × usefulness)</span>
          </div>
          <div className="grid gap-3 sm:grid-cols-2">
            {pareto.map(idea => (
              <IdeaCard
                key={idea.id}
                idea={idea}
                workId={workId}
                sessionId={session.id}
                onApproved={handleApproved}
              />
            ))}
          </div>
        </div>
      )}

      {/* Alternate ideas */}
      {others.length > 0 && (
        <div className="space-y-2">
          <button
            className="flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={() => setShowOthers(v => !v)}
          >
            {showOthers ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            {others.length} alternate idea{others.length !== 1 ? "s" : ""}
          </button>
          {showOthers && (
            <div className="grid gap-3 sm:grid-cols-2">
              {others.map(idea => (
                <IdeaCard
                  key={idea.id}
                  idea={idea}
                  workId={workId}
                  sessionId={session.id}
                  onApproved={handleApproved}
                />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Past sessions list ────────────────────────────────────────────────────────

function PastSessions({
  sessions,
  workId,
  onSelect,
}: {
  sessions: BrainstormSession[];
  workId: string;
  onSelect: (s: BrainstormSession) => void;
}) {
  if (!sessions.length) return null;
  return (
    <div className="space-y-1.5 pt-3 border-t border-border/40">
      <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
        Past sessions
      </p>
      {sessions.map(s => (
        <button
          key={s.id}
          onClick={() => onSelect(s)}
          className="w-full text-left flex items-center gap-2 p-2 min-h-11 rounded-lg hover:bg-muted/40 transition-colors"
        >
          <Clock className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          <span className="text-xs truncate flex-1">
            {s.seed_prompt.slice(0, 60)}{s.seed_prompt.length > 60 ? "…" : ""}
          </span>
          <span className="text-[10px] font-mono text-muted-foreground shrink-0">
            {s.ideas.length} ideas
          </span>
          <Badge
            variant="outline"
            className="h-4 text-[9px] px-1 shrink-0"
            style={
              s.status === "done"   ? { color: "var(--gd-success)", borderColor: "color-mix(in srgb, var(--gd-success) 28%, transparent)" } :
              s.status === "failed" ? {} :
                                      { color: "var(--gd-caution)", borderColor: "var(--gd-line-control)" }
            }
          >
            {s.status}
          </Badge>
        </button>
      ))}
    </div>
  );
}

// ── Main tab component ────────────────────────────────────────────────────────

export function BrainstormTab({ workId, initialSeed = "", initialContext = "general" }: { workId: string; initialSeed?: string; initialContext?: string }) {
  const qc = useQueryClient();
  const [seed,        setSeed]        = useState(initialSeed);
  const [contextType, setContextType] = useState<string>(initialContext);
  const [nDomains,    setNDomains]    = useState<number>(5);
  const [activeSession, setActiveSession] = useState<BrainstormSession | null>(null);

  // Fetch session history
  const { data: history = [], isError: historyError, refetch: refetchHistory } = useQuery<BrainstormSession[]>({
    queryKey: ["brainstorm-history", workId],
    queryFn:  async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/brainstorm`);
      if (!r.ok) throw new Error("Failed to load brainstorm history");
      return r.json();
    },
    staleTime: 60_000,
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      if (!seed.trim()) throw new Error("Seed prompt is required");
      const r = await apiFetch(`${BASE}/works/${workId}/brainstorm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          seed_prompt:  seed.trim(),
          context_type: contextType,
          n_domains:    nDomains,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? "Brainstorm failed");
      }
      return r.json() as Promise<BrainstormSession>;
    },
    onSuccess: (session) => {
      setActiveSession(session);
      qc.invalidateQueries({ queryKey: ["brainstorm-history", workId] });
      toast.success(`Generated ${session.ideas.length} ideas across ${session.domain_count} domains`);
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="space-y-6 pb-16">
      {/* Header */}
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Lightbulb className="w-4 h-4" style={{ color: "var(--gd-bronze)" }} />
          <h3 className="font-medium text-sm">Divergent Thinking</h3>
        </div>
        <p className="text-xs text-muted-foreground max-w-prose">
          Forces ideas through {nDomains} unrelated conceptual domains — ecology, jazz,
          game theory, law, architecture, and more — then scores each idea on
          originality and usefulness to surface the most promising approaches.
        </p>
      </div>

      {/* Input form */}
      <Card className="border-border/50">
        <CardContent className="p-4 space-y-3">
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-muted-foreground uppercase tracking-wider">
              Seed prompt
            </label>
            <Textarea
              value={seed}
              onChange={e => setSeed(e.target.value)}
              placeholder="What structural challenge do you want to approach differently? e.g. 'How should the middle section of the book build tension toward the climax?'"
              className="text-sm resize-none h-20 font-sans"
            />
          </div>
          <div className="flex items-end gap-3 flex-wrap">
            <div className="space-y-1 flex-1 min-w-[140px]">
              <label className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Context
              </label>
              <Select value={contextType} onValueChange={setContextType}>
                <SelectTrigger className="h-8 text-xs">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {CONTEXT_TYPES.map(ct => (
                    <SelectItem key={ct.value} value={ct.value} className="text-xs">
                      {ct.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1">
              <label className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                Domains
              </label>
              <Select value={String(nDomains)} onValueChange={v => setNDomains(Number(v))}>
                <SelectTrigger className="h-8 text-xs w-16">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {N_DOMAINS_OPTIONS.map(n => (
                    <SelectItem key={n} value={String(n)} className="text-xs">{n}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <Button
              size="sm"
              className="h-8 gap-1.5 text-xs"
              onClick={() => runMutation.mutate()}
              disabled={runMutation.isPending || !seed.trim()}
            >
              {runMutation.isPending
                ? <>
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    Exploring {nDomains} domains…
                  </>
                : <>
                    <Sparkles className="w-3.5 h-3.5" />
                    Generate ideas
                  </>}
            </Button>
          </div>

          {/* Running hint */}
          {runMutation.isPending && (
            <p className="text-[11px] text-muted-foreground">
              Running parallel domain-shift workers. This usually takes 15–30 seconds.
            </p>
          )}
        </CardContent>
      </Card>

      {/* Active session results */}
      {activeSession && activeSession.status === "done" && (
        <SessionResults session={activeSession} workId={workId} />
      )}

      {activeSession && activeSession.status === "failed" && (
        <div className="flex items-start gap-2 p-3 rounded-lg bg-destructive/5 border border-destructive/20">
          <AlertCircle className="w-4 h-4 text-destructive shrink-0 mt-0.5" />
          <div className="space-y-1">
            <p className="text-sm font-medium text-destructive">Session failed</p>
            <p className="text-xs text-muted-foreground">
              The local AI engine was unreachable. Ensure it is running,
              then try again.
            </p>
          </div>
        </div>
      )}

      {/* Recoverable error — history could not load */}
      {!activeSession && historyError && !runMutation.isPending && (
        <ErrorState
          title="Could not load brainstorm history"
          detail="Past sessions are temporarily unavailable. You can still generate new ideas above."
          onRetry={() => refetchHistory()}
        />
      )}

      {/* Empty state (no active session, no history) */}
      {!activeSession && !historyError && history.length === 0 && !runMutation.isPending && (
        <EmptyState
          icon={<Lightbulb />}
          title="No brainstorm sessions yet"
          description="Write a structural challenge above and generate ideas to explore it through radically different conceptual lenses."
          action={
            <div className="flex items-center gap-2 flex-wrap justify-center">
              {[
                "How should the book's central argument unfold?",
                "What structure supports the climax?",
                "How to sequence the chapters?",
              ].map(s => (
                <button
                  key={s}
                  onClick={() => setSeed(s)}
                  className="min-h-11 text-[11px] px-2.5 py-1 rounded-full border border-border/60 hover:bg-muted/40 text-muted-foreground hover:text-foreground transition-colors"
                >
                  {s}
                </button>
              ))}
            </div>
          }
        />
      )}

      {/* Past sessions */}
      {!activeSession && history.length > 0 && (
        <PastSessions
          sessions={history}
          workId={workId}
          onSelect={setActiveSession}
        />
      )}
      {activeSession && history.filter(s => s.id !== activeSession.id).length > 0 && (
        <PastSessions
          sessions={history.filter(s => s.id !== activeSession.id)}
          workId={workId}
          onSelect={setActiveSession}
        />
      )}
    </div>
  );
}

// ── Compact brainstorm panel for B3 pipeline stage ────────────────────────────
// Used by book-tab.tsx to offer a brainstorm run before the Architecture advance.

export function BrainstormB3Panel({ workId, workTitle }: { workId: string; workTitle?: string }) {
  const [expanded, setExpanded] = useState(false);
  const [seed, setSeed] = useState(
    workTitle ? `How should the structure and architecture of "${workTitle}" be designed?` : ""
  );
  const [sessionResult, setSessionResult] = useState<BrainstormSession | null>(null);
  const qc = useQueryClient();

  const runMutation = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${BASE}/works/${workId}/brainstorm`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          seed_prompt:  seed.trim() || `Architecture and structure for this work`,
          context_type: "chapter_architecture",
          n_domains:    5,
        }),
      });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error(body.detail ?? "Brainstorm failed");
      }
      return r.json() as Promise<BrainstormSession>;
    },
    onSuccess: (session) => {
      setSessionResult(session);
      qc.invalidateQueries({ queryKey: ["brainstorm-history", workId] });
    },
    onError: (err: Error) => toast.error(err.message),
  });

  return (
    <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--gd-line-control)", background: "var(--gd-bronze-soft)" }}>
      <button
        className="w-full flex items-center gap-2 p-3 text-left hover:opacity-80 transition-opacity min-h-11"
        onClick={() => setExpanded(v => !v)}
      >
        <Lightbulb className="w-4 h-4 shrink-0" style={{ color: "var(--gd-bronze)" }} />
        <div className="flex-1 min-w-0">
          <p className="text-xs font-medium">Explore architecture approaches first</p>
          <p className="text-[10px] text-muted-foreground">
            Generate unconventional structural ideas before committing to an architecture
          </p>
        </div>
        {expanded
          ? <ChevronUp className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
          : <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />}
      </button>

      {expanded && (
        <div className="px-3 pb-3 space-y-3 border-t" style={{ borderColor: "var(--gd-line-control)" }}>
          {!sessionResult ? (
            <>
              <Textarea
                value={seed}
                onChange={e => setSeed(e.target.value)}
                className="text-xs resize-none h-14 mt-3"
                placeholder="Describe the architectural challenge…"
              />
              <Button
                size="sm"
                variant="outline"
                className="h-7 gap-1.5 text-xs w-full"
                style={{ borderColor: "var(--gd-line-control)", color: "var(--gd-bronze)" }}
                onClick={() => runMutation.mutate()}
                disabled={runMutation.isPending}
              >
                {runMutation.isPending
                  ? <><Loader2 className="w-3 h-3 animate-spin" /> Exploring 5 domains…</>
                  : <><Sparkles className="w-3 h-3" /> Generate architecture ideas</>}
              </Button>
            </>
          ) : (
            <div className="mt-2 space-y-2">
              <p className="text-[10px] text-muted-foreground">
                {sessionResult.ideas.length} ideas generated · tap "Use this idea" to add to knowledge
              </p>
              {sessionResult.ideas.filter(i => i.on_pareto_front).slice(0, 2).map(idea => (
                <div key={idea.id} className="p-2.5 rounded-lg border border-border/50 bg-background space-y-1.5">
                  <DomainPill domain={idea.domain} />
                  <p className="text-xs leading-snug">{idea.text}</p>
                  <OriginalityBar value={idea.originality} />
                </div>
              ))}
              <a
                href={`?tab=brainstorm`}
                className="flex items-center gap-1 text-[11px] text-primary hover:underline"
              >
                <BookOpen className="w-3 h-3" />
                See all {sessionResult.ideas.length} ideas in Brainstorm tab
              </a>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
