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
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import {
  Search, Upload, FileText, Database, Filter,
  Library as LibraryIcon, AlertCircle, RefreshCw, Trash2,
  FileQuestion, X, Package, Layers,
  FolderOpen, Sparkles, GitMerge, Star, Download, Network, StopCircle,
  BookHeadphones, Plus,
} from "lucide-react";
import {
  useListeningProgressBadges,
  type ListeningProgress,
} from "@/lib/read-aloud";
import {
  Sheet, SheetContent, SheetHeader, SheetTitle, SheetTrigger, SheetFooter,
} from "@/components/ui/sheet";
import {
  Page, Panel, Status, EmptyState, ErrorState, LoadingState, FilterSheet, ConfirmAction,
  type StatusKind,
} from "@/components/primitives";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

/**
 * Download the original file through apiFetch (blob) rather than window.open,
 * so the Bearer-token fallback works in the PWA (window.open only carries the
 * session cookie, which the installed app can lose).
 */
async function downloadOriginal(docId: string, fallbackName = "download") {
  try {
    const r = await apiFetch(`${BASE}/library/${docId}/download`);
    if (!r.ok) throw new Error(`Download failed (${r.status})`);
    const disposition = r.headers.get("content-disposition") ?? "";
    const name = /filename="([^"]+)"/.exec(disposition)?.[1] ?? fallbackName;
    const blobUrl = URL.createObjectURL(await r.blob());
    const a = document.createElement("a");
    a.href = blobUrl;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(blobUrl);
  } catch (err: any) {
    toast.error(err?.message ?? "Download failed");
  }
}

// ── Lifecycle badge ────────────────────────────────────────────────────────────

