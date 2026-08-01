/**
 * Document detail page — /library/:docId
 *
 * Shows metadata, full extracted text, and all knowledge items
 * harvested from this specific document.
 */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { useGetDocument, useDeleteDocument, useGetWork, useDeleteKnowledgeItem, useUpdateDocument, useListWorks, getGetDocumentQueryKey } from "@workspace/api-client-react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft, FileText, AlertCircle, CheckCircle2, Clock,
  FileQuestion, RefreshCw, Trash2, Hash, Calendar, Database,
  BookOpen, Cpu, Sparkles, ThumbsUp, ThumbsDown, Link2,
} from "lucide-react";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "overview" | "text" | "knowledge";

interface KnowledgeItem {
  id: string;
  kind: string;
  text: string;
  subject?: string | null;
  predicate?: string | null;
  object?: string | null;
  confidence?: number | null;
  review_status?: string | null;
}

// ── Readiness badge ───────────────────────────────────────────────────────────

const READINESS_CFG = {
  ready:    { label: "READY",      Icon: CheckCircle2, cls: "text-emerald-600 border-emerald-200 bg-emerald-50" },
  imported: { label: "PROCESSING", Icon: Clock,        cls: "text-amber-600 border-amber-200 bg-amber-50" },
  no_text:  { label: "NO TEXT",    Icon: FileQuestion, cls: "text-orange-600 border-orange-200 bg-orange-50" },
  error:    { label: "ERROR",      Icon: AlertCircle,  cls: "text-red-600 border-red-200 bg-red-50" },
} as const;

function ReadinessBadge({ readiness }: { readiness: string }) {
  const cfg = READINESS_CFG[readiness as keyof typeof READINESS_CFG] ?? READINESS_CFG.imported;
  const { Icon } = cfg;
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono font-medium border ${cfg.cls}`}>
      <Icon className="w-3 h-3" />
      {cfg.label}
    </span>
  );
}

// ── Review-status badge ───────────────────────────────────────────────────────

function ReviewBadge({ status }: { status: string | null | undefined }) {
  if (!status) return null;
  if (status === "ai_auto") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-violet-200 bg-violet-50 text-violet-700">
        <Sparkles className="w-2.5 h-2.5" />
        AI
      </span>
    );
  }
  if (status === "approved") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-emerald-200 bg-emerald-50 text-emerald-700">
        ✓ approved
      </span>
    );
  }
  if (status === "rejected") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border border-red-200 bg-red-50 text-red-700">
        ✕ rejected
      </span>
    );
  }
  return (
    <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-mono border text-muted-foreground">
      {status}
    </span>
  );
}

// ── Knowledge tab content ─────────────────────────────────────────────────────

type KnFilter = "all" | "pending" | "approved" | "rejected";

function KnowledgeTabContent({
  knLoading,
  items,
  docWorkId,
  knFilter,
  setKnFilter,
  reviewing,
  onReview,
  onDelete,
}: {
  knLoading: boolean;
  items: KnowledgeItem[];
  docWorkId?: string | null;
  knFilter: KnFilter;
  setKnFilter: (f: KnFilter) => void;
  reviewing: string | null;
  onReview: (id: string, status: "approved" | "rejected") => void;
  onDelete: (id: string) => void;
}) {
  if (knLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 w-full" />)}
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg">
        <Cpu className="w-8 h-8 text-muted-foreground mx-auto mb-3 opacity-50" />
        <p className="text-muted-foreground">No knowledge items extracted from this document yet.</p>
        {docWorkId && (
          <p className="text-xs text-muted-foreground mt-1">
            Knowledge extraction runs automatically after import.
          </p>
        )}
      </div>
    );
  }

  const pendingCount = items.filter((k) => k.review_status === "ai_auto").length;
  const visible = items.filter((k) => {
    if (knFilter === "pending")  return k.review_status === "ai_auto";
    if (knFilter === "approved") return k.review_status === "approved";
    if (knFilter === "rejected") return k.review_status === "rejected";
    return true;
  });
  const KN_FILTERS: { key: KnFilter; label: string }[] = [
    { key: "all",      label: `All (${items.length})` },
    { key: "pending",  label: `AI Review${pendingCount > 0 ? ` (${pendingCount})` : ""}` },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Dismissed" },
  ];

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <p className="text-xs font-mono text-muted-foreground">
          {items.length} item{items.length !== 1 ? "s" : ""} extracted
        </p>
        <div className="flex items-center gap-1 p-1 bg-muted/40 rounded-lg">
          {KN_FILTERS.map(({ key, label }) => (
            <button
              key={key}
              onClick={() => setKnFilter(key)}
              className={`px-2.5 py-1 rounded text-xs font-mono transition-colors ${
                knFilter === key
                  ? "bg-background text-foreground shadow-sm font-semibold"
                  : "text-muted-foreground hover:text-foreground"
              } ${key === "pending" && pendingCount > 0 ? "text-violet-700" : ""}`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      {visible.map((item) => {
        const isAI = item.review_status === "ai_auto";
        const isApproved = item.review_status === "approved";
        const isRejected = item.review_status === "rejected";
        const isReviewing = reviewing === item.id;
        return (
          <Card key={item.id} className={`hover-elevate transition-opacity ${isRejected ? "opacity-50" : ""}`}>
            <CardContent className="p-4">
              <div className="flex items-start gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-2 flex-wrap">
                    <Badge variant="outline" className="text-[10px] uppercase font-mono border-primary/30 text-primary">
                      {item.kind}
                    </Badge>
                    <ReviewBadge status={item.review_status} />
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
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  {item.confidence != null && (
                    <div className="text-xs font-mono px-2 py-1 bg-muted rounded">
                      {(item.confidence * 100).toFixed(0)}%
                    </div>
                  )}
                  {(isAI || isApproved || isRejected) && (
                    <>
                      <button
                        disabled={isReviewing || isApproved}
                        onClick={() => onReview(item.id, "approved")}
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
                        onClick={() => onReview(item.id, "rejected")}
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
                    onClick={() => onDelete(item.id)}
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

// ── Main page ─────────────────────────────────────────────────────────────────

async function setKnowledgeReview(itemId: string, status: string): Promise<void> {
  const resp = await fetch(`${BASE}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ review_status: status }),
  });
  if (!resp.ok) throw new Error("Review update failed");
}

