/**
 * Document detail page — /library/:docId
 *
 * Shows metadata, full extracted text, and all knowledge items
 * harvested from this specific document.
 */
import { useState, useRef, useEffect } from "react";
import { useParams, useLocation } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import { useGetDocument, useDeleteDocument, useGetWork, useListWorks, getGetDocumentQueryKey, getGetWorkQueryKey } from "@workspace/api-client-react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import { Separator } from "@/components/ui/separator";
import {
  ArrowLeft, FileText, AlertCircle, CheckCircle2, Clock,
  FileQuestion, RefreshCw, Trash2, Hash, Calendar, Database,
  BookOpen, Cpu, Sparkles, ThumbsUp, ThumbsDown, Link2, Info,
  List, History, Star, GitBranch, ChevronDown,
  BookHeadphones, Loader2, Play, Pause, X,
} from "lucide-react";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "overview" | "text" | "knowledge" | "chapters" | "versions";

const AI_KINDS = ["entity", "claim", "relationship"] as const;
type AiKind = (typeof AI_KINDS)[number];

const AI_KIND_LABELS: Record<AiKind, string> = {
  entity:       "Entities",
  claim:        "Claims",
  relationship: "Relationships",
};

interface KnowledgeItem {
  id: string;
  kind: string;
  text: string;
  subject?: string | null;
  predicate?: string | null;
  object?: string | null;
  confidence?: number | null;
  review_status?: string | null;
  meta?: { source?: string } | null;
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

// ── Confidence bar ────────────────────────────────────────────────────────────

function confidenceTier(pct: number): { label: string; color: string } {
  if (pct >= 80) return { label: "High confidence",   color: "bg-emerald-500" };
  if (pct >= 50) return { label: "Medium confidence", color: "bg-amber-400" };
  return               { label: "Low confidence",    color: "bg-orange-400" };
}

function ConfidenceBar({ value, source }: { value: number; source?: string }) {
  const pct = Math.round(value * 100);
  const { label, color } = confidenceTier(pct);
  const origin = source === "llm" ? "LLM extraction" : source === "rule" ? "Rule-based" : null;
  const tipText = origin ? `${pct}% — ${label} · ${origin}` : `${pct}% — ${label}`;

  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-1.5 shrink-0 cursor-default">
            <div className="w-14 h-1.5 bg-muted rounded-full overflow-hidden">
              <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
            </div>
            <span className="text-[10px] font-mono text-muted-foreground w-7 text-right">{pct}%</span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-xs">
          {tipText}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Confidence legend info button ─────────────────────────────────────────────

function ConfidenceLegend() {
  return (
    <TooltipProvider delayDuration={200}>
      <Tooltip>
        <TooltipTrigger asChild>
          <button className="p-0.5 rounded text-muted-foreground/60 hover:text-muted-foreground transition-colors">
            <Info className="w-3.5 h-3.5" />
          </button>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-64 space-y-1.5 py-2">
          <p className="font-semibold text-xs mb-1">Confidence score</p>
          <div className="space-y-1 text-[11px]">
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
              <span>≥ 80% — High · typically LLM-extracted</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
              <span>50–79% — Medium · sentence or heading match</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-orange-400 shrink-0" />
              <span>{"< 50% — Low · heuristic noun-phrase mention"}</span>
            </div>
          </div>
          <p className="text-[10px] text-muted-foreground/80 pt-0.5 border-t border-border/40">
            LLM items are extracted by AI; rule-based items use pattern matching.
          </p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}

// ── Confidence-tier helpers ───────────────────────────────────────────────────

type ConfTier = "all" | "high" | "medium" | "low";

function itemConfTier(item: KnowledgeItem): "high" | "medium" | "low" {
  if (item.confidence == null) return "low";
  const pct = Math.round(item.confidence * 100);
  if (pct >= 80) return "high";
  if (pct >= 60) return "medium";
  return "low";
}

const CONF_FILTERS: { key: ConfTier; label: string; dot: string }[] = [
  { key: "all",    label: "All",    dot: "" },
  { key: "high",   label: "High",   dot: "bg-emerald-500" },
  { key: "medium", label: "Medium", dot: "bg-amber-400" },
  { key: "low",    label: "Low",    dot: "bg-orange-400" },
];

// ── AI-extracted knowledge section ────────────────────────────────────────────

function AiKindSection({
  kind,
  items,
  reviewing,
  onReview,
  onDelete,
}: {
  kind: AiKind;
  items: KnowledgeItem[];
  reviewing: string | null;
  onReview: (id: string, status: "approved" | "rejected") => void;
  onDelete: (id: string) => void;
}) {
  if (items.length === 0) return null;
  return (
    <div>
      <h4 className="text-[11px] font-mono font-semibold uppercase tracking-widest text-muted-foreground mb-2 flex items-center gap-2">
        {AI_KIND_LABELS[kind]}
        <span className="px-1.5 py-0.5 rounded bg-muted text-[10px] normal-case tracking-normal font-normal">
          {items.length}
        </span>
      </h4>
      <div className="space-y-2">
        {items.map((item) => {
          const isApproved = item.review_status === "approved";
          const isRejected = item.review_status === "rejected";
          const isReviewing = reviewing === item.id;
          return (
            <div
              key={item.id}
              className={`group flex items-start gap-3 p-3 rounded-lg border bg-violet-50/40 border-violet-100 transition-opacity ${isRejected ? "opacity-50" : ""}`}
            >
              <div className="flex-1 min-w-0">
                {kind === "relationship" && item.subject && item.predicate && item.object ? (
                  <p className="text-sm font-mono">
                    <span className="font-semibold text-primary">{item.subject}</span>
                    {" "}<span className="text-muted-foreground">{item.predicate}</span>{" "}
                    <span className="font-semibold">{item.object}</span>
                  </p>
                ) : (
                  <p className="text-sm leading-snug">{item.text}</p>
                )}
              </div>
              <div className="flex items-center gap-1 shrink-0">
                {item.confidence != null && <ConfidenceBar value={item.confidence} source="llm" />}
                <ReviewBadge status={item.review_status} />
                {(item.review_status === "ai_auto" || isApproved || isRejected) && (
                  <>
                    <button
                      disabled={isReviewing || isApproved}
                      onClick={() => onReview(item.id, "approved")}
                      title="Approve"
                      className={`p-1 rounded transition-colors ${isApproved ? "text-emerald-600 bg-emerald-50" : "text-muted-foreground hover:text-emerald-600 hover:bg-emerald-50"} disabled:opacity-40`}
                    >
                      <ThumbsUp className="w-3 h-3" />
                    </button>
                    <button
                      disabled={isReviewing || isRejected}
                      onClick={() => onReview(item.id, "rejected")}
                      title="Dismiss"
                      className={`p-1 rounded transition-colors ${isRejected ? "text-red-600 bg-red-50" : "text-muted-foreground hover:text-red-600 hover:bg-red-50"} disabled:opacity-40`}
                    >
                      <ThumbsDown className="w-3 h-3" />
                    </button>
                  </>
                )}
                <button
                  onClick={() => onDelete(item.id)}
                  title="Delete"
                  className="p-1 rounded text-muted-foreground/30 hover:text-destructive hover:bg-destructive/5 transition-colors opacity-0 group-hover:opacity-100"
                >
                  <Trash2 className="w-3 h-3" />
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ── Knowledge tab content ─────────────────────────────────────────────────────

type KnFilter = "all" | "pending" | "approved" | "rejected";

function KnowledgeTabContent({
  knLoading,
  items,
  docWorkId,
  docReadiness,
  aiEnabled,
  knFilter,
  setKnFilter,
  reviewing,
  onReview,
  onDelete,
}: {
  knLoading: boolean;
  items: KnowledgeItem[];
  docWorkId?: string | null;
  docReadiness?: string;
  aiEnabled: boolean;
  knFilter: KnFilter;
  setKnFilter: (f: KnFilter) => void;
  reviewing: string | null;
  onReview: (id: string, status: "approved" | "rejected") => void;
  onDelete: (id: string) => void;
}) {
  const [aiConfFilter, setAiConfFilter] = useState<ConfTier>("all");
  if (knLoading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => <Skeleton key={i} className="h-20 w-full" />)}
      </div>
    );
  }

  // Split items: AI-extracted vs rule-based.
  // Primary provenance: meta.source === "llm" (durable, survives review status changes).
  // Fallback for items created before meta provenance: review_status === "ai_auto".
  // Always split regardless of aiEnabled — stored AI items must always be visible.
  const isAiProvenance = (k: KnowledgeItem) =>
    k.meta?.source === "llm" || k.review_status === "ai_auto";
  const aiItems = items.filter((k) => (AI_KINDS as readonly string[]).includes(k.kind) && isAiProvenance(k));
  const ruleItems = items.filter((k) => !aiItems.includes(k));

  // Only use the generic empty state when both sections would be empty AND AI is
  // disabled (so there's no AI section to show at all).
  if (items.length === 0 && !aiEnabled) {
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

  const pendingCount = ruleItems.filter((k) => k.review_status === "ai_auto").length;
  const visibleRule = ruleItems.filter((k) => {
    if (knFilter === "pending")  return k.review_status === "ai_auto";
    if (knFilter === "approved") return k.review_status === "approved";
    if (knFilter === "rejected") return k.review_status === "rejected";
    return true;
  });
  const KN_FILTERS: { key: KnFilter; label: string }[] = [
    { key: "all",      label: `All (${ruleItems.length})` },
    { key: "pending",  label: `AI Review${pendingCount > 0 ? ` (${pendingCount})` : ""}` },
    { key: "approved", label: "Approved" },
    { key: "rejected", label: "Dismissed" },
  ];

  // Apply confidence-tier filter to AI items
  const filteredAiItems = aiConfFilter === "all"
    ? aiItems
    : aiItems.filter((item) => itemConfTier(item) === aiConfFilter);

  return (
    <div className="space-y-8">
      {/* ── AI-Extracted Knowledge ─────────────────────────────────────── */}
      {/* Show whenever items exist (always display stored AI knowledge) OR
          when AI is enabled (show status/empty-state prompt to the user). */}
      {(aiItems.length > 0 || aiEnabled) && (
        <div>
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <Sparkles className="w-4 h-4 text-violet-500" />
            <h3 className="text-sm font-semibold text-violet-700">AI-Extracted Knowledge</h3>
            {aiItems.length > 0 && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-violet-100 text-violet-700">
                {aiConfFilter === "all"
                  ? `${aiItems.length} item${aiItems.length !== 1 ? "s" : ""}`
                  : `${filteredAiItems.length} / ${aiItems.length}`}
              </span>
            )}
            <ConfidenceLegend />
            {/* Confidence filter chips — only shown when there are AI items */}
            {aiItems.length > 0 && (
              <div className="ml-auto flex items-center gap-1 p-1 bg-violet-50 border border-violet-100 rounded-lg">
                {CONF_FILTERS.map(({ key, label, dot }) => (
                  <button
                    key={key}
                    onClick={() => setAiConfFilter(key)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
                      aiConfFilter === key
                        ? "bg-white text-violet-700 shadow-sm font-semibold"
                        : "text-muted-foreground hover:text-violet-700"
                    }`}
                  >
                    {dot && (
                      <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${dot}`} />
                    )}
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {aiItems.length === 0 ? (
            <div className="py-8 border border-dashed border-violet-200 rounded-lg bg-violet-50/30 text-center">
              <Sparkles className="w-6 h-6 text-violet-300 mx-auto mb-2" />
              <p className="text-sm text-muted-foreground">
                {!aiEnabled
                  ? "Enable AI extraction in System settings to extract entities, claims, and relationships."
                  : docReadiness === "ready"
                  ? "No AI-extracted items found for this document."
                  : "AI extraction will run once the document is fully processed."}
              </p>
            </div>
          ) : filteredAiItems.length === 0 ? (
            <div className="py-6 border border-dashed border-violet-200 rounded-lg bg-violet-50/20 text-center">
              <p className="text-sm text-muted-foreground">
                No {aiConfFilter}-confidence items found.{" "}
                <button
                  onClick={() => setAiConfFilter("all")}
                  className="underline text-violet-600 hover:text-violet-700"
                >
                  Show all
                </button>
              </p>
            </div>
          ) : (
            <div className="space-y-5 pl-1">
              {AI_KINDS.map((k) => (
                <AiKindSection
                  key={k}
                  kind={k}
                  items={filteredAiItems.filter((item) => item.kind === k)}
                  reviewing={reviewing}
                  onReview={onReview}
                  onDelete={onDelete}
                />
              ))}
            </div>
          )}
        </div>
      )}

      {/* ── Rule-based Knowledge ───────────────────────────────────────── */}
      <div>
        {aiEnabled && ruleItems.length > 0 && (
          <div className="flex items-center gap-2 mb-4">
            <Cpu className="w-4 h-4 text-muted-foreground" />
            <h3 className="text-sm font-semibold text-muted-foreground">Rule-Based Extraction</h3>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-muted text-muted-foreground">
              {ruleItems.length}
            </span>
          </div>
        )}

        {ruleItems.length === 0 ? null : (
          <>
            <div className="flex items-center justify-between flex-wrap gap-2 mb-3">
              <p className="text-xs font-mono text-muted-foreground">
                {ruleItems.length} item{ruleItems.length !== 1 ? "s" : ""}
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
            <div className="space-y-3">
              {visibleRule.map((item) => {
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
                          {item.confidence != null && <ConfidenceBar value={item.confidence} source="rule" />}
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
          </>
        )}
      </div>
    </div>
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

// ── Main page ─────────────────────────────────────────────────────────────────

async function setKnowledgeReview(itemId: string, status: string): Promise<void> {
  const resp = await apiFetch(`${BASE}/knowledge/${itemId}/review`, {
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
  // Read Aloud (TTS) state
  const [ttsLoading, setTtsLoading] = useState(false);
  const [ttsAudioUrl, setTtsAudioUrl] = useState<string | null>(null);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);
  // Keep the latest object URL in a ref so the unmount cleanup never sees a stale value
  const ttsUrlRef = useRef<string | null>(null);
  ttsUrlRef.current = ttsAudioUrl;
  useEffect(() => {
    return () => {
      ttsAudioRef.current?.pause();
      if (ttsUrlRef.current) URL.revokeObjectURL(ttsUrlRef.current);
    };
  }, []);
  const queryClient = useQueryClient();

  const { data, isLoading, error, refetch } = useGetDocument(docId ?? "", {
    query: {
      queryKey: getGetDocumentQueryKey(docId ?? ""),
      // Auto-poll every 3 s while the document is still being processed
      refetchInterval: (query) => {
        const r = (query.state.data?.document as any)?.readiness;
        return r === "imported" ? 3_000 : false;
      },
    },
  });
  const deleteDoc = useDeleteDocument();

  const doc = data?.document as any;
  const workId = doc?.work_id as string | undefined;

  // Resolve work title when this document is linked to a work
  const { data: workData } = useGetWork(workId ?? "", {
    query: { queryKey: getGetWorkQueryKey(workId ?? ""), enabled: !!workId },
  });

  // Knowledge items for this document
  const { data: knData, isLoading: knLoading } = useQuery<{ knowledge: KnowledgeItem[]; count: number }>({
    queryKey: ["doc-knowledge", docId],
    queryFn: () => apiFetch(`${BASE}/library/${docId}/knowledge`).then((r) => r.json()),
    enabled: !!docId && activeTab === "knowledge",
    staleTime: 30_000,
  });

  // Chapter/section structure for this document
  const { data: chapData, isLoading: chapLoading } = useQuery<{
    chapters: Array<{ id: string; seq: number; title: string; word_count: number; status: string; extraction_method: string }>;
    count: number;
  }>({
    queryKey: ["doc-chapters", docId],
    queryFn: () => apiFetch(`${BASE}/library/${docId}/chapters`).then((r) => r.json()),
    enabled: !!docId && activeTab === "chapters",
    staleTime: 60_000,
  });

  // AI extraction setting — used to gate the AI section in the knowledge tab
  const { data: aiExtData } = useQuery<{ enabled: boolean }>({
    queryKey: ["system", "ai-extraction"],
    queryFn: () => apiFetch(`${BASE}/system/settings/ai-extraction`).then((r) => r.json()),
    staleTime: 60_000,
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

  // Work assignment — PATCH /api/library/:docId
  const updateDoc = useMutation<void, Error, { work_id: string | null }>({
    mutationFn: (body) =>
      apiFetch(`${BASE}/library/${docId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }).then((r) => { if (!r.ok) throw new Error("Update failed"); }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(docId!) });
    },
  });
  const { data: worksResp } = useListWorks();

  // Versions for this document (must stay above the early returns — hooks
  // may never run conditionally)
  const { data: versData, isLoading: versLoading, refetch: versRefetch } = useQuery<{
    versions: Array<{ id: string; version_num: number; sha256: string | null; word_count: number; notes: string | null; is_canonical: boolean; created_at: string }>;
    count: number;
  }>({
    queryKey: ["doc-versions", docId],
    queryFn: () => apiFetch(`${BASE}/library/${docId}/versions`).then((r) => r.json()),
    enabled: !!docId && activeTab === "versions",
    staleTime: 60_000,
  });
  const allWorks = worksResp?.works ?? [];
  const handleAssignWork = (newWorkId: string) => {
    const val = newWorkId === "__none__" ? null : newWorkId;
    updateDoc.mutate(
      { work_id: val },
      {
        onSuccess: () => toast.success(val ? "Document linked to work" : "Work link removed"),
        onError: () => toast.error("Could not update document"),
      }
    );
  };

  // Delete a knowledge item — DELETE /api/knowledge/:itemId
  const deleteKnowledge = useMutation<void, Error, string>({
    mutationFn: (itemId) =>
      apiFetch(`${BASE}/knowledge/${itemId}`, { method: "DELETE" }).then((r) => {
        if (!r.ok) throw new Error("Delete failed");
      }),
  });
  const handleDeleteKnowledge = (itemId: string) => {
    if (!window.confirm("Delete this knowledge item?")) return;
    deleteKnowledge.mutate(itemId, {
      onSuccess: () => {
        toast.success("Knowledge item deleted");
        queryClient.invalidateQueries({ queryKey: ["doc-knowledge", docId] });
      },
      onError: () => toast.error("Could not delete item"),
    });
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

  // ── Read Aloud (TTS) ──────────────────────────────────────────────────────
  const TTS_MAX_CHARS = 5000;

  const handleReadAloud = async () => {
    if (!docId || ttsLoading) return;
    setTtsLoading(true);
    setTtsPlaying(false);
    // Release any previous blob URL
    if (ttsAudioUrl) {
      URL.revokeObjectURL(ttsAudioUrl);
      setTtsAudioUrl(null);
    }
    try {
      // Prefer the already-extracted text on the doc object; fall back to chunks.
      let text: string = (doc?.extracted_text as string) || "";
      if (!text.trim()) {
        const resp = await apiFetch(`${BASE}/library/${docId}/chunks`);
        if (resp.ok) {
          const data = await resp.json();
          text = (data.chunks ?? [])
            .map((c: any) => c.text ?? "")
            .filter(Boolean)
            .join("\n\n");
        }
      }
      text = text.trim();
      if (!text) {
        toast.error("No extracted text available to read aloud.");
        return;
      }

      const truncated = text.length > TTS_MAX_CHARS;
      const payloadText = truncated ? text.slice(0, TTS_MAX_CHARS) : text;

      const ttsResp = await apiFetch(`${BASE}/studio/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: payloadText, voice: "af_heart", speed: 1.0 }),
      });
      if (!ttsResp.ok) {
        const err = await ttsResp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${ttsResp.status}`);
      }
      const blob = await ttsResp.blob();
      const url = URL.createObjectURL(blob);
      setTtsAudioUrl(url);
      if (truncated) {
        toast.info("Long document — reading first part.");
      }
      // Do NOT autoplay — iOS Safari blocks audio from async code.
      // The audio player below has native controls; user taps play.
    } catch (e: any) {
      toast.error(`Read aloud failed: ${e.message ?? "unknown error"}`, { duration: 8000 });
    } finally {
      setTtsLoading(false);
    }
  };

  const toggleTtsPlay = () => {
    const el = ttsAudioRef.current;
    if (!el) return;
    if (ttsPlaying) {
      el.pause();
      setTtsPlaying(false);
    } else {
      el.play().catch(() => {});
      setTtsPlaying(true);
    }
  };

  const closeTtsPlayer = () => {
    ttsAudioRef.current?.pause();
    setTtsPlaying(false);
    if (ttsAudioUrl) URL.revokeObjectURL(ttsAudioUrl);
    setTtsAudioUrl(null);
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
  const docLifecycle: string = (doc as any).lifecycle ?? "draft";

  const lifecycleOptions = [
    { value: "draft",      label: "Draft",      className: "text-muted-foreground" },
    { value: "canonical",  label: "Canonical",  className: "text-amber-700 font-semibold" },
    { value: "reference",  label: "Reference",  className: "text-blue-700" },
    { value: "superseded", label: "Superseded", className: "text-muted-foreground" },
  ] as const;

  const handleSetLifecycle = async (lc: string) => {
    const resp = await apiFetch(`${BASE}/library/${docId}/lifecycle`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lifecycle: lc }),
    });
    if (resp.ok) {
      toast.success(`Lifecycle set to "${lc}"`);
      queryClient.invalidateQueries({ queryKey: getGetDocumentQueryKey(docId!) });
    } else {
      toast.error("Could not update lifecycle");
    }
  };

  const tabs: { key: Tab; label: string; icon: React.ElementType; badge?: number }[] = [
    { key: "overview",  label: "Overview",  icon: FileText },
    { key: "chapters",  label: "Chapters",  icon: List,    badge: chapData?.count ?? 0 },
    { key: "versions",  label: "Versions",  icon: History },
    { key: "text",      label: "Text",      icon: BookOpen },
    { key: "knowledge", label: "Knowledge", icon: Cpu },
  ];

  const snapshotVersion = async () => {
    try {
      await apiFetch(`${BASE}/library/${docId}/versions`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ notes: "Manual snapshot" }) });
      toast.success("Version snapshot saved");
      versRefetch();
    } catch {
      toast.error("Could not snapshot version");
    }
  };

  const setCanonical = async (versionId: string) => {
    try {
      await apiFetch(`${BASE}/library/${docId}/versions/${versionId}/canonical`, { method: "PATCH" });
      toast.success("Canonical version updated");
      versRefetch();
    } catch {
      toast.error("Could not set canonical");
    }
  };

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
            onClick={handleReadAloud}
            disabled={ttsLoading}
          >
            {ttsLoading ? (
              <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Preparing audio…</>
            ) : (
              <><BookHeadphones className="w-3.5 h-3.5 mr-1.5" /> Read Aloud</>
            )}
          </Button>
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
              {/* Lifecycle badge + inline picker */}
              <Select value={docLifecycle} onValueChange={handleSetLifecycle}>
                <SelectTrigger
                  className={`h-auto py-0.5 px-1.5 text-[10px] font-mono uppercase border rounded gap-1 w-auto min-w-0 focus:ring-0 shadow-none ${
                    docLifecycle === "canonical"
                      ? "bg-amber-50 border-amber-300 text-amber-800"
                      : docLifecycle === "reference"
                      ? "bg-blue-50 border-blue-200 text-blue-700"
                      : docLifecycle === "superseded"
                      ? "bg-muted/50 border-border text-muted-foreground"
                      : "bg-muted/30 border-border/50 text-muted-foreground"
                  }`}
                >
                  {docLifecycle === "canonical" && <Star className="w-2.5 h-2.5 text-amber-600" />}
                  <SelectValue />
                  <ChevronDown className="w-3 h-3 opacity-50" />
                </SelectTrigger>
                <SelectContent>
                  {lifecycleOptions.map((o) => (
                    <SelectItem key={o.value} value={o.value} className={`text-xs font-mono ${o.className}`}>
                      {o.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {doc.word_count > 0 && (
                <span className="text-xs font-mono text-muted-foreground">
                  {doc.word_count.toLocaleString()} words
                </span>
              )}
            </div>
          </div>
        </div>

        {/* Read Aloud audio player */}
        {ttsAudioUrl && (
          <div className="mt-3 flex items-center gap-3 p-3 rounded-lg bg-muted/30 border border-border/50">
            <Button
              size="icon"
              variant="secondary"
              className="h-9 w-9 rounded-full shrink-0"
              onClick={toggleTtsPlay}
            >
              {ttsPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
            </Button>
            <audio
              ref={ttsAudioRef}
              src={ttsAudioUrl}
              onEnded={() => setTtsPlaying(false)}
              onPause={() => setTtsPlaying(false)}
              onPlay={() => setTtsPlaying(true)}
              className="flex-1 h-8"
              controls
              style={{ minWidth: 0 }}
            />
            <Button
              size="icon"
              variant="ghost"
              className="shrink-0"
              onClick={closeTtsPlayer}
              title="Close audio player"
            >
              <X className="w-4 h-4" />
            </Button>
          </div>
        )}

        {/* Error banner */}
        {hasError && doc.error_message && (
          <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-lg">
            <p className="text-xs font-mono text-red-700 break-all">{doc.error_message}</p>
          </div>
        )}

        {/* Extraction warnings (non-fatal issues from the pipeline) */}
        {Array.isArray(doc.warnings) && doc.warnings.length > 0 && (
          <div className="mt-3 p-3 bg-amber-50 border border-amber-200 rounded-lg space-y-1">
            <p className="text-[10px] font-mono font-semibold uppercase tracking-wide text-amber-700 mb-1.5">
              Extraction warnings ({doc.warnings.length})
            </p>
            {(doc.warnings as any[]).map((w, i) => (
              <p key={i} className="text-xs font-mono text-amber-800 break-all">
                <span className="font-semibold">{w.kind}:</span> {w.detail}
              </p>
            ))}
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
        {tabs.map(({ key, label, icon: Icon, badge }) => (
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
            {badge != null && badge > 0 && (
              <span className="ml-0.5 px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-muted text-muted-foreground">
                {badge}
              </span>
            )}
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
          {/* ZIP manifest — show when meta.zip_members is available */}
          {doc.kind === "zip" && Array.isArray((doc as any).meta?.zip_members) && (
            <div className="rounded-lg border border-border/40 bg-muted/10 overflow-hidden">
              <div className="flex items-center justify-between px-4 py-2 border-b border-border/40 bg-muted/20">
                <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
                  Contents ({(doc as any).meta.zip_members.filter((m: any) => m.status !== "skipped").length} files)
                </span>
                <span className="text-xs font-mono text-muted-foreground">
                  {(doc as any).meta.zip_summary}
                </span>
              </div>
              <div className="divide-y divide-border/30 max-h-48 overflow-y-auto">
                {(doc as any).meta.zip_members
                  .filter((m: any) => m.status !== "skipped")
                  .map((m: any, i: number) => (
                    <div key={i} className="flex items-center gap-3 px-4 py-2">
                      <span className={`w-2 h-2 rounded-full shrink-0 ${
                        m.status === "ok" ? "bg-emerald-500" :
                        m.status === "empty" ? "bg-amber-400" : "bg-red-400"
                      }`} />
                      <span className="text-xs font-mono flex-1 truncate text-foreground/80">
                        {m.name.split("/").pop()}
                      </span>
                      <span className="text-[10px] font-mono text-muted-foreground shrink-0">
                        {m.reason}
                      </span>
                    </div>
                  ))}
              </div>
            </div>
          )}

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

      {activeTab === "versions" && (
        <div className="space-y-4">
          <div className="flex items-center justify-between">
            <p className="text-xs font-mono text-muted-foreground">
              {versData?.count ?? 0} version snapshot{(versData?.count ?? 0) !== 1 ? "s" : ""}
            </p>
            <Button size="sm" variant="outline" onClick={snapshotVersion} className="gap-1.5 text-xs">
              <History className="w-3.5 h-3.5" /> Save Snapshot
            </Button>
          </div>
          {versLoading ? (
            [1,2].map(i => <div key={i} className="h-14 rounded-lg bg-muted/30 animate-pulse" />)
          ) : (versData?.versions ?? []).length === 0 ? (
            <div className="text-center py-14 border border-dashed rounded-lg text-muted-foreground">
              <History className="w-7 h-7 mx-auto mb-3 opacity-40" />
              <p className="text-sm">No version snapshots yet.</p>
              <p className="text-xs opacity-60 mt-1">Click "Save Snapshot" to record the current state of this document.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {(versData?.versions ?? []).map((v) => (
                <div key={v.id} className={`flex items-center gap-4 p-3.5 rounded-lg border transition-colors ${v.is_canonical ? "border-primary/40 bg-primary/5" : "border-border/50 bg-muted/10"}`}>
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-0.5">
                      <span className="text-sm font-mono font-semibold">v{v.version_num}</span>
                      {v.is_canonical && (
                        <span className="flex items-center gap-1 text-[10px] font-mono px-1.5 py-0.5 rounded bg-primary/10 text-primary border border-primary/20">
                          <Star className="w-2.5 h-2.5" /> canonical
                        </span>
                      )}
                    </div>
                    <div className="flex items-center gap-3 text-[11px] font-mono text-muted-foreground">
                      <span>{v.word_count.toLocaleString()} words</span>
                      {v.sha256 && <span title={v.sha256}>{v.sha256.slice(0, 8)}…</span>}
                      {v.notes && <span className="italic">{v.notes}</span>}
                      <span>{v.created_at ? new Date(v.created_at).toLocaleDateString() : ""}</span>
                    </div>
                  </div>
                  {!v.is_canonical && (
                    <button
                      onClick={() => setCanonical(v.id)}
                      title="Mark as canonical"
                      className="text-xs font-mono text-muted-foreground hover:text-primary transition-colors shrink-0"
                    >
                      Mark canonical
                    </button>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {activeTab === "chapters" && (
        <div className="space-y-3">
          {chapLoading ? (
            [1,2,3].map((i) => <div key={i} className="h-16 rounded-lg bg-muted/30 animate-pulse" />)
          ) : (chapData?.chapters ?? []).length === 0 ? (
            <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg">
              <List className="w-8 h-8 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground text-sm">
                {readiness === "imported"
                  ? "Extraction in progress — chapters will appear when ready."
                  : "No chapter structure detected in this document."}
              </p>
              <p className="text-xs text-muted-foreground/60 mt-1">
                Chapter extraction works on DOCX, PDF, and Markdown files with headings.
              </p>
            </div>
          ) : (
            <>
              <p className="text-xs font-mono text-muted-foreground">
                {chapData!.count} section{chapData!.count !== 1 ? "s" : ""} detected
                {chapData!.chapters[0]?.extraction_method && (
                  <span className="ml-2 opacity-60">· {chapData!.chapters[0].extraction_method}</span>
                )}
              </p>
              {chapData!.chapters.map((ch) => (
                <div
                  key={ch.id}
                  className="flex items-start gap-4 p-4 rounded-lg border border-border/50 bg-muted/10 hover:bg-muted/20 transition-colors"
                >
                  <span className="text-xs font-mono text-muted-foreground/50 w-6 pt-0.5 shrink-0 text-right">
                    {ch.seq + 1}
                  </span>
                  <div className="flex-1 min-w-0">
                    <p className="font-medium text-sm leading-snug">{ch.title}</p>
                  </div>
                  <div className="shrink-0 text-[11px] font-mono text-muted-foreground">
                    {ch.word_count > 0 ? `${ch.word_count.toLocaleString()} words` : "empty"}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      )}

      {activeTab === "knowledge" && (
        <ErrorBoundary label="knowledge tab">
          <KnowledgeTabContent
            knLoading={knLoading}
            items={knData?.knowledge ?? []}
            docWorkId={doc?.work_id}
            docReadiness={readiness}
            aiEnabled={aiExtData?.enabled ?? false}
            knFilter={knFilter}
            setKnFilter={setKnFilter}
            reviewing={reviewing}
            onReview={handleReview}
            onDelete={handleDeleteKnowledge}
          />
        </ErrorBoundary>
      )}
    </div>
  );
}