const LIFECYCLE_CFG: Record<string, {
  label: string;
  cls: string;
  style: React.CSSProperties;
  icon?: React.ElementType;
}> = {
  canonical:  {
    label: "canonical",
    cls: "",
    style: { color: "var(--gd-bronze)", borderColor: "var(--gd-bronze-soft)", background: "var(--gd-bronze-soft)" },
    icon: Star,
  },
  superseded: {
    label: "superseded",
    cls: "bg-muted/50 border-border text-muted-foreground line-through",
    style: {},
  },
  reference:  {
    label: "reference",
    cls: "text-muted-foreground border-border bg-transparent",
    style: {},
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

// ─── Doc-type badge — which ontology / pipeline applies ──────────────────────

const DOC_TYPE_CFG: Record<string, { label: string; cls: string }> = {
  manuscript: { label: "manuscript", cls: "border-primary/30 text-primary" },
  reference: { label: "reference", cls: "border-border text-muted-foreground" },
  doctrine: { label: "doctrine", cls: "border-border text-muted-foreground" },
  test_catalog: { label: "test catalog", cls: "border-border text-muted-foreground" },
  code: { label: "code", cls: "border-border text-muted-foreground" },
  workbook: { label: "workbook", cls: "border-border text-muted-foreground" },
  correspondence: { label: "mail", cls: "border-border text-muted-foreground opacity-70" },
  generated: { label: "generated", cls: "border-border text-muted-foreground opacity-70" },
  unknown: { label: "unclassified", cls: "border-dashed border-border text-muted-foreground opacity-70" },
};

export function DocTypeBadge({ docType, by }: { docType?: string | null; by?: string | null }) {
  if (!docType) return null;
  const cfg = DOC_TYPE_CFG[docType];
  if (!cfg) return null;
  const provenance = by?.startsWith("rule:")
    ? `Classified by rule (${by.slice(5)})`
    : by === "model"
    ? "Proposed by model"
    : by === "author"
    ? "Classified by you"
    : undefined;
  return (
    <span
      title={provenance}
      className={`text-[10px] font-mono flex items-center gap-0.5 rounded px-1.5 py-0.5 border ${cfg.cls}`}
    >
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
    <div className="flex flex-col gap-1.5 py-2 border-t first:border-t-0 first:pt-0" style={{ borderColor: "var(--gd-bronze-soft)" }}>
      <p className="text-[11px] font-mono flex items-center flex-wrap gap-x-1.5 gap-y-0.5" style={{ color: "var(--gd-bronze)" }}>
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
          className="min-h-11 text-[10px] font-mono px-2 rounded border disabled:opacity-40 transition-opacity hover:opacity-80"
          style={{ borderColor: "var(--gd-bronze-soft)", background: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}
        >
          {resolving === "mark_versions" ? "…" : "Link as versions"}
        </button>
        <button
          onClick={() => resolve("mark_superseded")}
          disabled={resolving !== null}
          className="min-h-11 text-[10px] font-mono px-2 rounded border bg-card disabled:opacity-40 transition-opacity hover:opacity-80"
          style={{ borderColor: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}
        >
          {resolving === "mark_superseded" ? "…" : "Mark older superseded"}
        </button>
        <button
          onClick={() => resolve("keep_both")}
          disabled={resolving !== null}
          className="min-h-11 text-[10px] font-mono px-2 rounded disabled:opacity-40 transition-opacity hover:opacity-80"
          style={{ color: "var(--gd-bronze)" }}
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

  if (count === 0) {
    if (readyDocCount === 0) return null;
    return (
      <Panel className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <GitMerge className="w-4 h-4 shrink-0" />
          <span>No duplicate pairs found yet.</span>
        </div>
        <Button
          size="sm"
          variant="outline"
          disabled={scanning}
          onClick={handleScan}
          className="gap-1.5 text-xs shrink-0 min-h-11"
        >
          {scanning
            ? <><RefreshCw className="w-3 h-3 animate-spin" /> Scanning…</>
            : <><Search className="w-3 h-3" /> Scan for duplicates</>}
        </Button>
      </Panel>
    );
  }

  return (
    <div className="rounded-lg border overflow-hidden" style={{ borderColor: "var(--gd-bronze-soft)", background: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}>
      <div className="flex items-center gap-2.5 px-4 py-2.5">
        <GitMerge className="w-4 h-4 shrink-0" style={{ color: "var(--gd-bronze)" }} />
        <p className="flex-1 text-sm font-medium">
          {count} near-duplicate pair{count !== 1 ? "s" : ""} detected
        </p>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="min-h-11 text-[10px] font-mono transition-opacity hover:opacity-80 px-2"
          style={{ color: "var(--gd-bronze)" }}
        >
          {collapsed ? "show" : "hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="px-4 pb-3 space-y-0">
          {(data?.pairs ?? []).slice(0, 5).map((p) => (
            <DuplicatePairRow key={p.id} pair={p} onResolved={handleResolved} />
          ))}
          {count > 5 && (
            <p className="text-[10px] font-mono pt-1.5 border-t" style={{ color: "var(--gd-bronze)", borderColor: "var(--gd-bronze-soft)" }}>
              {count - 5} more pair{count - 5 !== 1 ? "s" : ""} not shown
            </p>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Missing-source-files banner ──────────────────────────────────────────────

type MissingDoc = {
  id: string;
  title?: string;
  kind?: string;
  readiness?: string;
};

const MISSING_FILES_KEY = ["library", "missing-files"];

function MissingFileRow({ doc, onChanged }: { doc: MissingDoc; onChanged: () => void }) {
  const [busy, setBusy] = useState<"upload" | "delete" | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const handleReupload = async (file: File) => {
    setBusy("upload");
    try {
      const form = new FormData();
      form.append("file", file, file.name);
      const resp = await apiFetch(`${BASE}/library/${doc.id}/restore-file`, {
        method: "POST",
        body: form,
      });
      const body = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((body as any).detail ?? "Re-upload failed");
      toast.success(`${file.name} re-attached — extraction running`);
      onChanged();
    } catch (err: any) {
      toast.error(err.message ?? "Re-upload failed");
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const handleDelete = async () => {
    setBusy("delete");
    try {
      const resp = await apiFetch(`${BASE}/library/${doc.id}`, { method: "DELETE" });
      if (!resp.ok) throw new Error("Delete failed");
      toast.success("Document removed");
      onChanged();
    } catch (err: any) {
      toast.error(err.message ?? "Delete failed");
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-2 py-2 border-t first:border-t-0 first:pt-0 flex-wrap"
         style={{ borderColor: "var(--gd-danger-soft)" }}>
      <FileQuestion className="w-3.5 h-3.5 shrink-0" style={{ color: "var(--gd-danger)" }} />
      <span className="flex-1 min-w-0 text-[12px] font-mono truncate" style={{ color: "var(--gd-danger)" }}>
        {doc.title || doc.id.slice(0, 8)}
        {doc.kind ? <span className="opacity-60 ml-1.5">· {doc.kind}</span> : null}
      </span>
      <input
        ref={fileRef}
        type="file"
        className="hidden"
        onChange={(e) => { const f = e.target.files?.[0]; if (f) handleReupload(f); }}
      />
      <button
        onClick={() => fileRef.current?.click()}
        disabled={busy !== null}
        className="min-h-11 text-[10px] font-mono px-2 rounded border disabled:opacity-40 transition-opacity hover:opacity-80 flex items-center gap-1"
        style={{ borderColor: "var(--gd-danger-soft)", background: "var(--gd-danger-soft)", color: "var(--gd-danger)" }}
      >
        <Upload className="w-2.5 h-2.5" />
        {busy === "upload" ? "Uploading…" : "Re-upload file"}
      </button>
      <ConfirmAction
        destructive
        title="Remove dead record?"
        consequence={`"${doc.title || doc.id.slice(0, 8)}" has no source file on disk. This deletes the record permanently.`}
        confirmLabel="Remove record"
        onConfirm={handleDelete}
        trigger={
          <button
            disabled={busy !== null}
            className="min-h-11 text-[10px] font-mono px-2 rounded border bg-card disabled:opacity-40 transition-opacity hover:opacity-80 flex items-center gap-1"
            style={{ borderColor: "var(--gd-danger-soft)", color: "var(--gd-danger)" }}
          >
            <Trash2 className="w-2.5 h-2.5" />
            {busy === "delete" ? "Removing…" : "Remove record"}
          </button>
        }
      />
    </div>
  );
}

// ── Collections (import provenance) ───────────────────────────────────────────
//
// A collection records where a batch of documents came from (ZIP archive,
// watched folder, demoted migration batch). It is provenance only — never a
// subject: it can't seed a curriculum, enter a book pipeline, or scope a
// harvest. This panel is a read-only view of that provenance.

type CollectionRow = {
  id: string;
  label: string;
  source_kind: string;
  source_ref: string;
  domain?: string | null;
  imported_at: string;
  document_count: number;
  meta?: Record<string, unknown>;
};

const COLLECTIONS_KEY = ["library", "collections"];

function CollectionsPanel() {
  const [collapsed, setCollapsed] = useState(true);
  const { data } = useQuery<{ collections: CollectionRow[]; count: number }>({
    queryKey: COLLECTIONS_KEY,
    queryFn: () => apiFetch(`${BASE}/library/collections`).then((r) => r.json()),
    staleTime: 120_000,
  });

  const collections = data?.collections ?? [];
  if (collections.length === 0) return null;

  return (
    <div className="rounded-lg border border-card-border bg-card overflow-hidden" data-testid="collections-panel">
      <button
        onClick={() => setCollapsed((c) => !c)}
        className="w-full flex items-center gap-2.5 px-4 py-2.5 text-left min-h-11 hover:bg-accent transition-colors"
        aria-expanded={!collapsed}
        data-testid="collections-toggle"
      >
        <Package className="w-4 h-4 shrink-0 text-muted-foreground" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">
            {collections.length} import collection{collections.length !== 1 ? "s" : ""}
          </p>
          <p className="text-[11px] text-muted-foreground">
            Where batches of documents came from — provenance only, never a subject
          </p>
        </div>
        <span className="text-[10px] font-mono text-muted-foreground shrink-0">
          {collapsed ? "show" : "hide"}
        </span>
      </button>
      {!collapsed && (
        <div className="border-t border-border divide-y divide-border">
          {collections.map((c) => (
            <div key={c.id} className="flex items-center gap-3 px-4 py-2" data-testid={`collection-row-${c.id}`}>
              <FolderOpen className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
              <div className="flex-1 min-w-0">
                <p className="text-sm truncate" title={c.label}>{c.label}</p>
                <p className="text-[11px] text-muted-foreground truncate" title={c.source_ref}>
                  {c.source_ref || "—"}
                </p>
              </div>
              <Badge variant="outline" className="text-[10px] shrink-0">
                {c.source_kind}
              </Badge>
              {c.meta?.demoted_from_work === true && (
                <Badge variant="outline" className="text-[10px] shrink-0 text-muted-foreground">
                  demoted batch
                </Badge>
              )}
              <span className="text-xs text-muted-foreground shrink-0 tabular-nums">
                {c.document_count} doc{c.document_count !== 1 ? "s" : ""}
              </span>
              <span className="text-[11px] text-muted-foreground shrink-0 hidden sm:inline">
                {c.imported_at ? format(new Date(c.imported_at), "MMM d, yyyy") : ""}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function MissingFilesBanner() {
  const [collapsed, setCollapsed] = useState(false);
  const [showAll, setShowAll] = useState(false);
  const queryClient = useQueryClient();
  const { data } = useQuery<{ documents: MissingDoc[]; count: number }>({
    queryKey: MISSING_FILES_KEY,
    queryFn: () => apiFetch(`${BASE}/library/missing-files`).then((r) => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });

  const count = data?.count ?? 0;
  if (count === 0) return null;

  const onChanged = () => {
    queryClient.invalidateQueries({ queryKey: MISSING_FILES_KEY });
    queryClient.invalidateQueries({ queryKey: getListLibraryQueryKey({}) });
  };

  return (
    <div
      className="rounded-lg border overflow-hidden"
      style={{
        borderColor: "var(--gd-danger-soft)",
        background: "var(--gd-danger-soft)",
        color: "var(--gd-danger)",
      }}
      data-testid="missing-files-banner"
    >
      <div className="flex items-center gap-2.5 px-4 py-2.5">
        <AlertCircle className="w-4 h-4 shrink-0" style={{ color: "var(--gd-danger)" }} />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium">
            {count} document{count !== 1 ? "s" : ""} missing {count !== 1 ? "their" : "its"} source file
          </p>
          <p className="text-[11px] opacity-75">
            The stored file is gone from disk, so these can't be re-extracted. Re-upload the file or remove the record.
          </p>
        </div>
        <button
          onClick={() => setCollapsed((c) => !c)}
          className="min-h-11 text-[10px] font-mono transition-opacity hover:opacity-80 shrink-0 px-2"
          style={{ color: "var(--gd-danger)" }}
        >
          {collapsed ? "show" : "hide"}
        </button>
      </div>
      {!collapsed && (
        <div className="px-4 pb-3 space-y-0">
          {(showAll ? (data?.documents ?? []) : (data?.documents ?? []).slice(0, 8)).map((d) => (
            <MissingFileRow key={d.id} doc={d} onChanged={onChanged} />
          ))}
          {count > 8 && (
            <button
              onClick={() => setShowAll((v) => !v)}
              className="text-[10px] font-mono pt-1.5 border-t w-full text-left transition-opacity hover:opacity-80"
              style={{ color: "var(--gd-danger)", borderColor: "var(--gd-danger-soft)" }}
            >
              {showAll ? "show fewer" : `show all ${count}`}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

// ── Lifecycle status mapping ─────────────────────────────────────────────────
//
// Maps the EXISTING readiness field onto durable, dual-coded lifecycle labels.
// The pipeline only tracks a coarse readiness value, so we map honestly:
//   imported/transcribing → the processing pipeline (received → … → indexing)
//   ready                 → ready
//   no_text               → needs review (extracted nothing usable)
//   error                 → failed
// `stage` (from live SSE progress, detail page) refines the processing label
// when available; the list only has readiness, so it shows "Processing".

type LifecycleStage =
  | "received" | "extracting" | "classifying" | "indexing"
  | "ready" | "needs_review" | "failed";

const LIFECYCLE_STATUS: Record<LifecycleStage, { kind: StatusKind; label: string }> = {
  received:     { kind: "busy",   label: "Received" },
  extracting:   { kind: "busy",   label: "Extracting" },
  classifying:  { kind: "busy",   label: "Classifying" },
  indexing:     { kind: "busy",   label: "Indexing" },
  ready:        { kind: "ok",     label: "Ready" },
  needs_review: { kind: "warn",   label: "Needs review" },
  failed:       { kind: "danger", label: "Failed" },
};

/** Map an existing readiness value (+ optional live SSE stage) onto a durable
 *  lifecycle stage. Only readiness is tracked on the list; stage refines it. */
export function lifecycleStageFor(readiness: string, stage?: string | null): LifecycleStage {
  if (readiness === "ready") return "ready";
  if (readiness === "error") return "failed";
  if (readiness === "no_text") return "needs_review";
  // processing states (imported / transcribing) — refine by stage if known
  if (stage) {
    const s = stage.toLowerCase();
    if (s.includes("extract") || s.includes("transcrib") || s.includes("ocr")) return "extracting";
    if (s.includes("classif") || s.includes("type")) return "classifying";
    if (s.includes("index") || s.includes("embed") || s.includes("chunk")) return "indexing";
  }
  if (readiness === "transcribing") return "extracting";
  return "received";
}

function LifecycleStatus({ readiness, stage }: { readiness: string; stage?: string | null }) {
  const lc = lifecycleStageFor(readiness, stage);
  const cfg = LIFECYCLE_STATUS[lc];
  return <Status kind={cfg.kind} label={cfg.label} />;
}

// ── Reprocess helper ──────────────────────────────────────────────────────────

async function reprocessDoc(docId: string): Promise<void> {
  const resp = await apiFetch(`${BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

// ── Capture / Import sheet ────────────────────────────────────────────────────

interface CaptureSheetProps {
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

/**
 * CaptureSheet — the single Add/Import entry point for the Library.
 *
 * Capture modes:
 *   • Upload file(s)  — wired to the existing POST /library/upload XHR flow.
 *   • Paste text / URL — NOT rendered: no existing backend mutation supports
 *     them, so per the playbook these modes are omitted rather than faked.
 *   • Scan/photo      — omitted (no capture path exists today).
 *   • Watched folders — surfaced as a read-only pointer: the import
 *     Collections panel below the toolbar shows watched-folder provenance;
 *     there is no settings route to link to, so we just note it.
 */
function CaptureSheet({ onSuccess, defaultOpen = false }: CaptureSheetProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [queue, setQueue] = useState<FileStatus[]>([]);
  const [workId, setWorkId] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const cancelledRef = useRef(false);
  const xhrRef = useRef<XMLHttpRequest | null>(null);
  const [, navigateTo] = useLocation();
  const { data: worksResp } = useListWorks();

  const addFiles = (incoming: FileList | File[]) => {
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
    if (uploading) return;
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  };

  const updateStatus = (idx: number, patch: Partial<FileStatus>) =>
    setQueue((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)));

  const uploadOne = (status: FileStatus, idx: number, wId: string): Promise<void> =>
    new Promise((resolve) => {
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
        resolve();
      };

      xhr.onabort = () => {
        xhrRef.current = null;
        updateStatus(idx, { state: "cancelled", pct: 0 });
        resolve();
      };

      xhr.send(form);
    });

  const finishRun = (final: FileStatus[]) => {
    const done      = final.filter((s) => s.state === "done").length;
    const dupes     = final.filter((s) => s.state === "duplicate");
    const errors    = final.filter((s) => s.state === "error").length;
    const cancelled = final.filter((s) => s.state === "cancelled").length;
    const total     = final.length;

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
  };

  const handleImport = async () => {
    const pending = queue.filter((s) => s.state === "pending");
    if (!pending.length || uploading) return;
    cancelledRef.current = false;
    setUploading(true);

    for (let i = 0; i < queue.length; i++) {
      if (queue[i].state !== "pending") continue;
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

  const retryFile = async (status: FileStatus, idx: number) => {
    if (uploading) return;
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

  const stateStatus = (s: FileStatus) => {
    if (s.state === "done")      return <Status kind="ok" label="done" />;
    if (s.state === "duplicate") return <Status kind="warn" label="exists" />;
    if (s.state === "error")     return <Status kind="danger" label="failed" />;
    if (s.state === "uploading") return <Status kind="busy" label={`${s.pct}%`} />;
    if (s.state === "cancelled") return <Status kind="idle" label="cancelled" />;
    return <Status kind="idle" label="queued" />;
  };

  return (
    <Sheet open={open} onOpenChange={(v) => { if (!uploading) { setOpen(v); if (!v) { setQueue([]); setWorkId(""); } } }}>
      <SheetTrigger asChild>
        <Button className="gap-2 min-h-11" data-testid="library-add">
          <Plus className="w-4 h-4" />
          Add
        </Button>
      </SheetTrigger>

      <SheetContent side="bottom" className="max-h-[88dvh] overflow-y-auto pb-[calc(var(--sai-bottom,0px)+1rem)]">
        <SheetHeader>
          <SheetTitle>Add to Library</SheetTitle>
        </SheetHeader>

        <div className="space-y-4 py-2">
          <p className="text-xs text-muted-foreground">
            Upload files to capture them. Paste-text, URL, and photo capture are not
            available yet. Watched-folder imports appear as collections below the toolbar.
          </p>

          {/* Drop zone */}
          <div
            onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
            onDragLeave={() => setDragging(false)}
            onDrop={handleDrop}
            onClick={() => !uploading && inputRef.current?.click()}
            className={`border-2 border-dashed rounded-lg p-6 text-center transition-colors ${
              uploading ? "cursor-default opacity-60 border-border" :
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
                <div key={`${s.file.name}-${idx}`} className="flex items-center gap-2 rounded-md border border-border bg-muted/20 px-3 py-2">
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
                      <p className="text-[10px] font-mono mt-0.5" style={{ color: "var(--gd-bronze)" }}>already in library</p>
                    ) : s.state === "cancelled" ? (
                      <p className="text-[10px] text-muted-foreground font-mono mt-0.5">cancelled</p>
                    ) : (
                      <p className="text-[10px] text-muted-foreground font-mono">{fmt(s.file.size)}</p>
                    )}
                  </div>
                  <div className="shrink-0">{stateStatus(s)}</div>
                  {s.state === "pending" && !uploading && (
                    <button onClick={() => removeFile(idx)} className="min-h-11 px-1 text-muted-foreground hover:text-destructive shrink-0">
                      <X className="w-3.5 h-3.5" />
                    </button>
                  )}
                  {s.state === "duplicate" && s.docId && (
                    <button
                      onClick={() => navigateTo(`/library/${s.docId}`)}
                      className="min-h-11 px-1 text-[10px] font-mono hover:underline shrink-0"
                      style={{ color: "var(--gd-bronze)" }}
                    >
                      View
                    </button>
                  )}
                  {s.state === "error" && !uploading && (
                    <button
                      onClick={() => retryFile(s, idx)}
                      className="min-h-11 px-1 text-[10px] font-mono text-muted-foreground hover:text-foreground shrink-0"
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
              <SelectTrigger className="font-mono text-sm min-h-11">
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
        </div>

        <SheetFooter className="flex-row gap-2">
          <Button variant="outline" className="min-h-11" onClick={() => { setOpen(false); setQueue([]); setWorkId(""); }} disabled={uploading}>
            Cancel
          </Button>
          {uploading ? (
            <Button variant="destructive" onClick={handleStop} className="gap-1.5 min-h-11">
              <StopCircle className="w-4 h-4" />
              Stop
            </Button>
          ) : (
            <>
              {anyError && (
                <Button variant="outline" onClick={handleRetryFailed} className="gap-1.5 min-h-11">
                  <RefreshCw className="w-3.5 h-3.5" />
                  Retry {errorCount} failed
                </Button>
              )}
              <Button onClick={handleImport} disabled={!anyPending} className="min-h-11">
                {`Import ${queue.filter(s => s.state === "pending").length || ""} ${queue.filter(s => s.state === "pending").length === 1 ? "file" : "files"}`.trim()}
              </Button>
            </>
          )}
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}

// ── Resume-listening badge ────────────────────────────────────────────────────

/** "Resume listening — Part N of M" badge shown on cards for documents with a
 *  saved Read Aloud position. Clicking it opens the doc with ?listen=1. */
function ResumeListeningBadge({ prog, onClick }: { prog: ListeningProgress; onClick: (e: React.MouseEvent) => void }) {
  return (
    <button
      type="button"
      onClick={onClick}
      title="Resume listening"
      aria-label={`Resume listening — part ${prog.part + 1} of ${prog.partCount}`}
      className="text-[10px] flex items-center gap-1 font-mono border rounded px-1.5 py-0.5 hover:opacity-80 transition-opacity"
      style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", borderColor: "var(--gd-bronze-soft)" }}
    >
      <BookHeadphones className="w-2.5 h-2.5" />
      Part {prog.part + 1} of {prog.partCount}
    </button>
  );
}

// ── Document row ──────────────────────────────────────────────────────────────

function DocumentRow({
  doc,
  search,
  workTitles,
  listenProgress,
  isReprocessing,
  onOpen,
  onReprocess,
  onDownload,
  onDelete,
}: {
  doc: any;
  search: boolean;
  workTitles: Record<string, string>;
  listenProgress: Record<string, ListeningProgress>;
  isReprocessing: boolean;
  onOpen: () => void;
  onReprocess: (e: React.MouseEvent) => void;
  onDownload: (e: React.MouseEvent) => void;
  onDelete: () => void;
}) {
  const readiness: string = doc.readiness ?? "imported";
  const hasError = readiness === "error" || readiness === "no_text";
  const title = doc.title || doc.source || "Untitled Document";

  return (
    <div
      data-doc-id={doc.id}
      data-interactive
      className="group rounded-lg border bg-card text-card-foreground transition-colors hover:bg-accent"
      style={hasError ? { borderColor: "var(--gd-danger)", background: "var(--gd-danger-soft)" } : { borderColor: "var(--gd-line)" }}
    >
      <div className="p-4 flex flex-col sm:flex-row sm:items-start justify-between gap-4">
        {/* Left: icon + meta (clickable to open) */}
        <button
          type="button"
          onClick={onOpen}
          className="flex items-start gap-4 min-w-0 text-left flex-1 min-h-11 touch-manipulation"
        >
          <div className="w-9 h-9 rounded flex items-center justify-center shrink-0 border"
               style={hasError
                 ? { background: "var(--gd-danger-soft)", borderColor: "var(--gd-danger)" }
                 : { background: "var(--gd-recessed)", borderColor: "var(--gd-line)" }}>
            {hasError
              ? <AlertCircle className="w-4 h-4" style={{ color: "var(--gd-danger)" }} />
              : <FileText className="w-4 h-4 text-muted-foreground group-hover:text-primary transition-colors" />
            }
          </div>

          <div className="min-w-0 flex-1">
            <h3 className="font-medium truncate">{title}</h3>
            <div className="flex flex-wrap items-center gap-2 mt-1.5">
              <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                {doc.kind ?? "file"}
              </Badge>
              <LifecycleStatus readiness={readiness} />
              <LifecycleBadge lifecycle={doc.lifecycle} />
              <DocTypeBadge docType={doc.doc_type} by={doc.doc_type_by} />
              {listenProgress[doc.id] && (
                <ResumeListeningBadge
                  prog={listenProgress[doc.id]}
                  onClick={(e) => { e.stopPropagation(); onOpen(); }}
                />
              )}
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
                      style={{ color: "var(--gd-bronze)", background: "var(--gd-bronze-soft)", border: "1px solid var(--gd-bronze-soft)" }}>
                  <Package className="w-2.5 h-2.5" />
                  {doc.meta.zip_child_count ?? "?"} docs inside
                </span>
              )}
              {doc.meta?.from_zip && !doc.meta?.zip_exploded && (
                <span className="text-[10px] flex items-center gap-1 font-mono rounded px-1.5 py-0.5"
                      style={{ color: "var(--gd-slate)", border: "1px solid var(--gd-line)" }}>
                  <FolderOpen className="w-2.5 h-2.5" />
                  {doc.meta.zip_folder ? `${doc.meta.zip_folder}/` : "archive"}
                </span>
              )}
            </div>

            {/* Search snippet */}
            {search && doc.snippet && (
              <p className="mt-2 text-[11px] font-mono text-muted-foreground line-clamp-2 leading-relaxed">
                {String(doc.snippet).replace(/\[\[/g, "").replace(/\]\]/g, "")}
              </p>
            )}

            {/* Error message */}
            {hasError && doc.error_message && (
              <p className="mt-2 text-xs font-mono rounded px-2 py-1 break-all"
                 style={{ color: "var(--gd-danger)", background: "var(--gd-danger-soft)", border: "1px solid var(--gd-danger)" }}>
                {doc.error_message}
              </p>
            )}

            {/* Extraction warnings */}
            {hasError && doc.warnings && doc.warnings.length > 0 && (
              <div className="mt-2 space-y-1">
                {doc.warnings.map((w: any) => (
                  <div
                    key={w.id}
                    className="flex items-start gap-1.5 text-xs font-mono border rounded px-2 py-1"
                    style={{ color: "var(--gd-danger)", background: "var(--gd-danger-soft)", borderColor: "var(--gd-danger-soft)" }}
                  >
                    <AlertCircle className="w-3 h-3 mt-0.5 shrink-0" style={{ color: "var(--gd-danger)" }} />
                    <span className="break-all">
                      <span className="font-semibold uppercase text-[10px] mr-1" style={{ color: "var(--gd-danger)" }}>
                        {w.kind}
                      </span>
                      {w.detail}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </button>

        {/* Right: date + actions */}
        <div className="flex sm:flex-col items-center sm:items-end gap-3 sm:gap-2 shrink-0">
          <div className="text-xs font-mono text-muted-foreground">
            {doc.created_at ? format(new Date(doc.created_at), "MMM d, yyyy") : ""}
          </div>
          <div className="text-[10px] font-mono opacity-40" title={doc.sha256}>
            {doc.sha256?.slice(0, 8)}
          </div>

          <div className="flex items-center gap-1 mt-1">
            {hasError && (
              <Button variant="ghost" size="icon" aria-label="Retry extraction" className="h-9 w-9 min-h-11 hover:opacity-80" style={{ color: "var(--gd-bronze)" }} onClick={onReprocess} disabled={isReprocessing}>
                <RefreshCw className={`w-3.5 h-3.5 ${isReprocessing ? "animate-spin" : ""}`} />
              </Button>
            )}
            <Button variant="ghost" size="icon" aria-label="Download original file" className="h-9 w-9 min-h-11 text-muted-foreground"
              onClick={onDownload}>
              <Download className="w-3.5 h-3.5" />
            </Button>
            <ConfirmAction
              destructive
              title="Delete this document?"
              consequence="This removes the document and its extracted text, knowledge, and versions. This cannot be undone."
              confirmLabel="Delete"
              onConfirm={onDelete}
              trigger={
                <Button variant="ghost" size="icon" aria-label="Delete document" className="h-9 w-9 min-h-11 text-muted-foreground hover:text-destructive hover:bg-destructive/10">
                  <Trash2 className="w-3.5 h-3.5" />
                </Button>
              }
            />
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function Library() {
  const [search, setSearch] = useState("");
  const [reprocessingIds, setReprocessingIds] = useState<Set<string>>(new Set());
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();

  const listenProgress = useListeningProgressBadges();
  const searchStr = useSearch();
  const openImport = new URLSearchParams(searchStr).get("import") === "1";
  const urlTier = new URLSearchParams(searchStr).get("tier") ?? "all";

  const { data: listResp, isLoading: loadingList, error: listError, refetch: refetchList } = useListLibrary(
    {},
    {
      query: {
        enabled: !search,
        queryKey: getListLibraryQueryKey({}),
        // Poll every 4 s while any document is still processing so extraction
        // failures surface automatically without a manual refresh.
        refetchInterval: (query) => {
          const docs: any[] = query.state.data?.documents ?? [];
          return docs.some((d) => d.readiness === "imported") ? 4000 : false;
        },
      },
    }
  );
  const [searchMode, setSearchMode] = useState<"keyword" | "semantic" | "hybrid">("hybrid");
  const { data: searchResp, isLoading: loadingSearch, error: searchError, refetch: refetchSearch } = useSearchLibrary(
    { q: search, mode: searchMode },
    { query: { enabled: !!search, queryKey: ["librarySearch", search, searchMode] } }
  );
  const deleteDoc = useDeleteDocument();

  // Filter state — draft values live in the FilterSheet, applied on Apply.
  const [readinessFilter, setReadinessFilter] = useState<"all" | "ready" | "processing" | "error">("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [workFilter, setWorkFilter] = useState<string>("all");
  const [lifecycleFilter, setLifecycleFilter] = useState<string>("all");
  const [tierFilter, setTierFilter] = useState<string>(
    ["canon", "source", "artifact"].includes(urlTier) ? urlTier : "all"
  );
  const [filterSheetOpen, setFilterSheetOpen] = useState(false);
  // Draft filters mirror the applied ones while the sheet is open.
  const [draft, setDraft] = useState({
    readiness: "all" as "all" | "ready" | "processing" | "error",
    kind: "all",
    work: "all",
    lifecycle: "all",
  });
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
  const worksWithDocs = Array.from(
    new Set((listResp?.documents ?? []).map((d: any) => d.work_id).filter(Boolean))
  ) as string[];

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
  const loadError = search ? searchError : listError;
  const rawDocs: any[] = search
    ? (searchResp?.results ?? [])
    : (listResp?.documents ?? []);

  const availableKinds = Array.from(new Set(rawDocs.map((d) => d.kind ?? "file").filter(Boolean))).sort();

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

  const runReprocessAll = async (force = false) => {
    setReprocessingAll(true);
    try {
      const resp = await apiFetch(`${BASE}/library/reprocess-all${force ? "?force=true" : ""}`, { method: "POST" });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) throw new Error((data as any).detail ?? "Failed");
      const { queued, queued_zips, skipped, message } = data as any;
      if (queued === 0) {
        if ((skipped ?? 0) > 0) {
          toast.warning(
            `Nothing queued — ${skipped} document${skipped !== 1 ? "s" : ""} skipped because the source file is missing from disk.`,
            { description: "See the list at the top of the Library to re-upload files or remove dead records." }
          );
        } else {
          toast.success("All documents are already fully processed.");
        }
      } else {
        toast.success(message ?? `Queued ${queued} document(s) for re-extraction`);
        if (queued_zips > 0)
          toast.info(`${queued_zips} ZIP archive${queued_zips !== 1 ? "s" : ""} will be exploded into individual documents.`);
        if (skipped > 0)
          toast.warning(
            `${skipped} document${skipped !== 1 ? "s" : ""} skipped — source file missing from disk.`,
            { description: "See the list at the top of the Library to re-upload files or remove dead records." }
          );
      }
      if ((skipped ?? 0) > 0) {
        queryClient.invalidateQueries({ queryKey: MISSING_FILES_KEY });
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

  const handleDelete = (docId: string) => {
    deleteDoc.mutate({ docId }, {
      onSuccess: () => { invalidate(); toast.success("Document removed"); },
      onError: () => toast.error("Delete failed"),
    });
  };

  // Active-filter chips summarised in the toolbar
  const activeChips: { key: string; label: string; clear: () => void }[] = [];
  if (readinessFilter !== "all") activeChips.push({ key: "status", label: `Status: ${readinessFilter}`, clear: () => setReadinessFilter("all") });
  if (kindFilter !== "all") activeChips.push({ key: "kind", label: `Type: ${kindFilter}`, clear: () => setKindFilter("all") });
  if (workFilter !== "all") activeChips.push({ key: "work", label: `Work: ${workFilter === "__none__" ? "Unlinked" : (workTitles[workFilter] ?? workFilter.slice(0, 8))}`, clear: () => setWorkFilter("all") });
  if (lifecycleFilter !== "all") activeChips.push({ key: "lifecycle", label: `Lifecycle: ${lifecycleFilter}`, clear: () => setLifecycleFilter("all") });

  const openFilterSheet = () => {
    setDraft({ readiness: readinessFilter, kind: kindFilter, work: workFilter, lifecycle: lifecycleFilter });
    setFilterSheetOpen(true);
  };
  const applyFilters = () => {
    setReadinessFilter(draft.readiness);
    setKindFilter(draft.kind);
    setWorkFilter(draft.work);
    setLifecycleFilter(draft.lifecycle);
  };
  const clearFilters = () => {
    setDraft({ readiness: "all", kind: "all", work: "all", lifecycle: "all" });
  };

  const headerActions = (
    <div className="flex items-center gap-2 flex-wrap justify-end">
      <Button
        variant="outline"
        size="sm"
        className="gap-1.5 text-xs min-h-11"
        onClick={() => runReprocessAll(false)}
        disabled={reprocessingAll}
        title="Re-extract all stuck, errored, or ZIP documents"
      >
        <RefreshCw className={`w-3.5 h-3.5 ${reprocessingAll ? "animate-spin" : ""}`} />
        {reprocessingAll ? "Processing…" : "Reprocess"}
      </Button>
      <ConfirmAction
        title="Deep reprocess everything?"
        consequence="This re-extracts EVERY document, including ones already processed fine. Nothing is deleted, but a large library can take a long time to churn through."
        confirmLabel="Deep reprocess"
        onConfirm={() => runReprocessAll(true)}
        trigger={
          <Button variant="outline" size="sm" className="gap-1.5 text-xs min-h-11" disabled={reprocessingAll}>
            <RefreshCw className={`w-3.5 h-3.5 ${reprocessingAll ? "animate-spin" : ""}`} />
            Deep
          </Button>
        }
      />
      {zipCount > 0 && (
        <Button
          variant="outline"
          size="sm"
          className="gap-1.5 text-xs hover:opacity-80 min-h-11"
          style={{ borderColor: "var(--gd-bronze-soft)", color: "var(--gd-bronze)" }}
          onClick={handleExplodeZips}
          disabled={explodingZips}
        >
          <Package className={`w-3.5 h-3.5 ${explodingZips ? "animate-bounce" : ""}`} />
          {explodingZips ? "Extracting…" : `Extract ${zipCount} ZIP${zipCount !== 1 ? "s" : ""}`}
        </Button>
      )}
      <Button variant="outline" size="sm" className="gap-1.5 text-xs min-h-11" onClick={handleSmartOrganize} disabled={organizingDocs}>
        <Sparkles className={`w-3.5 h-3.5 ${organizingDocs ? "animate-spin" : ""}`} />
        {organizingDocs ? "Organising…" : "Smart Sort"}
      </Button>
      <Button
        variant={groupByWork ? "secondary" : "outline"}
        size="sm"
        className="gap-1.5 text-xs min-h-11"
        onClick={() => setGroupByWork((v) => !v)}
      >
        <Layers className="w-3.5 h-3.5" />
        By Topic
      </Button>
      <Button variant="outline" size="sm" className="gap-1.5 text-xs min-h-11" onClick={() => navigate("/graph")} title="View the entity knowledge graph across your library">
        <Network className="w-3.5 h-3.5" />
        Graph
      </Button>
      <CaptureSheet onSuccess={invalidate} defaultOpen={openImport} />
    </div>
  );

  const filtered = readinessFilter !== "all" || kindFilter !== "all" || workFilter !== "all" || lifecycleFilter !== "all";

  return (
    <Page
      wide
      eyebrow="The Collection"
      title="Library"
      actions={headerActions}
    >
      <p className="text-[13px] text-muted-foreground">
        {isLoading ? "Loading…" : `${docs.length} document${docs.length !== 1 ? "s" : ""}${search || filtered ? " matching filters" : ""}`}
      </p>

      {/* Banners */}
      <MissingFilesBanner />
      <DuplicatesBanner readyDocCount={(listResp?.documents ?? []).filter((d: any) => d.readiness === "ready").length} />
      <CollectionsPanel />

      {/* Search + toolbar */}
      <div className="space-y-3">
        <div className="flex items-center gap-3 flex-wrap">
          <div className="relative flex-1 min-w-[220px] max-w-md">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
            <Input
              placeholder="Search all documents…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-9 min-h-11"
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
                data-active={searchMode === value}
                className="gd-chip min-h-11 px-2.5 text-xs font-mono touch-manipulation"
              >
                {label}
              </button>
            ))}
          </div>
          <Select value={sortBy} onValueChange={(v) => setSortBy(v as typeof sortBy)}>
            <SelectTrigger className="w-auto min-h-11 text-xs font-mono text-muted-foreground shrink-0" aria-label="Sort order">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="newest">Newest</SelectItem>
              <SelectItem value="oldest">Oldest</SelectItem>
              <SelectItem value="a-z">A → Z</SelectItem>
              <SelectItem value="z-a">Z → A</SelectItem>
            </SelectContent>
          </Select>
          <Button
            variant={activeChips.length ? "secondary" : "outline"}
            size="sm"
            className="gap-1.5 shrink-0 min-h-11"
            onClick={openFilterSheet}
            data-testid="library-filters"
          >
            <Filter className="w-4 h-4" />
            Filters
            {activeChips.length > 0 && (
              <span className="ml-0.5 tabular-nums text-[10px] rounded-full bg-primary text-primary-foreground px-1.5">
                {activeChips.length}
              </span>
            )}
          </Button>
        </div>

        {/* Active-filter chips */}
        {activeChips.length > 0 && (
          <div className="flex flex-wrap items-center gap-1.5">
            {activeChips.map((chip) => (
              <button
                key={chip.key}
                onClick={chip.clear}
                className="gd-chip flex items-center gap-1 min-h-11 px-2.5 text-xs font-mono"
                data-active="true"
              >
                {chip.label}
                <X className="w-3 h-3" />
              </button>
            ))}
            <button
              onClick={() => { setReadinessFilter("all"); setKindFilter("all"); setWorkFilter("all"); setLifecycleFilter("all"); }}
              className="min-h-11 px-2 text-xs font-mono text-muted-foreground hover:text-foreground"
            >
              Clear all
            </button>
          </div>
        )}
      </div>

      {/* FilterSheet */}
      <FilterSheet
        open={filterSheetOpen}
        onOpenChange={setFilterSheetOpen}
        title="Filter documents"
        onApply={applyFilters}
        onClear={clearFilters}
      >
        <div className="space-y-2">
          <span className="section-label-mono !m-0">Status</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {(["all", "ready", "processing", "error"] as const).map((f) => (
              <button
                key={f}
                onClick={() => setDraft((d) => ({ ...d, readiness: f }))}
                data-active={draft.readiness === f}
                className="gd-chip min-h-11 px-3 text-xs font-mono"
              >
                {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
              </button>
            ))}
          </div>
        </div>

        {availableKinds.length > 1 && (
          <div className="space-y-2">
            <span className="section-label-mono !m-0">Type</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {["all", ...availableKinds].map((k) => (
                <button
                  key={k}
                  onClick={() => setDraft((d) => ({ ...d, kind: k }))}
                  data-active={draft.kind === k}
                  className="gd-chip min-h-11 px-3 text-xs font-mono uppercase"
                >
                  {k === "all" ? "All" : k}
                </button>
              ))}
            </div>
          </div>
        )}

        {worksWithDocs.length > 0 && (
          <div className="space-y-2">
            <span className="section-label-mono !m-0">Work</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {["all", "__none__", ...worksWithDocs].map((w) => (
                <button
                  key={w}
                  onClick={() => setDraft((d) => ({ ...d, work: w }))}
                  data-active={draft.work === w}
                  className="gd-chip min-h-11 px-3 text-xs font-mono"
                >
                  {w === "all" ? "All" : w === "__none__" ? "Unlinked" : (workTitles[w] ?? w.slice(0, 8))}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="space-y-2">
          <span className="section-label-mono !m-0">Lifecycle</span>
          <div className="flex flex-wrap items-center gap-1.5">
            {(["all", "canonical", "draft", "reference", "superseded"] as const).map((lc) => {
              const count = lifecycleCounts[lc] ?? 0;
              if (lc !== "all" && count === 0) return null;
              return (
                <button
                  key={lc}
                  onClick={() => setDraft((d) => ({ ...d, lifecycle: lc }))}
                  data-active={draft.lifecycle === lc}
                  className="gd-chip flex items-center gap-1 min-h-11 px-3 text-xs font-mono"
                >
                  {lc === "all" ? "All" : lc}
                  {count > 0 && <span className="text-[10px] tabular-nums opacity-70">{count}</span>}
                </button>
              );
            })}
          </div>
        </div>
      </FilterSheet>

      {/* Document list */}
      <div className="grid gap-3">
        {isLoading ? (
          <LoadingState rows={4} label="Loading documents" />
        ) : loadError ? (
          <ErrorState
            title="Couldn't load the library"
            detail="The document list failed to load. Check your connection and try again."
            onRetry={() => { search ? refetchSearch() : refetchList(); }}
          />
        ) : groupByWork && !search ? (
          // ── Grouped by semantic topic cluster ────────────────────────────
          (() => {
            const hasTopics = topicsResp && topicsResp.topics.length > 0;
            const grouped = new Map<string, any[]>();
            const unclassified: any[] = [];
            if (hasTopics) {
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
            const groups: Array<{ title: string; color: string; colorStyle?: React.CSSProperties; docs: any[] }> = [];
            for (const [label, gdocs] of grouped) {
              groups.push({ title: label, color: hasTopics ? "text-primary" : "", colorStyle: hasTopics ? undefined : { color: "var(--gd-bronze)" }, docs: gdocs });
            }
            if (unclassified.length > 0) {
              groups.push({ title: hasTopics ? "Unclassified" : "Unassigned", color: "text-muted-foreground", docs: unclassified });
            }
            if (groups.length === 0) return (
              <EmptyState
                icon={<LibraryIcon />}
                title="No documents found"
                description="Nothing matches the current filters."
              />
            );
            return groups.map((group) => (
              <div key={group.title} className="space-y-2">
                <div className="pt-2 pb-1 border-b border-border">
                  <div className="flex items-center gap-2">
                    <FolderOpen className={`w-4 h-4 ${group.color} shrink-0`} style={group.colorStyle} />
                    <span className={`text-sm font-semibold ${group.color}`} style={group.colorStyle}>{group.title}</span>
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
                {group.docs.map((doc: any) => (
                  <DocumentRow
                    key={doc.id}
                    doc={doc}
                    search={!!search}
                    workTitles={workTitles}
                    listenProgress={listenProgress}
                    isReprocessing={reprocessingIds.has(doc.id)}
                    onOpen={() => navigate(`/library/${doc.id}`)}
                    onReprocess={(e) => handleReprocess(doc.id, e)}
                    onDownload={(e) => { e.stopPropagation(); downloadOriginal(doc.id, doc.title || doc.id); }}
                    onDelete={() => handleDelete(doc.id)}
                  />
                ))}
              </div>
            ));
          })()
        ) : docs.length > 0 ? (
          docs.map((doc: any) => (
            <DocumentRow
              key={doc.id}
              doc={doc}
              search={!!search}
              workTitles={workTitles}
              listenProgress={listenProgress}
              isReprocessing={reprocessingIds.has(doc.id)}
              onOpen={() => navigate(`/library/${doc.id}`)}
              onReprocess={(e) => handleReprocess(doc.id, e)}
              onDownload={(e) => { e.stopPropagation(); downloadOriginal(doc.id, doc.title || doc.id); }}
              onDelete={() => handleDelete(doc.id)}
            />
          ))
        ) : (
          <EmptyState
            icon={<LibraryIcon />}
            title="No documents found"
            description={search
              ? "No full-text matches for your query."
              : "Import a PDF, DOCX, CSV, or text file to start building your library."}
            action={search ? undefined : (
              <Button variant="outline" className="gap-2 min-h-11" onClick={() => navigate("/library?import=1")}>
                <Plus className="w-4 h-4" /> Add a document
              </Button>
            )}
          />
        )}
      </div>
    </Page>
  );
}
