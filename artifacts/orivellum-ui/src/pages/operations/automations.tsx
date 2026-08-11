import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useListWorks, getListWorksQueryKey } from "@workspace/api-client-react";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  AlarmClock, Plus, Trash2, ChevronDown, ChevronRight, Loader2, XCircle,
  CheckCircle2, CircleDashed, Play,
} from "lucide-react";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// ── Types ─────────────────────────────────────────────────────────────────────

interface ScheduleRun {
  id: string;
  title: string;
  state: string;
  error: string | null;
  created_at: string;
  duration_seconds: number | null;
}

interface Schedule {
  id: string;
  playbook_id: string;
  playbook_title: string | null;
  playbook_missing: boolean;
  title: string;
  work_id: string | null;
  work_title: string | null;
  cadence: "daily" | "weekly";
  time_of_day: string;
  day_of_week: number | null;
  enabled: boolean;
  last_run_at: string | null;
  next_run_at: string;
  last_run: ScheduleRun | null;
}

interface PlaybookOption {
  id: string;
  title: string;
  custom?: boolean;
}

const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

function cadenceLabel(s: Schedule): string {
  if (s.cadence === "weekly") return `${DAYS[s.day_of_week ?? 0]}s at ${s.time_of_day}`;
  return `Every night at ${s.time_of_day}`;
}

