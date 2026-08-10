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
  Hammer, Plus, Loader2, ArrowRight, FileSpreadsheet, Code2, Archive,
  AlertCircle, Upload,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";

const API = `${import.meta.env.BASE_URL}api/workbench`.replace(/\/+/g, "/").replace(/\/$/, "");

export type WbProject = {
  id: string;
  title: string;
  kind: "xlsx" | "code";
  brief: string;
  status: "active" | "archived";
  building: boolean;
  last_error: string | null;
  archive_path: string | null;
  created_at: string;
  updated_at: string;
};

export default function WorkbenchPage() {
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [showNew, setShowNew] = useState(false);
  const [mode, setMode] = useState<"describe" | "import">("describe");
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
      const form = new FormData();
      form.append("file", importFile, importFile.name);
      if (title.trim()) form.append("title", title.trim());
      if (brief.trim()) form.append("brief", brief.trim());
      const r = await apiFetch(`${API}/projects/import`, { method: "POST", body: form });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error((body as { detail?: string }).detail ?? "Import failed");
      return body as { id: string };
    },
    onSuccess: (res) => {
      queryClient.invalidateQueries({ queryKey: ["wb-projects"] });
      toast.success("Imported — the files are saved as v1");
      setShowNew(false); setTitle(""); setBrief(""); setImportFile(null);
      navigate(`/workbench/${res.id}`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const projects = data?.projects ?? [];

  return (
    <div className="p-4 md:p-6 max-w-5xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
            <Hammer className="h-6 w-6 text-primary" /> Workbench
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            Describe a spreadsheet or coding project — the AI builds it, you refine it
            version by version, then archive the finished work.
          </p>
        </div>
        <Button onClick={() => setShowNew(true)} data-testid="button-new-wb-project">
          <Plus className="h-4 w-4 mr-1" /> New project
        </Button>
      </div>

      {isLoading ? (
        <div className="space-y-3">{[0, 1, 2].map(i => <Skeleton key={i} className="h-20 w-full" />)}</div>
      ) : projects.length === 0 ? (
        <div className="border border-dashed rounded-lg p-10 text-center text-muted-foreground">
          <Hammer className="h-8 w-8 mx-auto mb-3 opacity-40" />
          <p className="font-medium">No projects yet</p>
          <p className="text-sm mt-1">Start one — e.g. “a monthly budget workbook with a summary dashboard”.</p>
        </div>
      ) : (
        <div className="space-y-3">
          {projects.map(p => (
            <button
              key={p.id}
              onClick={() => navigate(`/workbench/${p.id}`)}
              data-testid={`card-wb-project-${p.id}`}
              className="w-full text-left border rounded-lg p-4 hover:bg-accent/50 transition-colors flex items-center gap-4"
            >
              {p.kind === "xlsx"
                ? <FileSpreadsheet className="h-6 w-6 text-emerald-600 shrink-0" />
                : <Code2 className="h-6 w-6 text-sky-600 shrink-0" />}
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-medium truncate">{p.title}</span>
                  {p.building && (
                    <Badge variant="outline" className="gap-1">
                      <Loader2 className="h-3 w-3 animate-spin" /> Building
                    </Badge>
                  )}
                  {p.status === "archived" && (
                    <Badge variant="secondary" className="gap-1">
                      <Archive className="h-3 w-3" /> Archived
                    </Badge>
                  )}
                  {p.last_error && !p.building && (
                    <Badge variant="destructive" className="gap-1">
                      <AlertCircle className="h-3 w-3" /> Needs attention
                    </Badge>
                  )}
                </div>
                <p className="text-sm text-muted-foreground truncate mt-0.5">{p.brief}</p>
              </div>
              <div className="text-xs text-muted-foreground shrink-0 hidden sm:block">
                {format(new Date(p.updated_at), "MMM d, HH:mm")}
              </div>
              <ArrowRight className="h-4 w-4 text-muted-foreground shrink-0" />
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
                : "Bring an existing Excel workbook or a zip of project files. The upload becomes version 1 exactly as it is — then you can analyze and improve it."}
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
                accept=".xlsx,.zip"
                onChange={e => {
                  const f = e.target.files?.[0] ?? null;
                  setImportFile(f);
                  if (f && !title.trim()) setTitle(f.name.replace(/\.(xlsx|zip)$/i, ""));
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
                Import as v1
              </Button>
            )}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}
