import { useState, useEffect, useRef } from "react";
import { useParams, useLocation, Link } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  ArrowLeft, Globe2, Play, CheckCircle2, Clock, AlertTriangle, Loader2,
  ChevronRight, Terminal, Sparkles, Eye, Hammer, ThumbsUp, ThumbsDown,
  RotateCcw, Palette, FileText, Wrench, RefreshCw,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";
import { Page, ErrorState, LoadingState } from "@/components/primitives";

const API = `${import.meta.env.BASE_URL}api/forge`.replace(/\/+/g, "/").replace(/\/$/, "");

type ForgeProject = {
  id: string; name: string; brief: string; status: string;
  work_id: string | null; config_data: Record<string, any>;
  created_at: string; updated_at: string;
};
type ForgeJob = {
  id: string; project_id: string; type: string; status: string;
  instruction: string | null; plan_job_id: string | null; design_job_id: string | null;
  target_job_id: string | null; build_dir: string | null;
  created_at: string; started_at: string | null; completed_at: string | null;
  meta_data: Record<string, any>; artifacts?: any[];
};
type ForgeEvent = {
  id: string; phase: string; message: string; data: any; ts: string;
};

// ── Pipeline step definitions ──────────────────────────────────────────────────

const STEPS = [
  { type: "PLAN",   label: "Plan",   icon: FileText,      desc: "AI generates site architecture" },
  { type: "DESIGN", label: "Design", icon: Palette,        desc: "Choose a visual direction" },
  { type: "BUILD",  label: "Build",  icon: Hammer,         desc: "Agent writes the site" },
  { type: "VERIFY", label: "Verify", icon: CheckCircle2,   desc: "Quality gates" },
];

// ── Status styling ─────────────────────────────────────────────────────────────

