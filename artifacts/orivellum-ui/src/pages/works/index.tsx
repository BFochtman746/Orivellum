import { useState } from "react";
import { Link, useSearch } from "wouter";
import {
  useListWorks, useCreateWork, useGetWorkTypes, getListWorksQueryKey,
  useListLibrary, useUpdateDocument, getListLibraryQueryKey,
} from "@workspace/api-client-react";
import { toast } from "sonner";
import { useQueryClient } from "@tanstack/react-query";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { format } from "date-fns";
import {
  BookOpen, Plus, Search, Library, FileText, CheckCircle2, Circle,
  ArrowRight, Loader2,
} from "lucide-react";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogFooter,
} from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";

// ── Import-from-Library dialog ────────────────────────────────────────────────

function ImportFromLibraryDialog({
  open, onClose,
}: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);

  // Fetch all library docs (we'll create Works from the unlinked ones the user picks)
  const { data: libResp, isLoading } = useListLibrary(
    { readiness: "ready" } as any,
    { query: { enabled: open, staleTime: 10_000 } } as any,
  );
  const docs = (libResp as any)?.documents ?? [];
  // Show all docs; unlinked ones are highlighted differently
  const unlinked = docs.filter((d: any) => !d.work_id);
  const linked   = docs.filter((d: any) =>  d.work_id);

  const createWork    = useCreateWork();
  const updateDocument = useUpdateDocument();

  const toggle = (id: string) =>
    setSelected(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);

  async function handleImport() {
    if (!selected.length) return;
    setBusy(true);
    let ok = 0;
    for (const docId of selected) {
      const doc = docs.find((d: any) => d.id === docId);
      if (!doc) continue;
      try {
        const res = await new Promise<any>((resolve, reject) =>
          createWork.mutate(
            { data: { title: doc.title || doc.source?.split("/").pop() || "Untitled", work_type: "research" } },
            { onSuccess: resolve, onError: reject },
          )
        );
        const workId = res?.work?.id ?? res?.id;
        if (workId) {
          await new Promise<void>((resolve, reject) =>
            updateDocument.mutate(
              { docId, data: { work_id: workId } as any },
              { onSuccess: () => resolve(), onError: reject },
            )
          );
          ok++;
        }
      } catch {
        toast.error(`Failed to create Work for "${doc.title || docId}"`);
      }
    }
    setBusy(false);
    if (ok > 0) {
      toast.success(`Created ${ok} Work${ok > 1 ? "s" : ""} from your library`);
      queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
      queryClient.invalidateQueries({ queryKey: getListLibraryQueryKey() });
    }
    setSelected([]);
    onClose();
  }

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-serif text-xl flex items-center gap-2">
            <Library className="w-5 h-5 text-primary" />
            Import from Library
          </DialogTitle>
          <p className="text-sm text-muted-foreground">
            Select documents to turn into Works. Each selected document becomes its own Work,
            linked and ready to chat with.
          </p>
        </DialogHeader>

        <ScrollArea className="max-h-80 -mx-1 px-1">
          {isLoading ? (
            <div className="space-y-2 py-2">
              {[1,2,3].map(i => <Skeleton key={i} className="h-14 w-full rounded-lg" />)}
            </div>
          ) : docs.length === 0 ? (
            <div className="text-center py-10 text-muted-foreground text-sm">
              No processed documents in your library yet.
              Upload files in the Library tab first.
            </div>
          ) : (
            <div className="space-y-1 py-1">
              {unlinked.length > 0 && (
                <>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground px-2 pt-1 pb-0.5">
                    Not yet in a Work ({unlinked.length})
                  </p>
                  {unlinked.map((doc: any) => {
                    const sel = selected.includes(doc.id);
                    return (
                      <button
                        key={doc.id}
                        onClick={() => toggle(doc.id)}
                        className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors
                          ${sel ? "bg-primary/10 border border-primary/30" : "hover:bg-muted/50 border border-transparent"}`}
                      >
                        {sel
                          ? <CheckCircle2 className="w-4 h-4 text-primary shrink-0" />
                          : <Circle className="w-4 h-4 text-muted-foreground/40 shrink-0" />}
                        <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                        <div className="min-w-0 flex-1">
                          <div className="text-sm font-medium truncate">
                            {doc.title || doc.source?.split("/").pop() || "Untitled"}
                          </div>
                          <div className="text-[10px] text-muted-foreground font-mono">
                            {doc.kind} · {doc.readiness}
                          </div>
                        </div>
                      </button>
                    );
                  })}
                </>
              )}
              {linked.length > 0 && (
                <>
                  <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground px-2 pt-3 pb-0.5">
                    Already in a Work ({linked.length})
                  </p>
                  {linked.map((doc: any) => (
                    <div
                      key={doc.id}
                      className="flex items-center gap-3 px-3 py-2.5 rounded-lg opacity-40 cursor-not-allowed"
                    >
                      <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                      <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                      <div className="min-w-0 flex-1">
                        <div className="text-sm font-medium truncate">
                          {doc.title || doc.source?.split("/").pop() || "Untitled"}
                        </div>
                      </div>
                    </div>
                  ))}
                </>
              )}
            </div>
          )}
        </ScrollArea>

        <DialogFooter className="gap-2">
          <Button variant="outline" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button
            onClick={handleImport}
            disabled={!selected.length || busy}
            className="gap-2"
          >
            {busy
              ? <><Loader2 className="w-4 h-4 animate-spin" />Creating…</>
              : <><ArrowRight className="w-4 h-4" />Create {selected.length || ""} Work{selected.length !== 1 ? "s" : ""}</>}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── New Work dialog (manual) ──────────────────────────────────────────────────

function NewWorkDialog({
  open, onClose,
}: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient();
  const { data: typesResp } = useGetWorkTypes();
  const createWork = useCreateWork();
  const [newWork, setNewWork] = useState({ title: "", description: "", work_type: "research" });

  const handleCreate = () => {
    if (!newWork.title) return;
    createWork.mutate({ data: newWork }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
        onClose();
        setNewWork({ title: "", description: "", work_type: "research" });
        toast.success(`"${newWork.title}" created`);
      },
      onError: () => toast.error("Could not create work"),
    });
  };

  return (
    <Dialog open={open} onOpenChange={v => !v && onClose()}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle className="font-serif text-2xl">New Work</DialogTitle>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label className="font-mono text-xs uppercase text-muted-foreground">Title</Label>
            <Input
              value={newWork.title}
              onChange={e => setNewWork({ ...newWork, title: e.target.value })}
              onKeyDown={e => e.key === "Enter" && handleCreate()}
              placeholder="e.g., The Architecture of Memory"
              className="font-serif text-lg py-6"
              autoFocus
            />
          </div>
          <div className="space-y-2">
            <Label className="font-mono text-xs uppercase text-muted-foreground">Type</Label>
            <Select value={newWork.work_type} onValueChange={val => setNewWork({ ...newWork, work_type: val })}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {typesResp?.types?.map(t => (
                  <SelectItem key={t.id} value={t.id || ""}>{t.label}</SelectItem>
                )) || (
                  <>
                    <SelectItem value="research">Research</SelectItem>
                    <SelectItem value="essay">Essay</SelectItem>
                    <SelectItem value="project">Project</SelectItem>
                  </>
                )}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label className="font-mono text-xs uppercase text-muted-foreground">Description (Optional)</Label>
            <Textarea
              value={newWork.description}
              onChange={e => setNewWork({ ...newWork, description: e.target.value })}
              placeholder="Brief context or goals for this work…"
              className="resize-none"
              rows={3}
            />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={handleCreate} disabled={!newWork.title || createWork.isPending}>
            {createWork.isPending ? "Creating…" : "Create Work"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function WorksList() {
  const searchStr = useSearch();
  const [search, setSearch]             = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | "active" | "archived">("all");
  const [isCreateOpen, setIsCreateOpen] = useState(
    () => new URLSearchParams(searchStr).get("create") === "1"
  );
  const [isImportOpen, setIsImportOpen] = useState(false);

  const { data: worksResp, isLoading } = useListWorks(
    { query: { refetchInterval: 30_000, staleTime: 20_000 } } as any,
  );

  const filteredWorks = worksResp?.works?.filter(w => {
    const matchesSearch = !search
      || w.title?.toLowerCase().includes(search.toLowerCase())
      || w.description?.toLowerCase().includes(search.toLowerCase());
    const matchesStatus = statusFilter === "all" || (w as any).status === statusFilter;
    return matchesSearch && matchesStatus;
  });

  return (
    <div className="space-y-6 animate-in fade-in duration-500">
      {/* Dialogs */}
      <NewWorkDialog       open={isCreateOpen} onClose={() => setIsCreateOpen(false)} />
      <ImportFromLibraryDialog open={isImportOpen}  onClose={() => setIsImportOpen(false)} />

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-4 border-b pb-4" style={{ borderColor: 'var(--line)' }}>
        <div>
          <span className="eyebrow mb-1">In Progress</span>
          <h1 className="vellum-h1">Works &amp; Books</h1>
          <div className="gilt-rule w-36" />
          <p className="text-[13px] mt-1.5" style={{ color: 'var(--ink-soft)' }}>
            Manuscripts through the pipeline, B0 to B17.
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button variant="outline" className="gap-2" onClick={() => setIsImportOpen(true)}>
            <Library className="w-4 h-4" />
            Import from Library
          </Button>
          <Button className="gap-2" onClick={() => setIsCreateOpen(true)}>
            <Plus className="w-4 h-4" />
            New Work
          </Button>
        </div>
      </div>

      {/* Filters */}
      <div className="flex items-center gap-4">
        <div className="relative flex-1 max-w-md">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            placeholder="Search works…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            className="pl-9 bg-background/50"
          />
        </div>
        <div className="flex items-center gap-1 border border-border/50 rounded-lg p-0.5 bg-muted/20">
          {(["all", "active", "archived"] as const).map(s => (
            <button
              key={s}
              onClick={() => setStatusFilter(s)}
              className={`px-3 py-2 rounded-md text-xs font-mono uppercase tracking-wider transition-colors min-h-[36px] touch-manipulation
                ${statusFilter === s ? "bg-background shadow-sm text-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>

      {/* List */}
      {isLoading ? (
        <div className="grid gap-4">
          {[1, 2, 3].map(i => <Skeleton key={i} className="h-32 w-full rounded-xl" />)}
        </div>
      ) : filteredWorks && filteredWorks.length > 0 ? (
        <div className="grid gap-4">
          {filteredWorks.map(work => (
            <Link key={work.id} href={`/works/${work.id}`}>
              <Card className="vellum-card tap spring-scale cursor-pointer group" data-interactive>
                <CardContent className="p-6">
                  <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                    <div className="space-y-2 flex-1 min-w-0">
                      <div className="flex items-center gap-3 flex-wrap">
                        <h2 className="text-2xl font-serif font-medium group-hover:text-primary transition-colors truncate text-balance">
                          {work.title}
                        </h2>
                        <Badge variant="outline" className="font-mono text-[10px] uppercase tracking-wider bg-primary/5 text-primary border-primary/20 shrink-0">
                          {work.status}
                        </Badge>
                        <Badge variant="secondary" className="font-mono text-[10px] uppercase tracking-wider shrink-0">
                          {work.work_type}
                        </Badge>
                        <WorkReadinessBadge work={work as any} />
                      </div>
                      <p className="text-muted-foreground text-sm leading-relaxed">
                        {work.description || <span className="italic opacity-50">No description provided.</span>}
                      </p>
                    </div>
                    <div className="flex items-center gap-6 text-sm text-muted-foreground shrink-0 md:border-l md:border-border/50 md:pl-6">
                      {[
                        { label: "Docs",      value: work.doc_count       || 0 },
                        { label: "Knowledge", value: work.knowledge_count || 0 },
                        { label: "Tasks",     value: work.pending_tasks   || 0 },
                        { label: "Chats",     value: (work as any).conv_count || 0 },
                      ].map(({ label, value }) => (
                        <div key={label} className="space-y-1 text-center">
                          <div className="font-mono text-[10px] uppercase">{label}</div>
                          <div className="font-medium text-foreground">{value}</div>
                        </div>
                      ))}
                      {(work as any).obj_created && (
                        <div className="space-y-1 text-center">
                          <div className="font-mono text-[10px] uppercase">Created</div>
                          <div className="font-medium text-foreground text-xs">
                            {format(new Date((work as any).obj_created), "MMM d")}
                          </div>
                        </div>
                      )}
                    </div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        /* Empty state */
        <div className="text-center py-16 bg-muted/10 rounded-xl border border-dashed border-border/50 space-y-4">
          <BookOpen className="w-12 h-12 text-muted-foreground mx-auto opacity-40" />
          <div>
            <h3 className="text-lg font-serif font-medium">
              {search ? "No works match your search" : "No works yet"}
            </h3>
            <p className="text-muted-foreground mt-1 text-sm max-w-sm mx-auto">
              {search
                ? "Try a different search term."
                : "If you've already uploaded books or documents to the Library, use Import to turn them into Works automatically."}
            </p>
          </div>
          {!search && (
            <div className="flex items-center justify-center gap-3">
              <Button variant="outline" className="gap-2" onClick={() => setIsImportOpen(true)}>
                <Library className="w-4 h-4" />
                Import from Library
              </Button>
              <Button className="gap-2" onClick={() => setIsCreateOpen(true)}>
                <Plus className="w-4 h-4" />
                New Work
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── WorkReadinessBadge ────────────────────────────────────────────────────────

interface WorkWithReadiness {
  doc_count: number;
  ready_doc_count: number;
  error_doc_count: number;
  processing_doc_count: number;
}

function WorkReadinessBadge({ work }: { work: WorkWithReadiness }) {
  const { doc_count, ready_doc_count, error_doc_count, processing_doc_count } = work;

  if (!doc_count) return null; // no docs → no badge

  let label: string;
  let cls: string;

  let badgeStyle: React.CSSProperties;

  if (error_doc_count > 0) {
    label      = `${error_doc_count} error${error_doc_count !== 1 ? "s" : ""}`;
    badgeStyle = { borderColor: 'var(--rust)', color: 'var(--rust)', background: 'var(--rust-soft)' };
  } else if (processing_doc_count > 0) {
    label      = "Processing";
    badgeStyle = { borderColor: 'var(--gilt-line)', color: 'var(--gilt)', background: 'var(--gilt-soft)' };
  } else if (ready_doc_count === doc_count) {
    label      = "Ready";
    badgeStyle = { borderColor: 'var(--green-2)', color: 'var(--green-2)', background: 'var(--green-soft)' };
  } else {
    label      = `${ready_doc_count}/${doc_count} ready`;
    badgeStyle = { borderColor: 'var(--line-2)', color: 'var(--ink-soft)', background: 'transparent' };
  }

  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded border text-[10px] font-mono font-medium shrink-0"
          style={badgeStyle}>
      {label}
    </span>
  );
}