function fmtLocal(iso: string | null): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString(undefined, {
      weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function fmtDuration(secs: number | null): string {
  if (secs == null) return "";
  if (secs < 60) return `${Math.round(secs)}s`;
  return `${Math.round(secs / 60)}m ${Math.round(secs % 60)}s`;
}

function RunStateIcon({ state }: { state: string }) {
  if (state === "done") return <CheckCircle2 className="w-3.5 h-3.5" style={{ color: "var(--green-2)" }} />;
  if (state === "running" || state === "pending")
    return <Loader2 className="w-3.5 h-3.5 animate-spin text-primary" />;
  if (state === "failed") return <XCircle className="w-3.5 h-3.5 text-destructive" />;
  return <CircleDashed className="w-3.5 h-3.5 text-muted-foreground/40" />;
}

// ── New automation form ───────────────────────────────────────────────────────

function NewAutomationForm({ playbooks, onDone }: { playbooks: PlaybookOption[]; onDone: () => void }) {
  const [playbookId, setPlaybookId] = useState("");
  const [workId, setWorkId] = useState("");
  const [cadence, setCadence] = useState<"daily" | "weekly">("daily");
  const [time, setTime] = useState("02:00");
  const [day, setDay] = useState("0");
  const { data: worksData } = useListWorks({}, { query: { queryKey: getListWorksQueryKey({}), staleTime: 30_000 } });
  const works = worksData?.works ?? [];

  const selected = playbooks.find((p) => p.id === playbookId);
  // Every built-in playbook operates on a single Work; custom (AI-planned)
  // playbooks may have everything baked into their steps already.
  const needsWork = !!selected && !selected.custom;

  const create = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          playbook_id: playbookId,
          cadence,
          time_of_day: time,
          day_of_week: cadence === "weekly" ? Number(day) : null,
          work_id: workId || null,
        }),
      });
      if (!r.ok) throw new Error((await r.json().catch(() => null))?.detail ?? "Could not save");
      return r.json();
    },
    onSuccess: () => {
      toast.success("Automation scheduled");
      onDone();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Card>
      <CardContent className="p-4 space-y-3">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
          <Select value={playbookId} onValueChange={setPlaybookId}>
            <SelectTrigger className="h-8 text-xs" data-testid="select-automation-playbook">
              <SelectValue placeholder="Which playbook?" />
            </SelectTrigger>
            <SelectContent>
              {playbooks.map((p) => (
                <SelectItem key={p.id} value={p.id} className="text-xs">{p.title}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select value={workId || "none"} onValueChange={(v) => setWorkId(v === "none" ? "" : v)}>
            <SelectTrigger className="h-8 text-xs" data-testid="select-automation-work">
              <SelectValue placeholder="Work (optional)" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="none" className="text-xs">No Work</SelectItem>
              {works.map((w) => (
                <SelectItem key={w.id} value={w.id ?? ""} className="text-xs">{w.title ?? w.id}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Select value={cadence} onValueChange={(v) => setCadence(v as "daily" | "weekly")}>
            <SelectTrigger className="h-8 text-xs w-32" data-testid="select-automation-cadence">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="daily" className="text-xs">Every night</SelectItem>
              <SelectItem value="weekly" className="text-xs">Weekly</SelectItem>
            </SelectContent>
          </Select>
          {cadence === "weekly" && (
            <Select value={day} onValueChange={setDay}>
              <SelectTrigger className="h-8 text-xs w-36" data-testid="select-automation-day">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DAYS.map((d, i) => (
                  <SelectItem key={d} value={String(i)} className="text-xs">{d}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          )}
          <Input
            type="time"
            value={time}
            onChange={(e) => setTime(e.target.value)}
            className="h-8 text-xs w-28"
            data-testid="input-automation-time"
          />
          <Button
            size="sm"
            className="h-8 text-xs ml-auto"
            disabled={!playbookId || !time || (needsWork && !workId) || create.isPending}
            onClick={() => create.mutate()}
            data-testid="button-automation-create"
          >
            {create.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : "Schedule it"}
          </Button>
        </div>
        {needsWork && !workId && (
          <p className="text-[11px] text-amber-600">This playbook works on one Work — pick one above.</p>
        )}
        <p className="text-[11px] text-muted-foreground">
          Times are your computer's local time. If something heavy is already running (like an
          audiobook render), the automation quietly waits and starts as soon as the machine is free.
        </p>
      </CardContent>
    </Card>
  );
}

// ── Schedule row ──────────────────────────────────────────────────────────────

function ScheduleRow({ schedule }: { schedule: Schedule }) {
  const [open, setOpen] = useState(false);
  const qc = useQueryClient();

  const { data: runsData, isLoading: runsLoading } = useQuery<{ runs: ScheduleRun[] }>({
    queryKey: ["operations", "schedules", schedule.id, "runs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules/${schedule.id}/runs`);
      if (!r.ok) throw new Error("Failed to load run history");
      return r.json();
    },
    enabled: open,
  });

  const toggle = useMutation({
    mutationFn: async (enabled: boolean) => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules/${schedule.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) throw new Error("Could not update");
      return r.json();
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ["operations", "schedules"] }),
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules/${schedule.id}`, {
        method: "DELETE",
      });
      if (!r.ok) throw new Error("Could not delete");
    },
    onSuccess: () => {
      toast.success("Automation deleted");
      qc.invalidateQueries({ queryKey: ["operations", "schedules"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const runNow = useMutation({
    mutationFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules/${schedule.id}/run`, {
        method: "POST",
      });
      if (!r.ok) {
        const err = await r.json().catch(() => null);
        throw new Error(
          typeof err?.detail === "string" ? err.detail : "Could not start the run"
        );
      }
      return r.json();
    },
    onSuccess: () => {
      toast.success("Running now — check the run history below.");
      qc.invalidateQueries({ queryKey: ["operations", "schedules"] });
      qc.invalidateQueries({ queryKey: ["operations", "schedules", schedule.id, "runs"] });
      qc.invalidateQueries({ queryKey: ["operations"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Card className={schedule.enabled ? "" : "opacity-60"}>
      <CardContent className="p-3">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setOpen(!open)}
            className="shrink-0 text-muted-foreground"
            data-testid={`button-automation-expand-${schedule.id}`}
          >
            {open ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="text-sm font-medium truncate">{schedule.title}</span>
              {schedule.work_title && (
                <Badge variant="outline" className="text-[10px]">{schedule.work_title}</Badge>
              )}
              {schedule.playbook_missing && (
                <Badge className="text-[10px] bg-destructive/15 text-destructive">
                  Playbook deleted
                </Badge>
              )}
              {schedule.last_run?.state === "failed" && (
                <Badge className="text-[10px] bg-destructive/15 text-destructive">
                  Last run failed
                </Badge>
              )}
            </div>
            <p className="text-[11px] text-muted-foreground mt-0.5">
              {cadenceLabel(schedule)}
              {schedule.enabled
                ? ` · next: ${fmtLocal(schedule.next_run_at)}`
                : " · paused"}
              {schedule.last_run_at ? ` · last: ${fmtLocal(schedule.last_run_at)}` : ""}
            </p>
          </div>
          <Button
            variant="ghost"
            size="sm"
            className="h-7 text-[11px] gap-1 px-2 text-muted-foreground"
            disabled={runNow.isPending || schedule.playbook_missing}
            onClick={() => runNow.mutate()}
            title="Start one run of this automation right now"
            data-testid={`button-automation-run-now-${schedule.id}`}
          >
            {runNow.isPending ? (
              <Loader2 className="w-3 h-3 animate-spin" />
            ) : (
              <Play className="w-3 h-3" />
            )}
            Run now
          </Button>
          <Switch
            checked={schedule.enabled}
            onCheckedChange={(v) => toggle.mutate(v)}
            data-testid={`switch-automation-${schedule.id}`}
          />
          <Button
            variant="ghost"
            size="icon"
            className="h-7 w-7 text-muted-foreground hover:text-destructive"
            onClick={() => remove.mutate()}
            data-testid={`button-automation-delete-${schedule.id}`}
          >
            <Trash2 className="w-3.5 h-3.5" />
          </Button>
        </div>

        {open && (
          <div className="mt-3 ml-6 space-y-1.5">
            {runsLoading ? (
              <Skeleton className="h-8 w-full" />
            ) : (runsData?.runs ?? []).length === 0 ? (
              <p className="text-[11px] text-muted-foreground">Hasn't run yet.</p>
            ) : (
              (runsData?.runs ?? []).map((run) => (
                <div key={run.id} className="flex items-center gap-2 text-[11px]">
                  <RunStateIcon state={run.state} />
                  <span className="text-muted-foreground">{fmtLocal(run.created_at)}</span>
                  {run.duration_seconds != null && (
                    <span className="text-muted-foreground/70">{fmtDuration(run.duration_seconds)}</span>
                  )}
                  {run.error && (
                    <span className="text-destructive truncate" title={run.error}>{run.error}</span>
                  )}
                </div>
              ))
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

// ── Section ───────────────────────────────────────────────────────────────────

export function AutomationsSection({ playbooks }: { playbooks: PlaybookOption[] }) {
  const [showForm, setShowForm] = useState(false);
  const qc = useQueryClient();

  const { data, isLoading } = useQuery<{ schedules: Schedule[] }>({
    queryKey: ["operations", "schedules"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/operations/schedules`);
      if (!r.ok) throw new Error("Failed to load automations");
      return r.json();
    },
    refetchInterval: 30_000,
  });
  const schedules = data?.schedules ?? [];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-medium flex items-center gap-2">
          <AlarmClock className="w-4 h-4 text-muted-foreground" />
          Automations
        </h2>
        <Button
          variant="outline"
          size="sm"
          className="h-7 text-xs"
          onClick={() => setShowForm(!showForm)}
          data-testid="button-automation-new"
        >
          <Plus className="w-3.5 h-3.5 mr-1" />
          New automation
        </Button>
      </div>

      {showForm && (
        <NewAutomationForm
          playbooks={playbooks}
          onDone={() => {
            setShowForm(false);
            qc.invalidateQueries({ queryKey: ["operations", "schedules"] });
          }}
        />
      )}

      {isLoading ? (
        <Skeleton className="h-16 w-full" />
      ) : schedules.length === 0 && !showForm ? (
        <div className="text-center py-6 text-muted-foreground text-xs border border-dashed rounded-lg">
          Nothing scheduled yet — automations run playbooks by themselves, like a nightly import
          or a weekly study refresh.
        </div>
      ) : (
        <div className="space-y-2">
          {schedules.map((s) => (
            <ScheduleRow key={s.id} schedule={s} />
          ))}
        </div>
      )}
    </div>
  );
}
