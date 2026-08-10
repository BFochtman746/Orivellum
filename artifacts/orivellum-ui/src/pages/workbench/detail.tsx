import { useState } from "react";
import { useLocation, useRoute } from "wouter";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { apiFetch } from "@/lib/auth";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent,
  AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  ArrowLeft, Loader2, Download, Archive, RotateCcw, Hammer, CheckCircle2,
  AlertCircle, FileSpreadsheet, Code2, Trash2, Send, ScanSearch, Upload,
  FileText,
} from "lucide-react";
import ReactMarkdown from "react-markdown";
import { format } from "date-fns";
import { toast } from "sonner";
import type { WbProject } from "./index";

const API = `${import.meta.env.BASE_URL}api/workbench`.replace(/\/+/g, "/").replace(/\/$/, "");

type WbFile = { name: string; size: number; sha256: string };
type WbVersion = {
  id: string;
  version_no: number;
  instruction: string;
  note: string;
  verdict: string | null;
  created_at: string;
  files: WbFile[];
  checks: Record<string, unknown>;
};
type WbDetail = WbProject & { versions: WbVersion[]; version_count: number };

function fmtBytes(n: number) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1024 / 1024).toFixed(1)} MB`;
}

async function downloadUrl(url: string, filename: string) {
  const r = await apiFetch(url);
  if (!r.ok) throw new Error("download failed");
  const blob = await r.blob();
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(a.href);
}

export default function WorkbenchDetail() {
  const [, params] = useRoute("/workbench/:projectId");
  const projectId = params?.projectId ?? "";
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const [instruction, setInstruction] = useState("");
  const [confirmComplete, setConfirmComplete] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [reportVersion, setReportVersion] = useState<number | null>(null);

  const { data: proj, isLoading } = useQuery<WbDetail>({
    queryKey: ["wb-project", projectId],
    queryFn: () => apiFetch(`${API}/projects/${projectId}`).then(r => {
      if (!r.ok) throw new Error("not found");
      return r.json();
    }),
    enabled: !!projectId,
    refetchInterval: (q) => (q.state.data?.building ? 2_500 : false),
  });

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ["wb-project", projectId] });
    queryClient.invalidateQueries({ queryKey: ["wb-projects"] });
  };

  const iterate = useMutation({
    mutationFn: (text: string) =>
      apiFetch(`${API}/projects/${projectId}/iterate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ instruction: text }),
      }).then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "failed"); return r.json(); }),
    onSuccess: () => { setInstruction(""); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const analyze = useMutation({
    mutationFn: (focus: string) =>
      apiFetch(`${API}/projects/${projectId}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ focus }),
      }).then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "failed"); return r.json(); }),
    onSuccess: () => {
      setInstruction("");
      toast.success("Analyzing — the report will appear as a new version");
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const { data: reportData, isLoading: reportLoading } = useQuery<{ report: string }>({
    queryKey: ["wb-report", projectId, reportVersion],
    queryFn: () => apiFetch(`${API}/projects/${projectId}/versions/${reportVersion}/report`)
      .then(r => { if (!r.ok) throw new Error("no report"); return r.json(); }),
    enabled: !!projectId && reportVersion !== null,
    staleTime: Infinity,
  });

  const revert = useMutation({
    mutationFn: (version_no: number) =>
      apiFetch(`${API}/projects/${projectId}/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ version_no }),
      }).then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "failed"); return r.json(); }),
    onSuccess: (v) => { toast.success(`Restored as v${v.version_no}`); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const complete = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/projects/${projectId}/complete`, { method: "POST" })
        .then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "failed"); return r.json(); }),
    onSuccess: () => { toast.success("Project archived — all versions preserved"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const remove = useMutation({
    mutationFn: () =>
      apiFetch(`${API}/projects/${projectId}`, { method: "DELETE" })
        .then(async r => { if (!r.ok) throw new Error((await r.json()).detail ?? "failed"); return r.json(); }),
    onSuccess: () => { toast.success("Project deleted"); navigate("/workbench"); },
    onError: (e: Error) => toast.error(e.message),
  });

  if (isLoading || !proj) {
    return (
      <div className="p-6 max-w-4xl mx-auto space-y-4">
        <Skeleton className="h-10 w-64" /><Skeleton className="h-32 w-full" /><Skeleton className="h-48 w-full" />
      </div>
    );
  }

  const versions = [...(proj.versions ?? [])].sort((a, b) => b.version_no - a.version_no);
  const latest = versions[0];
  const isArchived = proj.status === "archived";

  return (
    <div className="p-4 md:p-6 max-w-4xl mx-auto space-y-6">
      <div className="flex items-start gap-3">
        <Button variant="ghost" size="icon" onClick={() => navigate("/workbench")} data-testid="button-wb-back">
          <ArrowLeft className="h-4 w-4" />
        </Button>
        <div className="min-w-0 flex-1">
          <h1 className="text-xl font-semibold tracking-tight flex items-center gap-2 flex-wrap">
            {proj.kind === "xlsx"
              ? <FileSpreadsheet className="h-5 w-5 text-emerald-600" />
              : <Code2 className="h-5 w-5 text-sky-600" />}
            <span className="truncate">{proj.title}</span>
            {proj.building && (
              <Badge variant="outline" className="gap-1">
                <Loader2 className="h-3 w-3 animate-spin" /> Building…
              </Badge>
            )}
            {isArchived && (
              <Badge variant="secondary" className="gap-1"><Archive className="h-3 w-3" /> Archived</Badge>
            )}
          </h1>
          <p className="text-sm text-muted-foreground mt-1">{proj.brief}</p>
        </div>
        <div className="flex gap-2 shrink-0">
          {isArchived ? (
            <Button
              variant="outline"
              onClick={() => downloadUrl(`${API}/projects/${projectId}/archive/download`, `${proj.title}.zip`)
                .catch(() => toast.error("Download failed"))}
              data-testid="button-wb-archive-download"
            >
              <Download className="h-4 w-4 mr-1" /> Archive
            </Button>
          ) : (
            <Button
              variant="outline"
              disabled={proj.building || versions.length === 0}
              onClick={() => setConfirmComplete(true)}
              data-testid="button-wb-complete"
            >
              <Archive className="h-4 w-4 mr-1" /> Complete & archive
            </Button>
          )}
          <Button variant="ghost" size="icon" onClick={() => setConfirmDelete(true)} data-testid="button-wb-delete">
            <Trash2 className="h-4 w-4 text-muted-foreground" />
          </Button>
        </div>
      </div>

      {proj.last_error && !proj.building && (
        <div className="border border-destructive/40 bg-destructive/5 rounded-lg p-3 text-sm flex gap-2">
          <AlertCircle className="h-4 w-4 text-destructive shrink-0 mt-0.5" />
          <div>
            <p className="font-medium text-destructive">The last build didn't produce a new version</p>
            <p className="text-muted-foreground mt-0.5 break-words">{proj.last_error}</p>
            {latest && <p className="text-muted-foreground mt-0.5">v{latest.version_no} is still your latest good version. Adjust the instruction and try again.</p>}
          </div>
        </div>
      )}

      {!isArchived && (
        <div className="border rounded-lg p-4 space-y-3">
          <p className="text-sm font-medium flex items-center gap-2">
            <Hammer className="h-4 w-4 text-primary" />
            {versions.length === 0 ? "Building the first version…" : "What should change next?"}
          </p>
          <Textarea
            placeholder={proj.kind === "xlsx"
              ? "e.g. Add a Savings sheet, link its total into the dashboard, format currency columns…"
              : "e.g. Add input validation and split the parsing logic into its own module…"}
            rows={3}
            value={instruction}
            onChange={e => setInstruction(e.target.value)}
            disabled={proj.building}
            data-testid="input-wb-instruction"
          />
          <div className="flex justify-end gap-2">
            {versions.length > 0 && (
              <Button
                variant="outline"
                disabled={proj.building || analyze.isPending}
                onClick={() => analyze.mutate(instruction.trim())}
                title="Review the current files and produce a findings report as a new version. The text box above (optional) focuses the review."
                data-testid="button-wb-analyze"
              >
                {analyze.isPending
                  ? <Loader2 className="h-4 w-4 animate-spin mr-1" />
                  : <ScanSearch className="h-4 w-4 mr-1" />}
                Analyze
              </Button>
            )}
            <Button
              disabled={!instruction.trim() || proj.building || iterate.isPending}
              onClick={() => iterate.mutate(instruction.trim())}
              data-testid="button-wb-iterate"
            >
              {proj.building || iterate.isPending
                ? <Loader2 className="h-4 w-4 animate-spin mr-1" />
                : <Send className="h-4 w-4 mr-1" />}
              Build next version
            </Button>
          </div>
        </div>
      )}

      <div className="space-y-3">
        <h2 className="text-sm font-medium text-muted-foreground uppercase tracking-wide">
          Version history {versions.length > 0 && `(${versions.length})`}
        </h2>
        {versions.length === 0 ? (
          <div className="border border-dashed rounded-lg p-8 text-center text-muted-foreground text-sm">
            {proj.building
              ? <span className="flex items-center justify-center gap-2"><Loader2 className="h-4 w-4 animate-spin" /> The AI is building v1 — this can take a minute or two.</span>
              : "No versions yet."}
          </div>
        ) : versions.map(v => (
          <div key={v.id} className="border rounded-lg p-4" data-testid={`card-wb-version-${v.version_no}`}>
            <div className="flex items-center gap-2 flex-wrap">
              <Badge variant={v.version_no === latest?.version_no ? "default" : "outline"}>
                v{v.version_no}
              </Badge>
              {v.verdict === "verified" && (
                <Badge variant="outline" className="gap-1 text-emerald-700 border-emerald-300">
                  <CheckCircle2 className="h-3 w-3" /> Checks passed
                </Badge>
              )}
              {v.verdict === "imported" && (
                <Badge variant="outline" className="gap-1 text-sky-700 border-sky-300">
                  <Upload className="h-3 w-3" /> Imported
                </Badge>
              )}
              {v.verdict === "analyzed" && (
                <Badge variant="outline" className="gap-1 text-violet-700 border-violet-300">
                  <ScanSearch className="h-3 w-3" /> Analyzed
                </Badge>
              )}
              <span className="text-xs text-muted-foreground ml-auto">
                {format(new Date(v.created_at), "MMM d, yyyy HH:mm")}
              </span>
            </div>
            <p className="text-sm mt-2">{v.instruction}</p>
            {v.note && <p className="text-xs text-muted-foreground mt-1">{v.note}</p>}
            <div className="mt-3 space-y-1">
              {v.files.map(f => (
                <div key={f.name} className="text-xs text-muted-foreground flex items-center gap-2">
                  <span className="font-mono truncate">{f.name}</span>
                  <span className="shrink-0">{fmtBytes(f.size)}</span>
                  <span className="font-mono text-[10px] opacity-60 shrink-0 hidden md:inline">
                    {f.sha256.slice(0, 12)}…
                  </span>
                </div>
              ))}
            </div>
            <div className="flex gap-2 mt-3">
              {v.files.some(f => f.name === "ANALYSIS_REPORT.md") && (
                <Button
                  size="sm" variant="outline"
                  onClick={() => setReportVersion(v.version_no)}
                  data-testid={`button-wb-report-${v.version_no}`}
                >
                  <FileText className="h-3.5 w-3.5 mr-1" /> View report
                </Button>
              )}
              <Button
                size="sm" variant="outline"
                onClick={() => downloadUrl(
                  `${API}/projects/${projectId}/versions/${v.version_no}/download`,
                  `${proj.title}_v${v.version_no}.zip`,
                ).catch(() => toast.error("Download failed"))}
                data-testid={`button-wb-download-${v.version_no}`}
              >
                <Download className="h-3.5 w-3.5 mr-1" /> Download
              </Button>
              {!isArchived && v.version_no !== latest?.version_no && (
                <Button
                  size="sm" variant="ghost"
                  disabled={proj.building || revert.isPending}
                  onClick={() => revert.mutate(v.version_no)}
                  data-testid={`button-wb-revert-${v.version_no}`}
                >
                  <RotateCcw className="h-3.5 w-3.5 mr-1" /> Restore this version
                </Button>
              )}
            </div>
          </div>
        ))}
      </div>

      <Dialog open={reportVersion !== null} onOpenChange={(o) => { if (!o) setReportVersion(null); }}>
        <DialogContent className="max-w-3xl max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <FileText className="h-4 w-4" /> Analysis report — v{reportVersion}
            </DialogTitle>
          </DialogHeader>
          {reportLoading ? (
            <div className="space-y-3"><Skeleton className="h-6 w-2/3" /><Skeleton className="h-40 w-full" /></div>
          ) : (
            <div className="prose prose-sm dark:prose-invert max-w-none break-words" data-testid="text-wb-report">
              <ReactMarkdown>{reportData?.report ?? "_Report could not be loaded._"}</ReactMarkdown>
            </div>
          )}
        </DialogContent>
      </Dialog>

      <AlertDialog open={confirmComplete} onOpenChange={setConfirmComplete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Complete & archive this project?</AlertDialogTitle>
            <AlertDialogDescription>
              Every version is packed into one archive with a file-hash manifest, and the
              project becomes read-only. You can still download everything afterwards.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction onClick={() => complete.mutate()} data-testid="button-wb-confirm-complete">
              Archive project
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <AlertDialog open={confirmDelete} onOpenChange={setConfirmDelete}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete this project?</AlertDialogTitle>
            <AlertDialogDescription>
              {isArchived
                ? "The project entry and working files are removed. The archive zip already created is kept on disk."
                : "All versions and files for this project are removed. This cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => remove.mutate()}
              data-testid="button-wb-confirm-delete"
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
