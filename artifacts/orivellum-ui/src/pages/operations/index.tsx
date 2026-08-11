import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useListWorks, getListWorksQueryKey } from "@workspace/api-client-react";
import { useGdDark } from "@/lib/useGdDark";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  Workflow, Play, Pause, RotateCcw, XCircle, CheckCircle2, Loader2,
  CircleDashed, ChevronDown, ChevronRight, History, AlertTriangle,
  Sparkles, HelpCircle, BookmarkPlus, Trash2,
} from "lucide-react";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// ── Types ──────────────────────────────────────────────────────────────────────

interface Playbook {
  id: string;
  title: string;
  description: string;
  steps: { action_id: string; label: string; params?: Record<string, unknown> }[];
  custom?: boolean;
}

interface PlanStep {
  action_id: string;
  label: string;
  params: Record<string, unknown>;
}

interface PlanResult {
  status: "ok" | "clarify" | "error";
  plan?: { title: string; work_id: string | null; work_title: string | null; steps: PlanStep[] };
  question?: string;
  message?: string;
  problems?: string[];
}

interface OpStep {
  id: string;
  step_index: number;
  action_id: string;
  label: string;
  state: "pending" | "running" | "done" | "failed" | "cancelled";
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

interface Operation {
  id: string;
  title: string;
  playbook_id: string | null;
  work_id: string | null;
  state: "pending" | "running" | "paused" | "done" | "failed" | "cancelled";
  error: string | null;
  created_at: string;
  finished_at: string | null;
  steps?: OpStep[];
}

const ACTIVE_STATES = ["pending", "running", "paused"];

// ── Helpers ────────────────────────────────────────────────────────────────────

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

const STATE_BADGE: Record<Operation["state"], { label: string; cls: string }> = {
  pending: { label: "Queued", cls: "bg-muted text-muted-foreground" },
  running: { label: "Running", cls: "bg-primary/15 text-primary" },
  paused: { label: "Paused", cls: "bg-amber-500/15 text-amber-600" },
  done: { label: "Done", cls: "bg-emerald-500/15 text-emerald-600" },
  failed: { label: "Failed", cls: "bg-destructive/15 text-destructive" },
  cancelled: { label: "Cancelled", cls: "bg-muted text-muted-foreground" },
};

function StepIcon({ state }: { state: OpStep["state"] }) {
  if (state === "done") return <CheckCircle2 className="w-3.5 h-3.5" style={{ color: "var(--green-2)" }} />;
  if (state === "running") return <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />;
  if (state === "failed") return <XCircle className="w-3.5 h-3.5 text-destructive" />;
  if (state === "cancelled") return <XCircle className="w-3.5 h-3.5 text-muted-foreground/50" />;
  return <CircleDashed className="w-3.5 h-3.5 text-muted-foreground/40" />;
}

// ── Work selector ─────────────────────────────────────────────────────────────

function WorkSelector({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const { data } = useListWorks({}, { query: { queryKey: getListWorksQueryKey({}), staleTime: 30_000 } });
  const works = data?.works ?? [];
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="h-7 text-xs flex-1" data-testid="select-operation-work">
        <SelectValue placeholder="Select a Work…" />
      </SelectTrigger>
      <SelectContent>
        {works.map((w) => (
          <SelectItem key={w.id} value={w.id ?? ""} className="text-xs">
            {w.title ?? w.id}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ── Playbook card ──────────────────────────────────────────────────────────────

function PlaybookCard({
  playbook,
  onStart,
  onDelete,
  starting,
}: {
  playbook: Playbook;
  onStart: (playbookId: string, workId: string) => void;
  onDelete?: (playbookId: string) => void;
  starting: boolean;
}) {
  const [workId, setWorkId] = useState("");
  return (
    <Card className="border border-border/50 hover:border-border transition-colors">
      <CardContent className="pt-5 pb-4 space-y-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            {playbook.custom ? (
              <Sparkles className="w-4 h-4 text-primary" />
            ) : (
              <Workflow className="w-4 h-4 text-primary" />
            )}
          </div>
          <div className="flex-1 min-w-0">
            <span className="font-medium text-sm">{playbook.title}</span>
            {playbook.description && (
              <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
                {playbook.description}
              </p>
            )}
          </div>
          {playbook.custom && onDelete && (
            <Button
              size="sm"
              variant="ghost"
              className="h-6 w-6 p-0 text-muted-foreground/50 hover:text-destructive shrink-0"
              onClick={() => onDelete(playbook.id)}
              data-testid={`button-delete-${playbook.id}`}
            >
              <Trash2 className="w-3 h-3" />
            </Button>
          )}
        </div>
        <ol className="pl-12 space-y-1">
          {playbook.steps.map((s, i) => (
            <li key={i} className="text-[11px] text-muted-foreground flex items-center gap-1.5">
              <span className="font-mono text-muted-foreground/50">{i + 1}.</span> {s.label}
            </li>
          ))}
        </ol>
        <div className="flex items-center gap-2 pl-12">
          <WorkSelector value={workId} onChange={setWorkId} />
          <Button
            size="sm"
            className="h-7 text-xs gap-1.5 shrink-0"
            disabled={!workId || starting}
            onClick={() => onStart(playbook.id, workId)}
            data-testid={`button-start-${playbook.id}`}
          >
            <Play className="w-3 h-3" />
            Run
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

// ── Job planner (plain words → proposed plan) ─────────────────────────────────

function paramSummary(params: Record<string, unknown>): string {
  const entries = Object.entries(params ?? {}).filter(([, v]) => v !== null && v !== "");
  if (entries.length === 0) return "";
  return entries.map(([k, v]) => `${k}: ${String(v)}`).join(" · ");
}

function JobPlanner({ onStarted }: { onStarted: () => void }) {
  const [job, setJob] = useState("");
  const [result, setResult] = useState<PlanResult | null>(null);
  const [saveName, setSaveName] = useState("");
  const [saving, setSaving] = useState(false);
  const qc = useQueryClient();

  const planMutation = useMutation({
    mutationFn: async (jobText: string) => {
      const r = await apiFetch(`${API_BASE}/api/operations/plan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ job: jobText }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Planning failed" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Planning failed");
      }
      return r.json() as Promise<PlanResult>;
    },
    onSuccess: (data) => setResult(data),
    onError: (e: Error) => toast.error(e.message),
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const plan = result?.plan;
      if (!plan) throw new Error("No plan to run");
      const r = await apiFetch(`${API_BASE}/api/operations/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          title: plan.title,
          steps: plan.steps,
          work_id: plan.work_id ?? undefined,
        }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Could not start the operation" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Could not start the operation");
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Running — follow its progress below.");
      setResult(null);
      setJob("");
      onStarted();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const savePlaybook = async () => {
    const plan = result?.plan;
    if (!plan || !saveName.trim()) return;
    setSaving(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/operations/playbooks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: saveName.trim(), steps: plan.steps }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Could not save the playbook" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Could not save the playbook");
      }
      toast.success(`Saved "${saveName.trim()}" as a playbook.`);
      setSaveName("");
      qc.invalidateQueries({ queryKey: ["operations", "playbooks"] });
    } catch (e) {
      toast.error((e as Error).message);
    } finally {
      setSaving(false);
    }
  };

  const plan = result?.status === "ok" ? result.plan : undefined;

  return (
    <Card className="border border-primary/20 bg-primary/[0.02]">
      <CardContent className="pt-5 pb-4 space-y-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            <Sparkles className="w-4 h-4 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <span className="font-medium text-sm">Describe a job</span>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">
              Say what you want done in plain words — your local AI turns it into a step plan
              you approve before anything runs.
            </p>
          </div>
        </div>
        <div className="pl-12 space-y-2.5">
          <Textarea
            value={job}
            onChange={(e) => setJob(e.target.value)}
            placeholder={'e.g. "wait for my Sci-Fi Novel to finish processing, then render an audiobook with the George voice and let me know"'}
            className="text-xs min-h-[64px] resize-none"
            data-testid="input-job-description"
          />
          <div className="flex items-center gap-2">
            <Button
              size="sm"
              className="h-7 text-xs gap-1.5"
              disabled={!job.trim() || planMutation.isPending}
              onClick={() => planMutation.mutate(job.trim())}
              data-testid="button-plan-job"
            >
              {planMutation.isPending ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                <Sparkles className="w-3 h-3" />
              )}
              {planMutation.isPending ? "Planning…" : "Plan it"}
            </Button>
            {result && (
              <Button size="sm" variant="ghost" className="h-7 text-[11px] px-2 text-muted-foreground"
                onClick={() => setResult(null)}>
                Clear
              </Button>
            )}
          </div>

          {result?.status === "clarify" && (
            <div className="flex items-start gap-2 text-xs text-amber-600 bg-amber-500/10 rounded-md px-3 py-2"
              data-testid="text-plan-clarify">
              <HelpCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
              <span>{result.question} Add the answer to your description above and plan again.</span>
            </div>
          )}

          {result?.status === "error" && (
            <div className="space-y-1 text-xs text-destructive bg-destructive/10 rounded-md px-3 py-2"
              data-testid="text-plan-error">
              <div className="flex items-start gap-2">
                <AlertTriangle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{result.message}</span>
              </div>
              {(result.problems ?? []).slice(0, 4).map((p, i) => (
                <div key={i} className="pl-5 text-[11px] opacity-80">{p}</div>
              ))}
            </div>
          )}

          {plan && (
            <div className="border border-border/60 rounded-lg p-3 space-y-2.5 bg-background"
              data-testid="panel-proposed-plan">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-medium">{plan.title}</span>
                {plan.work_title && (
                  <Badge className="text-[10px] h-4 px-1.5 border-0 bg-primary/10 text-primary">
                    {plan.work_title}
                  </Badge>
                )}
              </div>
              <ol className="space-y-1">
                {plan.steps.map((s, i) => (
                  <li key={i} className="text-[11px] text-muted-foreground flex items-baseline gap-1.5">
                    <span className="font-mono text-muted-foreground/50">{i + 1}.</span>
                    <span className="text-foreground/80">{s.label}</span>
                    {paramSummary(s.params) && (
                      <span className="text-muted-foreground/60">— {paramSummary(s.params)}</span>
                    )}
                  </li>
                ))}
              </ol>
              <div className="flex items-center gap-2 flex-wrap pt-1">
                <Button
                  size="sm"
                  className="h-7 text-xs gap-1.5"
                  disabled={runMutation.isPending}
                  onClick={() => runMutation.mutate()}
                  data-testid="button-run-plan"
                >
                  <Play className="w-3 h-3" /> Run
                </Button>
                <div className="flex items-center gap-1.5">
                  <Input
                    value={saveName}
                    onChange={(e) => setSaveName(e.target.value)}
                    placeholder="Playbook name…"
                    className="h-7 text-[11px] w-44"
                    data-testid="input-playbook-name"
                  />
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-7 text-[11px] gap-1 px-2"
                    disabled={!saveName.trim() || saving}
                    onClick={savePlaybook}
                    data-testid="button-save-playbook"
                  >
                    <BookmarkPlus className="w-3 h-3" /> Save as playbook
                  </Button>
                </div>
              </div>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Operation row (expandable) ─────────────────────────────────────────────────

function OperationRow({ op }: { op: Operation }) {
  const [open, setOpen] = useState(ACTIVE_STATES.includes(op.state));
  const qc = useQueryClient();
  const active = ACTIVE_STATES.includes(op.state);

  const { data: detail } = useQuery<Operation>({
    queryKey: ["operations", op.id],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/${op.id}`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
    enabled: open,
    refetchInterval: open && active ? 2000 : false,
  });

  const act = useMutation({
    mutationFn: async (verb: "pause" | "resume" | "cancel") => {
      const r = await apiFetch(`${API_BASE}/api/operations/${op.id}/${verb}`, { method: "POST" });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Request failed" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Request failed");
      }
      return r.json();
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["operations"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const badge = STATE_BADGE[op.state] ?? STATE_BADGE.pending;
  const steps = detail?.steps ?? [];
  const state = detail?.state ?? op.state;
  const error = detail?.error ?? op.error;

  return (
    <div className="border border-border/50 rounded-lg overflow-hidden">
      <button
        className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors text-left"
        onClick={() => setOpen((o) => !o)}
        data-testid={`row-operation-${op.id}`}
      >
        {open ? (
          <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        ) : (
          <ChevronRight className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
        )}
        <span className="text-xs font-medium flex-1 min-w-0 truncate">{op.title}</span>
        <Badge className={`text-[10px] h-4 px-1.5 border-0 ${STATE_BADGE[state]?.cls ?? badge.cls}`}>
          {STATE_BADGE[state]?.label ?? state}
        </Badge>
        <span className="text-[10px] font-mono text-muted-foreground/50 shrink-0">
          {relTime(op.finished_at ?? op.created_at)}
        </span>
      </button>

      {open && (
        <div className="px-4 pb-3 pt-1 space-y-2 border-t border-border/30">
          {error && (
            <div className="flex items-start gap-2 text-[11px] text-amber-600 bg-amber-500/10 rounded px-2.5 py-1.5">
              <AlertTriangle className="w-3 h-3 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}
          {steps.length === 0 ? (
            <Skeleton className="h-16 w-full" />
          ) : (
            <ol className="space-y-1.5">
              {steps.map((s) => (
                <li key={s.id} className="flex items-center gap-2 text-xs">
                  <StepIcon state={s.state} />
                  <span className={s.state === "cancelled" ? "text-muted-foreground/50" : ""}>
                    {s.label}
                  </span>
                  {s.error && (
                    <span className="text-[10px] text-destructive truncate">{s.error.slice(0, 80)}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
          <div className="flex items-center gap-2 pt-1">
            {state === "running" && (
              <Button size="sm" variant="outline" className="h-6 text-[11px] gap-1 px-2"
                onClick={() => act.mutate("pause")} disabled={act.isPending}
                data-testid={`button-pause-${op.id}`}>
                <Pause className="w-3 h-3" /> Pause
              </Button>
            )}
            {(state === "paused" || state === "failed") && (
              <Button size="sm" variant="outline" className="h-6 text-[11px] gap-1 px-2"
                onClick={() => act.mutate("resume")} disabled={act.isPending}
                data-testid={`button-resume-${op.id}`}>
                <RotateCcw className="w-3 h-3" /> {state === "failed" ? "Retry" : "Resume"}
              </Button>
            )}
            {ACTIVE_STATES.includes(state) && (
              <Button size="sm" variant="ghost" className="h-6 text-[11px] gap-1 px-2 text-destructive"
                onClick={() => act.mutate("cancel")} disabled={act.isPending}
                data-testid={`button-cancel-${op.id}`}>
                <XCircle className="w-3 h-3" /> Cancel
              </Button>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function OperationsPage() {
  const gdDark = useGdDark();
  const qc = useQueryClient();

  const { data: playbooksData, isLoading: playbooksLoading } = useQuery<{ playbooks: Playbook[] }>({
    queryKey: ["operations", "playbooks"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/playbooks`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
    staleTime: 60_000,
  });

  const { data: opsData, isLoading: opsLoading } = useQuery<{ operations: Operation[] }>({
    queryKey: ["operations", "list"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations?limit=30`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
    refetchInterval: (q) =>
      (q.state.data?.operations ?? []).some((o) => ACTIVE_STATES.includes(o.state)) ? 3000 : 30_000,
  });

  const startMutation = useMutation({
    mutationFn: async ({ playbookId, workId }: { playbookId: string; workId: string }) => {
      const r = await apiFetch(`${API_BASE}/api/operations/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ playbook_id: playbookId, work_id: workId }),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Could not start the operation" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Could not start the operation");
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Operation started — follow its progress below.");
      qc.invalidateQueries({ queryKey: ["operations"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const deletePlaybook = useMutation({
    mutationFn: async (playbookId: string) => {
      const r = await apiFetch(`${API_BASE}/api/operations/playbooks/${playbookId}`, {
        method: "DELETE",
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Could not delete the playbook" }));
        throw new Error(typeof err.detail === "string" ? err.detail : "Could not delete the playbook");
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Playbook deleted.");
      qc.invalidateQueries({ queryKey: ["operations", "playbooks"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const playbooks = playbooksData?.playbooks ?? [];
  const operations = opsData?.operations ?? [];

  return (
    <div className={`max-w-4xl mx-auto p-6 space-y-8 ${gdDark ? "dark text-foreground" : ""}`}>
      <div>
        <h1 className="text-2xl font-serif font-medium flex items-center gap-2">
          <Workflow className="w-6 h-6 text-primary" />
          Operations
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Run a whole multi-step job with one button. Every step is checkpointed, so a run can be
          paused, survive a restart, and picked back up where it left off.
        </p>
      </div>

      <JobPlanner onStarted={() => qc.invalidateQueries({ queryKey: ["operations"] })} />

      {playbooksLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-48 w-full rounded-xl" />)}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {playbooks.map((pb) => (
            <PlaybookCard
              key={pb.id}
              playbook={pb}
              starting={startMutation.isPending}
              onStart={(playbookId, workId) => startMutation.mutate({ playbookId, workId })}
              onDelete={pb.custom ? (id) => deletePlaybook.mutate(id) : undefined}
            />
          ))}
        </div>
      )}

      <div className="space-y-3">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <History className="w-4 h-4 text-muted-foreground" />
          Runs
        </h2>
        {opsLoading ? (
          [1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
        ) : operations.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-xs border border-dashed rounded-lg">
            Nothing has run yet — pick a Work above and press Run.
          </div>
        ) : (
          <div className="space-y-2">
            {operations.map((op) => (
              <OperationRow key={op.id} op={op} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
