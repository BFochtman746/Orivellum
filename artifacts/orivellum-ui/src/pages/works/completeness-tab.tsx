/**
 * Completeness tab — the honest readiness report for a Work.
 *
 * Predicates (true/false facts), observed counts, raw progress numbers
 * (targets shown only when the author set them), and a Chao1/Good–Turing
 * coverage upper bound. No overall score, no readiness label, no assumed
 * denominators — the report refuses to guess.
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { useGetWork, useUpdateWork, getGetWorkQueryKey } from "@workspace/api-client-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  BarChart2, Check, CheckCircle2, Loader2, Pencil, X, AlertTriangle,
} from "lucide-react";
import { toast } from "sonner";

const WORK_API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

interface ComplPredicate {
  name: string; label: string; value: boolean; detail: string;
}
interface ComplCount {
  name: string; label: string; detail: string;
  value?: number; current?: number; total?: number;
}
interface ComplProgress {
  words: number; word_target: number | null;
  chapters: number; chapter_target: number | null;
  documents: number; note: string | null;
}
interface CoverageOverall {
  completeness: number | null;
  unseen_est: number | null;
  band?: string;
  summary?: string;
}
interface ComplReport {
  work_id: string; work_title: string; evaluated_at?: string;
  predicates: ComplPredicate[]; counts: ComplCount[]; progress: ComplProgress;
  coverage?: { overall?: CoverageOverall; scope_note?: string } | null;
}

export function CompletenessTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();

  // Subscribe to the pipeline cache that WorkDetail already keeps alive so we
  // can derive whether polling is needed — no extra network request.
  const { data: pipelineData } = useQuery<{ pipeline: any | null }>({
    queryKey: ["pipeline", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/pipeline`).then((r) => r.json()),
    enabled: !!workId,
    staleTime: 30_000,
  });
  // A pipeline is "active" when it exists and hasn't reached the terminal B17 gate.
  const pipelineActive =
    !!pipelineData?.pipeline && pipelineData.pipeline.status !== "B17";

  const { data, isLoading, error, refetch, isFetching } = useQuery<ComplReport>({
    queryKey: ["work-completeness", workId],
    queryFn: () =>
      apiFetch(`${WORK_API_BASE}/works/${workId}/completeness`).then((r) => {
        if (!r.ok) throw new Error("completeness fetch failed");
        return r.json();
      }),
    staleTime: 60_000,
    // Poll every 10 s while the pipeline is advancing so the report stays live.
    refetchInterval: pipelineActive ? 10_000 : false,
  });

  // Fetch the work to read/write meta.completeness_targets
  const { data: workResp } = useGetWork(workId, {
    query: { queryKey: getGetWorkQueryKey(workId), enabled: !!workId },
  });
  const updateWork = useUpdateWork();

  // ── Target editing state ─────────────────────────────────────────────────
  const [editingTargets, setEditingTargets] = useState(false);
  const currentMeta = (workResp?.work as any)?.meta ?? {};
  const savedTargets = (currentMeta?.completeness_targets ?? {}) as {
    word_target?: number;
    chapter_target?: number;
  };

  const [wordInput, setWordInput]       = useState("");
  const [chapterInput, setChapterInput] = useState("");

  const openTargetEditor = () => {
    // No defaults — an unset target stays blank; the author decides.
    setWordInput(savedTargets.word_target ? String(savedTargets.word_target) : "");
    setChapterInput(savedTargets.chapter_target ? String(savedTargets.chapter_target) : "");
    setEditingTargets(true);
  };

  const saveTargets = () => {
    const wt = wordInput.trim() === "" ? null : parseInt(wordInput, 10);
    const ct = chapterInput.trim() === "" ? null : parseInt(chapterInput, 10);
    if ((wt != null && (!wt || wt < 1)) || (ct != null && (!ct || ct < 1))) {
      toast.error("Targets must be positive numbers (or left blank)");
      return;
    }
    const targets: Record<string, number> = {};
    if (wt != null) targets.word_target = wt;
    if (ct != null) targets.chapter_target = ct;
    const mergedMeta = { ...currentMeta };
    if (Object.keys(targets).length > 0) {
      mergedMeta.completeness_targets = targets;
    } else {
      delete mergedMeta.completeness_targets;
    }
    updateWork.mutate(
      { workId, data: { meta: mergedMeta } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: ["work-completeness", workId] });
          setEditingTargets(false);
          toast.success(
            Object.keys(targets).length > 0 ? "Targets saved" : "Targets cleared — raw counts only",
          );
        },
        onError: () => toast.error("Could not save targets"),
      }
    );
  };

  // ── Render ───────────────────────────────────────────────────────────────

  if (isLoading) return (
    <div className="space-y-4">
      {[1,2,3,4,5].map(i => <Skeleton key={i} className="h-16 w-full" />)}
    </div>
  );

  if (error || !data) return (
    <div className="text-center py-16 text-muted-foreground border border-dashed rounded-lg">
      <BarChart2 className="w-8 h-8 mx-auto mb-3 opacity-40" />
      <p className="text-sm">Could not load completeness — re-extract documents first.</p>
    </div>
  );

  const predicatesMet = data.predicates.filter(p => p.value).length;
  const allMet = predicatesMet === data.predicates.length;
  const coverage = data.coverage?.overall ?? null;

  return (
    <div className="space-y-6">
      {/* Readiness banner — checks passed, never an invented percentage */}
      <div
        className="flex items-center justify-between p-4 rounded-xl border"
        style={
          allMet
            ? { color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)" }
            : { color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }
        }
      >
        <div>
          <p className="text-xs font-mono uppercase tracking-wider opacity-70 mb-0.5">Readiness checks</p>
          <p className="text-2xl font-serif font-semibold">
            {predicatesMet} of {data.predicates.length} passed
          </p>
          <p className="text-xs font-mono mt-1 opacity-70">
            Facts and observed counts only — no assumed targets.
          </p>
        </div>
        <div className="text-right shrink-0 ml-6">
          <p className="text-4xl font-mono font-bold">{predicatesMet}/{data.predicates.length}</p>
          <button
            onClick={() => refetch()}
            disabled={isFetching}
            className="text-[10px] font-mono opacity-60 hover:opacity-100 transition-opacity mt-1"
          >
            {isFetching ? "updating…" : "refresh"}
          </button>
        </div>
      </div>

      {/* Predicates */}
      <div className="space-y-3">
        <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
          Readiness predicates
        </h3>
        {data.predicates.map((p) => (
          <div key={p.name} className="p-4 rounded-lg border border-border/50 bg-muted/10 flex items-start gap-3">
            {p.value ? (
              <CheckCircle2 className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--green-2)" }} />
            ) : (
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" style={{ color: "var(--rust)" }} />
            )}
            <div className="min-w-0 flex-1">
              <div className="flex items-center justify-between gap-3">
                <span className="font-medium text-sm">{p.label}</span>
                <span
                  className="text-xs font-mono font-semibold shrink-0"
                  style={{ color: p.value ? "var(--green-2)" : "var(--rust)" }}
                >
                  {p.value ? "yes" : "no"}
                </span>
              </div>
              <p className="text-[11px] font-mono text-muted-foreground mt-1">{p.detail}</p>
            </div>
          </div>
        ))}
      </div>

      {/* Observed counts */}
      <div className="space-y-3">
        <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
          Observed counts
        </h3>
        {data.counts.map((c) => (
          <div key={c.name} className="p-4 rounded-lg border border-border/50 bg-muted/10">
            <div className="flex items-center justify-between gap-3">
              <span className="font-medium text-sm">{c.label}</span>
              <span className="text-sm font-mono font-semibold">
                {c.total != null ? `${c.current ?? 0} of ${c.total}` : String(c.value ?? 0)}
              </span>
            </div>
            <p className="text-[11px] font-mono text-muted-foreground mt-1">{c.detail}</p>
          </div>
        ))}
      </div>

      {/* Coverage — Chao1 upper bound with its own honest framing */}
      {coverage && (
        <div className="p-4 rounded-lg border border-border/50 bg-muted/10">
          <div className="flex items-center justify-between gap-3">
            <span className="font-medium text-sm">Entity coverage (upper bound)</span>
            <span className="text-sm font-mono font-semibold">
              {coverage.completeness != null ? `≤${Math.round(coverage.completeness * 100)}%` : "—"}
            </span>
          </div>
          <p className="text-[11px] font-mono text-muted-foreground mt-1">
            {coverage.summary ?? "Chao1 / Good–Turing estimate from mention frequencies — true coverage is at most this."}
          </p>
        </div>
      )}

      {/* Progress + author-set targets */}
      <div className="p-4 rounded-lg border border-border/50 bg-muted/10">
        <div className="flex items-center justify-between mb-3">
          <div>
            <h3 className="text-xs font-mono uppercase tracking-widest text-muted-foreground">
              Progress
            </h3>
            {!editingTargets && (
              <p className="text-[11px] text-muted-foreground/70 mt-0.5">
                {savedTargets.word_target || savedTargets.chapter_target
                  ? [
                      savedTargets.word_target
                        ? `${Number(savedTargets.word_target).toLocaleString()} word target`
                        : null,
                      savedTargets.chapter_target
                        ? `${savedTargets.chapter_target} chapter target`
                        : null,
                    ].filter(Boolean).join(" · ")
                  : "No targets set — raw counts only"}
              </p>
            )}
          </div>
          {!editingTargets && (
            <button
              onClick={openTargetEditor}
              className="flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground transition-colors"
            >
              <Pencil className="w-3 h-3" /> Edit targets
            </button>
          )}
        </div>

        {editingTargets && (
          <div className="space-y-3 mb-3">
            <div className="flex items-center gap-3 flex-wrap">
              <label className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                Word target
                <Input
                  type="number"
                  min={1}
                  value={wordInput}
                  onChange={(e) => setWordInput(e.target.value)}
                  className="w-28 h-7 text-sm font-mono"
                  placeholder="not set"
                />
              </label>
              <label className="flex items-center gap-2 text-xs font-mono text-muted-foreground">
                Chapter target
                <Input
                  type="number"
                  min={1}
                  value={chapterInput}
                  onChange={(e) => setChapterInput(e.target.value)}
                  className="w-20 h-7 text-sm font-mono"
                  placeholder="not set"
                />
              </label>
            </div>
            <p className="text-[10px] font-mono text-muted-foreground/60">
              Leave blank to clear — no default is assumed for you.
            </p>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                onClick={saveTargets}
                disabled={updateWork.isPending}
                className="h-7 text-xs gap-1.5"
              >
                {updateWork.isPending
                  ? <Loader2 className="w-3 h-3 animate-spin" />
                  : <Check className="w-3 h-3" />}
                Save
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => setEditingTargets(false)}
                disabled={updateWork.isPending}
                className="h-7 text-xs gap-1.5"
              >
                <X className="w-3 h-3" /> Cancel
              </Button>
            </div>
          </div>
        )}

        {/* Raw numbers; a bar appears ONLY against an author-set target */}
        {!editingTargets && (
          <div className="space-y-2.5">
            <ProgressRow
              label="Words"
              current={data.progress.words}
              target={data.progress.word_target}
            />
            <ProgressRow
              label="Chapters"
              current={data.progress.chapters}
              target={data.progress.chapter_target}
            />
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-mono text-muted-foreground">Documents ready</span>
              <span className="text-[11px] font-mono text-muted-foreground">
                {data.progress.documents}
              </span>
            </div>
            {data.progress.note && (
              <p className="text-[10px] font-mono text-muted-foreground/60">{data.progress.note}</p>
            )}
          </div>
        )}
      </div>

      <p className="text-[10px] font-mono text-muted-foreground/50 text-right">
        Evaluated {data.evaluated_at ? new Date(data.evaluated_at).toLocaleString() : "recently"}
      </p>
    </div>
  );
}

/** Raw count row. Renders a ratio + bar only when the author set a target. */
function ProgressRow({ label, current, target }: {
  label: string; current: number; target: number | null;
}) {
  const barColor = (pct: number): string =>
    pct >= 70 ? "var(--green-2)" : pct >= 30 ? "var(--gilt)" : "var(--rust)";

  if (target == null) {
    return (
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-mono text-muted-foreground">{label}</span>
        <span className="text-[11px] font-mono text-muted-foreground">
          {current.toLocaleString()}
          <span className="ml-1.5 opacity-60">(no target set)</span>
        </span>
      </div>
    );
  }

  const pct = Math.min(100, Math.round((current / target) * 100));
  return (
    <div>
      <div className="flex items-center justify-between mb-1">
        <span className="text-[11px] font-mono text-muted-foreground">{label}</span>
        <span className="text-[11px] font-mono text-muted-foreground">
          {current.toLocaleString()} / {target.toLocaleString()}
          <span className="ml-1.5 opacity-60">({pct}%)</span>
        </span>
      </div>
      <div className="h-1.5 bg-muted rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all duration-700"
          style={{ width: `${pct}%`, background: barColor(pct) }}
        />
      </div>
    </div>
  );
}
