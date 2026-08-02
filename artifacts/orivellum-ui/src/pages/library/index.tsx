import { useRef, useState, useEffect } from "react";
import { useLocation, useSearch } from "wouter";
import { apiFetch } from "@/lib/auth";
import {
  useListLibrary,
  useSearchLibrary,
  useImportDocument,
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
  FolderOpen, Sparkles, GitMerge, Star, GitBranch,
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

const LIFECYCLE_CFG: Record<string, { label: string; className: string; icon?: React.ElementType }> = {
  canonical:  { label: "canonical",  className: "bg-amber-50 border border-amber-300 text-amber-800", icon: Star },
  superseded: { label: "superseded", className: "bg-muted/50 border border-border text-muted-foreground line-through" },
  reference:  { label: "reference",  className: "bg-blue-50 border border-blue-200 text-blue-700" },
};

function LifecycleBadge({ lifecycle }: { lifecycle?: string }) {
  if (!lifecycle || lifecycle === "draft") return null;
  const cfg = LIFECYCLE_CFG[lifecycle];
  if (!cfg) return null;
  const Icon = cfg.icon;
  return (
    <span className={`text-[10px] font-mono flex items-center gap-0.5 rounded px-1.5 py-0.5 ${cfg.className}`}>
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

function DuplicatesBanner() {
  const [collapsed, setCollapsed] = useState(false);
  const queryClient = useQueryClient();
  const { data, refetch } = useQuery<{ pairs: DupePair[]; count: number }>({
    queryKey: ["library", "duplicates"],
    queryFn: () => apiFetch(`${BASE}/library/duplicates`).then((r) => r.json()),
    staleTime: 60_000,
    refetchInterval: 300_000,
  });

  const count = data?.count ?? 0;
  if (count === 0) return null;

  const handleResolved = () => {
    refetch();
    queryClient.invalidateQueries({ queryKey: ["library", "duplicates"] });
  };

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

const READINESS = {
  ready:    { label: "READY",      icon: CheckCircle2, cls: "text-emerald-600 border-emerald-200 bg-emerald-50" },
  imported: { label: "PROCESSING", icon: Clock,        cls: "text-amber-600 border-amber-200 bg-amber-50" },
  no_text:  { label: "NO TEXT",    icon: FileQuestion, cls: "text-orange-600 border-orange-200 bg-orange-50" },
  error:    { label: "ERROR",      icon: AlertCircle,  cls: "text-red-600 border-red-200 bg-red-50" },
} as const;

type Readiness = keyof typeof READINESS;

function ReadinessBadge({ readiness }: { readiness: string }) {
  const cfg = READINESS[readiness as Readiness] ?? READINESS.imported;
  const Icon = cfg.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-medium border ${cfg.cls}`}>
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

function ImportDialog({ onSuccess, defaultOpen = false }: ImportDialogProps) {
  const [open, setOpen] = useState(defaultOpen);
  const [file, setFile] = useState<File | null>(null);
  const [workId, setWorkId] = useState("");
  const [dragging, setDragging] = useState(false);
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const importDoc = useImportDocument();
  const [, navigateTo] = useLocation();
  const { data: worksResp } = useListWorks();

  const handleFile = (f: File) => setFile(f);

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragging(false);
    const f = e.dataTransfer.files[0];
    if (f) handleFile(f);
  };

  const handleImport = async () => {
    if (!file) return;
    setUploadPct(0);
    const bytes = await file.arrayBuffer();
    // Spread-into-String.fromCharCode crashes for large files (stack overflow).
    // Process in 8 KB chunks instead and report progress.
    const u8 = new Uint8Array(bytes);
    const chunkSize = 8192;
    let binary = "";
    const total = u8.length;
    for (let i = 0; i < total; i += chunkSize) {
      binary += String.fromCharCode(...u8.subarray(i, i + chunkSize));
      // Yield to the browser every 512 chunks to keep UI responsive
      if ((i / chunkSize) % 512 === 0 && i > 0) {
        setUploadPct(Math.round((i / total) * 90));
        await new Promise((r) => setTimeout(r, 0));
      }
    }
    setUploadPct(92);
    const b64 = btoa(binary);
    setUploadPct(95);
    importDoc.mutate(
      { data: { filename: file.name, content_b64: b64, work_id: workId || undefined } },
      {
        onSuccess: (res) => {
          setUploadPct(100);
          onSuccess();
          setOpen(false);
          setFile(null);
          setWorkId("");
          setUploadPct(null);
          if ((res as any).duplicate) {
            toast.info(`${file.name} already exists — opening existing document`);
            const existingId = (res as any).document?.id;
            if (existingId) navigateTo(`/library/${existingId}`);
          } else {
            toast.success(`${file.name} imported — extraction running`);
          }
        },
        onError: () => { setUploadPct(null); toast.error("Import failed"); },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="gap-2">
          <Upload className="w-4 h-4" />
          Import Document
        </Button>
      </DialogTrigger>

      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">Import Document</DialogTitle>
        </DialogHeader>

        {/* Drop zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
          onDragLeave={() => setDragging(false)}
          onDrop={handleDrop}
          onClick={() => inputRef.current?.click()}
          className={`mt-2 border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors ${
            dragging
              ? "border-primary bg-primary/5"
              : file
              ? "border-emerald-400 bg-emerald-50/30"
              : "border-border hover:border-primary/50 hover:bg-muted/30"
          }`}
        >
          <input
            ref={inputRef}
            type="file"
            className="hidden"
            accept=".pdf,application/pdf,.docx,.doc,application/vnd.openxmlformats-officedocument.wordprocessingml.document,.xlsx,.xls,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,application/vnd.ms-excel,.csv,text/csv,.pptx,.ppt,application/vnd.openxmlformats-officedocument.presentationml.presentation,.txt,text/plain,.md,text/markdown,.png,.jpg,.jpeg,.webp,.gif,image/*,.py,.js,.ts,.jsx,.tsx,.java,.cpp,.c,.cs,.go,.rs,.rb,.html,.htm,text/html,.json,application/json,.zip,application/zip,.rtf,.epub,.xml"
            onChange={(e) => { const f = e.target.files?.[0]; if (f) handleFile(f); }}
          />
          {file ? (
            <div className="flex items-center justify-center gap-3">
              <FileText className="w-6 h-6 text-emerald-600" />
              <div className="text-left">
                <p className="font-medium text-sm">{file.name}</p>
                <p className="text-xs text-muted-foreground font-mono">
                  {(file.size / 1024).toFixed(1)} KB
                </p>
              </div>
              <button
                onClick={(e) => { e.stopPropagation(); setFile(null); }}
                className="ml-auto text-muted-foreground hover:text-destructive"
              >
                <X className="w-4 h-4" />
              </button>
            </div>
          ) : (
            <>
              <Upload className="w-8 h-8 text-muted-foreground mx-auto mb-2" />
              <p className="text-sm font-medium">Drop a file or click to browse</p>
              <p className="text-xs text-muted-foreground mt-1">
                PDF, DOCX, XLSX, CSV, PPTX, TXT, MD, HTML, JSON, ZIP, images, code, and more
              </p>
            </>
          )}
        </div>

        <div className="space-y-1">
          <label className="text-xs font-mono uppercase text-muted-foreground">
            Link to Work (optional)
          </label>
          <Select value={workId || "__none__"} onValueChange={(v) => setWorkId(v === "__none__" ? "" : v)}>
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

        {/* Upload progress bar — shown during base64 conversion + upload */}
        {uploadPct !== null && (
          <div className="space-y-1.5">
            <div className="flex items-center justify-between text-xs font-mono text-muted-foreground">
              <span>{uploadPct < 95 ? "Preparing file…" : uploadPct < 100 ? "Uploading…" : "Done"}</span>
              <span>{uploadPct}%</span>
            </div>
            <div className="h-1.5 w-full rounded-full bg-secondary overflow-hidden">
              <div
                className="h-full bg-primary transition-all duration-200 rounded-full"
                style={{ width: `${uploadPct}%` }}
              />
            </div>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)} disabled={importDoc.isPending}>Cancel</Button>
          <Button
            onClick={handleImport}
            disabled={!file || importDoc.isPending}
          >
            {importDoc.isPending ? "Importing…" : "Import"}
          </Button>
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
  const { data: searchResp, isLoading: loadingSearch } = useSearchLibrary(
    { q: search },
    { query: { enabled: !!search, queryKey: ["librarySearch", search] } }
  );
  const deleteDoc = useDeleteDocument();
  const [readinessFilter, setReadinessFilter] = useState<"all" | "ready" | "processing" | "error">("all");
  const [kindFilter, setKindFilter] = useState<string>("all");
  const [workFilter, setWorkFilter] = useState<string>("all");
  const [lifecycleFilter, setLifecycleFilter] = useState<string>("all");
  const [showFilters, setShowFilters] = useState(false);
  const [sortBy, setSortBy] = useState<"newest" | "oldest" | "a-z" | "z-a">("newest");
  const [groupByWork, setGroupByWork] = useState(false);
  const [explodingZips, setExplodingZips] = useState(false);
  const [organizingDocs, setOrganizingDocs] = useState(false);
  const { data: worksResp } = useListWorks();
  const workTitles: Record<string, string> = {};
  for (const w of worksResp?.works ?? []) {
    if (w.id) workTitles[w.id] = w.title ?? w.id.slice(0, 8);
  }
  // Works that actually have at least one document in the list
  const worksWithDocs = Array.from(
    new Set((listResp?.documents ?? []).map((d: any) => d.work_id).filter(Boolean))
  ) as string[];

  const isLoading = search ? loadingSearch : loadingList;
  const rawDocs: any[] = search
    ? (searchResp?.results ?? [])
    : (listResp?.documents ?? []);

  // Derive available kinds from the list for dynamic filter chips
  const availableKinds = Array.from(new Set(rawDocs.map((d) => d.kind ?? "file").filter(Boolean))).sort();

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
      return matchesReadiness && matchesKind && matchesWork && matchesLifecycle;
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
        <div className="flex items-center justify-between border-b border-border/50 pb-4">
          <div>
            <h1 className="text-3xl font-serif font-semibold tracking-tight">Library</h1>
            <p className="text-muted-foreground mt-1 font-serif">
              {isLoading ? "Loading…" : `${docs.length} document${docs.length !== 1 ? "s" : ""}${search || readinessFilter !== "all" || kindFilter !== "all" || workFilter !== "all" || lifecycleFilter !== "all" ? " matching filters" : ""}`}
            </p>
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {zipCount > 0 && (
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5 text-xs border-amber-300 text-amber-700 hover:bg-amber-50"
                onClick={handleExplodeZips}
                disabled={explodingZips}
                title={`Extract ${zipCount} ZIP archive${zipCount !== 1 ? "s" : ""} into individual documents`}
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
              title="Auto-group unassigned documents into Works by topic"
            >
              <Sparkles className={`w-3.5 h-3.5 ${organizingDocs ? "animate-spin" : ""}`} />
              {organizingDocs ? "Organising…" : "Smart Sort"}
            </Button>
            <Button
              variant={groupByWork ? "secondary" : "outline"}
              size="sm"
              className="gap-1.5 text-xs"
              onClick={() => setGroupByWork((v) => !v)}
              title="Group documents by Work/topic"
            >
              <Layers className="w-3.5 h-3.5" />
              By Topic
            </Button>
            <ImportDialog onSuccess={invalidate} defaultOpen={openImport} />
          </div>
        </div>

        {/* Near-duplicates banner */}
        <DuplicatesBanner />

        {/* Search */}
        <div className="space-y-3">
          <div className="flex items-center gap-4">
            <div className="relative flex-1 max-w-md">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
              <Input
                placeholder="Search all documents (full-text)…"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                className="pl-9 bg-background/50"
              />
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
                  {(["all", "canonical", "draft", "reference", "superseded"] as const).map((lc) => (
                    <button
                      key={lc}
                      onClick={() => setLifecycleFilter(lc)}
                      className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                        lifecycleFilter === lc
                          ? "bg-background shadow-sm text-foreground"
                          : "text-muted-foreground hover:text-foreground"
                      }`}
                    >
                      {lc === "all" ? "All" : lc}
                    </button>
                  ))}
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
            // ── Grouped by Work/topic ────────────────────────────────────────
            (() => {
              const withWork = new Map<string, any[]>();
              const unassigned: any[] = [];
              for (const doc of docs) {
                if (doc.work_id) {
                  const arr = withWork.get(doc.work_id) ?? [];
                  arr.push(doc);
                  withWork.set(doc.work_id, arr);
                } else {
                  unassigned.push(doc);
                }
              }
              const groups: Array<{ title: string; color: string; docs: any[] }> = [];
              for (const [wid, wdocs] of withWork) {
                groups.push({ title: workTitles[wid] ?? wid.slice(0, 8), color: "text-violet-600", docs: wdocs });
              }
              if (unassigned.length > 0) {
                groups.push({ title: "Unassigned", color: "text-muted-foreground", docs: unassigned });
              }
              if (groups.length === 0) return (
                <div className="text-center py-20 bg-muted/10 border border-dashed rounded-lg">
                  <LibraryIcon className="w-10 h-10 text-muted-foreground mx-auto mb-4 opacity-50" />
                  <h3 className="text-lg font-serif font-medium">No documents found</h3>
                </div>
              );
              return groups.map((group) => (
                <div key={group.title} className="space-y-2">
                  <div className={`flex items-center gap-2 pt-2 pb-1 border-b border-border/40`}>
                    <FolderOpen className={`w-4 h-4 ${group.color}`} />
                    <span className={`text-sm font-semibold font-serif ${group.color}`}>{group.title}</span>
                    <span className="text-xs font-mono text-muted-foreground">
                      {group.docs.length} doc{group.docs.length !== 1 ? "s" : ""}
                    </span>
                  </div>
                  {group.docs.map((doc: any) => {
                    const readiness: string = doc.readiness ?? "imported";
                    const hasError = readiness === "error" || readiness === "no_text";
                    const isReprocessing = reprocessingIds.has(doc.id);
                    return (
                      <Card
                        key={doc.id}
                        onClick={() => navigate(`/library/${doc.id}`)}
                        className={`transition-colors group cursor-pointer ${hasError ? "border-red-200/60" : "hover-elevate"}`}
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
                            </div>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            {hasError && (
                              <Button variant="ghost" size="icon" className="h-7 w-7 text-amber-600 hover:text-amber-700 hover:bg-amber-50" onClick={(e) => handleReprocess(doc.id, e)} disabled={isReprocessing}>
                                <RefreshCw className={`w-3.5 h-3.5 ${isReprocessing ? "animate-spin" : ""}`} />
                              </Button>
                            )}
                            <Button variant="ghost" size="icon" className="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 opacity-40 group-hover:opacity-100 transition-opacity" onClick={(e) => handleDelete(doc.id, e)}>
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
                  onClick={() => navigate(`/library/${doc.id}`)}
                  className={`transition-colors group cursor-pointer ${hasError ? "border-red-200/60" : "hover-elevate"}`}
                >
                  <CardContent className="p-4 sm:p-5 flex flex-col sm:flex-row sm:items-start justify-between gap-4">
                    {/* Left: icon + meta */}
                    <div className="flex items-start gap-4 min-w-0">
                      <div className={`w-9 h-9 rounded flex items-center justify-center shrink-0 border ${
                        hasError ? "bg-red-50 border-red-200" : "bg-muted/50 border-border/50"
                      }`}>
                        {hasError
                          ? <AlertCircle className="w-4 h-4 text-red-500" />
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
                            <span className="text-[10px] text-amber-600 flex items-center gap-1 font-mono bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                              <Package className="w-2.5 h-2.5" />
                              {doc.meta.zip_child_count ?? "?"} docs inside
                            </span>
                          )}
                          {doc.meta?.from_zip && !doc.meta?.zip_exploded && (
                            <span className="text-[10px] text-violet-600 flex items-center gap-1 font-mono bg-violet-50 border border-violet-200 rounded px-1.5 py-0.5">
                              <FolderOpen className="w-2.5 h-2.5" />
                              {doc.meta.zip_folder ? `${doc.meta.zip_folder}/` : "archive"}
                            </span>
                          )}
                        </div>

                        {/* Error message */}
                        {hasError && doc.error_message && (
                          <p className="mt-2 text-xs text-red-600 font-mono bg-red-50 border border-red-100 rounded px-2 py-1 break-all">
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
                              <Button
                                variant="ghost"
                                size="icon"
                                className="h-7 w-7 text-amber-600 hover:text-amber-700 hover:bg-amber-50"
                                onClick={(e) => handleReprocess(doc.id, e)}
                                disabled={isReprocessing}
                              >
                                <RefreshCw className={`w-3.5 h-3.5 ${isReprocessing ? "animate-spin" : ""}`} />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>Retry extraction</TooltipContent>
                          </Tooltip>
                        )}
                        <Tooltip>
                          <TooltipTrigger asChild>
                            <Button
                              variant="ghost"
                              size="icon"
                              className="h-7 w-7 text-muted-foreground hover:text-destructive hover:bg-destructive/10 opacity-0 group-hover:opacity-100 transition-opacity"
                              onClick={(e) => handleDelete(doc.id, e)}
                            >
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
            <div className="text-center py-20 bg-muted/10 border border-dashed rounded-lg">
              <LibraryIcon className="w-10 h-10 text-muted-foreground mx-auto mb-4 opacity-50" />
              <h3 className="text-lg font-serif font-medium">No documents found</h3>
              <p className="text-muted-foreground mt-1 text-sm">
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
