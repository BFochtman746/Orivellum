import { useState, useEffect } from "react";
import { useParams, Link, useLocation } from "wouter";
import {
  useGetWork,
  useGetWorkStats,
  useUpdateWork,
  useDeleteWork,
  useDeleteKnowledgeItem,
  useGetWorkDocuments,
  useGetWorkKnowledge,
  useGetWorkTasks,
  useGetWorkConversations,
  useCreateWorkTask,
  useUpdateWorkTask,
  useCreateConversation,
  useListLibrary,
  getGetWorkQueryKey,
  getGetWorkStatsQueryKey,
  getListWorksQueryKey,
  getGetWorkTasksQueryKey,
  getGetWorkDocumentsQueryKey,
  getGetWorkKnowledgeQueryKey,
  getGetWorkConversationsQueryKey,
  getListConversationsQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import {
  ArrowLeft,
  FileText,
  Network,
  CheckSquare,
  MessageSquare,
  Plus,
  Clock,
  Loader2,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
  Pencil,
  Check,
  X,
  Trash2,
  GraduationCap,
  RefreshCw,
  ChevronRight,
  MessageSquarePlus,
  Unlink,
  Search,
  BookOpen,
  ChevronDown,
  Trophy,
} from "lucide-react";
import { Textarea } from "@/components/ui/textarea";
import { ScrollArea } from "@/components/ui/scroll-area";
import { toast } from "sonner";

// ─── Work detail shell ────────────────────────────────────────────────────────

export default function WorkDetail() {
  const { workId } = useParams();
  const [, navigate] = useLocation();
  const queryClient = useQueryClient();
  const { data: workResp, isLoading: loadingWork } = useGetWork(workId!, {
    query: { enabled: !!workId, queryKey: getGetWorkQueryKey(workId!) },
  });
  const work = workResp?.work;
  const { data: statsResp } = useGetWorkStats(workId!, {
    query: {
      queryKey: getGetWorkStatsQueryKey(workId!),
      enabled: !!workId,
      // Poll while any docs are still processing so the readiness strip stays current
      refetchInterval: (query) => {
        const byR = ((query.state.data as any)?.documents_by_readiness ?? {}) as Record<string, number>;
        return (byR.imported ?? 0) > 0 ? 4_000 : false;
      },
    },
  });
  const stats = statsResp as any;
  const updateWork = useUpdateWork();
  const deleteWork = useDeleteWork();

  const handleDelete = () => {
    if (!workId) return;
    if (!window.confirm("Delete this work? This cannot be undone.")) return;
    deleteWork.mutate(
      { workId },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getListWorksQueryKey() });
          navigate("/works");
        },
        onError: () => toast.error("Could not delete work"),
      }
    );
  };

  // Inline editing state
  const [editing, setEditing] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editDesc, setEditDesc] = useState("");

  const startEdit = () => {
    setEditTitle((work as any)?.title ?? "");
    setEditDesc((work as any)?.description ?? "");
    setEditing(true);
  };

  const cancelEdit = () => setEditing(false);

  const saveEdit = () => {
    if (!workId || !editTitle.trim()) return;
    updateWork.mutate(
      { workId, data: { title: editTitle.trim(), description: editDesc.trim() || null } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId) });
          toast.success("Work updated");
          setEditing(false);
        },
        onError: () => toast.error("Could not save changes"),
      }
    );
  };

  return (
    <div className="space-y-6 animate-in fade-in duration-500 pb-20">
      {/* Breadcrumb */}
      <div className="flex items-center justify-between mb-8">
        <div className="flex items-center gap-4 text-sm font-mono uppercase tracking-widest text-muted-foreground">
          <Link href="/works" className="hover:text-foreground transition-colors flex items-center gap-1">
            <ArrowLeft className="w-3 h-3" /> Works
          </Link>
          <span>/</span>
          <span className="text-foreground">
            {loadingWork ? <Skeleton className="w-20 h-4 inline-block align-middle" /> : work?.title}
          </span>
        </div>
        {work && (
          <div className="flex items-center gap-2">
            <QuickChatButton workId={workId!} />
            <button
              onClick={handleDelete}
              disabled={deleteWork.isPending}
              className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-destructive transition-colors px-2 py-1 rounded hover:bg-destructive/5"
            >
              {deleteWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
              Delete
            </button>
          </div>
        )}
      </div>

      {/* Header */}
      {loadingWork ? (
        <div className="space-y-4">
          <Skeleton className="h-12 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      ) : work ? (
        <div className="space-y-4">
          {editing ? (
            <div className="space-y-3">
              <Input
                value={editTitle}
                onChange={(e) => setEditTitle(e.target.value)}
                className="text-2xl font-serif font-semibold h-auto py-2 px-3 border-primary/40"
                placeholder="Work title"
                autoFocus
              />
              <Textarea
                value={editDesc}
                onChange={(e) => setEditDesc(e.target.value)}
                className="font-serif text-base resize-none"
                placeholder="Description (optional)"
                rows={2}
              />
              <div className="flex items-center gap-2">
                <Button size="sm" onClick={saveEdit} disabled={updateWork.isPending || !editTitle.trim()} className="gap-1.5">
                  {updateWork.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Save
                </Button>
                <Button size="sm" variant="ghost" onClick={cancelEdit} disabled={updateWork.isPending} className="gap-1.5">
                  <X className="w-3.5 h-3.5" /> Cancel
                </Button>
              </div>
            </div>
          ) : (
            <div>
              <div className="flex items-start gap-3">
                <h1 className="text-4xl font-serif font-semibold tracking-tight">{work.title}</h1>
                <button
                  onClick={startEdit}
                  className="mt-2 p-1.5 rounded text-muted-foreground/50 hover:text-muted-foreground hover:bg-muted/50 transition-colors"
                  title="Edit title and description"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
              </div>
              {work.description ? (
                <p className="text-lg text-muted-foreground font-serif italic mt-2 max-w-3xl leading-relaxed">
                  {work.description}
                </p>
              ) : (
                <button
                  onClick={startEdit}
                  className="text-sm text-muted-foreground/40 italic mt-2 hover:text-muted-foreground transition-colors"
                >
                  Add a description…
                </button>
              )}
            </div>
          )}
          <div className="flex items-center gap-3 flex-wrap">
            <Select
              value={(work as any).status ?? "active"}
              onValueChange={(val) =>
                updateWork.mutate(
                  { workId: workId!, data: { status: val } },
                  {
                    onSuccess: () => {
                      queryClient.invalidateQueries({ queryKey: getGetWorkQueryKey(workId!) });
                      toast.success(val === "archived" ? "Work archived" : "Work set to active");
                    },
                    onError: () => toast.error("Could not update status"),
                  }
                )
              }
              disabled={updateWork.isPending}
            >
              <SelectTrigger className="h-6 text-[11px] font-mono uppercase px-2 py-0 w-auto border-primary/20 bg-primary/5 text-primary rounded-full focus:ring-0">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="active" className="text-xs font-mono uppercase">Active</SelectItem>
                <SelectItem value="archived" className="text-xs font-mono uppercase">Archived</SelectItem>
              </SelectContent>
            </Select>
            <Badge variant="secondary" className="font-mono text-xs uppercase">{work.work_type}</Badge>
            <span className="text-sm font-mono text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" />
              Created {work.created_at ? format(new Date(work.created_at), "MMM d, yyyy") : "Unknown"}
            </span>
          </div>
          {stats && (
            <div className="flex flex-wrap items-center gap-4 pt-1">
              {[
                {
                  label: "Documents",
                  value: Object.values(stats.documents_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                },
                {
                  label: "Knowledge",
                  value: Object.values(stats.knowledge_by_kind as Record<string, number> ?? {}).reduce((a, b) => a + b, 0),
                },
                {
                  label: "Pending tasks",
                  value: (stats.tasks_by_status as Record<string, number> ?? {}).pending ?? 0,
                },
                {
                  label: "Conversations",
                  value: stats.conversation_count ?? 0,
                },
              ].map(({ label, value }) => (
                <div key={label} className="text-center">
                  <div className="text-lg font-semibold font-mono leading-none">{value}</div>
                  <div className="text-[10px] font-mono uppercase text-muted-foreground mt-0.5">{label}</div>
                </div>
              ))}
              {/* Readiness strip — shown when any doc is still processing or has errors */}
              {(() => {
                const byR = stats.documents_by_readiness as Record<string, number> ?? {};
                const processing = byR.imported ?? 0;
                const errors = (byR.error ?? 0) + (byR.no_text ?? 0);
                if (processing === 0 && errors === 0) return null;
                return (
                  <div className="flex items-center gap-2 ml-2 pl-4 border-l border-border/50">
                    {processing > 0 && (
                      <span className="flex items-center gap-1 text-[10px] font-mono text-amber-600 bg-amber-50 border border-amber-200 rounded px-1.5 py-0.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-amber-400 animate-pulse" />
                        {processing} processing
                      </span>
                    )}
                    {errors > 0 && (
                      <span className="flex items-center gap-1 text-[10px] font-mono text-red-600 bg-red-50 border border-red-200 rounded px-1.5 py-0.5">
                        <span className="w-1.5 h-1.5 rounded-full bg-red-500" />
                        {errors} error{errors !== 1 ? "s" : ""}
                      </span>
                    )}
                  </div>
                );
              })()}
            </div>
          )}
        </div>
      ) : null}

      {/* Tabs */}
      <div className="pt-8">
        <Tabs defaultValue="documents" className="w-full">
          <TabsList className="w-full justify-start border-b border-border/50 rounded-none bg-transparent h-auto p-0 space-x-6">
            {[
              { value: "documents", icon: FileText, label: "Documents" },
              { value: "knowledge", icon: Network, label: "Knowledge" },
              { value: "tasks", icon: CheckSquare, label: "Tasks" },
              { value: "conversations", icon: MessageSquare, label: "Conversations" },
              { value: "search", icon: Search, label: "Search" },
              { value: "quiz",  icon: GraduationCap, label: "Quiz" },
              { value: "learn", icon: BookOpen,      label: "Learn" },
            ].map(({ value, icon: Icon, label }) => (
              <TabsTrigger
                key={value}
                value={value}
                className="rounded-none border-b-2 border-transparent data-[state=active]:border-primary data-[state=active]:bg-transparent py-3 px-1 font-mono text-xs uppercase tracking-wider"
              >
                <Icon className="w-4 h-4 mr-2" /> {label}
              </TabsTrigger>
            ))}
          </TabsList>

          <div className="mt-8">
            <TabsContent value="documents"><DocumentsTab workId={workId!} /></TabsContent>
            <TabsContent value="knowledge"><KnowledgeTab workId={workId!} /></TabsContent>
            <TabsContent value="tasks"><TasksTab workId={workId!} /></TabsContent>
            <TabsContent value="conversations"><ConversationsTab workId={workId!} /></TabsContent>
            <TabsContent value="search"><SearchTab workId={workId!} /></TabsContent>
            <TabsContent value="quiz"><QuizTab workId={workId!} workTitle={(work as any)?.title ?? "this Work"} /></TabsContent>
            <TabsContent value="learn"><LearnTab workId={workId!} /></TabsContent>
          </div>
        </Tabs>
      </div>
    </div>
  );
}

// ─── Documents tab ────────────────────────────────────────────────────────────

function DocumentsTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const [open, setOpen] = useState(false);
  const [docFilter, setDocFilter] = useState("");

  const { data: docsResp, isLoading } = useGetWorkDocuments(workId, {
    query: {
      enabled: !!workId,
      queryKey: getGetWorkDocumentsQueryKey(workId),
      // Poll every 4 s while any doc is still in "imported" state
      refetchInterval: (query) => {
        const docs = (query.state.data as any)?.documents ?? [];
        return docs.some((d: any) => d.readiness === "imported") ? 4_000 : false;
      },
    },
  });

  // Library documents not yet linked to this work — for the picker
  const { data: libraryResp } = useListLibrary();
  const unlinked = (libraryResp?.documents ?? []).filter(
    (d) => !d.work_id && d.id !== workId
  );

  const [linking, setLinking] = useState(false);

  const handleLink = async (docId: string) => {
    setLinking(true);
    try {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const r = await fetch(`${base}/library/${docId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ work_id: workId }),
      });
      if (!r.ok) throw new Error("Link failed");
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) }),
        queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) }),
      ]);
      toast.success("Document linked");
      setOpen(false);
    } catch {
      toast.error("Could not link document");
    } finally {
      setLinking(false);
    }
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const docs = docsResp?.documents ?? [];
  const filteredDocs = docFilter.trim()
    ? docs.filter((d) => {
        const hay = `${d.title ?? ""} ${(d as any).source ?? ""}`.toLowerCase();
        return hay.includes(docFilter.trim().toLowerCase());
      })
    : docs;

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center flex-wrap gap-3">
        <h3 className="text-xl font-serif font-medium">Source Material</h3>
        <div className="flex items-center gap-2">
          {docs.length > 5 && (
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <Input
                className="pl-8 h-8 text-xs w-48 font-mono"
                placeholder="Filter documents…"
                value={docFilter}
                onChange={(e) => setDocFilter(e.target.value)}
              />
            </div>
          )}
          <Button size="sm" variant="outline" className="gap-2" onClick={() => setOpen(true)}>
            <Plus className="w-4 h-4" /> Add Document
          </Button>
        </div>
      </div>

      {filteredDocs.length > 0 ? (
        <div className="grid gap-3">
          {filteredDocs.map((doc) => (
            <Card
              key={doc.id}
              className="hover-elevate cursor-pointer group"
              onClick={() => navigate(`/library/${doc.id}`)}
            >
              <CardContent className="p-4 flex items-center justify-between">
                <div className="flex items-center gap-3">
                  <FileText className="w-5 h-5 text-muted-foreground" />
                  <div>
                    <h4 className="font-medium">{doc.title || doc.source || "Untitled"}</h4>
                    <div className="flex gap-2 mt-1">
                      <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                      <Badge variant="outline" className="text-[10px] uppercase font-mono">{doc.readiness}</Badge>
                    </div>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  <div className="text-xs font-mono text-muted-foreground">
                    {doc.created_at ? format(new Date(doc.created_at), "MMM d, yyyy") : ""}
                  </div>
                  <button
                    onClick={async (e) => {
                      e.stopPropagation();
                      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
                      const r = await fetch(`${base}/library/${doc.id}`, {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ work_id: null }),
                      });
                      if (r.ok) {
                        await Promise.all([
                          queryClient.invalidateQueries({ queryKey: getGetWorkDocumentsQueryKey(workId) }),
                          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) }),
                        ]);
                        toast.success("Document unlinked");
                      } else {
                        toast.error("Could not unlink document");
                      }
                    }}
                    title="Unlink from this work"
                    className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded text-muted-foreground/50 hover:text-destructive hover:bg-destructive/5"
                  >
                    <Unlink className="w-3.5 h-3.5" />
                  </button>
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      ) : docFilter.trim() ? (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground text-sm">No documents match "{docFilter}".</p>
          <button onClick={() => setDocFilter("")} className="text-xs text-primary underline mt-2">Clear filter</button>
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No documents added to this work yet.</p>
          <Button size="sm" variant="outline" className="gap-2 mt-4" onClick={() => setOpen(true)}>
            <Plus className="w-4 h-4" /> Add from Library
          </Button>
        </div>
      )}

      {/* Document picker dialog */}
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-lg">
          <DialogHeader>
            <DialogTitle className="font-serif">Link a Document</DialogTitle>
            <DialogDescription>
              Choose a document from your library to associate with this work.
            </DialogDescription>
          </DialogHeader>
          <ScrollArea className="max-h-80 mt-2">
            {unlinked.length === 0 ? (
              <p className="text-sm text-muted-foreground text-center py-8">
                No unlinked documents in your library.{" "}
                <Link href="/library" className="underline">Import one</Link> first.
              </p>
            ) : (
              <div className="space-y-2 pr-2">
                {unlinked.map((doc) => (
                  <button
                    key={doc.id}
                    disabled={linking}
                    onClick={() => handleLink(doc.id!)}
                    className="w-full text-left flex items-center gap-3 p-3 rounded-lg border border-border/50 hover:bg-muted/50 transition-colors disabled:opacity-50"
                  >
                    <FileText className="w-4 h-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">{doc.title || doc.source || "Untitled"}</div>
                      <div className="flex gap-1.5 mt-0.5">
                        <Badge variant="secondary" className="text-[10px] uppercase font-mono">{doc.kind}</Badge>
                        <Badge variant="outline" className="text-[10px] uppercase font-mono">{doc.readiness}</Badge>
                      </div>
                    </div>
                    {linking && <Loader2 className="w-3.5 h-3.5 animate-spin shrink-0" />}
                  </button>
                ))}
              </div>
            )}
          </ScrollArea>
        </DialogContent>
      </Dialog>
    </div>
  );
}

// ─── Knowledge tab ────────────────────────────────────────────────────────────

const BASE_KN = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

async function setKnowledgeReview(itemId: string, status: string): Promise<void> {
  const resp = await fetch(`${BASE_KN}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status }),
  });
  if (!resp.ok) throw new Error("Review update failed");
}

