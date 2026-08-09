import { useState } from "react";
import { apiFetch } from "@/lib/auth";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { toast } from "sonner";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useListWorks, getListWorksQueryKey } from "@workspace/api-client-react";
import { useGdDark } from "@/lib/useGdDark";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Zap, Download, CheckCircle2, XCircle, Loader2,
  FileText, BookOpen, BarChart2, GraduationCap, FileSpreadsheet, History,
} from "lucide-react";

const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";

// ── Types ──────────────────────────────────────────────────────────────────────

interface ActionDef {
  name: string;
  description: string;
  category: string;
  input_schema: Record<string, unknown>;
}

interface ActionRun {
  id: string;
  action_name: string;
  inputs: string;
  status: "running" | "done" | "error";
  output_path: string | null;
  output_label: string | null;
  output_doc_id: string | null;
  work_id: string | null;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

// ── Category icons ──────────────────────────────────────────────────────────────

const CATEGORY_ICON: Record<string, React.ElementType> = {
  finance: FileSpreadsheet,
  export: Download,
  generate: FileText,
  learn: GraduationCap,
  general: Zap,
};

const ACTION_ICON: Record<string, React.ElementType> = {
  tax_package: FileSpreadsheet,
  report_assembler: BarChart2,
  book_export: BookOpen,
  study_plan: GraduationCap,
  template_fill: FileText,
};

// ── Helpers ────────────────────────────────────────────────────────────────────

function relTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const diff = Date.now() - new Date(iso).getTime();
  const s = Math.round(diff / 1000);
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

// ── Work selector ─────────────────────────────────────────────────────────────

function WorkSelector({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  const { data } = useListWorks({}, { query: { queryKey: getListWorksQueryKey({}), staleTime: 30_000 } });
  const works = data?.works ?? [];
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger className="h-7 text-xs flex-1">
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

// ── Action card ────────────────────────────────────────────────────────────────

function ActionCard({
  action,
  onRun,
}: {
  action: ActionDef;
  onRun: (name: string, inputs: Record<string, string>) => void;
}) {
  const Icon = ACTION_ICON[action.name] ?? CATEGORY_ICON[action.category] ?? Zap;
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<string | null>(null);
  const [loadingPreview, setLoadingPreview] = useState(false);

  // Build input fields from required schema properties
  const props = (action.input_schema as any)?.properties ?? {};
  const required: string[] = (action.input_schema as any)?.required ?? [];
  // work_id gets a dedicated Work selector, other required fields get text inputs
  const needsWork = required.includes("work_id");
  const textFields = required.filter((k) => k !== "work_id");

  // Validate that all required inputs are present before allowing run
  const missingWorkId = needsWork && !inputs["work_id"];
  const missingTextFields = textFields.filter((k) => !inputs[k]);
  const canRun = !missingWorkId && missingTextFields.length === 0;

  const handlePreview = async () => {
    setLoadingPreview(true);
    try {
      const r = await apiFetch(`${API_BASE}/api/actions/${action.name}/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputs),
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      setPreview(d.confirm_message);
    } catch {
      setPreview("Could not load preview.");
    } finally {
      setLoadingPreview(false);
    }
  };

  return (
    <Card className="border border-border/50 hover:border-border transition-colors">
      <CardContent className="pt-5 pb-4 space-y-3">
        <div className="flex items-start gap-3">
          <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
            <Icon className="w-4 h-4 text-primary" />
          </div>
          <div className="flex-1 min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <span className="font-medium text-sm">
                {action.name.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
              </span>
              <Badge variant="outline" className="text-[10px] h-4 px-1.5">{action.category}</Badge>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5 leading-relaxed">{action.description}</p>
          </div>
        </div>

        {/* Input fields */}
        <div className="space-y-2 pl-12">
          {/* Work selector for work-scoped actions */}
          {needsWork && (
            <div className="flex items-center gap-2">
              <label className="text-xs font-mono text-muted-foreground w-16 shrink-0">work</label>
              <WorkSelector
                value={inputs["work_id"] ?? ""}
                onChange={(v) => setInputs((prev) => ({ ...prev, work_id: v }))}
              />
            </div>
          )}
          {/* Text fields for other required inputs (e.g. year) */}
          {textFields.map((key) => {
            const fieldSchema = (props as any)[key] ?? {};
            return (
              <div key={key} className="flex items-center gap-2">
                <label className="text-xs font-mono text-muted-foreground w-16 shrink-0">{key}</label>
                <Input
                  className="h-7 text-xs flex-1"
                  placeholder={(fieldSchema.description as string) || key}
                  value={inputs[key] ?? ""}
                  onChange={(e) => setInputs((prev) => ({ ...prev, [key]: e.target.value }))}
                />
              </div>
            );
          })}
          {!canRun && (
            <p className="text-[11px] text-amber-600 dark:text-amber-400">
              {missingWorkId ? "Select a Work to continue." : `Fill in: ${missingTextFields.join(", ")}`}
            </p>
          )}
        </div>

        {/* Preview / confirmation */}
        {preview ? (
          <div className="pl-12">
            <div className="text-xs text-muted-foreground bg-muted/40 rounded px-3 py-2 leading-relaxed border border-border/40">
              {preview}
            </div>
          </div>
        ) : null}

        {/* Actions */}
        <div className="flex items-center gap-2 pl-12">
          <Button
            size="sm"
            className="h-7 text-xs gap-1.5"
            disabled={!canRun}
            onClick={() => onRun(action.name, inputs)}
          >
            <Zap className="w-3 h-3" />
            Run
          </Button>
          {!preview && (
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              disabled={loadingPreview || !canRun}
              onClick={handlePreview}
            >
              {loadingPreview ? <Loader2 className="w-3 h-3 animate-spin" /> : "Preview"}
            </Button>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

// ── Run row ────────────────────────────────────────────────────────────────────

function RunRow({ run, onDownload }: { run: ActionRun; onDownload: (run: ActionRun) => void }) {
  const Icon =
    run.status === "done"
      ? CheckCircle2
      : run.status === "error"
      ? XCircle
      : Loader2;

  const iconCls =
    run.status === "done"
      ? "text-emerald-500"
      : run.status === "error"
      ? "text-destructive"
      : "text-muted-foreground animate-spin";

  return (
    <div className="flex items-center gap-3 px-4 py-2.5 hover:bg-muted/20 transition-colors">
      <Icon className={`w-3.5 h-3.5 shrink-0 ${iconCls}`} />
      <div className="flex-1 min-w-0">
        <span className="text-xs font-mono font-medium">
          {run.action_name.replace(/_/g, " ")}
        </span>
        {run.error && (
          <span className="text-[11px] text-destructive ml-2">{run.error.slice(0, 80)}</span>
        )}
        {run.output_label && !run.error && (
          <span className="text-[11px] text-muted-foreground ml-2">{run.output_label}</span>
        )}
      </div>
      <div className="flex items-center gap-2 shrink-0">
        {run.status === "done" && run.output_path && (
          <Button
            size="sm"
            variant="ghost"
            className="h-6 text-[10px] gap-1 px-2"
            onClick={() => onDownload(run)}
          >
            <Download className="w-3 h-3" />
            Download
          </Button>
        )}
        <span className="text-[10px] font-mono text-muted-foreground/50">
          {relTime(run.completed_at ?? run.created_at)}
        </span>
      </div>
    </div>
  );
}

// ── Main page ──────────────────────────────────────────────────────────────────

export default function ActionsPage() {
  const gdDark = useGdDark();
  const { data: actionsData, isLoading: actionsLoading } = useQuery<{ actions: ActionDef[] }>({
    queryKey: ["actions", "list"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/actions`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
    staleTime: 60_000,
  });

  const { data: runsData, isLoading: runsLoading, refetch: refetchRuns } = useQuery<{
    runs: ActionRun[];
    count: number;
  }>({
    queryKey: ["actions", "runs"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/api/actions/runs?limit=20`);
      if (!r.ok) throw new Error("failed");
      return r.json();
    },
    staleTime: 15_000,
    refetchInterval: 15_000,
  });

  const runMutation = useMutation({
    mutationFn: async ({ name, inputs }: { name: string; inputs: Record<string, string> }) => {
      const r = await apiFetch(`${API_BASE}/api/actions/${name}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(inputs),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Action failed" }));
        throw new Error(err.detail ?? "Action failed");
      }
      return r.json();
    },
    onSuccess: (result) => {
      toast.success(`Action complete: ${result.output_label ?? result.summary ?? "done"}`);
      refetchRuns();
    },
    onError: (err: Error) => {
      toast.error(err.message);
    },
  });

  const handleRun = (name: string, inputs: Record<string, string>) => {
    runMutation.mutate({ name, inputs });
  };

  const handleDownload = (run: ActionRun) => {
    if (!run.output_path) return;
    const url = `${API_BASE}/api/studio/outputs/serve?path=${encodeURIComponent(run.output_path)}`;
    window.open(url, "_blank");
  };

  const actions = actionsData?.actions ?? [];
  const runs = runsData?.runs ?? [];

  // Group actions by category
  const categories = Array.from(new Set(actions.map((a) => a.category)));

  return (
    <div className={`max-w-4xl mx-auto p-6 space-y-8 ${gdDark ? "dark text-foreground" : ""}`}>
      {/* Header */}
      <div>
        <h1 className="text-2xl font-serif font-medium flex items-center gap-2">
          <Zap className="w-6 h-6 text-primary" />
          Actions
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Typed, grounded actions — each shows a confirmation before running and saves its output to your Library.
        </p>
      </div>

      {/* Action grid */}
      {actionsLoading ? (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {[1, 2, 3].map((i) => <Skeleton key={i} className="h-48 w-full rounded-xl" />)}
        </div>
      ) : actions.length === 0 ? (
        <div className="text-center py-12 text-muted-foreground text-sm border border-dashed rounded-xl">
          No actions registered.
        </div>
      ) : (
        categories.map((cat) => (
          <div key={cat} className="space-y-3">
            <h2 className="text-xs font-mono uppercase tracking-wider text-muted-foreground/60 pl-1">
              {cat}
            </h2>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {actions.filter((a) => a.category === cat).map((action) => (
                <ActionCard key={action.name} action={action} onRun={handleRun} />
              ))}
            </div>
          </div>
        ))
      )}

      {/* Recent runs */}
      <div className="space-y-3">
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-medium flex items-center gap-2">
            <History className="w-4 h-4 text-muted-foreground" />
            Recent runs
          </h2>
          <button
            onClick={() => refetchRuns()}
            className="text-xs font-mono text-muted-foreground hover:text-foreground transition-colors"
          >
            {runsData?.count ?? 0} total · refresh
          </button>
        </div>

        {runsLoading ? (
          [1, 2, 3].map((i) => <Skeleton key={i} className="h-10 w-full" />)
        ) : runs.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground text-xs border border-dashed rounded-lg">
            No runs yet — fill in the inputs above and click Run.
          </div>
        ) : (
          <div className="rounded-lg border border-border/50 overflow-hidden divide-y divide-border/30">
            {runs.map((run) => (
              <RunRow key={run.id} run={run} onDownload={handleDownload} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
