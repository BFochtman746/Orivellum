import { useRef, useState, useEffect } from "react";
import { useLocation, useSearch } from "wouter";
import { apiFetch } from "@/lib/auth";
import {
  useListLibrary,
  useSearchLibrary,
  useDeleteDocument,
  useListWorks,
  getListLibraryQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Search, Upload, FileText, Database, Filter,
  Library as LibraryIcon, AlertCircle, RefreshCw, Trash2,
  CheckCircle2, Clock, FileQuestion, X, Package, Layers,
  FolderOpen, Sparkles, GitMerge, Star, GitBranch, Download, Network, StopCircle,
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle,
  DialogTrigger, DialogFooter,
} from "@/components/ui/dialog";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Lifecycle badge ────────────────────────────────────────────────────────────

type Lifecycle = "draft" | "canonical" | "superseded" | "reference";

const LIFECYCLE_CFG: Record<string, {
  label: string;
  cls: string;
  style: React.CSSProperties;
  icon?: React.ElementType;
}> = {
  canonical:  {
    label: "canonical",
    cls: "",
    style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
    icon: Star,
  },
  superseded: {
    label: "superseded",
    cls: "bg-muted/50 border-border text-muted-foreground line-through",
    style: {},
  },
  reference:  {
    label: "reference",
    cls: "",
    style: { color: "var(--ink-soft)", borderColor: "var(--line)", background: "transparent" },
  },
};

function LifecycleBadge({ lifecycle }: { lifecycle?: string }) {
  if (!lifecycle || lifecycle === "draft") return null;
  const cfg = LIFECYCLE_CFG[lifecycle];
  if (!cfg) return null;
  const Icon = cfg.icon;
  return (
    <span
      className={`text-[10px] font-mono flex items-center gap-0.5 rounded px-1.5 py-0.5 border ${cfg.cls}`}
      style={cfg.style}
    >
      {Icon && <Icon className="w-2.5 h-2.5" />}
      {cfg.label}
    </span>
  );
}

// ─── Near-duplicates banner ───────────────────────────────────────────────────

type DupePair = {
  id: string;
  doc_a_id: string;
  doc_b_id: string;
  doc_a_title: string;
  doc_b_title: string;
  similarity: number;
  kind: string;
};