export default function DocumentDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [, navigate] = useLocation();
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [reprocessing, setReprocessing] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [knFilter, setKnFilter] = useState<"all" | "pending" | "approved" | "rejected">("all");
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch } = useGetDocument(docId ?? "");
  const deleteDoc = useDeleteDocument();

  const doc = data?.document as any;
  const workId = doc?.work_id as string | undefined;

  // Resolve work title when this document is linked to a work
  const { data: workData } = useGetWork(workId ?? "", {
    query: { enabled: !!workId },
  });

  // Knowledge items for this document
  const { data: knData, isLoading: knLoading } = useQuery<{ knowledge: KnowledgeItem[]; count: number }>({
    queryKey: ["doc-knowledge", docId],
    queryFn: () => fetch(`${BASE}/library/${docId}/knowledge`).then((r) => r.json()),
    enabled: !!docId && activeTab === "knowledge",
    staleTime: 30_000,
  });

  const handleReview = async (itemId: string, status: "approved" | "rejected") => {
    setReviewing(itemId);
    try {
      await setKnowledgeReview(itemId, status);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      queryClient.invalidateQueries({ queryKey: ["doc-knowledge", docId] });
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing(null);
    }
  };

  // Work assignment
  const updateDoc = useUpdateDocument();
  const { data: worksResp } = useListWorks();
  const allWorks = worksResp?.works ?? [];
  const handleAssignWork = (newWorkId: string) => {
    const val = newWorkId === "__none__" ? null : newWorkId;
    updateDoc.mutate(
      { docId: docId!, data: { work_id: val } },
      {
        onSuccess: () => {
          toast.success(val ? "Document linked to work" : "Work link removed");
          queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(docId!) });
        },
        onError: () => toast.error("Could not update document"),
      }
    );
  };

  const deleteKnowledge = useDeleteKnowledgeItem();
  const handleDeleteKnowledge = (itemId: string) => {
    if (!window.confirm("Delete this knowledge item?")) return;
    deleteKnowledge.mutate(
      { itemId },
      {
        onSuccess: () => {
          toast.success("Knowledge item deleted");
          queryClient.invalidateQueries({ queryKey: ["doc-knowledge", docId] });
        },
        onError: () => toast.error("Could not delete item"),
      }
    );
  };

  const handleReprocess = async () => {
    if (!docId) return;
    setReprocessing(true);
    try {
      await reprocessDoc(docId);
      toast.success("Re-extraction queued — polling for result…");
      // Poll every 2 s until readiness leaves "imported" state (max 30 s)
      let attempts = 0;
      const poll = setInterval(async () => {
        attempts++;
        const result = await refetch();
        const newReadiness = (result.data?.document as any)?.readiness;
        if (newReadiness && newReadiness !== "imported") {
          clearInterval(poll);
          if (newReadiness === "ready") {
            toast.success("Extraction complete.");
          } else if (newReadiness === "error" || newReadiness === "no_text") {
            toast.error("Extraction finished with issues — check the error message below.");
          }
        }
        if (attempts >= 15) clearInterval(poll);
      }, 2000);
    } catch (e: any) {
      toast.error(e.message ?? "Reprocess failed");
    } finally {
      setReprocessing(false);
    }
  };

  const handleDelete = () => {
    if (!docId || !confirm("Delete this document? This cannot be undone.")) return;
    deleteDoc.mutate(
      { docId },
      {
        onSuccess: () => { toast.success("Document deleted"); navigate("/library"); },
        onError: () => toast.error("Could not delete document"),
      }
    );
  };

  if (isLoading) {
    return (
      <div className="space-y-6 animate-in fade-in duration-300">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !doc) {
    return (
      <div className="text-center py-20">
        <AlertCircle className="w-10 h-10 text-destructive mx-auto mb-3" />
        <p className="text-lg font-medium">Document not found</p>
        <Button variant="outline" className="mt-4" onClick={() => navigate("/library")}>
          <ArrowLeft className="w-4 h-4 mr-2" /> Back to Library
        </Button>
      </div>
    );
  }

  const readiness: string = doc.readiness ?? "imported";
  const hasError = readiness === "error" || readiness === "no_text";
  const title = doc.title || doc.source || "Untitled Document";

  const tabs: { key: Tab; label: string; icon: React.ElementType }[] = [
    { key: "overview",  label: "Overview",  icon: FileText },
    { key: "text",      label: "Text",       icon: BookOpen },
    { key: "knowledge", label: "Knowledge",  icon: Cpu },
  ];

  return (
    <div className="space-y-6 animate-in fade-in duration-300 max-w-4xl">
      {/* Back + actions */}
      <div className="flex items-center justify-between">
        <Button variant="ghost" size="sm" onClick={() => navigate("/library")} className="-ml-2">
          <ArrowLeft className="w-4 h-4 mr-1.5" /> Library
        </Button>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={handleReprocess}
            disabled={reprocessing}
          >
            <RefreshCw className={`w-3.5 h-3.5 mr-1.5 ${reprocessing ? "animate-spin" : ""}`} />
            {reprocessing ? "Queued…" : "Re-extract"}
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={handleDelete}
            className="text-destructive hover:bg-destructive/10 hover:text-destructive border-destructive/30"
          >
            <Trash2 className="w-3.5 h-3.5 mr-1.5" /> Delete
          </Button>
        </div>
      </div>

      {/* Header */}
      <div>
        <div className="flex items-start gap-3 mb-3">
          <div className={`w-10 h-10 rounded-lg flex items-center justify-center shrink-0 border ${
            hasError ? "bg-red-50 border-red-200" : "bg-muted/50 border-border/50"
          }`}>
            {hasError
              ? <AlertCircle className="w-5 h-5 text-red-500" />
              : <FileText className="w-5 h-5 text-muted-foreground" />
            }
          </div>
          <div className="min-w-0 flex-1">
            <h1 className="text-2xl font-serif font-semibold tracking-tight truncate">{title}</h1>
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                {doc.kind ?? "file"}
              </Badge>
              <ReadinessBadge readiness={readiness} />
              {doc.word_count > 0 && (
                <span className="text-xs font-mono text-muted-foreground">
                  {doc.word_count.toLocaleString()} words
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Error banner */}
        {hasError && doc.error_message && (
          <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-xs font-mono text-red-700 break-all">{doc.error_message}</p>
          </div>
        )}

        {/* Metadata strip */}
        <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-1.5 text-xs font-mono text-muted-foreground">
          {doc.created_at && (
            <span className="flex items-center gap-1.5">
              <Calendar className="w-3 h-3" />
              {format(new Date(doc.created_at), "MMM d, yyyy 'at' HH:mm")}
            </span>
          )}
          {doc.sha256 && (
            <span className="flex items-center gap-1.5" title={doc.sha256}>
              <Hash className="w-3 h-3" />
              {doc.sha256.slice(0, 12)}
            </span>
          )}
          {doc.work_id && (
            <button
              onClick={() => navigate(`/works/${doc.work_id}`)}
              className="flex items-center gap-1.5 hover:text-primary transition-colors"
            >
              <Database className="w-3 h-3" />
              {(workData?.work as any)?.title ?? "Linked Work"}
            </button>
          )}
        </div>
      </div>

      <Separator />

      {/* Tab bar */}
      <div className="flex items-center gap-1 border-b border-border/50">
        {tabs.map(({ key, label, icon: Icon }) => (
          <button
            key={key}
            onClick={() => setActiveTab(key)}
            className={`flex items-center gap-1.5 px-4 py-2.5 text-sm font-medium transition-colors border-b-2 -mb-px ${
              activeTab === key
                ? "border-primary text-primary"
                : "border-transparent text-muted-foreground hover:text-foreground hover:border-border"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      {activeTab === "overview" && (
        <div className="grid gap-3">
          {[
            { label: "Source",    value: doc.source ? doc.source.split("/").pop()! : "—" },
            { label: "Kind",      value: doc.kind ?? "—" },
            { label: "Readiness", value: readiness },
            { label: "Words",     value: doc.word_count ? doc.word_count.toLocaleString() : "—" },
            { label: "Imported",  value: doc.created_at ? format(new Date(doc.created_at), "PPP") : "—" },
            { label: "SHA-256",   value: doc.sha256 ?? "—" },
          ].map(({ label, value }) => (
            <div
              key={label}
              className="flex items-start justify-between py-2.5 px-4 rounded-lg bg-muted/20 border border-border/40"
            >
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide w-24 shrink-0">
                {label}
              </span>
              <span className="text-sm font-mono text-right break-all ml-4">{String(value)}</span>
            </div>
          ))}
          {/* Work assignment row */}
          <div className="flex items-center justify-between py-2.5 px-4 rounded-lg bg-muted/20 border border-border/40">
            <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide w-24 shrink-0 flex items-center gap-1.5">
              <Link2 className="w-3 h-3" /> Work
            </span>
            <Select
              value={doc.work_id ?? "__none__"}
              onValueChange={handleAssignWork}
              disabled={updateDoc.isPending}
            >
              <SelectTrigger className="h-7 text-xs font-mono w-auto max-w-[260px] border-border/40 bg-background/50">
                <SelectValue placeholder="Unlinked" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="__none__" className="text-xs font-mono text-muted-foreground">
                  — Unlinked —
                </SelectItem>
                {allWorks.map((w) => (
                  <SelectItem key={w.id!} value={w.id!} className="text-xs font-mono">
                    {w.title ?? w.id}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
      )}

      {activeTab === "text" && (
        <div>
          {doc.extracted_text ? (
            <div className="bg-muted/20 border border-border/50 rounded-lg p-5 max-h-[60vh] overflow-y-auto">
              <pre className="text-sm font-mono whitespace-pre-wrap leading-relaxed text-foreground/80">
                {doc.extracted_text}
              </pre>
            </div>
          ) : (
            <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg">
              <BookOpen className="w-8 h-8 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground">
                {readiness === "imported"
                  ? "Extraction is still in progress — check back in a moment."
                  : readiness === "error"
                  ? "Extraction failed. Use Re-extract to try again."
                  : "No text was extracted from this document."}
              </p>
            </div>
          )}
        </div>
      )}

      {activeTab === "knowledge" && (
        <KnowledgeTabContent
          knLoading={knLoading}
          items={knData?.knowledge ?? []}
          docWorkId={doc?.work_id}
          knFilter={knFilter}
          setKnFilter={setKnFilter}
          reviewing={reviewing}
          onReview={handleReview}
          onDelete={handleDeleteKnowledge}
        />
      )}
    </div>
  );
}
