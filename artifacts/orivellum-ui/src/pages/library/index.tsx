import { useRef, useState } from "react";
import { useLocation } from "wouter";
import {
  useListLibrary,
  useSearchLibrary,
  useImportDocument,
  useDeleteDocument,
  useListWorks,
  getListLibraryQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
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
  CheckCircle2, Clock, FileQuestion, X,
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
  const resp = await fetch(`${BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

// ── Import dialog ─────────────────────────────────────────────────────────────

interface ImportDialogProps {
  onSuccess: () => void;
}

function ImportDialog({ onSuccess }: ImportDialogProps) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [workId, setWorkId] = useState("");
  const [dragging, setDragging] = useState(false);
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
    const bytes = await file.arrayBuffer();
    const b64 = btoa(String.fromCharCode(...new Uint8Array(bytes)));
    importDoc.mutate(
      { data: { filename: file.name, content_b64: b64, work_id: workId || undefined } },
      {
        onSuccess: (res) => {
          onSuccess();
          setOpen(false);
          setFile(null);
          setWorkId("");
          if ((res as any).duplicate) {
            toast.info(`${file.name} already exists — opening existing document`);
            const existingId = (res as any).document?.id;
            if (existingId) navigateTo(`/library/${existingId}`);
          } else {
            toast.success(`${file.name} imported — extraction running`);
          }
        },
        onError: () => toast.error("Import failed"),
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
            accept=".pdf,.docx,.doc,.xlsx,.xls,.csv,.pptx,.ppt,.txt,.md,.png,.jpg,.jpeg,.py,.js,.ts"
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
                PDF, DOCX, XLSX, CSV, TXT, MD, images, code
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

        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>Cancel</Button>
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
  const [showFilters, setShowFilters] = useState(false);
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

  const docs = rawDocs.filter((d) => {
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
    return matchesReadiness && matchesKind && matchesWork;
  });

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: getListLibraryQueryKey({}) });

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
              {isLoading ? "Loading…" : `${docs.length} document${docs.length !== 1 ? "s" : ""}${search || readinessFilter !== "all" || kindFilter !== "all" || workFilter !== "all" ? " matching filters" : ""}`}
            </p>
          </div>
          <ImportDialog onSuccess={invalidate} />
        </div>

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
            </div>
          )}
        </div>

        {/* Document list */}
        <div className="grid gap-3">
          {isLoading ? (
            [1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-20 w-full" />)
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