type KnowledgeFilter = "all" | "pending" | "approved" | "rejected";

function KnowledgeTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const [, navigate] = useLocation();
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [filter, setFilter] = useState<KnowledgeFilter>("all");
  const [searchText, setSearchText] = useState("");
  const deleteKnowledge = useDeleteKnowledgeItem();
  const { data: knowResp, isLoading } = useGetWorkKnowledge(workId, {}, {
    query: { enabled: !!workId, queryKey: getGetWorkKnowledgeQueryKey(workId, {}) },
  });
  const { data: docsResp } = useGetWorkDocuments(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkDocumentsQueryKey(workId) },
  });
  // Build doc id → display name lookup
  const docNames: Record<string, string> = {};
  for (const d of docsResp?.documents ?? []) {
    if (d.id) {
      const src = (d as any).source ?? "";
      docNames[d.id] = d.title || src.split("/").pop() || d.id.slice(0, 8);
    }
  }

  const handleReview = async (itemId: string, status: "approved" | "rejected") => {
    setReviewing(itemId);
    try {
      await setKnowledgeReview(itemId, status);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing(null);
    }
  };

  const handleDeleteKnowledge = (itemId: string) => {
    if (!window.confirm("Delete this knowledge item?")) return;
    deleteKnowledge.mutate(
      { itemId },
      {
        onSuccess: () => {
          toast.success("Knowledge item deleted");
          queryClient.invalidateQueries({ queryKey: getGetWorkKnowledgeQueryKey(workId, {}) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
        },
        onError: () => toast.error("Could not delete item"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const allKnowledge = knowResp?.knowledge ?? [];
  const pendingCount = allKnowledge.filter((k) => k.review_status === "ai_auto").length;

  const reviewFiltered = allKnowledge.filter((k) => {
    if (filter === "pending")  return k.review_status === "ai_auto";
    if (filter === "approved") return k.review_status === "approved";
    if (filter === "rejected") return k.review_status === "rejected";
    return true;
  });

  const knowledge = searchText.trim()
    ? reviewFiltered.filter((k) => {
        const q = searchText.trim().toLowerCase();
        return (
          (k.text ?? "").toLowerCase().includes(q) ||
          ((k as any).subject ?? "").toLowerCase().includes(q) ||
          ((k as any).object ?? "").toLowerCase().includes(q) ||
          (k.kind ?? "").toLowerCase().includes(q)
        );
      })
    : reviewFiltered;

  const FILTERS: { key: KnowledgeFilter; label: string }[] = [
    { key: "all",      label: `All (${allKnowledge.length})` },
    { key: "pending",  label: `AI Review${pendingCount > 0 ? ` (${pendingCount})` : ""}` },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Dismissed" },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-start justify-between flex-wrap gap-3">
        <h3 className="text-xl font-serif font-medium">Structured Knowledge</h3>
        <div className="flex items-center gap-2 flex-wrap justify-end">
          {allKnowledge.length > 10 && (
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
              <Input
                className="pl-8 h-8 text-xs w-52 font-mono"
                placeholder="Filter knowledge…"
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
              />
            </div>
          )}
          {allKnowledge.length > 0 && (
            <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
              {FILTERS.map(({ key, label }) => (
                <button
                  key={key}
                  onClick={() => setFilter(key)}
                  className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                    filter === key
                      ? "bg-background text-foreground shadow-sm font-semibold"
                      : "text-muted-foreground hover:text-foreground"
                  } ${key === "pending" && pendingCount > 0 ? "text-violet-700" : ""}`}
                >
                  {label}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {knowledge.length > 0 ? (
        <div className="grid gap-3">
          {knowledge.map((item) => {
            const isAI = item.review_status === "ai_auto";
            const isApproved = item.review_status === "approved";
            const isRejected = item.review_status === "rejected";
            const isReviewing = reviewing === item.id;
            return (
            <Card key={item.id} className={`transition-opacity ${isRejected ? "opacity-50" : ""}`}>
              <CardContent className="p-4">
                <div className="flex items-start gap-4">
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">
                        {item.kind}
                      </Badge>
                      {item.review_status === "ai_auto" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-violet-200 bg-violet-50 text-violet-700">
                          <Sparkles className="w-2.5 h-2.5" /> AI
                        </span>
                      ) : item.review_status === "approved" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-emerald-200 bg-emerald-50 text-emerald-700">
                          ✓ approved
                        </span>
                      ) : item.review_status === "rejected" ? (
                        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-red-200 bg-red-50 text-red-700">
                          ✕ rejected
                        </span>
                      ) : (
                        <Badge variant="secondary" className="text-[10px] uppercase font-mono">
                          {item.review_status}
                        </Badge>
                      )}
                    </div>
                    {item.subject && item.predicate && item.object ? (
                      <div className="font-mono text-sm bg-muted/30 p-2 rounded border border-border/50">
                        <span className="font-semibold text-primary">{item.subject}</span>{" "}
                        <span className="text-muted-foreground">{item.predicate}</span>{" "}
                        <span className="font-semibold">{item.object}</span>
                      </div>
                    ) : (
                      <p className="text-sm font-serif leading-relaxed">{item.text}</p>
                    )}
                    {(item as any).source_doc_id && (
                      <a
                        href={`/library/${(item as any).source_doc_id}`}
                        onClick={(e) => { e.stopPropagation(); navigate(`/library/${(item as any).source_doc_id}`); e.preventDefault(); }}
                        className="text-[10px] font-mono text-muted-foreground/70 hover:text-primary mt-1.5 inline-block transition-colors"
                      >
                        ↗ {docNames[(item as any).source_doc_id] ?? "source doc"}
                      </a>
                    )}
                  </div>
                  <div className="flex items-center gap-1.5 shrink-0">
                    {item.confidence !== undefined && item.confidence !== null && (() => {
                      const pct = item.confidence * 100;
                      const tier =
                        pct >= 80 ? { label: "High", color: "text-emerald-700 bg-emerald-50 border-emerald-200" }
                        : pct >= 50 ? { label: "Med", color: "text-amber-700 bg-amber-50 border-amber-200" }
                        : { label: "Low", color: "text-red-700 bg-red-50 border-red-200" };
                      return (
                        <div className="flex flex-col items-end gap-0.5" title={`Confidence: ${pct.toFixed(1)}% — ${tier.label === "High" ? "Strong signal, likely accurate" : tier.label === "Med" ? "Moderate signal, worth verifying" : "Weak signal, treat with caution"}`}>
                          <span className={`text-[10px] font-mono font-semibold px-1.5 py-0.5 rounded border ${tier.color}`}>
                            {pct.toFixed(0)}% {tier.label}
                          </span>
                          <div className="w-12 h-1 rounded-full bg-muted overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${pct >= 80 ? "bg-emerald-500" : pct >= 50 ? "bg-amber-500" : "bg-red-500"}`}
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })()}
                    {(isAI || isApproved || isRejected) && (
                      <>
                        <button
                          disabled={isReviewing || isApproved}
                          onClick={() => handleReview(item.id!, "approved")}
                          title="Approve"
                          className={`p-1.5 rounded transition-colors ${
                            isApproved
                              ? "text-emerald-600 bg-emerald-50"
                              : "text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50"
                          } disabled:opacity-40`}
                        >
                          <ThumbsUp className="w-3.5 h-3.5" />
                        </button>
                        <button
                          disabled={isReviewing || isRejected}
                          onClick={() => handleReview(item.id!, "rejected")}
                          title="Dismiss"
                          className={`p-1.5 rounded transition-colors ${
                            isRejected
                              ? "text-red-600 bg-red-50"
                              : "text-muted-foreground hover:text-red-600 hover:bg-red-50"
                          } disabled:opacity-40`}
                        >
                          <ThumbsDown className="w-3.5 h-3.5" />
                        </button>
                      </>
                    )}
                    <button
                      onClick={() => handleDeleteKnowledge(item.id!)}
                      title="Delete item"
                      className="p-1.5 rounded text-muted-foreground/40 hover:text-destructive hover:bg-destructive/5 transition-colors"
                    >
                      <Trash2 className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              </CardContent>
            </Card>
            );
          })}
        </div>
      ) : searchText.trim() ? (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground text-sm">No knowledge items match "{searchText}".</p>
          <button onClick={() => setSearchText("")} className="text-xs text-primary underline mt-2">Clear filter</button>
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-muted-foreground">No knowledge extracted yet.</p>
          <p className="text-xs text-muted-foreground mt-1">
            Link a document and Orivellum will extract concepts, facts, and excerpts automatically.
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Tasks tab ────────────────────────────────────────────────────────────────

function TasksTab({ workId }: { workId: string }) {
  const queryClient = useQueryClient();
  const { data: tasksResp, isLoading } = useGetWorkTasks(workId, {}, {
    query: { enabled: !!workId, queryKey: getGetWorkTasksQueryKey(workId) },
  });
  const createTask = useCreateWorkTask();
  const updateTask = useUpdateWorkTask();
  const [newTaskText, setNewTaskText] = useState("");

  const handleAdd = (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskText.trim()) return;
    createTask.mutate(
      { workId, data: { text: newTaskText } },
      {
        onSuccess: () => {
          setNewTaskText("");
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          toast.success("Task added");
        },
        onError: () => toast.error("Could not add task"),
      }
    );
  };

  const handleToggle = (taskId: string, current: string) => {
    const next = current === "completed" ? "pending" : "completed";
    updateTask.mutate(
      { workId, taskId, data: { status: next } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getGetWorkTasksQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
        },
        onError: () => toast.error("Could not update task"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const tasks = tasksResp?.tasks ?? [];

  return (
    <div className="space-y-6 max-w-3xl">
      <form onSubmit={handleAdd} className="flex gap-2">
        <Input
          placeholder="Add a new task…"
          value={newTaskText}
          onChange={(e) => setNewTaskText(e.target.value)}
          className="bg-background/50"
        />
        <Button type="submit" disabled={!newTaskText.trim() || createTask.isPending}>
          {createTask.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : "Add"}
        </Button>
      </form>

      <div className="space-y-2">
        {tasks.length > 0 ? (
          tasks.map((task) => (
            <div
              key={task.id}
              className="flex items-start gap-3 p-3 rounded-lg hover:bg-muted/30 transition-colors group border border-transparent hover:border-border/50"
            >
              <Checkbox
                id={task.id}
                className="mt-1"
                checked={task.status === "completed"}
                onCheckedChange={() => handleToggle(task.id!, task.status ?? "pending")}
                disabled={updateTask.isPending}
              />
              <div className="flex-1 space-y-1">
                <label
                  htmlFor={task.id}
                  className={`text-sm font-medium leading-none cursor-pointer ${
                    task.status === "completed" ? "line-through text-muted-foreground" : ""
                  }`}
                >
                  {task.text}
                </label>
              </div>
              <Badge
                variant="outline"
                className="text-[9px] uppercase font-mono opacity-0 group-hover:opacity-100 transition-opacity"
              >
                Priority {task.priority || 0}
              </Badge>
            </div>
          ))
        ) : (
          <p className="text-sm text-muted-foreground italic">No tasks yet for this work.</p>
        )}
      </div>
    </div>
  );
}

// ─── Quick chat button (header shortcut) ──────────────────────────────────────

function QuickChatButton({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const createConv = useCreateConversation();

  const handleClick = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          queryClient.invalidateQueries({ queryKey: getGetWorkStatsQueryKey(workId) });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  return (
    <button
      onClick={handleClick}
      disabled={createConv.isPending}
      title="Start a new discussion about this work"
      className="flex items-center gap-1.5 text-xs font-mono text-muted-foreground/50 hover:text-primary transition-colors px-2 py-1 rounded hover:bg-primary/5"
    >
      {createConv.isPending ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <MessageSquarePlus className="w-3.5 h-3.5" />}
      Chat
    </button>
  );
}

// ─── Conversations tab ────────────────────────────────────────────────────────

function ConversationsTab({ workId }: { workId: string }) {
  const [, setLocation] = useLocation();
  const queryClient = useQueryClient();
  const { data: convResp, isLoading } = useGetWorkConversations(workId, {
    query: { enabled: !!workId, queryKey: getGetWorkConversationsQueryKey(workId) },
  });
  const createConv = useCreateConversation();

  const handleNewDiscussion = () => {
    createConv.mutate(
      { data: { title: "New Discussion", work_id: workId } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getGetWorkConversationsQueryKey(workId) });
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not start conversation"),
      }
    );
  };

  if (isLoading) return <Skeleton className="h-64 w-full" />;
  const conversations = convResp?.conversations ?? [];

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h3 className="text-xl font-serif font-medium">Conversations</h3>
        <Button
          size="sm"
          variant="outline"
          className="gap-2"
          onClick={handleNewDiscussion}
          disabled={createConv.isPending}
        >
          {createConv.isPending ? (
            <Loader2 className="w-4 h-4 animate-spin" />
          ) : (
            <Plus className="w-4 h-4" />
          )}
          New Discussion
        </Button>
      </div>

      {conversations.length > 0 ? (
        <div className="grid gap-3">
          {conversations.map((conv) => (
            <Link key={conv.id} href={`/chat?id=${conv.id}`}>
              <Card className="hover-elevate cursor-pointer">
                <CardContent className="p-4 flex items-center justify-between">
                  <div className="space-y-1">
                    <h4 className="font-medium text-lg">{conv.title || "Untitled Conversation"}</h4>
                    <p className="text-sm text-muted-foreground truncate max-w-xl">
                      {conv.last_message || "No messages yet."}
                    </p>
                  </div>
                  <div className="text-right text-xs font-mono text-muted-foreground space-y-1 shrink-0">
                    <div>{conv.message_count || 0} msgs</div>
                    <div>{conv.updated_at ? format(new Date(conv.updated_at), "MMM d") : ""}</div>
                  </div>
                </CardContent>
              </Card>
            </Link>
          ))}
        </div>
      ) : (
        <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
          <MessageSquare className="w-8 h-8 mx-auto mb-3 opacity-20" />
          <p className="text-muted-foreground">No conversations linked to this work.</p>
          <Button size="sm" variant="outline" className="gap-2 mt-4" onClick={handleNewDiscussion} disabled={createConv.isPending}>
            <Plus className="w-4 h-4" /> Start a Discussion
          </Button>
        </div>
      )}
    </div>
  );
}

// ─── Search tab ───────────────────────────────────────────────────────────────

function SearchTab({ workId }: { workId: string }) {
  const [, navigate] = useLocation();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [results, setResults] = useState<{ knowledge: any[]; chunks: any[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSearch = async (e?: React.FormEvent) => {
    e?.preventDefault();
    const q = query.trim();
    if (!q) return;
    setSubmitted(q);
    setLoading(true);
    setError(null);
    try {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const res = await fetch(`${base}/works/${workId}/search?q=${encodeURIComponent(q)}&limit=20`);
      if (!res.ok) throw new Error(`Search failed: ${res.status}`);
      const data = await res.json();
      setResults(data);
    } catch (err: any) {
      setError(err.message ?? "Search failed");
    } finally {
      setLoading(false);
    }
  };

  const total = (results?.knowledge.length ?? 0) + (results?.chunks.length ?? 0);

  return (
    <div className="space-y-6">
      <form onSubmit={handleSearch} className="flex gap-3">
        <div className="relative flex-1">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
          <Input
            className="pl-9 font-mono text-sm"
            placeholder="Search knowledge and documents…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={!query.trim() || loading}>
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : "Search"}
        </Button>
      </form>

      {error && (
        <p className="text-sm text-destructive">{error}</p>
      )}

      {results && (
        <div className="space-y-8">
          <p className="text-xs font-mono text-muted-foreground">
            {total} result{total !== 1 ? "s" : ""} for <span className="text-foreground">"{submitted}"</span>
          </p>

          {/* Knowledge hits */}
          {results.knowledge.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <Network className="w-3.5 h-3.5" /> Knowledge ({results.knowledge.length})
              </h3>
              <div className="space-y-2">
                {results.knowledge.map((item: any) => (
                  <Card key={item.id} className="p-3">
                    <div className="flex items-start gap-3">
                      <Badge variant="secondary" className="text-[10px] shrink-0 mt-0.5">
                        {item.kind ?? "fact"}
                      </Badge>
                      <div className="flex-1 min-w-0">
                        <p className="text-sm">{item.text}</p>
                        {item.subject && (
                          <p className="text-xs font-mono text-muted-foreground mt-1">
                            {item.subject}
                            {item.relation && <> · {item.relation}</>}
                            {item.object && <> · {item.object}</>}
                          </p>
                        )}
                      </div>
                      {item.confidence != null && (
                        <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                          {Math.round(item.confidence * 100)}%
                        </span>
                      )}
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {/* Document chunk hits */}
          {results.chunks.length > 0 && (
            <div className="space-y-3">
              <h3 className="text-xs font-mono uppercase tracking-wider text-muted-foreground flex items-center gap-2">
                <FileText className="w-3.5 h-3.5" /> Documents ({results.chunks.length})
              </h3>
              <div className="space-y-2">
                {results.chunks.map((chunk: any) => (
                  <Card
                    key={chunk.id}
                    className="p-3 cursor-pointer hover:bg-muted/30 transition-colors"
                    onClick={() => navigate(`/library/${chunk.doc_id}`)}
                  >
                    <div className="space-y-1">
                      <div className="flex items-center gap-2">
                        <span className="text-xs font-mono font-medium truncate">{chunk.doc_title ?? chunk.doc_id}</span>
                        {chunk.doc_kind && (
                          <Badge variant="outline" className="text-[10px]">{chunk.doc_kind}</Badge>
                        )}
                      </div>
                      <p className="text-sm text-muted-foreground line-clamp-3">{chunk.text}</p>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          )}

          {total === 0 && (
            <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
              <Search className="w-8 h-8 mx-auto mb-3 opacity-20" />
              <p className="text-muted-foreground text-sm">No results found for "{submitted}"</p>
              <p className="text-xs text-muted-foreground mt-1">Try different keywords or check that documents have been fully extracted.</p>
            </div>
          )}
        </div>
      )}

      {!results && !loading && (
        <div className="text-center py-16 text-muted-foreground">
          <Search className="w-10 h-10 mx-auto mb-4 opacity-15" />
          <p className="text-sm">Search across all knowledge items and document text in this Work.</p>
        </div>
      )}
    </div>
  );
}

// ─── Learn tab (adaptive Socratic study) ─────────────────────────────────────

const API_BASE_WORKS = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

type LearnPhase = "loading" | "seeding" | "question" | "assessing" | "feedback" | "all_done";
type RouteAction = "STEP_FORWARD" | "STEP_BACKWARD" | "STAY_HERE";

interface LearningSession {
  concept_id: string;
  subject: string;
  description: string;
  question: string;
  context_snippet: string;
}

interface AssessResult {
  score: number;
  feedback: string;
  route: RouteAction;
  graduated: boolean;
  next_concept_id: string | null;
  summary: { total: number; graduated: number; mastery_pct: number };
}

function LearnTab({ workId }: { workId: string }) {
  const [phase, setPhase]       = useState<LearnPhase>("loading");
  const [session, setSession]   = useState<LearningSession | null>(null);
  const [answer, setAnswer]     = useState("");
  const [result, setResult]     = useState<AssessResult | null>(null);
  const [summary, setSummary]   = useState<{ total: number; graduated: number; mastery_pct: number } | null>(null);
  const [error, setError]       = useState<string | null>(null);
  const [showConcepts, setShowConcepts] = useState(false);
  const [concepts, setConcepts] = useState<any[]>([]);

  const apiBase = API_BASE_WORKS;

  const loadSummary = async () => {
    const r = await fetch(`${apiBase}/works/${workId}/learning/summary`);
    if (!r.ok) throw new Error("Could not load learning summary");
    return r.json();
  };

  const startOrContinue = async (conceptId?: string | null) => {
    setError(null);
    setAnswer("");
    setResult(null);
    setPhase("question");
    try {
      const url = conceptId
        ? `${apiBase}/works/${workId}/learning/question?concept_id=${conceptId}`
        : `${apiBase}/works/${workId}/learning/question`;
      const r = await fetch(url);
      if (r.status === 422) {
        setPhase("all_done");
        return;
      }
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data = await r.json();
      setSession({
        concept_id:      data.concept_id,
        subject:         data.subject ?? "Concept",
        description:     data.description ?? "",
        question:        data.question,
        context_snippet: data.context_snippet ?? "",
      });
    } catch (e: any) {
      setError(e.message ?? "Could not load question");
      setPhase("feedback");
    }
  };

  const init = async () => {
    setPhase("loading");
    setError(null);
    try {
      const data = await loadSummary();
      setSummary(data);
      setConcepts(data.concepts ?? []);
      if (data.total === 0) {
        // Auto-seed
        setPhase("seeding");
        const sr = await fetch(`${apiBase}/works/${workId}/learning/seed`, { method: "POST" });
        if (!sr.ok) throw new Error("Could not seed concepts");
        const sd = await sr.json();
        if ((sd.concepts ?? []).length === 0) {
          setError("No knowledge items found. Import and process documents first.");
          setPhase("feedback");
          return;
        }
        const sumData = await loadSummary();
        setSummary(sumData);
        setConcepts(sumData.concepts ?? []);
      }
      if (data.mastery_pct === 100 && data.total > 0) {
        setPhase("all_done");
        return;
      }
      await startOrContinue(null);
    } catch (e: any) {
      setError(e.message ?? "Could not initialise learning");
      setPhase("feedback");
    }
  };

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { let active = true; void init().then(() => {}).catch(() => {}); return () => { active = false; }; }, [workId]);

  const submitAnswer = async () => {
    if (!session || !answer.trim()) return;
    setPhase("assessing");
    setError(null);
    try {
      const r = await fetch(`${apiBase}/works/${workId}/learning/assess`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          concept_id: session.concept_id,
          question:   session.question,
          answer:     answer.trim(),
        }),
      });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const data: AssessResult = await r.json();
      setResult(data);
      setSummary(data.summary);
      setPhase("feedback");
    } catch (e: any) {
      setError(e.message ?? "Could not assess answer");
      setPhase("feedback");
    }
  };

  const next = async () => {
    if (!result) { await startOrContinue(null); return; }
    if (result.summary.mastery_pct === 100) { setPhase("all_done"); return; }
    await startOrContinue(result.next_concept_id);
  };

  const routeLabel: Record<RouteAction, string> = {
    STEP_FORWARD:  "Great — moving to the next concept",
    STEP_BACKWARD: "Let's revisit a foundational concept first",
    STAY_HERE:     "Keep practising this concept",
  };

  // ── Mastery bar ────────────────────────────────────────────────────────────
  const MasteryBar = () => {
    if (!summary) return null;
    const pct = summary.mastery_pct;
    return (
      <div className="space-y-1.5 mb-6">
        <div className="flex items-center justify-between text-xs font-mono text-muted-foreground">
          <span>{summary.graduated}/{summary.total} concepts graduated</span>
          <span className="font-semibold text-foreground">{pct}%</span>
        </div>
        <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
          <div
            className="h-full rounded-full bg-primary transition-all duration-700"
            style={{ width: `${pct}%` }}
          />
        </div>
      </div>
    );
  };

  // ── All done ───────────────────────────────────────────────────────────────
  if (phase === "all_done") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-6">
        <div className="w-16 h-16 rounded-2xl bg-emerald-500/10 flex items-center justify-center">
          <Trophy className="w-8 h-8 text-emerald-500" />
        </div>
        <div className="text-center space-y-2">
          <h3 className="font-serif text-2xl font-medium">All concepts mastered!</h3>
          <p className="text-sm text-muted-foreground max-w-xs">
            You've graduated every concept in this Work. Add more documents to unlock new material.
          </p>
        </div>
        {summary && <MasteryBar />}
      </div>
    );
  }

  // ── Loading / seeding ──────────────────────────────────────────────────────
  if (phase === "loading" || phase === "seeding") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-4">
        <Loader2 className="w-8 h-8 animate-spin text-primary" />
        <p className="text-sm text-muted-foreground font-mono">
          {phase === "seeding" ? "Seeding concepts from your knowledge base…" : "Loading your learning session…"}
        </p>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto py-4 space-y-6">
      <MasteryBar />

      {/* Error banner */}
      {error && (
        <div className="px-4 py-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive">
          {error}
          <Button size="sm" variant="ghost" className="ml-3" onClick={init}>Retry</Button>
        </div>
      )}

      {/* Active concept header */}
      {session && (
        <div className="border border-border/60 rounded-xl p-4 bg-muted/20 space-y-1">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-primary" />
              <span className="font-mono text-xs uppercase tracking-wider text-muted-foreground">Studying</span>
            </div>
          </div>
          <h3 className="font-serif text-lg font-semibold">{session.subject}</h3>
          {session.description && (
            <p className="text-sm text-muted-foreground leading-relaxed">{session.description}</p>
          )}
        </div>
      )}

      {/* Question */}
      {(phase === "question" || phase === "assessing" || phase === "feedback") && session && (
        <Card className="p-6 space-y-4">
          {session.context_snippet && (
            <div className="text-xs font-mono text-muted-foreground/70 pl-3 border-l-2 border-border/50 italic leading-relaxed">
              {session.context_snippet}
            </div>
          )}
          <p className="font-medium leading-relaxed text-base">{session.question}</p>

          {phase !== "feedback" ? (
            <>
              <textarea
                className="w-full rounded-lg border border-border/60 bg-background p-3 text-sm font-serif leading-relaxed resize-none focus:outline-none focus:ring-1 focus:ring-primary/50 min-h-[100px]"
                placeholder="Write your answer here…"
                value={answer}
                onChange={(e) => setAnswer(e.target.value)}
                disabled={phase === "assessing"}
              />
              <div className="flex justify-end">
                <Button
                  onClick={submitAnswer}
                  disabled={!answer.trim() || phase === "assessing"}
                  className="gap-2"
                >
                  {phase === "assessing"
                    ? <><Loader2 className="w-4 h-4 animate-spin" /> Assessing…</>
                    : <><ChevronRight className="w-4 h-4" /> Submit Answer</>}
                </Button>
              </div>
            </>
          ) : result ? (
            /* Feedback */
            <div className="space-y-4">
              <div className="px-3 py-2 rounded bg-muted/40 text-sm font-serif text-muted-foreground italic">
                {answer}
              </div>

              {/* Score */}
              <div className={`flex items-center gap-3 p-3 rounded-lg border ${
                result.score >= 0.75
                  ? "bg-emerald-500/10 border-emerald-500/30"
                  : result.score >= 0.5
                  ? "bg-amber-500/10 border-amber-500/30"
                  : "bg-red-500/10 border-red-500/30"
              }`}>
                <div className="text-2xl font-bold font-mono">
                  {Math.round(result.score * 100)}%
                </div>
                <div className="flex-1">
                  <p className="text-sm leading-relaxed">{result.feedback}</p>
                </div>
                {result.graduated && (
                  <div className="shrink-0 flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-500/20 border border-emerald-500/40 text-emerald-700 text-xs font-mono font-semibold">
                    <Trophy className="w-3 h-3" /> Graduated!
                  </div>
                )}
              </div>

              {/* Routing hint */}
              <p className="text-xs font-mono text-muted-foreground">
                → {routeLabel[result.route]}
              </p>

              <div className="flex justify-end">
                <Button onClick={next} className="gap-2">
                  {result.summary.mastery_pct === 100
                    ? <><Trophy className="w-4 h-4" /> Done!</>
                    : result.route === "STEP_FORWARD"
                    ? <><ChevronRight className="w-4 h-4" /> Next Concept</>
                    : <><RefreshCw className="w-4 h-4" /> Try Again</>}
                </Button>
              </div>
            </div>
          ) : null}
        </Card>
      )}

      {/* Concept map (collapsible) */}
      {concepts.length > 0 && (
        <div className="border border-border/50 rounded-xl overflow-hidden">
          <button
            onClick={() => setShowConcepts(!showConcepts)}
            className="w-full flex items-center justify-between px-4 py-3 text-xs font-mono uppercase tracking-wider text-muted-foreground hover:bg-muted/30 transition-colors"
          >
            <span>Concept map ({concepts.length})</span>
            <ChevronDown className={`w-4 h-4 transition-transform ${showConcepts ? "rotate-180" : ""}`} />
          </button>
          {showConcepts && (
            <div className="divide-y divide-border/30">
              {concepts.map((c: any) => (
                <div key={c.id} className="flex items-center justify-between px-4 py-2.5 text-sm">
                  <div className="flex items-center gap-2">
                    {c.graduated
                      ? <Check className="w-3.5 h-3.5 text-emerald-500" />
                      : c.consecutive_passes > 0
                      ? <div className="w-3.5 h-3.5 rounded-full border-2 border-amber-400" />
                      : <div className="w-3.5 h-3.5 rounded-full border border-muted-foreground/40" />}
                    <span className={c.graduated ? "text-emerald-700 dark:text-emerald-400 font-medium" : ""}>{c.subject}</span>
                  </div>
                  <span className="text-xs font-mono text-muted-foreground">
                    {c.graduated ? "✓ done" : c.consecutive_passes > 0 ? `${c.consecutive_passes}/3` : "—"}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ─── Quiz tab ─────────────────────────────────────────────────────────────────

interface QuizQuestion {
  q: string;
  options: string[];
  answer: number;
  explanation: string;
}

function QuizTab({ workId, workTitle }: { workId: string; workTitle: string }) {
  const [phase, setPhase]       = useState<"idle" | "loading" | "active" | "done">("idle");
  const [questions, setQuestions] = useState<QuizQuestion[]>([]);
  const [answers, setAnswers]   = useState<Record<number, number>>({});
  const [error, setError]       = useState<string | null>(null);

  const generate = async () => {
    setPhase("loading");
    setError(null);
    setAnswers({});
    try {
      const r = await fetch(`${API_BASE_WORKS}/works/${workId}/quiz`, { method: "POST" });
      if (!r.ok) {
        const body = await r.json().catch(() => ({}));
        throw new Error((body as any).detail ?? `HTTP ${r.status}`);
      }
      const data = await r.json();
      setQuestions(data.questions ?? []);
      setPhase("active");
    } catch (err: any) {
      setError(err.message ?? "Failed to generate quiz");
      setPhase("idle");
    }
  };

  const submit = () => setPhase("done");
  const reset  = () => { setPhase("idle"); setQuestions([]); setAnswers({}); setError(null); };

  const score = Object.entries(answers).filter(([qi, ai]) => questions[+qi]?.answer === ai).length;

  if (phase === "idle" || phase === "loading") {
    return (
      <div className="flex flex-col items-center justify-center py-24 gap-6">
        <div className="w-16 h-16 rounded-2xl bg-primary/10 flex items-center justify-center">
          <GraduationCap className="w-8 h-8 text-primary" />
        </div>
        <div className="text-center space-y-1 max-w-sm">
          <h3 className="font-serif text-xl font-medium">Adaptive Quiz</h3>
          <p className="text-sm text-muted-foreground">
            Test your understanding of <span className="font-medium text-foreground">{workTitle}</span>.
            Orivellum will generate 5 multiple-choice questions from your knowledge base.
          </p>
        </div>
        {error && (
          <div className="px-4 py-3 rounded-lg bg-destructive/10 border border-destructive/20 text-sm text-destructive max-w-sm text-center">
            {error}
          </div>
        )}
        <Button onClick={generate} disabled={phase === "loading"} className="gap-2 px-8">
          {phase === "loading" ? <><Loader2 className="w-4 h-4 animate-spin" /> Generating…</> : <><Sparkles className="w-4 h-4" /> Generate Quiz</>}
        </Button>
      </div>
    );
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6 py-4">
      {/* Score banner (done phase) */}
      {phase === "done" && (
        <div className={`p-4 rounded-xl border text-center ${score >= 4 ? "bg-emerald-500/10 border-emerald-500/30" : score >= 3 ? "bg-amber-500/10 border-amber-500/30" : "bg-red-500/10 border-red-500/30"}`}>
          <p className="text-2xl font-serif font-bold">
            {score}/{questions.length}
            <span className="text-base font-normal font-sans text-muted-foreground ml-2">
              {score >= 4 ? "Excellent!" : score >= 3 ? "Good effort" : "Keep studying"}
            </span>
          </p>
        </div>
      )}

      {/* Questions */}
      {questions.map((q, qi) => {
        const chosen  = answers[qi];
        const correct = q.answer;
        const isDone  = phase === "done";
        return (
          <Card key={qi} className="p-5 space-y-3">
            <p className="font-medium text-sm leading-relaxed">
              <span className="font-mono text-muted-foreground mr-2">{qi + 1}.</span>{q.q}
            </p>
            <div className="space-y-2">
              {q.options.map((opt, oi) => {
                const isChosen  = chosen === oi;
                const isCorrect = correct === oi;
                let cls = "flex items-center gap-3 px-3 py-2 rounded-lg border text-sm cursor-pointer transition-colors ";
                if (isDone) {
                  if (isCorrect)      cls += "bg-emerald-500/10 border-emerald-500/40 text-emerald-700 dark:text-emerald-400";
                  else if (isChosen)  cls += "bg-red-500/10 border-red-500/40 text-red-700 dark:text-red-400";
                  else                cls += "border-border/40 text-muted-foreground";
                } else {
                  cls += isChosen
                    ? "bg-primary/10 border-primary/50 text-primary"
                    : "border-border/50 hover:bg-muted/40 hover:border-border";
                }
                return (
                  <div key={oi} className={cls} onClick={() => !isDone && setAnswers(a => ({ ...a, [qi]: oi }))}>
                    <span className="w-5 h-5 rounded-full border border-current flex items-center justify-center text-[10px] font-mono shrink-0">
                      {String.fromCharCode(65 + oi)}
                    </span>
                    <span className="flex-1">{opt}</span>
                    {isDone && isCorrect && <Check className="w-4 h-4 text-emerald-500 shrink-0" />}
                    {isDone && isChosen && !isCorrect && <X className="w-4 h-4 text-red-500 shrink-0" />}
                  </div>
                );
              })}
            </div>
            {isDone && q.explanation && (
              <div className="pt-2 pl-3 border-l-2 border-primary/30">
                <p className="text-xs text-muted-foreground">{q.explanation}</p>
              </div>
            )}
          </Card>
        );
      })}

      {/* Actions */}
      <div className="flex justify-between items-center pt-2">
        {phase === "active" ? (
          <>
            <span className="text-xs text-muted-foreground">{Object.keys(answers).length}/{questions.length} answered</span>
            <Button onClick={submit} disabled={Object.keys(answers).length < questions.length} className="gap-2">
              <ChevronRight className="w-4 h-4" /> Submit Answers
            </Button>
          </>
        ) : (
          <>
            <span />
            <Button variant="outline" onClick={reset} className="gap-2">
              <RefreshCw className="w-4 h-4" /> New Quiz
            </Button>
          </>
        )}
      </div>
    </div>
  );
}
