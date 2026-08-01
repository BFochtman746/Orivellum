/**
 * Document detail page — /library/:docId
 *
 * Shows metadata, full extracted text, and all knowledge items
 * harvested from this specific document.
 */
import { useState } from "react";
import { useParams, useLocation } from "wouter";
import { useGetDocument, useDeleteDocument } from "@workspace/api-client-react";
import { useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft, FileText, AlertCircle, CheckCircle2, Clock,
  FileQuestion, RefreshCw, Trash2, Hash, Calendar, Database,
  BookOpen, Cpu, Sparkles,
} from "lucide-react";
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

// ── Reprocess helper ──────────────────────────────────────────────────────────

async function reprocessDoc(docId: string): Promise<void> {
  const resp = await fetch(`${BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function DocumentDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [, navigate] = useLocation();
  const [activeTab, setActiveTab] = useState<Tab>("overview");
  const [reprocessing, setReprocessing] = useState(false);

  const { data, isLoading, error, refetch } = useGetDocument(docId ?? "");
  const deleteDoc = useDeleteDocument();

  const doc = data?.document as any;

  // Knowledge items for this document
  const { data: knData, isLoading: knLoading } = useQuery<{ knowledge: KnowledgeItem[]; count: number }>({
    queryKey: ["doc-knowledge", docId],
    queryFn: () => fetch(`${BASE}/library/${docId}/knowledge`).then((r) => r.json()),
    enabled: !!docId && activeTab === "knowledge",
    staleTime: 30_000,
  });

  const handleReprocess = async () => {
    if (!docId) return;
    setReprocessing(true);
    try {
      await reprocessDoc(docId);
      toast.success("Re-extraction queued — refresh in a moment.");
      setTimeout(() => refetch(), 3000);
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
            <span className="flex items-center gap-1.5">
              <Database className="w-3 h-3" />
              Linked to Work
            </span>
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
            { label: "Source",    value: doc.source ?? "—" },
            { label: "Kind",      value: doc.kind ?? "—" },
            { label: "Readiness", value: readiness },
            { label: "Words",     value: doc.word_count ? doc.word_count.toLocaleString() : "—" },
            { label: "Work",      value: doc.work_id ?? "Unlinked" },
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
        <div className="space-y-3">
          {knLoading ? (
            [1, 2, 3].map((i) => <Skeleton key={i} className="h-20 w-full" />)
          ) : (knData?.knowledge ?? []).length === 0 ? (
            <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg">
              <Cpu className="w-8 h-8 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground">No knowledge items extracted from this document yet.</p>
              {doc.work_id && (
                <p className="text-xs text-muted-foreground mt-1">
                  Knowledge extraction runs automatically after import.
                </p>
              )}
            </div>
          ) : (
            <>
              <p className="text-xs font-mono text-muted-foreground">
                {knData!.count} item{knData!.count !== 1 ? "s" : ""} extracted
              </p>
              {knData!.knowledge.map((item) => (
                <Card key={item.id} className="hover-elevate">
                  <CardContent className="p-4">
                    <div className="flex items-start justify-between gap-4">
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
                      {item.confidence != null && (
                        <div className="text-xs font-mono px-2 py-1 bg-muted rounded shrink-0">
                          {(item.confidence * 100).toFixed(0)}%
                        </div>
                      )}
                    </div>
                  </CardContent>
                </Card>
              ))}
            </>
          )}
        </div>
      )}
    </div>
  );
}