const STATUS_STYLE: Record<string, { cls: string; style: React.CSSProperties }> = {
  pending:           { cls: "text-muted-foreground bg-muted/50", style: {} },
  running:           { cls: "", style: { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" } },
  // awaiting_approval (was blue / info-running) → gilt, nearest VELLUM token
  awaiting_approval: { cls: "", style: { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" } },
  passed:            { cls: "", style: { color: "var(--gd-success)", background: "var(--gd-primary-soft)" } },
  // conditional (was orange / warn-with-caveat) → gilt/rust blend, between gilt and rust
  conditional:       { cls: "", style: { color: "color-mix(in srgb, var(--gd-bronze) 55%, var(--gd-danger))", background: "color-mix(in srgb, var(--gd-danger) 8%, transparent)" } },
  blocked:           { cls: "text-destructive bg-destructive/10", style: {} },
  failed:            { cls: "text-destructive bg-destructive/10", style: {} },
  rejected:          { cls: "text-muted-foreground bg-muted/50", style: {} },
};

// Event-phase accent colors. Kept distinct across phase families using VELLUM
// tokens: plan/build → gilt, design (was purple, AI accent) → gilt/rust blend,
// cmd_run (was cyan) → green-2, *_complete/done → green-2.
const PHASE_STYLE: Record<string, React.CSSProperties> = {
  plan_start:      { color: "color-mix(in srgb, var(--gd-bronze) 70%, transparent)" },
  plan_ready:      { color: "var(--gd-bronze)" },
  plan_complete:   { color: "var(--gd-success)" },
  design_start:    { color: "color-mix(in srgb, var(--gd-bronze) 60%, var(--gd-danger))" },
  design_ready:    { color: "color-mix(in srgb, var(--gd-bronze) 45%, var(--gd-danger))" },
  design_complete: { color: "var(--gd-success)" },
  build_start:     { color: "var(--gd-bronze)" },
  cmd_run:         { color: "var(--gd-primary)" },
  build_done:      { color: "var(--gd-success)" },
  build_complete:  { color: "var(--gd-success)" },
  gates_done:      { color: "var(--gd-success)" },
  approved:        { color: "var(--gd-success)" },
};

// Phases that use a neutral utility class rather than a token color.
const PHASE_CLS: Record<string, string> = {
  file_written: "text-muted-foreground",
  gate_result:  "text-muted-foreground",
  job_error:    "text-destructive",
  rejected:     "text-muted-foreground",
  __done__:     "text-primary",
};

function phaseIcon(phase: string): string {
  if (phase.includes("start")) return "◐";
  if (phase.includes("done") || phase.includes("complete") || phase === "approved") return "✓";
  if (phase.includes("error") || phase.includes("rejected")) return "✗";
  if (phase === "file_written") return "·";
  if (phase === "gate_result") return "—";
  return "·";
}

// ── Sub-components ─────────────────────────────────────────────────────────────

function EventLog({ events }: { events: ForgeEvent[] }) {
  const bottomRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="font-mono text-[11px] leading-relaxed space-y-0.5">
      {events.map(ev => (
        <div key={ev.id} className="flex gap-2">
          <span className="text-muted-foreground/40 shrink-0">
            {ev.ts ? format(new Date(ev.ts), "HH:mm:ss") : "—"}
          </span>
          <span
            className={`shrink-0 ${PHASE_CLS[ev.phase] ?? (PHASE_STYLE[ev.phase] ? "" : "text-muted-foreground")}`}
            style={PHASE_STYLE[ev.phase]}
          >
            {phaseIcon(ev.phase)}
          </span>
          <span
            className={PHASE_CLS[ev.phase] ?? (PHASE_STYLE[ev.phase] ? "" : "text-foreground")}
            style={PHASE_STYLE[ev.phase]}
          >
            {ev.message}
          </span>
        </div>
      ))}
      <div ref={bottomRef} />
    </div>
  );
}

function ConceptCard({
  concept, selected, onSelect,
}: { concept: any; selected: boolean; onSelect: () => void }) {
  const pal = concept.palette ?? {};
  return (
    <button
      onClick={onSelect}
      className={`w-full text-left border rounded-xl p-4 transition-all ${
        selected
          ? "border-primary ring-1 ring-primary/30 bg-primary/5"
          : "border-border/60 hover:border-primary/30"
      }`}
    >
      {/* Colour swatches */}
      <div className="flex gap-1.5 mb-3">
        {Object.values(pal).slice(0, 5).map((hex, i) => (
          <div
            key={i}
            className="w-6 h-6 rounded-full border border-black/10"
            style={{ background: hex as string }}
            title={hex as string}
          />
        ))}
      </div>
      <div className="font-semibold text-sm mb-1">{concept.name}</div>
      <p className="text-xs text-muted-foreground leading-relaxed line-clamp-3">
        {concept.summary}
      </p>
      {concept.rationale && (
        <p className="mt-1.5 text-[11px] text-foreground/70 italic leading-relaxed line-clamp-2">
          Why: {concept.rationale}
        </p>
      )}
      {concept.typography && (
        <div className="mt-2 text-[10px] text-muted-foreground font-mono">
          {concept.typography.display} / {concept.typography.body}
        </div>
      )}
      {selected && (
        <div className="mt-2 text-[10px] text-primary font-mono uppercase tracking-wider">
          ✓ Selected
        </div>
      )}
    </button>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function ForgeDetail() {
  const { projectId } = useParams();
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();

  // Live event state
  const [events, setEvents] = useState<ForgeEvent[]>([]);
  const [streaming, setStreaming] = useState(false);
  const evtSourceRef = useRef<EventSource | null>(null);

  // UI state
  const [instruction, setInstruction] = useState("");
  const [selectedConceptId, setSelectedConceptId] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);

  // ── Queries
  const { data, isLoading, isError, refetch: refetchProject } = useQuery<{
    project: ForgeProject; jobs: ForgeJob[];
  }>({
    queryKey: ["forge-project", projectId],
    queryFn: () => apiFetch(`${API}/projects/${projectId}`).then(r => {
      if (!r.ok) throw new Error("Not found");
      return r.json();
    }),
    enabled: !!projectId,
    staleTime: 10_000,
    refetchInterval: streaming ? 3_000 : 15_000,
  });

  const project = data?.project;
  const jobs = data?.jobs ?? [];

  // Latest job by type
  const latestByType = (type: string) =>
    [...jobs].filter(j => j.type === type).sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
    )[0];

  const latestPlan   = latestByType("PLAN");
  const latestDesign = latestByType("DESIGN");
  const latestBuild  = latestByType("BUILD");
  const activeJob    = jobs.find(j => j.status === "running" || j.status === "pending");

  // Auto-subscribe to events of the newest non-terminal job
  const streamJobId = activeJob?.id ?? jobs[0]?.id;

  const connectStream = (jobId: string) => {
    if (evtSourceRef.current) {
      evtSourceRef.current.close();
    }
    const lastId = events.at(-1)?.id;
    const url = `${API}/projects/${projectId}/jobs/${jobId}/events${lastId ? `?after_id=${lastId}` : ""}`;
    const es = new EventSource(url);
    evtSourceRef.current = es;
    setStreaming(true);
    es.onmessage = (e) => {
      try {
        const ev = JSON.parse(e.data) as ForgeEvent & { phase?: string; status?: string };
        if (ev.phase === "__done__") {
          setStreaming(false);
          es.close();
          refetchProject();
          return;
        }
        setEvents(prev => [...prev, ev as ForgeEvent]);
      } catch { /* ignore */ }
    };
    es.onerror = () => {
      setStreaming(false);
      es.close();
    };
  };

  // ── Mutations
  const startJob = useMutation({
    mutationFn: (body: any) =>
      apiFetch(`${API}/projects/${projectId}/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(r => { if (!r.ok) throw new Error("Failed"); return r.json(); }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["forge-project", projectId] });
      setEvents([]);
      setInstruction("");
      connectStream(res.job.id);
    },
    onError: () => toast.error("Could not start job"),
  });

  const approveJob = useMutation({
    mutationFn: (body: { jobId: string; selectedConceptId?: string | null }) =>
      apiFetch(`${API}/projects/${projectId}/jobs/${body.jobId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ selected_concept_id: body.selectedConceptId ?? null }),
      }).then(r => { if (!r.ok) throw new Error("Failed"); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forge-project", projectId] });
      toast.success("Approved — ready for next stage");
    },
    onError: () => toast.error("Could not approve"),
  });

  const rejectJob = useMutation({
    mutationFn: (jobId: string) =>
      apiFetch(`${API}/projects/${projectId}/jobs/${jobId}/reject`, { method: "POST" })
        .then(r => { if (!r.ok) throw new Error("Failed"); return r.json(); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["forge-project", projectId] });
      toast.success("Rejected");
    },
    onError: () => toast.error("Could not reject"),
  });

  // Artifact fetching for design concepts
  const { data: designArt } = useQuery({
    queryKey: ["forge-artifact", latestDesign?.id, "visual-design"],
    queryFn: () =>
      apiFetch(`${API}/projects/${projectId}/jobs/${latestDesign!.id}/artifact/visual-design`)
        .then(r => r.json()),
    enabled: !!latestDesign,
    staleTime: 300_000,
  });

  const { data: planArt } = useQuery({
    queryKey: ["forge-artifact", latestPlan?.id, "site-plan"],
    queryFn: () =>
      apiFetch(`${API}/projects/${projectId}/jobs/${latestPlan!.id}/artifact/site-plan`)
        .then(r => r.json()),
    enabled: !!latestPlan && latestPlan.status === "passed",
    staleTime: 300_000,
  });

  const designConcepts: any[] = designArt?.content?.concepts ?? [];

  // Current pipeline step
  const currentStep = (() => {
    if (!latestPlan || latestPlan.status === "awaiting_approval") return "PLAN";
    if (!latestDesign || latestDesign.status === "awaiting_approval") return "DESIGN";
    if (!latestBuild || ["blocked", "failed"].includes(latestBuild.status)) return "BUILD";
    return "DONE";
  })();

  if (isLoading) {
    return (
      <Page wide>
        <LoadingState rows={4} label="Loading project" />
      </Page>
    );
  }

  if (isError) {
    return (
      <Page wide>
        <ErrorState
          title="Could not load project"
          detail="The Pressworks service may be unreachable."
          onRetry={() => refetchProject()}
        />
      </Page>
    );
  }

  if (!project) {
    return (
      <Page wide>
        <div className="text-center py-20 text-muted-foreground">
          Project not found. <Link href="/forge" className="text-primary hover:underline">Back to Pressworks</Link>
        </div>
      </Page>
    );
  }

  return (
    <Page wide>
      <div className="space-y-8 animate-in fade-in duration-500 pb-20">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 text-sm font-mono uppercase tracking-widest text-muted-foreground">
          <Link href="/forge" className="hover:text-foreground transition-colors flex items-center gap-1">
            <ArrowLeft className="w-3 h-3" /> Pressworks
          </Link>
          <span>/</span>
          <span className="text-foreground">{project.name}</span>
        </div>
        {latestBuild?.status === "passed" && latestBuild.build_dir && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setShowPreview(!showPreview)}
            className="gap-1.5"
          >
            <Eye className="w-3.5 h-3.5" />
            {showPreview ? "Hide preview" : "Preview site"}
          </Button>
        )}
      </div>

      {/* Header */}
      <div>
        <div className="flex items-center gap-2">
          <Globe2 className="w-5 h-5 text-primary" />
          <h1 className="page-h1 truncate">{project.name}</h1>
        </div>
        <div className="gilt-rule w-32" />
        {project.brief && (
          <p className="text-sm text-muted-foreground mt-2 max-w-2xl leading-relaxed">
            {project.brief}
          </p>
        )}
      </div>

      {/* Pipeline stepper */}
      <div className="flex items-center gap-0 overflow-x-auto pb-2">
        {STEPS.map((step, i) => {
          const job = latestByType(step.type);
          const status = job?.status ?? "not_started";
          const isActive = step.type === currentStep;
          const isDone = job?.status === "passed";
          const isAwaiting = job?.status === "awaiting_approval";
          const isRunning = job?.status === "running";

          return (
            <div key={step.type} className="flex items-center">
              <div
                className={`flex flex-col items-center px-4 py-3 rounded-lg min-w-[96px] border transition-all ${
                  isActive && !isDone
                    ? "border-primary/30 bg-primary/5"
                    : isDone
                    ? ""
                    : "border-border/40 bg-card"
                }`}
                style={isDone && !(isActive && !isDone)
                  ? { borderColor: "var(--gd-line-control)", background: "var(--gd-primary-soft)" }
                  : undefined}
              >
                <div
                  className={`w-8 h-8 rounded-full flex items-center justify-center mb-1.5 ${
                    (isDone || isRunning || isAwaiting) ? "" : "bg-muted text-muted-foreground"
                  }`}
                  style={
                    isDone ? { color: "var(--gd-success)", background: "var(--gd-primary-soft)" }
                    : isRunning ? { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" }
                    : isAwaiting ? { color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)" }
                    : undefined
                  }
                >
                  {isRunning
                    ? <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    : isDone
                    ? <CheckCircle2 className="w-3.5 h-3.5" />
                    : <step.icon className="w-3.5 h-3.5" />
                  }
                </div>
                <div className="text-[11px] font-semibold font-mono">{step.label}</div>
                {isAwaiting && (
                  <div className="text-[9px] font-mono uppercase mt-0.5" style={{ color: "var(--gd-bronze)" }}>
                    Needs review
                  </div>
                )}
              </div>
              {i < STEPS.length - 1 && (
                <ChevronRight className="w-4 h-4 text-muted-foreground/40 shrink-0 mx-0.5" />
              )}
            </div>
          );
        })}
      </div>

      {/* Stage panels */}
      <div className="space-y-4">

        {/* PLAN stage */}
        <StagePanel
          title="Plan"
          icon={FileText}
          job={latestPlan}
          open={!latestPlan || latestPlan.status === "awaiting_approval"}
        >
          {!latestPlan ? (
            <div className="space-y-3">
              <p className="text-sm text-muted-foreground">
                The AI will analyse your brief and produce a structured site plan — pages, navigation,
                tone, and audience — for your review before any code is written.
              </p>
              {latestPlan === undefined && (
                <Textarea
                  placeholder="Optional additional instruction for the planner…"
                  value={instruction}
                  onChange={e => setInstruction(e.target.value)}
                  rows={2}
                  className="resize-none text-sm"
                />
              )}
              <Button
                onClick={() => startJob.mutate({ type: "PLAN", instruction: instruction || undefined })}
                disabled={startJob.isPending}
                className="gap-1.5"
              >
                {startJob.isPending
                  ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Starting…</>
                  : <><Sparkles className="w-3.5 h-3.5" /> Generate plan</>}
              </Button>
            </div>
          ) : latestPlan.status === "awaiting_approval" ? (
            <div className="space-y-4">
              {/* Show plan summary */}
              {planArt?.content && (
                <div className="border border-border/60 rounded-lg p-4 text-sm space-y-2 bg-muted/20">
                  <div className="font-semibold">{planArt.content.title}</div>
                  <p className="text-muted-foreground text-xs">{planArt.content.description}</p>
                  <div className="flex flex-wrap gap-1 mt-2">
                    {(planArt.content.pages ?? []).map((p: any) => (
                      <Badge key={p.slug} variant="outline" className="text-[10px]">{p.title}</Badge>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex gap-2">
                <Button
                  size="sm"
                  onClick={() => approveJob.mutate({ jobId: latestPlan.id })}
                  disabled={approveJob.isPending}
                  className="gap-1.5 hover:brightness-95 dark:hover:brightness-110"
                  style={{ background: "var(--gd-success)", color: "var(--gd-accent-ink)" }}
                >
                  <ThumbsUp className="w-3.5 h-3.5" /> Approve plan
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => rejectJob.mutate(latestPlan.id)}
                  disabled={rejectJob.isPending}
                  className="gap-1.5 text-destructive hover:bg-destructive/10"
                >
                  <ThumbsDown className="w-3.5 h-3.5" /> Reject
                </Button>
              </div>
            </div>
          ) : latestPlan.status === "passed" ? (
            <div className="flex items-center gap-2 text-sm" style={{ color: "var(--gd-success)" }}>
              <CheckCircle2 className="w-4 h-4" /> Plan approved
            </div>
          ) : (
            <JobStatusBadge status={latestPlan.status} />
          )}
        </StagePanel>

        {/* DESIGN stage */}
        {latestPlan?.status === "passed" && (
          <StagePanel
            title="Visual design"
            icon={Palette}
            job={latestDesign}
            open={!latestDesign || latestDesign.status === "awaiting_approval"}
          >
            {!latestDesign ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  Three distinct visual directions — palette, typography, layout — generated from your plan.
                  You pick one before the build starts.
                </p>
                <Textarea
                  placeholder="Optional direction (e.g. 'dark and literary', 'bright and playful')…"
                  value={instruction}
                  onChange={e => setInstruction(e.target.value)}
                  rows={2}
                  className="resize-none text-sm"
                />
                <Button
                  onClick={() => startJob.mutate({
                    type: "DESIGN",
                    plan_job_id: latestPlan.id,
                    instruction: instruction || undefined,
                  })}
                  disabled={startJob.isPending}
                  className="gap-1.5"
                >
                  <Palette className="w-3.5 h-3.5" /> Generate concepts
                </Button>
              </div>
            ) : latestDesign.status === "awaiting_approval" ? (
              <div className="space-y-4">
                {designConcepts.length > 0 ? (
                  <>
                    <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                      {designConcepts.map(c => (
                        <ConceptCard
                          key={c.id}
                          concept={c}
                          selected={selectedConceptId === c.id}
                          onSelect={() => setSelectedConceptId(c.id)}
                        />
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <Button
                        size="sm"
                        onClick={() => approveJob.mutate({
                          jobId: latestDesign.id,
                          selectedConceptId,
                        })}
                        disabled={approveJob.isPending || !selectedConceptId}
                        className="gap-1.5 hover:brightness-95 dark:hover:brightness-110"
                        style={{ background: "var(--gd-success)", color: "var(--gd-accent-ink)" }}
                      >
                        <ThumbsUp className="w-3.5 h-3.5" />
                        {selectedConceptId ? "Approve selected concept" : "Select a concept first"}
                      </Button>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => rejectJob.mutate(latestDesign.id)}
                        disabled={rejectJob.isPending}
                        className="gap-1.5 text-destructive hover:bg-destructive/10"
                      >
                        <RotateCcw className="w-3.5 h-3.5" /> Regenerate
                      </Button>
                    </div>
                  </>
                ) : (
                  <p className="text-sm text-muted-foreground">Loading concepts…</p>
                )}
              </div>
            ) : latestDesign.status === "passed" ? (
              <div className="flex items-center gap-2 text-sm" style={{ color: "var(--gd-success)" }}>
                <CheckCircle2 className="w-4 h-4" />
                Design approved
                {designConcepts.find(c => c.id === latestDesign.meta_data?.selected_concept_id) && (
                  <span className="text-muted-foreground text-xs ml-1">
                    — {designConcepts.find(c => c.id === latestDesign.meta_data?.selected_concept_id)?.name}
                  </span>
                )}
              </div>
            ) : (
              <JobStatusBadge status={latestDesign.status} />
            )}
          </StagePanel>
        )}

        {/* BUILD stage */}
        {latestDesign?.status === "passed" && (
          <StagePanel
            title="Build"
            icon={Hammer}
            job={latestBuild}
            open={!latestBuild || latestBuild.status === "running" || latestBuild.status === "blocked"}
          >
            {!latestBuild ? (
              <div className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  The AI agent writes the full static website — HTML, CSS, JS, and design tokens —
                  following your approved plan and visual direction.
                </p>
                <Button
                  onClick={() => {
                    startJob.mutate({
                      type: "BUILD",
                      plan_job_id: latestPlan?.id,
                      design_job_id: latestDesign.id,
                    });
                  }}
                  disabled={startJob.isPending}
                  className="gap-1.5"
                >
                  <Hammer className="w-3.5 h-3.5" /> Start build
                </Button>
              </div>
            ) : (
              <div className="space-y-3">
                <JobStatusBadge status={latestBuild.status} />
                {latestBuild.status === "blocked" && (
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      startJob.mutate({
                        type: "REPAIR",
                        target_job_id: latestBuild.id,
                        instruction: "Fix quality-gate failures from the previous build.",
                      });
                    }}
                    disabled={startJob.isPending}
                    className="gap-1.5"
                  >
                    <Wrench className="w-3.5 h-3.5" /> Repair build
                  </Button>
                )}
              </div>
            )}
          </StagePanel>
        )}
      </div>

      {/* Live event log */}
      {events.length > 0 && (
        <div className="border border-border/60 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/60 bg-muted/30">
            <div className="flex items-center gap-2 text-xs font-mono">
              <Terminal className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Build log</span>
              {streaming && (
                <span className="flex items-center gap-1" style={{ color: "var(--gd-bronze)" }}>
                  <span className="w-1.5 h-1.5 rounded-full animate-pulse" style={{ background: "var(--gd-bronze)" }} />
                  live
                </span>
              )}
            </div>
            <button
              onClick={() => setEvents([])}
              className="text-[10px] font-mono text-muted-foreground hover:text-foreground transition-colors"
            >
              clear
            </button>
          </div>
          <ScrollArea className="h-64">
            <div className="p-4">
              <EventLog events={events} />
            </div>
          </ScrollArea>
        </div>
      )}

      {/* Inline preview */}
      {showPreview && latestBuild?.build_dir && (
        <div className="border border-border/60 rounded-xl overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/60 bg-muted/30">
            <div className="flex items-center gap-2 text-xs font-mono">
              <Eye className="w-3.5 h-3.5 text-muted-foreground" />
              <span className="text-muted-foreground">Site preview</span>
            </div>
            <a
              href={`${API}/projects/${projectId}/jobs/${latestBuild.id}/preview/`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-[10px] font-mono text-primary hover:underline"
            >
              open in new tab ↗
            </a>
          </div>
          <iframe
            src={`${API}/projects/${projectId}/jobs/${latestBuild.id}/preview/`}
            className="w-full h-[500px] border-0"
            title="Site preview"
          />
        </div>
      )}

      {/* Job history */}
      {jobs.length > 0 && (
        <div>
          <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground mb-3">
            Job history
          </h3>
          <div className="space-y-1.5">
            {jobs.map(j => (
              <div
                key={j.id}
                className="flex items-center justify-between text-xs border border-border/40 rounded-lg px-3 py-2 bg-card hover:bg-muted/20 transition-colors"
              >
                <div className="flex items-center gap-2">
                  <span className="font-mono text-muted-foreground">{j.type}</span>
                  <JobStatusBadge status={j.status} small />
                </div>
                <div className="flex items-center gap-3 text-muted-foreground">
                  {j.completed_at && (
                    <span>{format(new Date(j.completed_at), "MMM d HH:mm")}</span>
                  )}
                  {(j.status === "running" || j.status === "pending") && (
                    <button
                      onClick={() => { setEvents([]); connectStream(j.id); }}
                      className="text-primary hover:underline text-[10px]"
                    >
                      Watch
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
      </div>
    </Page>
  );
}

// ── Shared sub-components ─────────────────────────────────────────────────────

function JobStatusBadge({ status, small }: { status: string; small?: boolean }) {
  const label = status.replace(/_/g, " ");
  const cfg = STATUS_STYLE[status] ?? { cls: "text-muted-foreground bg-muted/50", style: {} };
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono ${
        small ? "text-[10px]" : "text-xs"
      } ${cfg.cls}`}
      style={cfg.style}
    >
      {status === "running" && <Loader2 className="w-2.5 h-2.5 animate-spin" />}
      {label}
    </span>
  );
}

function StagePanel({
  title, icon: Icon, job, open, children,
}: {
  title: string;
  icon: any;
  job?: ForgeJob;
  open?: boolean;
  children: React.ReactNode;
}) {
  const [expanded, setExpanded] = useState(open ?? false);
  // Keep open when the prop changes
  useEffect(() => { if (open) setExpanded(true); }, [open]);

  return (
    <div className="border border-border/60 rounded-xl overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between px-4 py-3 bg-card hover:bg-muted/20 transition-colors"
      >
        <div className="flex items-center gap-2">
          <Icon className="w-4 h-4 text-muted-foreground" />
          <span className="text-sm font-semibold">{title}</span>
          {job && <JobStatusBadge status={job.status} small />}
        </div>
        <ChevronRight className={`w-4 h-4 text-muted-foreground transition-transform ${expanded ? "rotate-90" : ""}`} />
      </button>
      {expanded && (
        <div className="px-4 pb-4 pt-2 border-t border-border/40">
          {children}
        </div>
      )}
    </div>
  );
}