function DuplicatePairRow({
  pair,
  onResolved,
}: {
  pair: DupePair;
  onResolved: () => void;
}) {
  const [resolving, setResolving] = useState<string | null>(null);

  const resolve = async (action: string) => {
    setResolving(action);
    try {
      const resp = await apiFetch(`${BASE}/library/duplicates/${pair.id}/resolve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!resp.ok) throw new Error("Failed");
      const label =
        action === "keep_both"
          ? "Dismissed"
          : action === "mark_versions"
          ? "Linked as versions"
          : "Marked superseded";
      toast.success(label);
      onResolved();
    } catch {
      toast.error("Could not resolve — try again");
    } finally {
      setResolving(null);
    }
  };

  return (
    <div className="flex flex-col gap-1.5 py-2 border-t border-amber-200/60 first:border-t-0 first:pt-0">
      <p className="text-[11px] font-mono text-amber-800 flex items-center flex-wrap gap-x-1.5 gap-y-0.5">
        <span className="font-semibold">{pair.doc_a_title || pair.doc_a_id.slice(0, 8)}</span>
        <span className="opacity-60">↔</span>
        <span className="font-semibold">{pair.doc_b_title || pair.doc_b_id.slice(0, 8)}</span>
        <span className="opacity-60 ml-1">
          {Math.round(pair.similarity * 100)}% similar · {pair.kind.replace(/_/g, " ")}
        </span>
      </p>
      <div className="flex items-center gap-1.5 flex-wrap">
        <button
          onClick={() => resolve("mark_versions")}
          disabled={resolving !== null}
          className="text-[10px] font-mono px-2 py-0.5 rounded border border-amber-400 bg-amber-100 hover:bg-amber-200 text-amber-900 disabled:opacity-40 transition-colors"
        >
          {resolving === "mark_versions" ? "…" : "Link as versions"}
        </button>
        <button
          onClick={() => resolve("mark_superseded")}
          disabled={resolving !== null}
          className="text-[10px] font-mono px-2 py-0.5 rounded border border-amber-300 bg-white/50 hover:bg-amber-100 text-amber-800 disabled:opacity-40 transition-colors"
        >
          {resolving === "mark_superseded" ? "…" : "Mark older superseded"}
        </button>
        <button
          onClick={() => resolve("keep_both")}
          disabled={resolving !== null}
          className="text-[10px] font-mono px-2 py-0.5 rounded text-amber-600/70 hover:text-amber-700 disabled:opacity-40 transition-colors"
        >
          {resolving === "keep_both" ? "…" : "Keep both"}
        </button>
      </div>
    </div>
  );
}

function DuplicatesBanner({ readyDocCount = 0 }: { readyDocCount?: number }) {
  const [collapsed, setCollapsed] = useState(false);
  const [scanning, setScanning] = useState(false);
  const queryClient = useQueryClient();
  const { data, refetch } = useQuery<{ pairs: DupePair[]; count: number }>({
    queryKey: ["library", "duplicates"],
    queryFn: () => apiFetch(`${BASE}/library/duplicates`).then((r) => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });

  const count = data?.count ?? 0;

  const handleScan = async () => {
    setScanning(true);
    try {
      const r = await apiFetch(`${BASE}/library/scan-duplicates`, { method: "POST" });
      const body = await r.json().catch(() => ({}));
      const queued: number = body.queued ?? 0;

      if (queued === 0) {
        toast.info("All documents already indexed — no new pairs found.");
        setScanning(false);
        return;
      }

      toast.info(`Scanning ${queued} document${queued !== 1 ? "s" : ""}…`, { duration: 3000 });

      // Poll for results; stop after ~45 s or once pairs appear
      const started = Date.now();
      const pairsBefore = count;
      const poll = setInterval(async () => {
        try {
          const pr = await apiFetch(`${BASE}/library/duplicates`);
          const pd = await pr.json();
          const newCount: number = pd.count ?? 0;
          queryClient.setQueryData(["library", "duplicates"], pd);
          if (newCount > pairsBefore) {
            clearInterval(poll);
            setScanning(false);
            const found = newCount - pairsBefore;
            toast.success(
              `Scan complete — ${found} near-duplicate pair${found !== 1 ? "s" : ""} found`,
              { duration: 6000 }
            );
            return;
          }
          if (Date.now() - started > 45_000) {
            clearInterval(poll);
            setScanning(false);
            toast.success("Scan complete — no duplicate pairs detected.", { duration: 4000 });
          }
        } catch {
          clearInterval(poll);
          setScanning(false);
        }
      }, 2_000);
    } catch {
      toast.error("Could not start duplicate scan");
      setScanning(false);
    }
  };

  const handleResolved = () => {
    refetch();
    queryClient.invalidateQueries({ queryKey: ["library", "duplicates"] });
  };

  // When there are no pairs yet, show a compact scan prompt (if there are ready docs)
  if (count === 0) {
    if (readyDocCount === 0) return null;
    return (
      <div className="rounded-lg border border-border/50 bg-muted/20 px-4 py-3 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <GitMerge className="w-4 h-4 shrink-0" />
          <span>No duplicate pairs found yet.</span>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={scanning}
          onClick={handleScan}
          className="gap-1.5 text-xs shrink-0"
        >
          {scanning
            ? <><RefreshCw className="w-3 h-3 animate-spin" /> Scanning…</>
            : <><Search className="w-3 h-3" /> Scan for duplicates</>}
        </Button>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-amber-200 bg-amber-50/60 text-amber-900 overflow-hidden">
      {/* Header row */}
      <div className="flex items-center gap-2.5 px-4 py-2.5">
        <GitMerge className="w-4 h-4 shrink-0 text-amber-600" />
        <p className="flex-1 text-sm font-medium">
          {count} near-duplicate pair{count !== 1 ? "s" : ""} detected
        </p>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="text-[10px] font-mono text-amber-600/70 hover:text-amber-700 transition-colors"
        >
          {collapsed ? "show" : "hide"}
        </button>
      </div>
      {/* Expandable pair list */}
      {!collapsed && (
        <div className="px-4 pb-3 space-y-0">
          {(data?.pairs ?? []).slice(0, 5).map((p) => (
            <DuplicatePairRow key={p.id} pair={p} onResolved={handleResolved} />
          ))}
          {count > 5 && (
            <p className="text-[10px] font-mono text-amber-700/60 pt-1.5 border-t border-amber-200/60">
              {count - 5} more pair{count - 5 !== 1 ? "s" : ""} not shown
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ── Readiness config ─────────────────────────────────────────────────────────

const READINESS: Record<string, {
  label: string;
  icon: React.ElementType;
  cls: string;
  style: React.CSSProperties;
}> = {
  ready:        {
    label: "READY",        icon: CheckCircle2,
    cls: "", style: { color: "var(--green-2)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", background: "var(--green-soft)" },
  },
  imported:     {
    label: "PROCESSING",   icon: Clock,
    cls: "", style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
  },
  transcribing: {
    label: "TRANSCRIBING", icon: Clock,
    // No violet VELLUM token — gilt is the nearest processing-state equivalent
    cls: "", style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
  },
  no_text:      {
    label: "NO TEXT",      icon: FileQuestion,
    cls: "", style: { color: "var(--rust)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)" },
  },
  error:        {
    label: "ERROR",        icon: AlertCircle,
    cls: "", style: { color: "var(--rust)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)" },
  },
};

type Readiness = keyof typeof READINESS;

function ReadinessBadge({ readiness }: { readiness: string }) {
  const cfg = READINESS[readiness] ?? READINESS.imported;
  const Icon = cfg.icon;
  return (
    <span
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border ${cfg.cls}`}
      style={cfg.style}
    >
      <Icon className="w-2.5 h-2.5" />
      {cfg.label}
    </span>
  );
}

// ── Reprocess helper ──────────────────────────────────────────────────────────

async function reprocessDoc(docId: string): Promise<void> {
  const resp = await apiFetch(`${BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

// ── Import dialog ─────────────────────────────────────────────────────────────

interface ImportDialogProps {
  onSuccess: () => void;
  defaultOpen?: boolean;
}

type FileState = "pending" | "uploading" | "done" | "duplicate" | "error" | "cancelled";

interface FileStatus {
  file: File;
  state: FileState;
  pct: number;       // 0-100
  docId?: string;
  error?: string;
}

function fmt(bytes: number) {
  if (bytes >= 1_048_576) return `${(bytes / 1_048_576).toFixed(1)} MB`;
  return `${(bytes / 1024).toFixed(1)} KB`;
}

function ImportDialog({ onSuccess, defaultOpen = false }: ImportDialogProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [queue, setQueue] = useState<FileStatus[]>([]);
  const [workId, setWorkId] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  // Abort support: cancelledRef is a stop-flag; xhrRef holds the in-flight XHR
  // so it can be aborted synchronously from handleStop without a state update.
  const cancelledRef = useRef(false);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [, navigateTo] = useLocation();
  const { data: worksResp } = useListWorks();

  const addFiles = (incoming: FileList | File[]) => {
    // No client-side deduplication by name — two files can share a basename
    // (e.g. from different folders). Content identity is the backend's job via SHA.
    const arr = Array.from(incoming);
    setQueue((prev) => [
      ...prev,
      ...arr.map((f): FileStatus => ({ file: f, state: "pending", pct: 0 })),
    ]);
  };

  const removeFile = (idx: number) =>
    setQueue((prev) => prev.filter((_, i) => i !== idx));

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    // Reject drops while the queue is running — the loop snapshot would miss them.
    if (uploading) return;
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  const updateStatus = (idx: number, patch: Partial<FileStatus>) =>
    setQueue((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));

  const uploadOne = (status: FileStatus, idx: number, wId: string): Promise<void> =>
    new Promise((resolve) => {
      // If the stop flag is already set, mark this file cancelled immediately.
      if (cancelledRef.current) {
        updateStatus(idx, { state: "cancelled", pct: 0 });
        resolve();
        return;
      }

      updateStatus(idx, { state: "uploading", pct: 0 });
      const form = new FormData();
      form.append("file", status.file, status.file.name);
      if (wId) form.append("work_id", wId);

      const xhr = new XMLHttpRequest();
      xhrRef.current = xhr;
      xhr.open("POST", `${BASE}/library/upload`);
      xhr.withCredentials = true;

      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) {
          updateStatus(idx, { pct: Math.min(99, Math.round((e.loaded / e.total) * 100)) });
        }
      };

      xhr.onload = () => {
        xhrRef.current = null;
        if (xhr.status < 200 || xhr.status >= 300) {
          let detail = `HTTP ${xhr.status}`;
          try { detail = JSON.parse(xhr.responseText)?.detail ?? detail; } catch {}
          updateStatus(idx, { state: "error", pct: 0, error: detail });
        } else {
          let res: any = {};
          try { res = JSON.parse(xhr.responseText); } catch {}
          if (res.duplicate) {
            updateStatus(idx, { state: "duplicate", pct: 100, docId: res.document?.id });
          } else {
            updateStatus(idx, { state: "done", pct: 100, docId: res.document?.id });
          }
        }
        resolve();
      };

      xhr.onerror = () => {
        xhrRef.current = null;
        updateStatus(idx, { state: "error", pct: 0, error: "Network error" });
        resolve(); // continue queue even on failure
      };

      xhr.onabort = () => {
        xhrRef.current = null;
        updateStatus(idx, { state: "cancelled", pct: 0 });
        resolve();
      };

      xhr.send(form);
    });

  // Shared finish logic — inspects the final queue snapshot after a run completes.
  // Does not auto-close when any file was cancelled (user stopped deliberately).
  const finishRun = (final: FileStatus[]) => {
    const done      = final.filter((s) => s.state === "done").length;
    const dupes     = final.filter((s) => s.state === "duplicate");
    const errors    = final.filter((s) => s.state === "error").length;
    const cancelled = final.filter((s) => s.state === "cancelled").length;
    const total     = final.length;

    // Don't auto-close when files were cancelled — the user stopped the run on
    // purpose and may want to inspect what happened before dismissing.
    if (cancelled > 0) return;

    if (errors === 0) {
      if (total === 1) {
        const s = final[0];
        if (s.state === "duplicate") {
          toast.info(`${s.file.name} already exists — opening existing document`);
          if (s.docId) navigateTo(`/library/${s.docId}`);
        } else {
          toast.success(`${s.file.name} imported — extraction running`, {
            description: s.docId ? "View Intake Profile →" : undefined,
            action: s.docId ? { label: "Intake Profile", onClick: () => navigateTo(`/intake?doc=${s.docId}`) } : undefined,
            duration: 8000,
          });
        }
      } else {
        const parts: string[] = [];
        if (done) parts.push(`${done} imported`);
        if (dupes.length) parts.push(`${dupes.length} already existed`);
        toast.success(`${parts.join(", ")} — extraction running`);
        if (dupes.length === 1 && done === 0 && dupes[0].docId) {
          navigateTo(`/library/${dupes[0].docId}`);
        }
      }
      setTimeout(() => { setOpen(false); setQueue([]); setWorkId(""); }, 300);
    } else {
      toast.warning(`${done + dupes.length} of ${total} imported — ${errors} failed`);
    }
  };

  const handleStop = () => {
    cancelledRef.current = true;
    xhrRef.current?.abort();
    // uploading + setUploading(false) happen after the current uploadOne promise
    // resolves (via onabort), so the loop in handleImport exits naturally.
  };

  const handleImport = async () => {
    const pending = queue.filter((s) => s.state === "pending");
    if (!pending.length || uploading) return;
    cancelledRef.current = false;
    setUploading(true);

    // Upload sequentially using the closure snapshot — drops are blocked while
    // uploading so queue.length is stable for the duration of this loop.
    for (let i = 0; i < queue.length; i++) {
      if (queue[i].state !== "pending") continue;
      // If stop was requested mid-loop, mark remaining files cancelled immediately.
      if (cancelledRef.current) {
        updateStatus(i, { state: "cancelled", pct: 0 });
        continue;
      }
      await uploadOne(queue[i], i, workId);
    }

    onSuccess();
    setUploading(false);

    if (cancelledRef.current) {
      // Show a summary toast but leave the dialog open so the user can see results.
      setQueue((final) => {
        const done = final.filter((s) => s.state === "done" || s.state === "duplicate").length;
        const total = final.length;
        if (done > 0) {
          toast.info(`Import stopped — ${done} of ${total} uploaded`, { duration: 5000 });
        } else {
          toast.info("Import cancelled — no files were uploaded");
        }
        return final;
      });
    } else {
      setQueue((final) => { finishRun(final); return final; });
    }
  };

  // Immediately retry a single failed file without re-running the whole queue.
  const retryFile = async (status: FileStatus, idx: number) => {
    if (uploading) return;
    // Clear any lingering stop flag so a single-file retry always sends the XHR.
    cancelledRef.current = false;
    setUploading(true);
    await uploadOne(status, idx, workId);
    onSuccess();
    setUploading(false);
    setQueue((final) => {
      const allTerminal = final.every(
        (s) => s.state === "done" || s.state === "duplicate" || s.state === "error" || s.state === "cancelled"
      );
      if (allTerminal) finishRun(final);
      return final;
    });
  };

  // Retry all failed files at once — same loop as handleImport but scoped to
  // error-state entries. Clears the stop flag first so the XHRs are sent.
  const handleRetryFailed = async () => {
    const errorIndices = queue
      .map((s, i) => (s.state === "error" ? i : -1))
      .filter((i) => i >= 0);
    if (!errorIndices.length || uploading) return;
    cancelledRef.current = false;
    setUploading(true);

    for (const i of errorIndices) {
      if (cancelledRef.current) {
        updateStatus(i, { state: "cancelled", pct: 0 });
        continue;
      }
      await uploadOne(queue[i], i, workId);
    }

    onSuccess();
    setUploading(false);

    if (cancelledRef.current) {
      setQueue((final) => {
        const done = final.filter((s) => s.state === "done" || s.state === "duplicate").length;
        const total = final.length;
        if (done > 0) {
          toast.info(`Import stopped — ${done} of ${total} uploaded`, { duration: 5000 });
        } else {
          toast.info("Import cancelled — no files were uploaded");
        }
        return final;
      });
    } else {
      setQueue((final) => { finishRun(final); return final; });
    }
  };

  const anyPending = queue.some((s) => s.state === "pending");
  const anyError   = queue.some((s) => s.state === "error");
  const errorCount = queue.filter((s) => s.state === "error").length;
  const uploadingCount = queue.filter((s) => s.state === "uploading").length;
  const doneCount = queue.filter((s) => s.state === "done" || s.state === "duplicate").length;
  const total = queue.length;

  const stateIcon = (s: FileStatus) => {
    if (s.state === "done")      return <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />;
    if (s.state === "duplicate") return <CheckCircle2 className="w-3.5 h-3.5 text-blue-400 shrink-0" />;
    if (s.state === "error")     return <AlertCircle  className="w-3.5 h-3.5 text-destructive shrink-0" />;
    if (s.state === "uploading") return <Clock        className="w-3.5 h-3.5 text-primary animate-pulse shrink-0" />;
    if (s.state === "cancelled") return <X            className="w-3.5 h-3.5 text-muted-foreground shrink-0" />;
    return <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />;
  };

  return (
    <Dialog open={open} onOpenChange={(v) => { if (!uploading) { setOpen(v); if (!v) { setQueue([]); setWorkId(""); } } }}>
      <DialogTrigger asChild>
        <Button className="gap-2">
          <Upload className="w-4 h-4" />
          Import Documents
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">Import Documents</DialogTitle>
        </DialogHeader>

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => !uploading && inputRef.current?.click()}
          className={`mt-2 border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
            uploading ? "cursor-default opacity-60" :
            dragging ? "border-primary bg-primary/5 cursor-copy" :
            queue.length ? "border-primary/40 bg-muted/10 cursor-pointer hover:border-primary/60" :
            "border-border hover:border-primary/50 hover:bg-muted/30 cursor-pointer"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept=".pdf,application/pdf,.docx,.doc,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,.csv,text/csv,.pptx,.ppt,application/vnd.openxmlformats-officedocument.presentationml.presentation,.txt,text/plain,.md,text/markdown,.png,.jpg,.jpeg,.webp,.gif,image/*,.mp3,audio/mpeg,.wav,audio/wav,.m4a,audio/mp4,.ogg,audio/ogg,.flac,audio/flac,audio/*,.py,.js,.ts,.jsx,.tsx,.java,.cpp,.c,.cs,.go,.rs,.rb,.html,.htm,text/html,.json,application/json,.zip,application/zip,.rtf,.epub,.xml"
            multiple
            disabled={uploading}
            onChange={(e) => { if (e.target.files?.length) { addFiles(e.target.files); e.target.value = ""; } }}
          />
          {queue.length === 0 ? (
            <>
              <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
              <p className="text-sm font-medium">Drop files or click to browse</p>
              <p className="text-xs text-muted-foreground mt-1">
                Select multiple files — PDF, DOCX, XLSX, CSV, PPTX, TXT, MD, images, audio, code, ZIP, and more
              </p>
            </>
          ) : (
            <p className="text-xs text-muted-foreground">
              {uploading
                ? `Uploading ${doneCount + uploadingCount} of ${total}…`
                : `${total} file${total !== 1 ? "s" : ""} queued · click or drop to add more`}
            </p>
          )}
        </div>

        {/* File queue */}
        {queue.length > 0 && (
          <div className="max-h-52 overflow-y-auto space-y-1.5 pr-0.5">
            {queue.map((s, idx) => (
              <div key={`${s.file.name}-${idx}`} className="flex items-center gap-2 rounded-md border border-border/50 bg-muted/20 px-3 py-2">
                {stateIcon(s)}
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-medium truncate" title={s.file.name}>{s.file.name}</p>
                  {s.state === "uploading" ? (
                    <div className="mt-1 h-1 w-full rounded-full bg-muted overflow-hidden">
                      <div
                        className="h-full bg-primary transition-all duration-200 rounded-full"
                        style={{ width: `${s.pct}%` }}
                      />
                    </div>
                  ) : s.state === "error" ? (
                    <p className="text-[10px] text-destructive font-mono mt-0.5 truncate">{s.error}</p>
                  ) : s.state === "duplicate" ? (
                    <p className="text-[10px] text-blue-500 font-mono mt-0.5">already in library</p>
                  ) : s.state === "cancelled" ? (
                    <p className="text-[10px] text-muted-foreground font-mono mt-0.5">cancelled</p>
                  ) : (
                    <p className="text-[10px] text-muted-foreground font-mono">{fmt(s.file.size)}</p>
                  )}
                </div>
                {s.state === "pending" && !uploading && (
                  <button onClick={() => removeFile(idx)} className="text-muted-foreground hover:text-destructive shrink-0">
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
                {s.state === "duplicate" && s.docId && (
                  <button
                    onClick={() => navigateTo(`/library/${s.docId}`)}
                    className="text-[10px] font-mono text-blue-500 hover:underline shrink-0"
                  >
                    View
                  </button>
                )}
                {s.state === "error" && !uploading && (
                  <button
                    onClick={() => retryFile(s, idx)}
                    className="text-[10px] font-mono text-muted-foreground hover:text-foreground shrink-0"
                  >
                    Retry now
                  </button>
                )}
              </div>
            ))}
          </div>
        )}

        {/* Work link selector */}
        <div className="space-y-1">
          <label className="text-xs font-mono uppercase text-muted-foreground">
            Link all to Work (optional)
          </label>
          <Select value={workId || "__none__"} onValueChange={(v) => setWorkId(v === "__none__" ? "" : v)} disabled={uploading}>
            <SelectTrigger className="font-mono text-sm">
              <SelectValue placeholder="— None —" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="__none__" className="text-xs font-mono text-muted-foreground">— None —</SelectItem>
              {(worksResp?.works ?? []).map((w) => (
                <SelectItem key={w.id!} value={w.id!} className="text-xs font-mono">
                  {w.title ?? w.id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => { setOpen(false); setQueue([]); setWorkId(""); }} disabled={uploading}>
            Cancel
          </Button>
          {uploading ? (
            <Button
              variant="destructive"
              onClick={handleStop}
              className="gap-1.5"
            >
              <StopCircle className="w-4 h-4" />
              Stop
            </Button>
          ) : (
            <>
              {anyError && (
                <Button
                  variant="outline"
                  onClick={handleRetryFailed}
                  className="gap-1.5"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  Retry {errorCount} failed
                </Button>
              )}
              <Button onClick={handleImport} disabled={!anyPending}>
                {`Import ${queue.filter(s => s.state === "pending").length || ""} ${queue.filter(s => s.state === "pending").length === 1 ? "file" : "files"}`.trim()}
              </Button>
            </>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Library() {
  const [search, setSearch] = useState("");
  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(new Set());
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const searchStr = useSearch();
  const openImport = new URLSearchParams(searchStr).get("import") === "1";
  // Tier filter pre-selected from URL (e.g. linked from dashboard scorecard tiles)
  const urlTier = new URLSearchParams(searchStr).get("tier") ?? "all";

  const { data: listResp, isLoading: loadingList } = useListLibrary(
    {},
    {
      query: {
        enabled: !search,
        queryKey: getListLibraryQueryKey({}),
        // Poll every 3 s while any document is still processing so extraction
        // failures surface automatically without a manual refresh.
        refetchInterval: (query) => {
          const docs: any[] = query.state.data?.documents ?? [];
          return docs.some((d) => d.readiness === "imported") ? 3000 : false;
        },
      },
    }
  );
  const [searchMode, setSearchMode] = useState<"keyword" | "semantic" | "hybrid">("hybrid");
  const { data: searchResp, isLoading: loadingSearch } = useSearchLibrary(
    { q: search, mode: searchMode },
    { query: { enabled: !!search, queryKey: ["librarySearch", search, searchMode] } }
  );
  const deleteDoc = useDeleteDocument();
  const [readinessFilter, setReadinessFilter] = useState<"all" | "ready" | "processing" | "error">("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [workFilter, setWorkFilter] = useState<string>("all");
  const [lifecycleFilter, setLifecycleFilter] = useState<string>("all");
  // Tier filter — initialised from ?tier= URL param so dashboard scorecard links work
  const [tierFilter, setTierFilter] = useState<string>(
    ["canon", "source", "artifact"].includes(urlTier) ? urlTier : "all"
  );
  const [showFilters, setShowFilters] = useState(false);
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "a-z" | "z-a">("newest");
  const [groupByWork, setGroupByWork] = useState(false);
  const [explodingZips, setExplodingZips] = useState(false);
  const [organizingDocs, setOrganizingDocs] = useState(false);
  const [reprocessingAll, setReprocessingAll] = useState(false);
  const { data: worksResp } = useListWorks();
  const workTitles: Record<string, string> = {};
  for (const w of worksResp?.works ?? []) {
    if (w.id) workTitles[w.id] = w.title ?? w.id.slice(0, 8);
  }
  // Works that actually have at least one document in the list
  const worksWithDocs = Array.from(
    new Set((listResp?.documents ?? []).map((d: any) => d.work_id).filter(Boolean))
  ) as string[];
  // Topic clusters — fetched only when "By Topic" grouping is active
  const { data: topicsResp } = useQuery<{
    topics: Array<{ id: string; name: string; doc_count: number; what_it_is?: string | null; doc_ids?: string[] }>;
    doc_titles?: Record<string, string>;
  }>({
    queryKey: ["topics-with-docs"],
    queryFn: () =>
      apiFetch(`${BASE}/topics?with_docs=true`).then((r) => r.json()),
    enabled: groupByWork && !search,
    staleTime: 120_000,
  });
  // Build a doc_id → topicName index and topicName → what_it_is index
  const docTopicIndex: Record<string, string> = {};
  const topicDescriptions: Record<string, string> = {};
  if (topicsResp) {
    for (const t of topicsResp.topics) {
      if (t.what_it_is) topicDescriptions[t.name] = t.what_it_is;
      for (const did of (t.doc_ids ?? [])) {
        docTopicIndex[did] = t.name;
      }
    }
  }

  const isLoading = search ? loadingSearch : loadingList;
  const rawDocs: any[] = search
    ? (searchResp?.results ?? [])
    : (listResp?.documents ?? []);

  // Derive available kinds from the list for dynamic filter chips
  const availableKinds = Array.from(new Set(rawDocs.map((d) => d.kind ?? "file").filter(Boolean))).sort();

  // Counts per lifecycle for the filter chips
  const lifecycleCounts: Record<string, number> = { all: rawDocs.length };
  for (const d of rawDocs) {
    const lc = d.lifecycle ?? "draft";
    lifecycleCounts[lc] = (lifecycleCounts[lc] ?? 0) + 1;
  }

  const docs = rawDocs
    .filter((d) => {
      const matchesReadiness = (() => {
        if (readinessFilter === "all") return true;
        if (readinessFilter === "ready") return d.readiness === "ready";
        if (readinessFilter === "processing") return d.readiness === "imported";
        if (readinessFilter === "error") return d.readiness === "error" || d.readiness === "no_text";
        return true;
      })();
      const matchesKind = kindFilter === "all" || (d.kind ?? "file") === kindFilter;
      const matchesWork = workFilter === "all"
        ? true
        : workFilter === "__none__"
          ? !d.work_id
          : d.work_id === workFilter;
      const matchesLifecycle = lifecycleFilter === "all" || (d.lifecycle ?? "draft") === lifecycleFilter;
      const matchesTier = (() => {
        if (tierFilter === "all") return true;
        const docTier = d.tier ?? "source";
        // "artifact" scorecard tile covers both artifact + system tiers
        if (tierFilter === "artifact") return docTier === "artifact" || docTier === "system";
        return docTier === tierFilter;
      })();
      return matchesReadiness && matchesKind && matchesWork && matchesLifecycle && matchesTier;
    })
    .sort((a, b) => {
      if (sortBy === "newest") return new Date(b.created_at ?? 0).getTime() - new Date(a.created_at ?? 0).getTime();
      if (sortBy === "oldest") return new Date(a.created_at ?? 0).getTime() - new Date(b.created_at ?? 0).getTime();
      const aTitle = (a.title || a.source?.split("/").pop() || "").toLowerCase();
      const bTitle = (b.title || b.source?.split("/").pop() || "").toLowerCase();
      if (sortBy === "a-z") return aTitle.localeCompare(bTitle);
      if (sortBy === "z-a") return bTitle.localeCompare(aTitle);
      return 0;
    });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: getListLibraryQueryKey({}) });

  const zipCount = rawDocs.filter((d: any) => d.kind === "zip").length;

  const handleExplodeZips = async () => {
    setExplodingZips(true);
    try {
      const resp = await apiFetch(`${BASE}/library/explode-zips`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((data as any).detail ?? "Failed");
      toast.success((data as any).message ?? "ZIP extraction queued");
      setTimeout(invalidate, 2000);
    } catch (err: any) {
      toast.error(err.message ?? "ZIP extraction failed");
    } finally {
      setExplodingZips(false);
    }
  };

  const handleReprocessAll = async () => {
    setReprocessingAll(true);
    try {
      const resp = await apiFetch(`${BASE}/library/reprocess-all`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((data as any).detail ?? "Failed");
      const { queued, queued_zips, queued_stuck, skipped, message } = data as any;
      if (queued === 0) {
        toast.success("All documents are already fully processed.");
      } else {
        toast.success(message ?? `Queued ${queued} document(s) for re-extraction`);
        if (queued_zips > 0)
          toast.info(`${queued_zips} ZIP archive${queued_zips !== 1 ? "s" : ""} will be exploded into individual documents.`);
        if (skipped > 0)
          toast.warning(`${skipped} document${skipped !== 1 ? "s" : ""} skipped — source file missing from disk.`);
      }
      setTimeout(invalidate, 2000);
    } catch (err: any) {
      toast.error(err.message ?? "Reprocess failed");
    } finally {
      setReprocessingAll(false);
    }
  };

  const handleSmartOrganize = async () => {
    setOrganizingDocs(true);
    try {
      const resp = await apiFetch(`${BASE}/library/smart-organize`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((data as any).detail ?? "Failed");
      const { works_created, docs_organised } = data as any;
      toast.success(`Created ${works_created} topic(s), organised ${docs_organised} document(s)`);
      setTimeout(invalidate, 1000);
    } catch (err: any) {
      toast.error(err.message ?? "Smart organize failed");
    } finally {
      setOrganizingDocs(false);
    }
  };

  const handleReprocess = async (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    setReprocessingIds((s) => new Set(s).add(docId));
    try {
      await reprocessDoc(docId);
      toast.success("Reprocessing queued");
      setTimeout(() => {
        invalidate();
        setReprocessingIds((s) => { const n = new Set(s); n.delete(docId); return n; });
      }, 3000);
    } catch (err: any) {
      toast.error(err.message ?? "Reprocess failed");
      setReprocessingIds((s) => { const n = new Set(s); n.delete(docId); return n; });
    }
  };

  const handleDelete = (docId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    deleteDoc.mutate({ docId }, {
      onSuccess: () => { invalidate(); toast.success("Document removed"); },
      onError: () => toast.error("Delete failed"),
    });
  };

  return (
    <TooltipProvider>
      <div className="space-y-6 animate-in fade-in duration-500">
        {/* Header */}
        <div className="border-b border-border/50 pb-4">
          <div className="flex items-start gap-4 flex-wrap justify-between">
            <div className="min-w-0">
              <span className="eyebrow mb-1">The Collection</span>
              <h1 className="vellum-h1">Library</h1>
              <div className="gilt-rule w-40" />
              <p className="text-[13px] mt-1.5" style={{ color: 'var(--ink-soft)' }}>
                {isLoading ? "Loading…" : `${docs.length} document${docs.length !== 1 ? "s" : ""}${search || readinessFilter !== "all" || kindFilter !== "all" || workFilter !== "all" || lifecycleFilter !== "all" ? " matching filters" : ""}`}
              </p>
            </div>
            <div className="flex items-center gap-2 flex-wrap shrink-0">
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs border-primary/40 text-primary hover:bg-primary/5"
                onClick={handleReprocessAll}
                disabled={reprocessingAll}
                title="Re-extract all stuck, errored, or ZIP documents"
              >
                <RefreshCw className={`w-3.5 h-3.5 ${reprocessingAll ? "animate-spin" : ""}`} />
                {reprocessingAll ? "Processing…" : "Reprocess All"}
              </Button>
              {zipCount > 0 && (
                <Button
                  variant="outline"
                  size="sm"
                  className="gap-1.5 text-xs border-amber-300 text-amber-700 hover:bg-amber-50"
                  onClick={handleExplodeZips}
                  disabled={explodingZips}
                >
                  <Package className={`w-3.5 h-3.5 ${explodingZips ? "animate-bounce" : ""}`} />
                  {explodingZips ? "Extracting…" : `Extract ${zipCount} ZIP${zipCount !== 1 ? "s" : ""}`}
                </Button>
              )}
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={handleSmartOrganize}
                disabled={organizingDocs}
              >
                <Sparkles className={`w-3.5 h-3.5 ${organizingDocs ? "animate-spin" : ""}`} />
                {organizingDocs ? "Organising…" : "Smart Sort"}
              </Button>
              <Button
                variant={groupByWork ? "secondary" : "outline"}
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => setGroupByWork((v) => !v)}
              >
                <Layers className="w-3.5 h-3.5" />
                By Topic
              </Button>
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs"
                onClick={() => navigate("/graph")}
                title="View the entity knowledge graph across your library"
              >
                <Network className="w-3.5 h-3.5" />
                Graph
              </Button>
              <ImportDialog onSuccess={invalidate} defaultOpen={openImport} />
            </div>
          </div>
        </div>

        {/* Near-duplicates banner */}
        <DuplicatesBanner readyDocCount={(listResp?.documents ?? []).filter((d: any) => d.readiness === "ready").length} />

        {/* Search */}
        <div className="space-y-3">
          <div className="flex items-center gap-4">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search all documents…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9 bg-background/50"
              />
            </div>
            <div className="flex items-center gap-1 p-0.5 bg-muted/30 rounded-lg shrink-0" title="Search mode">
              {([
                ["keyword", "Keyword"],
                ["semantic", "Semantic"],
                ["hybrid", "Hybrid"],
              ] as const).map(([value, label]) => (
                <button
                  key={value}
                  onClick={() => setSearchMode(value)}
                  className={`px-2.5 py-1.5 rounded text-xs font-mono transition-colors min-h-[34px] touch-manipulation ${
                    searchMode === value
                      ? "bg-background shadow-sm text-foreground"
                      : "text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {label}
                </button>
              ))}
            </div>
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
              className="h-9 rounded-md border border-input bg-background px-2 text-xs font-mono text-muted-foreground focus:outline-none focus:ring-1 focus:ring-ring shrink-0"
              title="Sort order"
            >
              <option value="newest">Newest</option>
              <option value="oldest">Oldest</option>
              <option value="a-z">A → Z</option>
              <option value="z-a">Z → A</option>
            </select>
            <Button
              variant={showFilters ? "secondary" : "outline"}
              size="icon"
              className="shrink-0"
              onClick={() => setShowFilters((v) => !v)}
              title="Toggle filters"
            >
              <Filter className="w-4 h-4" />
            </Button>
          </div>
          {showFilters && (
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground uppercase">Status:</span>
                <div className="flex items-center gap-1 p-0.5 bg-muted/30 rounded-lg">
                  {(["all", "ready", "processing", "error"] as const).map((f) => (
                    <button
                      key={f}
                      onClick={() => setReadinessFilter(f)}
                      className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                        readinessFilter === f
                          ? "bg-background shadow-sm text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {f === "all" ? "All" : f === "processing" ? "Processing" : f.charAt(0).toUpperCase() + f.slice(1)}
                    </button>
                  ))}
                </div>
              </div>
              {availableKinds.length > 1 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground uppercase">Type:</span>
                  <div className="flex items-center gap-1 p-0.5 bg-muted/30 rounded-lg">
                    {["all", ...availableKinds].map((k) => (
                      <button
                        key={k}
                        onClick={() => setKindFilter(k)}
                        className={`px-2.5 py-1 rounded text-xs font-mono uppercase transition-colors ${
                          kindFilter === k
                            ? "bg-background shadow-sm text-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {k === "all" ? "All" : k}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              {worksWithDocs.length > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground uppercase">Work:</span>
                  <div className="flex items-center gap-1 p-0.5 bg-muted/30 rounded-lg flex-wrap">
                    {["all", "__none__", ...worksWithDocs].map((w) => (
                      <button
                        key={w}
                        onClick={() => setWorkFilter(w)}
                        className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                          workFilter === w
                            ? "bg-background shadow-sm text-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {w === "all" ? "All" : w === "__none__" ? "Unlinked" : (workTitles[w] ?? w.slice(0, 8))}
                      </button>
                    ))}
                  </div>
                </div>
              )}
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono text-muted-foreground uppercase">Lifecycle:</span>
                <div className="flex items-center gap-1 p-0.5 bg-muted/30 rounded-lg">
                  {(["all", "canonical", "draft", "reference", "superseded"] as const).map((lc) => {
                    const count = lifecycleCounts[lc] ?? 0;
                    if (lc !== "all" && count === 0) return null;
                    return (
                      <button
                        key={lc}
                        onClick={() => setLifecycleFilter(lc)}
                        className={`px-2.5 py-1 rounded text-xs font-mono transition-colors flex items-center gap-1 ${
                          lifecycleFilter === lc
                            ? "bg-background shadow-sm text-foreground"
                            : "text-muted-foreground hover:text-foreground"
                        }`}
                      >
                        {lc === "all" ? "All" : lc}
                        {count > 0 && (
                          <span className={`text-[10px] tabular-nums ${lifecycleFilter === lc ? "text-muted-foreground" : "text-muted-foreground/60"}`}>
                            {count}
                          </span>
                        )}
                      </button>
                    );
                  })}
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Document list */}
        <div className="grid gap-3">
          {isLoading ? (
            [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-20 w-full" />)
          ) : groupByWork && !search ? (
            // ── Grouped by semantic topic cluster ────────────────────────────
            (() => {
              // If we have real topic clusters, use them; otherwise fall back to work-based grouping
              const hasTopics = topicsResp && topicsResp.topics.length > 0;
              const grouped = new Map<string, any[]>();
              const unclassified: any[] = [];
              if (hasTopics) {
                // Group by the topic this document belongs to (via docTopicIndex)
                for (const doc of docs) {
                  const topicName = docTopicIndex[doc.id];
                  if (topicName) {
                    const arr = grouped.get(topicName) ?? [];
                    arr.push(doc);
                    grouped.set(topicName, arr);
                  } else {
                    unclassified.push(doc);
                  }
                }
              } else {
                // Fallback: group by work
                for (const doc of docs) {
                  if (doc.work_id) {
                    const label = workTitles[doc.work_id] ?? doc.work_id.slice(0, 8);
                    const arr = grouped.get(label) ?? [];
                    arr.push(doc);
                    grouped.set(label, arr);
                  } else {
                    unclassified.push(doc);
                  }
                }
              }
              const groups: Array<{ title: string; color: string; docs: any[] }> = [];
              for (const [label, gdocs] of grouped) {
                groups.push({ title: label, color: hasTopics ? "text-primary" : "text-violet-600", docs: gdocs });
              }
              if (unclassified.length > 0) {
                groups.push({ title: hasTopics ? "Unclassified" : "Unassigned", color: "text-muted-foreground", docs: unclassified });
              }
              if (groups.length === 0) return (
                <div className="vellum-card text-center py-16 px-8" style={{ border: '1px dashed var(--gilt-line)' }}>
                  <LibraryIcon className="w-10 h-10 mx-auto mb-4" style={{ color: 'var(--ink-faint)', opacity: 0.5 }} />
                  <div className="gilt-rule w-16 mx-auto mb-3" />
                  <h3 className="text-lg font-serif font-medium text-balance">No documents found</h3>
                </div>
              );
              return groups.map((group) => (
                <div key={group.title} className="space-y-2">
                  <div className={`pt-2 pb-1 border-b border-border/40`}>
                    <div className="flex items-center gap-2">
                      <FolderOpen className={`w-4 h-4 ${group.color} shrink-0`} />
                      <span className={`text-sm font-semibold font-serif ${group.color}`}>{group.title}</span>
                      <span className="text-xs font-mono text-muted-foreground">
                        {group.docs.length} doc{group.docs.length !== 1 ? "s" : ""}
                      </span>
                    </div>
                    {topicDescriptions[group.title] && (
                      <p className="text-[11px] text-muted-foreground mt-0.5 ml-6 line-clamp-1">
                        {topicDescriptions[group.title]}
                      </p>
                    )}
                  </div>
                  {group.docs.map((doc: any) => {
                    const readiness: string = doc.readiness ?? "imported";
                    const hasError = readiness === "error" || readiness === "no_text";
                    const isReprocessing = reprocessingIds.has(doc.id);
                    return (
                      <Card
                        key={doc.id}
                        data-doc-id={doc.id}
                        onClick={() => navigate(`/library/${doc.id}`)}
                        className={`vellum-card tap spring-scale group cursor-pointer ${hasError ? "" : ""}`}
                        style={hasError ? { borderColor: 'var(--rust)', background: 'var(--rust-soft)' } : {}}
                        data-interactive
                      >
                        <CardContent className="p-3 sm:p-4 flex items-center justify-between gap-4">
                          <div className="flex items-center gap-3 min-w-0">
                            <div className={`w-8 h-8 rounded flex items-center justify-center shrink-0 border ${hasError ? "bg-red-50 border-red-200" : "bg-muted/50 border-border/50"}`}>
                              {hasError ? <AlertCircle className="w-3.5 h-3.5 text-red-500" /> : <FileText className="w-3.5 h-3.5 text-muted-foreground" />}
                            </div>
                            <div className="min-w-0">
                              <h3 className="font-medium truncate text-sm">{doc.title || doc.source || "Untitled"}</h3>
                              <div className="flex flex-wrap items-center gap-1.5 mt-0.5">
                                <Badge variant="secondary" className="font-mono text-[10px] uppercase">{doc.kind ?? "file"}</Badge>
                                <ReadinessBadge readiness={readiness} />
                                <LifecycleBadge lifecycle={doc.lifecycle} />
                                {doc.word_count > 0 && <span className="text-[10px] font-mono text-muted-foreground">{doc.word_count.toLocaleString()} words</span>}
                                {doc.meta?.zip_exploded && (
                                  <span className="text-[10px] text-amber-600 flex items-center gap-1 font-mono bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                                    <Package className="w-2.5 h-2.5" />{doc.meta.zip_child_count ?? "?"} inside
                                  </span>
                                )}
                                {doc.meta?.from_zip && !doc.meta?.zip_exploded && (
                                  <span className="text-[10px] text-violet-500 flex items-center gap-1 font-mono bg-violet-50 border border-violet-200 rounded px-1.5 py-0.5">
                                    <FolderOpen className="w-2.5 h-2.5" />archive
                                  </span>
                                )}
                              </div>
                              {search && doc.snippet && (
                                <p className="mt-1.5 text-[11px] font-mono text-muted-foreground/70 line-clamp-2 leading-relaxed">
                                  {String(doc.snippet).replace(/\[\[/g, "").replace(/\]\]/g, "")}
                                </p>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            {hasError && (
                              <Button variant="ghost" size="icon" className="h-7 w-7 text-amber-600 hover:text-amber-700 hover:bg-amber-50" onClick={(e) => handleReprocess(doc.id, e)} disabled={isReprocessing}>
                                <RefreshCw className={`w-3.5 h-3.5 ${isReprocessing ? "animate-spin" : ""}`} />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" title="Download original file"
                              onClick={(e) => { e.stopPropagation(); window.open(`${BASE}/library/${doc.id}/download`, "_blank"); }}>
                              <Download className="w-3.5 h-3.5" />
                            </Button>
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => handleDelete(doc.id, e)}>
                              <Trash2 className="w-3.5 h-3.5" />
                            </Button>
                          </div>
                        </CardContent>
                      </Card>
                    );
                  })}
                </div>
              ));
            })()
          ) : docs.length > 0 ? (
            docs.map((doc: any) => {
              const readiness: string = doc.readiness ?? "imported";
              const hasError = readiness === "error" || readiness === "no_text";
              const isReprocessing = reprocessingIds.has(doc.id);

              return (
                <Card
                  key={doc.id}
                  data-doc-id={doc.id}
                  onClick={() => navigate(`/library/${doc.id}`)}
                  className="vellum-card tap spring-scale group cursor-pointer"
                  style={hasError ? { borderColor: 'var(--rust)', background: 'var(--rust-soft)' } : {}}
                  data-interactive
                >
                  <CardContent className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-start justify-between gap-4">
                    {/* Left: icon + meta */}
                    <div className="flex items-start gap-4 min-w-0">
                      <div className="w-9 h-9 rounded flex items-center justify-center shrink-0 border"
                           style={hasError
                             ? { background: 'var(--rust-soft)', borderColor: 'var(--rust)' }
                             : { background: 'hsl(var(--muted) / 0.5)', borderColor: 'hsl(var(--border) / 0.5)' }}>
                        {hasError
                          ? <AlertCircle className="w-4 h-4" style={{ color: 'var(--rust)' }} />
                          : <FileText className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors" />
                        }
                      </div>

                      <div className="min-w-0 flex-1">
                        <h3 className="font-medium truncate">
                          {doc.title || doc.source || "Untitled Document"}
                        </h3>
                        <div className="flex flex-wrap items-center gap-2 mt-1.5">
                          <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                            {doc.kind ?? "file"}
                          </Badge>
                          <ReadinessBadge readiness={readiness} />
                          <LifecycleBadge lifecycle={doc.lifecycle} />
                          {doc.word_count > 0 && (
                            <span className="text-[10px] font-mono text-muted-foreground">
                              {doc.word_count.toLocaleString()} words
                            </span>
                          )}
                          {doc.work_id && (
                            <span className="text-[10px] text-muted-foreground flex items-center gap-1 font-mono">
                              <Database className="w-2.5 h-2.5" />
                              {workTitles[doc.work_id] ?? "Linked Work"}
                            </span>
                          )}
                          {doc.meta?.zip_exploded && (
                            <span className="text-[10px] flex items-center gap-1 font-mono rounded px-1.5 py-0.5"
                                  style={{ color: 'var(--gilt)', background: 'var(--gilt-soft)', border: '1px solid var(--gilt-line)' }}>
                              <Package className="w-2.5 h-2.5" />
                              {doc.meta.zip_child_count ?? "?"} docs inside
                            </span>
                          )}
                          {doc.meta?.from_zip && !doc.meta?.zip_exploded && (
                            <span className="text-[10px] flex items-center gap-1 font-mono rounded px-1.5 py-0.5"
                                  style={{ color: 'var(--t-artifact)', border: '1px solid var(--t-artifact)' }}>
                              <FolderOpen className="w-2.5 h-2.5" />
                              {doc.meta.zip_folder ? `${doc.meta.zip_folder}/` : "archive"}
                            </span>
                          )}
                        </div>

                        {/* Search snippet */}
                        {search && doc.snippet && (
                          <p className="mt-2 text-[11px] font-mono text-muted-foreground/70 line-clamp-2 leading-relaxed">
                            {String(doc.snippet).replace(/\[\[/g, "").replace(/\]\]/g, "")}
                          </p>
                        )}

                        {/* Error message */}
                        {hasError && doc.error_message && (
                          <p className="mt-2 text-xs font-mono rounded px-2 py-1 break-all"
                             style={{ color: 'var(--rust)', background: 'var(--rust-soft)', border: '1px solid var(--rust)' }}>
                            {doc.error_message}
                          </p>
                        )}

                        {/* Extraction warnings */}
                        {hasError && doc.warnings && doc.warnings.length > 0 && (
                          <div className="mt-2 space-y-1">
                            {doc.warnings.map((w: any) => (
                              <div
                                key={w.id}
                                className="flex items-start gap-1.5 text-xs font-mono text-red-700 bg-red-50/70 border border-red-100 rounded px-2 py-1"
                              >
                                <AlertCircle className="w-3 h-3 mt-0.5 shrink-0 text-red-400" />
                                <span className="break-all">
                                  <span className="font-semibold uppercase text-[10px] text-red-500 mr-1">
                                    {w.kind}
                                  </span>
                                  {w.detail}
                                </span>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                    </div>

                    {/* Right: date + actions */}
                    <div className="flex sm:flex-col items-center sm:items-end gap-3 sm:gap-2 shrink-0">
                      <div className="text-xs font-mono text-muted-foreground">
                        {doc.created_at ? format(new Date(doc.created_at), "MMM d, yyyy") : ""}
                      </div>
                      <div className="text-[10px] font-mono opacity-40" title={doc.sha256}>
                        {doc.sha256?.slice(0, 8)}
                      </div>

                      {/* Action buttons */}
                      <div className="flex items-center gap-1 mt-1">
                        {hasError && (
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-7 w-7 text-amber-600 hover:text-amber-700 hover:bg-amber-50" onClick={(e) => handleReprocess(doc.id, e)} disabled={isReprocessing}>
                                <RefreshCw className={`w-3.5 h-3.5 ${isReprocessing ? "animate-spin" : ""}`} />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Retry extraction</TooltipContent>
                          </Tooltip>
                        )}
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity"
                              onClick={(e) => { e.stopPropagation(); window.open(`${BASE}/library/${doc.id}/download`, "_blank"); }}>
                              <Download className="w-3.5 h-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Download original file</TooltipContent>
                        </Tooltip>
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity" onClick={(e) => handleDelete(doc.id, e)}>
                              <Trash2 className="w-3.5 h-3.5" />
                            </Button>
                          </TooltipTrigger>
                          <TooltipContent>Delete</TooltipContent>
                        </Tooltip>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              );
            })
          ) : (
            <div className="vellum-card text-center py-16 px-8" style={{ border: '1px dashed var(--gilt-line)' }}>
              <LibraryIcon className="w-10 h-10 mx-auto mb-4" style={{ color: 'var(--ink-faint)', opacity: 0.5 }} />
              <div className="gilt-rule w-16 mx-auto mb-3" />
              <h3 className="text-lg font-serif font-medium text-balance">No documents found</h3>
              <p className="mt-1 text-[13px] text-balance" style={{ color: 'var(--ink-soft)' }}>
                {search
                  ? "No full-text matches for your query."
                  : "Import a PDF, DOCX, CSV, or text file to start building your library."}
              </p>
            </div>
          )}
        </div>
      </div>
    </TooltipProvider>
  );
}
