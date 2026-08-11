import { useState } from "react";
import { useLocation } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import {
  Hammer, Plus, Loader2, FileSpreadsheet, Code2, Archive,
  AlertCircle, Upload, PauseCircle,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";

const API = `${import.meta.env.BASE_URL}api/workbench`.replace(/\/+/g, "/").replace(/\/$/, "");

export type WbHealth = {
  score: number | null;
  grade: "new" | "healthy" | "watch" | "at_risk";
  parts: { label: string; delta: number }[];
  open_findings?: number;
};

export type WbProject = {
  id: string;
  title: string;
  kind: "xlsx" | "code";
  brief: string;
  status: "active" | "archived" | "shelved";
  building: boolean;
  last_error: string | null;
  archive_path: string | null;
  created_at: string;
  updated_at: string;
  health?: WbHealth;
};

export const HEALTH_COLORS: Record<string, string> = {
  healthy: "text-emerald-700 border-emerald-300 bg-emerald-50 dark:bg-emerald-950/40 dark:text-emerald-300",
  watch: "text-amber-700 border-amber-300 bg-amber-50 dark:bg-amber-950/40 dark:text-amber-300",
  at_risk: "text-red-700 border-red-300 bg-red-50 dark:bg-red-950/40 dark:text-red-300",
  new: "text-muted-foreground border-border",
};

export function HealthBadge({ health }: { health?: WbHealth }) {
  if (!health) return null;
  if (health.grade === "new") {
    return <Badge variant="outline" className="text-muted-foreground">New</Badge>;
  }
  const label = health.grade === "healthy" ? "Healthy" : health.grade === "watch" ? "Watch" : "At risk";
  return (
    <Badge variant="outline" className={`gap-1 font-mono ${HEALTH_COLORS[health.grade]}`}>
      {health.score} · {label}
    </Badge>
  );
}

const FILTERS = [
  ["all", "All"],
  ["active", "Active"],
  ["archived", "Completed"],
  ["shelved", "Shelved"],
] as const;

export default function WorkbenchPage() {
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [mode, setMode] = useState<"describe" | "import">("describe");
  const [filter, setFilter] = useState<(typeof FILTERS)[number][0]>("all");
  const [title, setTitle] = useState("");
  const [kind, setKind] = useState<"xlsx" | "code">("xlsx");
  const [brief, setBrief] = useState("");
  const [importFile, setImportFile] = useState<File | null>(null);

  const { data, isLoading } = useQuery<{ projects: WbProject[] }>({
    queryKey: ["wb-projects"],
    queryFn: () => apiFetch(`${API}/projects`).then(r => r.json()),
    staleTime: 15_000,
    refetchInterval: (q) =>
      (q.state.data?.projects ?? []).some(p => p.building) ? 3_000 : 15_000,
  });

  const createProject = useMutation({
    mutationFn: (body: { title: string; kind: string; brief: string }) =>
      apiFetch(`${API}/projects`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then(r => { if (!r.ok) throw new Error("Failed to create"); return r.json(); }),
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["wb-projects"] });
      toast.success("Project created — building the first version");
      setShowNew(false); setTitle(""); setBrief("");
      navigate(`/workbench/${res.id}`);
    },
    onError: () => toast.error("Could not create project"),
  });

  const importProject = useMutation({
    mutationFn: async () => {
      if (!importFile) throw new Error("Choose a file first");
      const isPdf = /\.pdf$/i.test(importFile.name);
      const form = new FormData();
      form.append("file", importFile, importFile.name);
      if (title.trim()) form.append("title", title.trim());
      if (brief.trim()) form.append("brief", brief.trim());
      const url = isPdf ? `${API}/transcribe` : `${API}/projects/import`;
      const r = await apiFetch(url, { method: "POST", body: form });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as { detail?: string }).detail ?? "Import failed");
      return { ...(body as { id: string }), isPdf, isXlsx: /\.xlsx$/i.test(importFile.name) };
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["wb-projects"] });
      toast.success(res.isPdf
        ? "Transcribing the PDF into a verified workbook — this runs in the background"
        : res.isXlsx
          ? "Workbook saved as v1 — the review is running and will publish a findings report"
          : "Imported — the files are saved as v1 and a review is running");
      setShowNew(false); setTitle(""); setBrief(""); setImportFile(null);
      navigate(`/workbench/${res.id}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const all = data?.projects ?? [];
  const projects = filter === "all" ? all : all.filter(p => p.status === filter);
  const counts = {
    all: all.length,
    active: all.filter(p => p.status === "active").length,
    archived: all.filter(p => p.status === "archived").length,
    shelved: all.filter(p => p.status === "shelved").length,
  };

  return (
    <div className="p-4 md:p-6 max-w-6xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Hammer className="h-6 w-6 text-primary" /> Workbench
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Your project portfolio — every workbook and program with its health at a glance.
            Open one for the full rundown, what it needs next, and the close-out when it's done.
          </p>
        </div>
        <Button onClick={() => setShowNew(true)} data-testid="button-new-wb-project">
          <Plus className="h-4 w-4 mr-1" /> New project
        </Button>
      </div>

      <div className="flex gap-2 flex-wrap">
        {FILTERS.map(([value, label]) => (
          <Button
            key={value}
            size="sm"
            variant={filter === value ? "default" : "outline"}
            onClick={() => setFilter(value)}
            data-testid={`button-wb-filter-${value}`}
          >
            {label}
            <span className="ml-1.5 text-xs opacity-70">{counts[value]}</span>
          </Button>
        ))}
      </div>

      {isLoading ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {[0, 1, 2, 3, 4, 5].map(i => <Skeleton key={i} className="h-36 w-full" />)}
        </div>
      ) : projects.length === 0 ? (
        <div className="border border-dashed rounded-lg p-10 text-center text-muted-foreground">
          <Hammer className="h-8 w-8 mx-auto mb-3 opacity-40" />
          <p className="font-medium">{filter === "all" ? "No projects yet" : "Nothing here"}</p>
          {filter === "all" && (
            <p className="text-sm mt-1">Start one — e.g. “a monthly budget workbook with a summary dashboard”.</p>
          )}
        </div>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => navigate(`/workbench/${p.id}`)}
              data-testid={`card-wb-project-${p.id}`}
              className="text-left border rounded-lg p-4 hover:bg-accent/50 transition-colors flex flex-col gap-2 min-h-36"
            >
              <div className="flex items-center gap-2">
                {p.kind === "xlsx"
                  ? <FileSpreadsheet className="h-5 w-5 text-emerald-600 shrink-0" />
                  : <Code2 className="h-5 w-5 text-sky-600 shrink-0" />}
                <span className="font-medium truncate flex-1">{p.title}</span>
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                <HealthBadge health={p.health} />
                {p.building && (
                  <Badge variant="outline" className="gap-1">
                    <Loader2 className="h-3 w-3 animate-spin" /> Building
                  </Badge>
                )}
                {p.status === "archived" && (
                  <Badge variant="secondary" className="gap-1">
                    <Archive className="h-3 w-3" /> Completed
                  </Badge>
                )}
                {p.status === "shelved" && (
                  <Badge variant="outline" className="gap-1 text-muted-foreground">
                    <PauseCircle className="h-3 w-3" /> Shelved
                  </Badge>
                )}
                {p.last_error && !p.building && (
                  <Badge variant="destructive" className="gap-1">
                    <AlertCircle className="h-3 w-3" /> Needs attention
                  </Badge>
                )}
              </div>
              <p className="text-sm text-muted-foreground line-clamp-2 flex-1">{p.brief}</p>
              <p className="text-xs text-muted-foreground">
                {format(new Date(p.updated_at), "MMM d, yyyy")}
              </p>
            </button>
          ))}
        </div>
      )}

      <Dialog open={showNew} onOpenChange={setShowNew}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>New Workbench project</DialogTitle>
            <DialogDescription>
              {mode === "describe"
                ? "The AI builds the first version from your brief. You then refine it with instructions — every accepted change is saved as a new version."
                : "Bring an existing Excel workbook, a zip of project files, or a PDF. Workbooks and zips become version 1 exactly as they are and get an automatic review; a PDF is transcribed into a verified workbook with an exception register."}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4">
            <div className="flex gap-2">
              {([["describe", "Describe it", Hammer], ["import", "Import files", Upload]] as const).map(([m, label, Icon]) => (
                <Button
                  key={m}
                  type="button"
                  variant={mode === m ? "default" : "outline"}
                  onClick={() => setMode(m)}
                  className="flex-1"
                  data-testid={`button-wb-mode-${m}`}
                >
                  <Icon className="h-4 w-4 mr-1" /> {label}
                </Button>
              ))}
            </div>
            {mode === "import" && (
              <Input
                type="file"
                accept=".xlsx,.zip,.pdf"
                onChange={e => {
                  const f = e.target.files?.[0] ?? null;
                  setImportFile(f);
                  if (f && !title.trim()) setTitle(f.name.replace(/\.(xlsx|zip|pdf)$/i, ""));
                }}
                data-testid="input-wb-import-file"
              />
            )}
            <Input
              placeholder={mode === "import" ? "Project title (optional — filename is used)" : "Project title"}
              value={title}
              onChange={e => setTitle(e.target.value)}
              data-testid="input-wb-title"
            />
            {mode === "describe" && (
              <div className="flex gap-2">
                {(["xlsx", "code"] as const).map(k => (
                  <Button
                    key={k}
                    type="button"
                    variant={kind === k ? "default" : "outline"}
                    onClick={() => setKind(k)}
                    className="flex-1"
                    data-testid={`button-wb-kind-${k}`}
                  >
                    {k === "xlsx"
                      ? <><FileSpreadsheet className="h-4 w-4 mr-1" /> Excel workbook</>
                      : <><Code2 className="h-4 w-4 mr-1" /> Code project</>}
                  </Button>
                ))}
              </div>
            )}
            <Textarea
              placeholder={mode === "import"
                ? "Optional: what this project is, so later AI builds have context…"
                : kind === "xlsx"
                  ? "Describe the workbook — sheets, columns, formulas, what it should calculate…"
                  : "Describe the program — what it does, language, inputs and outputs…"}
              rows={4}
              value={brief}
              onChange={e => setBrief(e.target.value)}
              data-testid="input-wb-brief"
            />
            {mode === "describe" ? (
              <Button
                className="w-full"
                disabled={!title.trim() || !brief.trim() || createProject.isPending}
                onClick={() => createProject.mutate({ title: title.trim(), kind, brief: brief.trim() })}
                data-testid="button-wb-create"
              >
                {createProject.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  : <Hammer className="h-4 w-4 mr-1" />}
                Create & build v1
              </Button>
            ) : (
              <Button
                className="w-full"
                disabled={!importFile || importProject.isPending}
                onClick={() => importProject.mutate()}
                data-testid="button-wb-import"
              >
                {importProject.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  : <Upload className="h-4 w-4 mr-1" />}
                {importFile && /\.pdf$/i.test(importFile.name)
                  ? "Transcribe PDF → Excel"
                  : importFile && /\.xlsx$/i.test(importFile.name)
                    ? "Review workbook"
                    : "Import as v1"}
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
