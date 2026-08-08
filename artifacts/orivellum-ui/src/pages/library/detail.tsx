/**
 * Document detail page — /library/:docId
 *
 * Shows metadata, full extracted text, and all knowledge items
 * harvested from this specific document.
 */
import { useState, useRef, useEffect, useMemo } from "react";
import { useParams, useLocation, useSearch } from "wouter";
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
  BookHeadphones, Loader2, Play, Pause, X, Download, Network, Search,
} from "lucide-react";
import {
  Tooltip, TooltipContent, TooltipProvider, TooltipTrigger,
} from "@/components/ui/tooltip";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { toast } from "sonner";
import { apiFetch } from "@/lib/auth";

const BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ── TTS voice + speed options ─────────────────────────────────────────────────

const TTS_VOICE_OPTIONS = [
  // American Female
  { id: "af_heart",   label: "Heart",   accent: "♀ US" },
  { id: "af_bella",   label: "Bella",   accent: "♀ US" },
  { id: "af_nova",    label: "Nova",    accent: "♀ US" },
  { id: "af_sarah",   label: "Sarah",   accent: "♀ US" },
  { id: "af_jessica", label: "Jessica", accent: "♀ US" },
  { id: "af_nicole",  label: "Nicole",  accent: "♀ US" },
  { id: "af_sky",     label: "Sky",     accent: "♀ US" },
  { id: "af_kore",    label: "Kore",    accent: "♀ US" },
  { id: "af_aoede",   label: "Aoede",   accent: "♀ US" },
  { id: "af_river",   label: "River",   accent: "♀ US" },
  // American Male
  { id: "am_adam",    label: "Adam",    accent: "♂ US" },
  { id: "am_echo",    label: "Echo",    accent: "♂ US" },
  { id: "am_eric",    label: "Eric",    accent: "♂ US" },
  { id: "am_liam",    label: "Liam",    accent: "♂ US" },
  { id: "am_onyx",    label: "Onyx",    accent: "♂ US" },
  { id: "am_fenrir",  label: "Fenrir",  accent: "♂ US" },
  { id: "am_puck",    label: "Puck",    accent: "♂ US" },
  // British Female
  { id: "bf_emma",     label: "Emma",     accent: "♀ UK" },
  { id: "bf_alice",    label: "Alice",    accent: "♀ UK" },
  { id: "bf_isabella", label: "Isabella", accent: "♀ UK" },
  { id: "bf_lily",     label: "Lily",     accent: "♀ UK" },
  // British Male
  { id: "bm_george", label: "George", accent: "♂ UK" },
  { id: "bm_daniel", label: "Daniel", accent: "♂ UK" },
  { id: "bm_fable",  label: "Fable",  accent: "♂ UK" },
  { id: "bm_lewis",  label: "Lewis",  accent: "♂ UK" },
] as const;

const TTS_SPEED_OPTIONS = [
  { value: 0.75, label: "0.75×" },
  { value: 1.0,  label: "1×" },
  { value: 1.25, label: "1.25×" },
  { value: 1.5,  label: "1.5×" },
] as const;

// ── Types ─────────────────────────────────────────────────────────────────────

type Tab = "overview" | "text" | "knowledge" | "chapters" | "versions" | "chunks" | "related";

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

function formatBytes(b: number): string {
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${(b / 1024).toFixed(1)} KB`;
  return `${(b / (1024 * 1024)).toFixed(1)} MB`;
}

/**
 * Estimate audiobook generation time from word count.
 *
 * Model: narration runs at ~150 wpm; Kokoro synthesises at ~4× real-time;
 * the TTS speed setting linearly scales the amount of audio produced.
 *
 *   audio_seconds  = wordCount / (150 wpm / 60)  = wordCount × 0.4 s
 *   synth_seconds  = audio_seconds / 4 / speed
 *
 * Returns a human-readable string like "~30 sec" or "~4 min", or null when
 * the word count is unknown.
 */
function audiobookTimeEstimate(wordCount: number, speed: number): string | null {
  if (!wordCount || wordCount <= 0) return null;
  const audioSecs = (wordCount / 150) * 60;
  const synthSecs = audioSecs / 4 / Math.max(speed, 0.25);
  if (synthSecs < 45)   return "~30 sec";
  if (synthSecs < 90)   return "~1 min";
  const mins = Math.ceil(synthSecs / 60);
  return `~${mins} min`;
}

// ── Editable doc title ────────────────────────────────────────────────────────

function TextSearchableContent({ text }: { text: string }) {
  const [q, setQ] = useState("");
  const query = q.trim().toLowerCase();
  const matchCount = query
    ? (text.toLowerCase().match(new RegExp(query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "g")) ?? []).length
    : 0;

  const highlighted = useMemo(() => {
    if (!query) return [{ text, match: false }];
    const escaped = query.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const parts = text.split(new RegExp(`(${escaped})`, "gi"));
    return parts.map((p) => ({ text: p, match: p.toLowerCase() === query }));
  }, [text, query]);

  return (
    <div className="space-y-2">
      <div className="flex items-center gap-2">
        <input
          type="search"
          placeholder="Search within text…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          className="h-8 flex-1 rounded-md border border-input bg-background px-3 text-sm outline-none focus:ring-1 focus:ring-ring font-mono"
        />
        {query && (
          <span className="text-xs font-mono text-muted-foreground shrink-0">
            {matchCount} match{matchCount !== 1 ? "es" : ""}
          </span>
        )}
      </div>
      <div className="bg-muted/20 border border-border/50 rounded-lg p-5 max-h-[60vh] overflow-y-auto">
        <pre className="text-sm font-mono whitespace-pre-wrap leading-relaxed text-foreground/80">
          {highlighted.map((part: { text: string; match: boolean }, i: number) =>
            part.match ? (
              <mark key={i} className="text-foreground rounded-[2px]" style={{ background: "color-mix(in srgb, var(--gilt) 28%, transparent)" }}>{part.text}</mark>
            ) : (
              <span key={i}>{part.text}</span>
            )
          )}
        </pre>
      </div>
    </div>
  );
}

function EditableTitle({ docId: _docId, title, onSave }: { docId: string; title: string; onSave: (t: string) => void }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);
  const commit = () => {
    setEditing(false);
    if (value.trim() && value.trim() !== title) onSave(value.trim());
    else setValue(title);
  };
  if (editing) {
    return (
      <input
        autoFocus
        className="text-2xl font-serif font-semibold tracking-tight w-full bg-transparent border-b-2 border-primary outline-none pb-0.5"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={commit}
        onKeyDown={(e) => { if (e.key === "Enter") commit(); if (e.key === "Escape") { setValue(title); setEditing(false); } }}
      />
    );
  }
  return (
    <h1
      className="text-2xl font-serif font-semibold tracking-tight truncate cursor-pointer hover:text-primary transition-colors"
      onDoubleClick={() => { setValue(title); setEditing(true); }}
      title="Double-click to edit title"
    >
      {title}
    </h1>
  );
}

// ── Readiness badge ───────────────────────────────────────────────────────────

const READINESS_CFG: Record<string, {
  label: string;
  Icon: React.ElementType;
  cls: string;
  style: React.CSSProperties;
}> = {
  ready:        {
    label: "READY",        Icon: CheckCircle2,
    cls: "", style: { color: "var(--green-2)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", background: "var(--green-soft)" },
  },
  imported:     {
    label: "PROCESSING",   Icon: Clock,
    cls: "", style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
  },
  transcribing: {
    label: "TRANSCRIBING", Icon: Clock,
    // No violet VELLUM token — gilt is the nearest processing-state equivalent
    cls: "", style: { color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" },
  },
  no_text:      {
    label: "NO TEXT",      Icon: FileQuestion,
    cls: "", style: { color: "var(--rust)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)" },
  },
  error:        {
    label: "ERROR",        Icon: AlertCircle,
    cls: "", style: { color: "var(--rust)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)" },
  },
};

function ReadinessBadge({ readiness }: { readiness: string }) {
  const cfg = READINESS_CFG[readiness] ?? READINESS_CFG.imported;
  const { Icon } = cfg;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-1 rounded text-xs font-mono font-medium border ${cfg.cls}`}
      style={cfg.style}
    >
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
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--gilt)", borderColor: "var(--gilt-line)", background: "var(--gilt-soft)" }}>
        <Sparkles className="w-2.5 h-2.5" />
        AI
      </span>
    );
  }
  if (status === "approved") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--green-2)", borderColor: "color-mix(in srgb, var(--green-2) 28%, transparent)", background: "var(--green-soft)" }}>
        ✓ approved
      </span>
    );
  }
  if (status === "rejected") {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-mono font-semibold border" style={{ color: "var(--rust)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)", background: "var(--rust-soft)" }}>
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
  if (pct >= 80) return { label: "High confidence",   color: "var(--green-2)" };
  if (pct >= 50) return { label: "Medium confidence", color: "var(--gilt)" };
  return               { label: "Low confidence",    color: "var(--rust)" };
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
              <div className="h-full rounded-full" style={{ width: `${pct}%`, background: color }} />
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
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--green-2)" }} />
              <span>≥ 80% — High · typically LLM-extracted</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--gilt)" }} />
              <span>50–79% — Medium · sentence or heading match</span>
            </div>
            <div className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full shrink-0" style={{ background: "var(--rust)" }} />
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
  { key: "high",   label: "High",   dot: "var(--green-2)" },
  { key: "medium", label: "Medium", dot: "var(--gilt)" },
  { key: "low",    label: "Low",    dot: "var(--rust)" },
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
  onReview: (id: string, status: "approved" | "rejected", force?: boolean) => void;
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
              data-item-id={item.id}
              className={`group flex items-start gap-3 p-3 rounded-lg border transition-opacity ${isRejected ? "opacity-50" : ""}`}
              style={{ background: "color-mix(in srgb, var(--gilt-soft) 60%, transparent)", borderColor: "var(--gilt-line)" }}
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
                      onClick={() => onReview(item.id, "approved", isRejected)}
                      title="Approve"
                      className={`p-1 rounded transition-colors disabled:opacity-40 ${
                        isApproved
                          ? "text-[var(--green-2)] bg-[var(--green-soft)]"
                          : "text-muted-foreground/50 hover:text-[var(--green-2)] hover:bg-[var(--green-soft)]"
                      }`}
                    >
                      <ThumbsUp className="w-3 h-3" />
                    </button>
                    <button
                      disabled={isReviewing || isRejected}
                      onClick={() => onReview(item.id, "rejected", isApproved)}
                      title="Dismiss"
                      className={`p-1 rounded transition-colors disabled:opacity-40 ${
                        isRejected
                          ? "text-[var(--rust)] bg-[var(--rust-soft)]"
                          : "text-muted-foreground/50 hover:text-[var(--rust)] hover:bg-[var(--rust-soft)]"
                      }`}
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

const KN_SEARCH_THRESHOLD = 50; // switch to API search above this count

function KnowledgeTabContent({
  knLoading,
  items,
  docId,
  docWorkId,
  docReadiness,
  aiEnabled,
  knFilter,
  setKnFilter,
  reviewing,
  onReview,
  onDelete,
  highlightItemId,
}: {
  knLoading: boolean;
  items: KnowledgeItem[];
  docId: string;
  docWorkId?: string | null;
  docReadiness?: string;
  aiEnabled: boolean;
  knFilter: KnFilter;
  setKnFilter: (f: KnFilter) => void;
  reviewing: string | null;
  onReview: (id: string, status: "approved" | "rejected", force?: boolean) => void;
  onDelete: (id: string) => void;
  highlightItemId?: string | null;
}) {
  const [aiConfFilter, setAiConfFilter] = useState<ConfTier>("all");
  const [knSearch, setKnSearch] = useState("");
  const [debouncedSearch, setDebouncedSearch] = useState("");
  const [apiSearching, setApiSearching] = useState(false);
  const [apiResults, setApiResults] = useState<KnowledgeItem[] | null>(null);

  // Scroll-to-item when arriving from the review queue with ?tab=knowledge&item=ID.
  // Waits for the knowledge data to have loaded before querying the DOM.
  useEffect(() => {
    if (!highlightItemId || knLoading) return;
    const timer = setTimeout(() => {
      const el = document.querySelector<HTMLElement>(`[data-item-id="${highlightItemId}"]`);
      if (!el) return;
      el.scrollIntoView({ behavior: "smooth", block: "center" });
      el.classList.add("chapter-highlight");
      setTimeout(() => el.classList.remove("chapter-highlight"), 2000);
    }, 300);
    return () => clearTimeout(timer);
  }, [highlightItemId, knLoading]);

  // Debounce search input by 300 ms
  useEffect(() => {
    const t = setTimeout(() => setDebouncedSearch(knSearch.trim()), 300);
    return () => clearTimeout(t);
  }, [knSearch]);

  // API search when items > threshold and there is a query
  const useApiSearch = items.length > KN_SEARCH_THRESHOLD && debouncedSearch.length > 0;
  useEffect(() => {
    if (!useApiSearch) {
      setApiResults(null);
      return;
    }
    let cancelled = false;
    setApiSearching(true);
    const params = new URLSearchParams({ q: debouncedSearch, doc_id: docId, limit: "50" });
    apiFetch(`${BASE}/knowledge/ask?${params}`)
      .then((r) => r.json())
      .then((data) => {
        if (!cancelled) setApiResults((data.knowledge ?? []) as KnowledgeItem[]);
      })
      .catch(() => { if (!cancelled) setApiResults([]); })
      .finally(() => { if (!cancelled) setApiSearching(false); });
    return () => { cancelled = true; };
  }, [useApiSearch, debouncedSearch, docId]);

  // Derive visible items: API results when in API mode, else client-side filter
  const query = knSearch.trim().toLowerCase();
  const clientFiltered = (useApiSearch || !query)
    ? items
    : items.filter((k) =>
        (k.text ?? "").toLowerCase().includes(query) ||
        (k.subject ?? "").toLowerCase().includes(query) ||
        (k.object ?? "").toLowerCase().includes(query)
      );
  const displayItems = useApiSearch ? (apiResults ?? []) : clientFiltered;

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
  // Use displayItems (API results or client-filtered) for the visible split.
  const isAiProvenance = (k: KnowledgeItem) =>
    k.meta?.source === "llm" || k.review_status === "ai_auto";
  const aiItems = displayItems.filter((k) => (AI_KINDS as readonly string[]).includes(k.kind) && isAiProvenance(k));
  const ruleItems = displayItems.filter((k) => !aiItems.includes(k));

  // Only use the generic empty state when the ORIGINAL items are empty AND AI is
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
      {/* ── Knowledge search ───────────────────────────────────────────── */}
      {items.length > 0 && (
        <div className="relative">
          <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground pointer-events-none" />
          <input
            type="search"
            placeholder={`Search knowledge${items.length > KN_SEARCH_THRESHOLD ? " (smart search)" : ""}…`}
            value={knSearch}
            onChange={(e) => setKnSearch(e.target.value)}
            className="h-8 w-full rounded-md border border-input bg-background pl-8 pr-8 text-sm outline-none focus:ring-1 focus:ring-ring font-mono"
          />
          {apiSearching && (
            <Loader2 className="absolute right-7 top-1/2 -translate-y-1/2 w-3.5 h-3.5 animate-spin text-muted-foreground" />
          )}
          {knSearch && (
            <button
              onClick={() => setKnSearch("")}
              className="absolute right-2 top-1/2 -translate-y-1/2 p-0.5 rounded text-muted-foreground hover:text-foreground"
              aria-label="Clear search"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
          {useApiSearch && !apiSearching && knSearch && (
            <span className="absolute right-7 top-1/2 -translate-y-1/2 text-[10px] font-mono px-1 rounded bg-primary/10 text-primary">
              API
            </span>
          )}
        </div>
      )}
      {/* Search no-results */}
      {knSearch && !apiSearching && displayItems.length === 0 && items.length > 0 && (
        <div className="text-center py-8 bg-muted/10 border border-dashed rounded-lg">
          <p className="text-sm text-muted-foreground">No knowledge items match "{knSearch}"</p>
          <button onClick={() => setKnSearch("")} className="mt-2 text-xs text-primary hover:underline">
            Clear search
          </button>
        </div>
      )}

      {/* ── AI-Extracted Knowledge ─────────────────────────────────────── */}
      {/* Show whenever items exist (always display stored AI knowledge) OR
          when AI is enabled (show status/empty-state prompt to the user). */}
      {(aiItems.length > 0 || aiEnabled) && (
        <div>
          <div className="flex items-center gap-2 mb-4 flex-wrap">
            <Sparkles className="w-4 h-4" style={{ color: "var(--gilt)" }} />
            <h3 className="text-sm font-semibold" style={{ color: "var(--gilt)" }}>AI-Extracted Knowledge</h3>
            {aiItems.length > 0 && (
              <span className="text-[10px] font-mono px-1.5 py-0.5 rounded" style={{ color: "var(--gilt)", background: "var(--gilt-soft)" }}>
                {aiConfFilter === "all"
                  ? `${aiItems.length} item${aiItems.length !== 1 ? "s" : ""}`
                  : `${filteredAiItems.length} / ${aiItems.length}`}
              </span>
            )}
            <ConfidenceLegend />
            {/* Confidence filter chips — only shown when there are AI items */}
            {aiItems.length > 0 && (
              <div className="ml-auto flex items-center gap-1 p-1 rounded-lg border" style={{ background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}>
                {CONF_FILTERS.map(({ key, label, dot }) => (
                  <button
                    key={key}
                    onClick={() => setAiConfFilter(key)}
                    className={`flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
                      aiConfFilter === key
                        ? "bg-white shadow-sm font-semibold"
                        : "text-muted-foreground"
                    }`}
                  >
                    {dot && (
                      <span className="w-1.5 h-1.5 rounded-full shrink-0" style={{ background: dot }} />
                    )}
                    {label}
                  </button>
                ))}
              </div>
            )}
          </div>

          {aiItems.length === 0 ? (
            <div className="py-8 border border-dashed rounded-lg text-center" style={{ borderColor: "var(--gilt-line)", background: "color-mix(in srgb, var(--gilt-soft) 40%, transparent)" }}>
              <Sparkles className="w-6 h-6 mx-auto mb-2" style={{ color: "var(--gilt)" }} />
              <p className="text-sm text-muted-foreground">
                {!aiEnabled
                  ? "Enable AI extraction in System settings to extract entities, claims, and relationships."
                  : docReadiness === "ready"
                  ? "No AI-extracted items found for this document."
                  : "AI extraction will run once the document is fully processed."}
              </p>
            </div>
          ) : filteredAiItems.length === 0 ? (
            <div className="py-6 border border-dashed rounded-lg text-center" style={{ borderColor: "var(--gilt-line)", background: "color-mix(in srgb, var(--gilt-soft) 30%, transparent)" }}>
              <p className="text-sm text-muted-foreground">
                No {aiConfFilter}-confidence items found.{" "}
                <button
                  onClick={() => setAiConfFilter("all")}
                  className="underline" style={{ color: "var(--gilt)" }}
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
                    }`}
                    style={key === "pending" && pendingCount > 0 ? { color: "var(--gilt)" } : undefined}
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
                  <Card key={item.id} data-item-id={item.id} className={`hover-elevate transition-opacity ${isRejected ? "opacity-50" : ""}`}>
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
                                className={`p-1.5 rounded transition-colors disabled:opacity-40 ${
                                  isApproved
                                    ? "text-[var(--green-2)] bg-[var(--green-soft)]"
                                    : "text-muted-foreground/50 hover:text-[var(--green-2)] hover:bg-[var(--green-soft)]"
                                }`}
                              >
                                <ThumbsUp className="w-3.5 h-3.5" />
                              </button>
                              <button
                                disabled={isReviewing || isRejected}
                                onClick={() => onReview(item.id, "rejected")}
                                title="Dismiss"
                                className={`p-1.5 rounded transition-colors disabled:opacity-40 ${
                                  isRejected
                                    ? "text-[var(--rust)] bg-[var(--rust-soft)]"
                                    : "text-muted-foreground/50 hover:text-[var(--rust)] hover:bg-[var(--rust-soft)]"
                                }`}
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

// ── Processing progress via SSE ───────────────────────────────────────────────

const _PROCESSING_STATES = new Set(["imported", "transcribing"]);
const _TERMINAL_STATES   = new Set(["ready", "error", "no_text"]);

type ProgressInfo = {
  stage: string;
  pct: number;
  items_found: number;
  readiness: string;
  chunk_count: number;
};

// ── Reprocess helper ──────────────────────────────────────────────────────────

async function reprocessDoc(docId: string): Promise<void> {
  const resp = await apiFetch(`${BASE}/library/${docId}/reprocess`, { method: "POST" });
  if (!resp.ok) {
    const err = await resp.json().catch(() => ({}));
    throw new Error((err as any).detail ?? "Reprocess failed");
  }
}

// ── Main page ─────────────────────────────────────────────────────────────────

async function setKnowledgeReview(itemId: string, status: string, force = false): Promise<void> {
  const resp = await apiFetch(`${BASE}/knowledge/${itemId}/review`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    // force is required to deliberately flip an already-finalized decision;
    // without it the API rejects stale/concurrent overwrites with 409.
    body: JSON.stringify({ review_status: status, force }),
  });
  if (!resp.ok) throw new Error("Review update failed");
}

export default function DocumentDetail() {
  const { docId } = useParams<{ docId: string }>();
  const [, navigate] = useLocation();
  // Deep-link: ?tab=<tab> switches to that tab on arrival; ?item=<id> highlights a knowledge item.
  const _libSearch = useSearch();
  const [activeTab, setActiveTab] = useState<Tab>(() => {
    const p = new URLSearchParams(_libSearch);
    return (p.get("tab") as Tab | null) ?? "overview";
  });
  const highlightKnItemId = useMemo(() => new URLSearchParams(_libSearch).get("item"), [_libSearch]);
  const [reprocessing, setReprocessing] = useState(false);
  const [reviewing, setReviewing] = useState<string | null>(null);
  const [knFilter, setKnFilter] = useState<"all" | "pending" | "approved" | "rejected">("all");
  // Read Aloud (TTS) state — long documents are split into parts and
  // synthesized one part at a time, auto-advancing during playback.
  const [ttsLoading, setTtsLoading] = useState(false);       // synthesizing the part about to play
  const [ttsAudioUrl, setTtsAudioUrl] = useState<string | null>(null);
  const [ttsPlaying, setTtsPlaying] = useState(false);
  const [ttsChunks, setTtsChunks] = useState<string[]>([]);
  const [ttsIndex, setTtsIndex] = useState(0);
  const ttsAudioRef = useRef<HTMLAudioElement | null>(null);
  const ttsUrlCacheRef = useRef<Map<number, string>>(new Map()); // part index → blob URL
  const ttsPromisesRef = useRef<Map<number, Promise<string>>>(new Map()); // in-flight single-flight
  const ttsAutoPlayRef = useRef(false); // play as soon as the next part's URL lands
  // Monotonic session id — bumped on close, new read, doc change, and unmount.
  // Any synthesis result from an older session is discarded (never cached).
  const ttsSessionRef = useRef(0);
  // Voice + speed — persisted to localStorage
  const TTS_LS_VOICE = "orivellum:tts_voice";
  const TTS_LS_SPEED = "orivellum:tts_speed";
  const [ttsVoice, setTtsVoice] = useState<string>(() => localStorage.getItem(TTS_LS_VOICE) ?? "af_heart");
  const [ttsSpeed, setTtsSpeed] = useState<number>(() => parseFloat(localStorage.getItem(TTS_LS_SPEED) ?? "1"));
  // Refs mirror state so async callbacks always read current values without stale closure
  const ttsVoiceRef = useRef(ttsVoice);
  const ttsSpeedRef = useRef(ttsSpeed);
  useEffect(() => {
    return () => {
      ttsSessionRef.current++;
      ttsAudioRef.current?.pause();
      for (const url of ttsUrlCacheRef.current.values()) URL.revokeObjectURL(url);
      ttsUrlCacheRef.current.clear();
      ttsPromisesRef.current.clear();
    };
  }, []);
  // Auto-continue playback when advancing to the next part
  useEffect(() => {
    if (ttsAutoPlayRef.current && ttsAudioUrl && ttsAudioRef.current) {
      ttsAutoPlayRef.current = false;
      ttsAudioRef.current.play().catch(() => setTtsPlaying(false));
    }
  }, [ttsAudioUrl]);
  const queryClient = useQueryClient();

  // SSE mode tracks whether the EventSource is active so polling is suppressed
  // while SSE is delivering live events.  Falls back to 4 s polling when SSE
  // is unavailable or the connection drops.
  const [sseMode, setSseMode] = useState<"active" | "fallback" | "off">("off");
  const [processingProgress, setProcessingProgress] = useState<ProgressInfo | null>(null);

  const { data, isLoading, error, refetch } = useGetDocument(docId ?? "", {
    query: {
      queryKey: getGetDocumentQueryKey(docId ?? ""),
      // Poll at 4 s only when SSE is unavailable (fallback mode).
      // While SSE is active we suppress polling to save bandwidth.
      refetchInterval: (query) => {
        const r = (query.state.data?.document as any)?.readiness;
        if (!r || !_PROCESSING_STATES.has(r)) return false;
        return sseMode === "fallback" ? 4_000 : false;
      },
    },
  });
  const deleteDoc = useDeleteDocument();

  const doc = data?.document as any;
  const workId = doc?.work_id as string | undefined;

  // ── SSE live-progress connection ─────────────────────────────────────────────
  // Opens an EventSource when the document is in a processing state.  Emits
  // live progress events every 500 ms; closes when the document reaches a
  // terminal state or the 5-minute server-side deadline fires.  Falls back
  // to 4-second polling (sseMode="fallback") if EventSource is unavailable
  // or the connection drops — the existing refetchInterval picks this up.
  useEffect(() => {
    const readiness = doc?.readiness as string | undefined;
    if (!docId || !readiness || !_PROCESSING_STATES.has(readiness)) {
      setProcessingProgress(null);
      setSseMode("off");
      return;
    }

    let es: EventSource | null = null;

    try {
      if (typeof EventSource === "undefined") throw new Error("No EventSource");
      es = new EventSource(`${BASE}/library/${docId}/progress`, { withCredentials: true });
      setSseMode("active");

      es.onmessage = (event) => {
        try {
          const evt: ProgressInfo = JSON.parse(event.data);
          setProcessingProgress(evt);
          if (_TERMINAL_STATES.has(evt.readiness)) {
            es?.close();
            setSseMode("off");
            refetch();
          }
        } catch {
          // malformed event — ignore
        }
      };

      es.onerror = () => {
        es?.close();
        setSseMode("fallback");  // activates the 4 s polling refetchInterval
      };
    } catch {
      setSseMode("fallback");
    }

    return () => {
      es?.close();
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId, doc?.readiness]);

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

  const handleReview = async (itemId: string, status: "approved" | "rejected", force = false) => {
    setReviewing(itemId);
    try {
      await setKnowledgeReview(itemId, status, force);
      toast.success(status === "approved" ? "Approved" : "Dismissed");
      queryClient.invalidateQueries({ queryKey: ["doc-knowledge", docId] });
    } catch {
      toast.error("Could not update review status");
    } finally {
      setReviewing(null);
    }
  };

  // Work assignment — PATCH /api/library/:docId
  const updateDoc = useMutation<void, Error, { work_id?: string | null; title?: string | null }>({
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

  // Extracted text chunks for this document
  const { data: chunksData, isLoading: chunksLoading } = useQuery<{ chunks: Array<{ id: string; page: number; text: string }>; count: number }>({
    queryKey: ["doc-chunks", docId],
    queryFn: () => apiFetch(`${BASE}/library/${docId}/chunks`).then((r) => r.json()),
    enabled: !!docId && activeTab === "chunks",
    staleTime: 60_000,
  });

  // Related documents — fetched lazily when the Related tab is active
  const { data: relatedData, isLoading: relatedLoading } = useQuery<{
    doc_id: string;
    related: Array<{
      doc_id: string; title: string; kind: string | null; readiness: string | null;
      similarity: number | null; link_type: string; shared_topics: Array<{ id: string; name: string }>;
    }>;
  }>({
    queryKey: ["doc-related", docId],
    queryFn: () => apiFetch(`${BASE}/library/${docId}/related`).then((r) => r.json()),
    enabled: !!docId && activeTab === "related",
    staleTime: 120_000,
  });

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
  // The TTS endpoint caps requests at 10 000 chars; stay well under it and
  // split at paragraph/sentence boundaries so parts sound natural. The whole
  // document is read — parts are synthesized lazily (current + next prefetch)
  // and old blob URLs are evicted so memory stays bounded regardless of length.
  const TTS_PART_CHARS = 4500;
  const TTS_KEEP_BEHIND = 1; // keep this many already-played parts cached for quick back-seek

  const splitTextForTts = (text: string): string[] => {
    const paras = text.replace(/\n{3,}/g, "\n\n").split(/\n\n+/);
    const parts: string[] = [];
    let cur = "";
    const flush = () => { if (cur.trim()) parts.push(cur.trim()); cur = ""; };
    for (const p of paras) {
      if (p.length > TTS_PART_CHARS) {
        for (const s of p.split(/(?<=[.!?])\s+/)) {
          if (cur && cur.length + s.length + 1 > TTS_PART_CHARS) flush();
          cur += (cur ? " " : "") + s;
          while (cur.length > TTS_PART_CHARS) { // pathological unbroken text
            parts.push(cur.slice(0, TTS_PART_CHARS));
            cur = cur.slice(TTS_PART_CHARS);
          }
        }
      } else {
        if (cur && cur.length + p.length + 2 > TTS_PART_CHARS) flush();
        cur += (cur ? "\n\n" : "") + p;
      }
    }
    flush();
    return parts;
  };

  const TTS_STALE = "tts-session-stale";

  /** Synthesize one part, cache the blob URL, and return it.
   *  Single-flight per part via a promise map; results from an older
   *  session are discarded before any blob URL is created. */
  const synthesizePart = (parts: string[], i: number, voice: string, speed: number): Promise<string> => {
    const session = ttsSessionRef.current;
    const cached = ttsUrlCacheRef.current.get(i);
    if (cached) return Promise.resolve(cached);
    const inflight = ttsPromisesRef.current.get(i);
    if (inflight) return inflight;
    const p = (async () => {
      const resp = await apiFetch(`${BASE}/studio/tts`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: parts[i], voice, speed }),
      });
      if (ttsSessionRef.current !== session) throw new Error(TTS_STALE);
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}));
        throw new Error((err as any).detail ?? `HTTP ${resp.status}`);
      }
      const blob = await resp.blob();
      if (ttsSessionRef.current !== session) throw new Error(TTS_STALE);
      const url = URL.createObjectURL(blob);
      ttsUrlCacheRef.current.set(i, url);
      return url;
    })();
    p.finally(() => {
      if (ttsPromisesRef.current.get(i) === p) ttsPromisesRef.current.delete(i);
    }).catch(() => {}); // avoid unhandled-rejection from the tracking chain
    ttsPromisesRef.current.set(i, p);
    return p;
  };

  /** Fire-and-forget prefetch of the next part so playback never stalls. */
  const prefetchPart = (parts: string[], i: number, voice: string, speed: number) => {
    if (i >= parts.length) return;
    synthesizePart(parts, i, voice, speed).catch(() => {});
  };

  /** Revoke cached blob URLs far behind the current part so memory stays
   *  bounded on arbitrarily long documents. */
  const evictOldParts = (current: number) => {
    for (const [idx, url] of ttsUrlCacheRef.current) {
      if (idx < current - TTS_KEEP_BEHIND) {
        URL.revokeObjectURL(url);
        ttsUrlCacheRef.current.delete(idx);
      }
    }
  };

  const resetTts = () => {
    ttsSessionRef.current++;
    ttsAutoPlayRef.current = false;
    ttsAudioRef.current?.pause();
    setTtsPlaying(false);
    setTtsAudioUrl(null);
    setTtsChunks([]);
    setTtsIndex(0);
    for (const url of ttsUrlCacheRef.current.values()) URL.revokeObjectURL(url);
    ttsUrlCacheRef.current.clear();
    ttsPromisesRef.current.clear();
  };

  const handleReadAloud = async () => {
    if (!docId || ttsLoading) return;
    setTtsLoading(true);
    resetTts();
    // Capture the session BEFORE any await — if the player is closed or the
    // user navigates away while the text/chunks fetch is pending, this run
    // must not touch state belonging to a newer session.
    const session = ttsSessionRef.current;
    try {
      // Prefer the already-extracted text on the doc object; fall back to chunks.
      let text: string = (doc?.extracted_text as string) || "";
      if (!text.trim()) {
        const resp = await apiFetch(`${BASE}/library/${docId}/chunks`);
        if (ttsSessionRef.current !== session) return; // closed/superseded meanwhile
        if (resp.ok) {
          const data = await resp.json();
          if (ttsSessionRef.current !== session) return;
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

      const parts = splitTextForTts(text);
      setTtsChunks(parts);
      setTtsIndex(0);
      const url = await synthesizePart(parts, 0, ttsVoiceRef.current, ttsSpeedRef.current);
      if (ttsSessionRef.current !== session) return; // closed/superseded meanwhile
      setTtsAudioUrl(url);
      prefetchPart(parts, 1, ttsVoiceRef.current, ttsSpeedRef.current);
      // Do NOT autoplay the first part — iOS Safari blocks audio from async
      // code. The playback bar below appears; the user taps play.
    } catch (e: any) {
      if (e?.message !== TTS_STALE && ttsSessionRef.current === session) {
        toast.error(`Read aloud failed: ${e.message ?? "unknown error"}`, { duration: 8000 });
        resetTts();
      }
    } finally {
      setTtsLoading(false);
    }
  };

  /** Advance to part `i` (auto-play unless this is a manual seek while paused). */
  const goToPart = async (i: number, autoplay: boolean) => {
    if (i < 0 || i >= ttsChunks.length) return;
    const session = ttsSessionRef.current;
    setTtsLoading(true);
    try {
      const url = await synthesizePart(ttsChunks, i, ttsVoiceRef.current, ttsSpeedRef.current);
      if (ttsSessionRef.current !== session) return; // closed/superseded meanwhile
      ttsAutoPlayRef.current = autoplay;
      setTtsIndex(i);
      setTtsAudioUrl(url);
      prefetchPart(ttsChunks, i + 1, ttsVoiceRef.current, ttsSpeedRef.current);
      evictOldParts(i);
    } catch (e: any) {
      if (e?.message !== TTS_STALE && ttsSessionRef.current === session) {
        setTtsPlaying(false);
        toast.error(`Could not synthesize part ${i + 1}: ${e.message ?? "unknown error"}`);
      }
    } finally {
      setTtsLoading(false);
    }
  };

  const handleTtsEnded = () => {
    setTtsPlaying(false);
    if (ttsIndex + 1 < ttsChunks.length) {
      void goToPart(ttsIndex + 1, true);
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

  const closeTtsPlayer = () => resetTts();

  /** Change voice and/or speed; clears the part cache so stale audio never replays.
   *  If a player is currently open, re-synthesizes the current part immediately. */
  const applyTtsSettings = async (newVoice: string, newSpeed: number) => {
    // Update refs first so any concurrent synthesizePart calls use the new values
    ttsVoiceRef.current = newVoice;
    ttsSpeedRef.current = newSpeed;
    setTtsVoice(newVoice);
    setTtsSpeed(newSpeed);
    localStorage.setItem(TTS_LS_VOICE, newVoice);
    localStorage.setItem(TTS_LS_SPEED, String(newSpeed));

    // If a player is currently shown, evict the cache and resynthesize from the current part
    if (ttsAudioUrl && ttsChunks.length > 0) {
      // Bump the session counter BEFORE capturing it so that any in-flight
      // synthesizePart calls that started under the old session (including
      // prefetches) will detect staleness and discard their results rather
      // than overwriting the new voice's cache entry.
      ttsSessionRef.current++;
      const session = ttsSessionRef.current;
      // Pause current audio and clear old blobs
      ttsAudioRef.current?.pause();
      for (const url of ttsUrlCacheRef.current.values()) URL.revokeObjectURL(url);
      ttsUrlCacheRef.current.clear();
      ttsPromisesRef.current.clear();
      setTtsPlaying(false);
      setTtsLoading(true);
      try {
        const url = await synthesizePart(ttsChunks, ttsIndex, newVoice, newSpeed);
        if (ttsSessionRef.current !== session) return;
        setTtsAudioUrl(url);
        prefetchPart(ttsChunks, ttsIndex + 1, newVoice, newSpeed);
      } catch (e: any) {
        if (e?.message !== TTS_STALE && ttsSessionRef.current === session) {
          toast.error(`Could not apply new voice: ${e.message ?? "error"}`);
        }
      } finally {
        if (ttsSessionRef.current === session) setTtsLoading(false);
      }
    }
  };

  // Reset TTS session + cancel any in-flight audiobook job when navigating.
  useEffect(() => {
    return () => {
      resetTts();
      _clearAbPoll();
      _cancelAbOnServer();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [docId]);

  // ── Download audiobook (async job) ──────────────────────────────────────
  const [abJobId, setAbJobId]       = useState<string | null>(null);
  const [abSegsDone, setAbSegsDone] = useState(0);
  const [abSegsTotal, setAbSegsTotal] = useState(0);
  const [abState, setAbState]       = useState<"idle" | "running" | "cancelling">("idle");
  const abPollRef   = useRef<ReturnType<typeof setInterval> | null>(null);
  const abJobIdRef  = useRef<string | null>(null);   // stable ref for cleanup

  const _clearAbPoll = () => {
    if (abPollRef.current !== null) {
      clearInterval(abPollRef.current);
      abPollRef.current = null;
    }
  };

  const _cancelAbOnServer = () => {
    const jid = abJobIdRef.current;
    if (jid) {
      apiFetch(`${BASE}/studio/tts/document/${jid}`, { method: "DELETE" }).catch(() => {});
      abJobIdRef.current = null;
    }
  };

  const _resetAb = () => {
    _clearAbPoll();
    abJobIdRef.current = null;
    setAbJobId(null);
    setAbSegsDone(0);
    setAbSegsTotal(0);
    setAbState("idle");
  };

  const handleDownloadAudiobook = async () => {
    if (!docId || abState !== "idle") return;
    const toastId = toast.loading("Starting audiobook generation…");
    try {
      const resp = await apiFetch(`${BASE}/studio/tts/document`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          doc_id: docId,
          voice: ttsVoiceRef.current,
          speed: ttsSpeedRef.current,
        }),
      });
      if (!resp.ok) {
        let detail = `HTTP ${resp.status}`;
        try { detail = (await resp.json()).detail ?? detail; } catch { /* ignore */ }
        throw new Error(detail);
      }
      const { job_id, total_segments } = await resp.json();
      toast.dismiss(toastId);
      abJobIdRef.current = job_id;
      setAbJobId(job_id);
      setAbSegsTotal(total_segments);
      setAbSegsDone(0);
      setAbState("running");

      // Poll for progress every 2 s.
      abPollRef.current = setInterval(async () => {
        try {
          const sr = await apiFetch(`${BASE}/studio/tts/document/${job_id}/status`);
          if (!sr.ok) return;
          const status = await sr.json();
          setAbSegsDone(status.segments_done ?? 0);
          setAbSegsTotal(status.total_segments ?? total_segments);

          if (status.state === "done") {
            _clearAbPoll();
            // Trigger download via anchor — session cookies are sent automatically.
            const a = document.createElement("a");
            a.href = `${BASE}/studio/outputs/serve?path=${encodeURIComponent(status.mp3_path)}`;
            a.download = status.filename ?? "audiobook.mp3";
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            _resetAb();
            toast.success("Audiobook downloaded!");
          } else if (status.state === "failed") {
            _resetAb();
            toast.error(`Audiobook failed: ${status.error ?? "unknown error"}`, { duration: 10_000 });
          } else if (status.state === "cancelled") {
            _resetAb();
            toast("Audiobook generation cancelled.");
          }
        } catch { /* poll errors are transient */ }
      }, 2000);

    } catch (e: any) {
      toast.error(`Audiobook failed: ${e?.message ?? "unknown error"}`, { id: toastId, duration: 10_000 });
      _resetAb();
    }
  };

  const handleCancelAudiobook = async () => {
    if (!abJobId) return;
    setAbState("cancelling");
    try {
      await apiFetch(`${BASE}/studio/tts/document/${abJobId}`, { method: "DELETE" });
    } catch { /* best-effort */ }
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
    { value: "canonical",  label: "Canonical",  className: "font-semibold", style: { color: "var(--gilt)" } as React.CSSProperties },
    { value: "reference",  label: "Reference",  className: "" },
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
    { key: "chunks",    label: "Chunks",    icon: Hash,    badge: chunksData?.count },
    { key: "knowledge", label: "Knowledge", icon: Cpu },
    { key: "related",   label: "Related",   icon: Network, badge: relatedData?.related?.length },
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
            disabled={ttsLoading || readiness === "no_text"}
            title={readiness === "no_text" ? "This document has no readable text" : "Listen to this document"}
          >
            {ttsLoading ? (
              <><Loader2 className="w-3.5 h-3.5 mr-1.5 animate-spin" /> Preparing audio…</>
            ) : (
              <><BookHeadphones className="w-3.5 h-3.5 mr-1.5" /> Read Aloud</>
            )}
          </Button>
          {abState !== "idle" ? (
            /* ── Audiobook progress inline ──────────────────────────────── */
            <div className="flex items-center gap-2">
              <div className="flex flex-col min-w-36">
                <div className="flex justify-between text-xs text-muted-foreground mb-0.5">
                  <span>
                    {abState === "cancelling" ? "Cancelling…" : "Generating audiobook…"}
                  </span>
                  <span>{abSegsDone}/{abSegsTotal}</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary rounded-full transition-all duration-500"
                    style={{ width: abSegsTotal > 0 ? `${Math.round((abSegsDone / abSegsTotal) * 100)}%` : "0%" }}
                  />
                </div>
              </div>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 shrink-0 text-destructive hover:text-destructive hover:bg-destructive/10"
                onClick={handleCancelAudiobook}
                disabled={abState === "cancelling"}
                title="Cancel audiobook generation"
              >
                <X className="w-3.5 h-3.5" />
              </Button>
            </div>
          ) : (
            /* ── Idle: show Audiobook download button ───────────────────── */
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  {/* Wrap in a div so the estimate label sits flush below the button */}
                  <div className="flex flex-col items-center gap-0.5">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleDownloadAudiobook}
                      disabled={readiness !== "ready"}
                    >
                      <Download className="w-3.5 h-3.5 mr-1.5" /> Audiobook
                    </Button>
                    {/* Inline time-estimate hint — visible without hovering */}
                    {readiness === "ready" && (() => {
                      const est = audiobookTimeEstimate(doc.word_count ?? 0, ttsSpeed);
                      return est ? (
                        <span className="text-[10px] font-mono text-muted-foreground/70 leading-none">
                          {est}
                        </span>
                      ) : null;
                    })()}
                  </div>
                </TooltipTrigger>
                <TooltipContent side="bottom" className="max-w-56 text-center text-xs space-y-0.5">
                  {readiness !== "ready" ? (
                    "Document must be fully processed before generating an audiobook"
                  ) : (
                    <>
                      <p>Download the whole document as a single MP3</p>
                      {(() => {
                        const est = audiobookTimeEstimate(doc.word_count ?? 0, ttsSpeed);
                        return est ? (
                          <p className="text-muted-foreground">
                            Estimated generation time: <span className="font-semibold text-foreground">{est}</span>
                            {doc.word_count ? ` (${doc.word_count.toLocaleString()} words)` : ""}
                          </p>
                        ) : null;
                      })()}
                      <p className="text-muted-foreground">Uses your current voice and speed settings</p>
                    </>
                  )}
                </TooltipContent>
              </Tooltip>
            </TooltipProvider>
          )}
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
            title="Copy link to this document"
            onClick={() => {
              navigator.clipboard.writeText(window.location.href).then(
                () => toast.success("Link copied"),
                () => toast.error("Could not copy link"),
              );
            }}
          >
            <Link2 className="w-3.5 h-3.5 mr-1.5" /> Copy Link
          </Button>
          <Button
            variant="outline"
            size="sm"
            asChild
            title="Download original file"
          >
            <a href={`${BASE}/library/${docId}/download`} download>
              <Download className="w-3.5 h-3.5 mr-1.5" /> Download
            </a>
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
            hasError ? "" : "bg-muted/50 border-border/50"
          }`} style={hasError ? { background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" } : undefined}>
            {hasError
              ? <AlertCircle className="w-5 h-5" style={{ color: "var(--rust)" }} />
              : <FileText className="w-5 h-5 text-muted-foreground" />
            }
          </div>
          <div className="min-w-0 flex-1">
            <EditableTitle docId={docId!} title={title} onSave={(t) => updateDoc.mutate({ title: t })} />
            <div className="flex flex-wrap items-center gap-2 mt-2">
              <Badge variant="secondary" className="font-mono text-[10px] uppercase">
                {doc.kind ?? "file"}
              </Badge>
              <ReadinessBadge readiness={readiness} />
              {/* Lifecycle badge + inline picker */}
              <Select value={docLifecycle} onValueChange={handleSetLifecycle}>
                <SelectTrigger
                  className={`h-auto py-0.5 px-1.5 text-[10px] font-mono uppercase border rounded gap-1 w-auto min-w-0 focus:ring-0 shadow-none ${
                    docLifecycle === "superseded"
                      ? "bg-muted/50 border-border text-muted-foreground"
                      : docLifecycle !== "canonical" && docLifecycle !== "reference"
                      ? "bg-muted/30 border-border/50 text-muted-foreground"
                      : ""
                  }`}
                  style={
                    docLifecycle === "canonical"
                      ? { background: "var(--gilt-soft)", borderColor: "var(--gilt-line)", color: "var(--gilt)" }
                      : docLifecycle === "reference"
                      ? { background: "transparent", borderColor: "var(--line)", color: "var(--ink-soft)" }
                      : undefined
                  }
                >
                  {docLifecycle === "canonical" && <Star className="w-2.5 h-2.5" style={{ color: "var(--gilt)" }} />}
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

            {/* Live processing progress bar — shown while SSE is delivering events */}
            {_PROCESSING_STATES.has(readiness) && processingProgress && (
              <div className="mt-3 space-y-1 max-w-sm">
                <div className="flex items-center justify-between text-[11px] font-mono text-muted-foreground">
                  <span className="capitalize">{processingProgress.stage.replace(/_/g, " ")}…</span>
                  <span>{processingProgress.pct}%</span>
                </div>
                <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                  <div
                    className="h-full bg-primary/70 rounded-full transition-all duration-500 ease-out"
                    style={{ width: `${processingProgress.pct}%` }}
                  />
                </div>
                {processingProgress.items_found > 0 && (
                  <p className="text-[10px] text-muted-foreground">
                    {processingProgress.items_found} knowledge item
                    {processingProgress.items_found !== 1 ? "s" : ""} found so far
                  </p>
                )}
              </div>
            )}
          </div>
        </div>

        {/* Native audio player — shown for uploaded audio files (kind === "audio") */}
        {doc.kind === "audio" && doc.readiness === "ready" && (
          <div className="mt-3 p-3 rounded-lg bg-muted/30 border border-border/50">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-xs font-mono text-muted-foreground uppercase tracking-wide">
                Original audio
              </span>
            </div>
            <audio
              controls
              className="w-full h-9"
              src={`${BASE}/library/${docId}/download`}
              style={{ minWidth: 200 }}
              preload="metadata"
            >
              Your browser does not support the audio element.
            </audio>
          </div>
        )}

        {/* Read Aloud playback bar */}
        {ttsAudioUrl && (
          <div className="mt-3 rounded-lg bg-muted/30 border border-border/50 overflow-hidden">
            {/* Main controls row */}
            <div className="flex items-center gap-2 p-3 flex-wrap">
              <Button
                size="icon"
                variant="secondary"
                className="h-9 w-9 rounded-full shrink-0"
                onClick={toggleTtsPlay}
                disabled={ttsLoading}
              >
                {ttsLoading
                  ? <Loader2 className="w-4 h-4 animate-spin" />
                  : ttsPlaying ? <Pause className="w-4 h-4" /> : <Play className="w-4 h-4" />}
              </Button>
              <audio
                ref={ttsAudioRef}
                src={ttsAudioUrl}
                onEnded={handleTtsEnded}
                onPause={() => setTtsPlaying(false)}
                onPlay={() => setTtsPlaying(true)}
                className="flex-1 h-8"
                controls
                style={{ minWidth: 160 }}
              />
              {ttsChunks.length > 1 && (
                <div className="flex items-center gap-1 shrink-0">
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    onClick={() => goToPart(ttsIndex - 1, ttsPlaying)}
                    disabled={ttsLoading || ttsIndex === 0}
                    title="Previous part"
                  >
                    <ChevronDown className="w-3.5 h-3.5 rotate-90" />
                  </Button>
                  <span className="text-[11px] font-mono text-muted-foreground whitespace-nowrap tabular-nums">
                    Part {ttsIndex + 1} / {ttsChunks.length}
                  </span>
                  <Button
                    size="icon"
                    variant="ghost"
                    className="h-7 w-7"
                    onClick={() => goToPart(ttsIndex + 1, ttsPlaying)}
                    disabled={ttsLoading || ttsIndex >= ttsChunks.length - 1}
                    title="Next part"
                  >
                    <ChevronDown className="w-3.5 h-3.5 -rotate-90" />
                  </Button>
                </div>
              )}
              <Button
                size="icon"
                variant="ghost"
                className="shrink-0"
                onClick={closeTtsPlayer}
                title="Stop and close"
              >
                <X className="w-4 h-4" />
              </Button>
            </div>
            {/* Voice + speed picker row */}
            <div className="flex items-center gap-3 px-3 pb-2.5 flex-wrap border-t border-border/30 pt-2">
              <span className="text-[10px] font-mono font-semibold uppercase tracking-widest text-muted-foreground shrink-0">
                Voice
              </span>
              <Select
                value={ttsVoice}
                onValueChange={(v) => applyTtsSettings(v, ttsSpeedRef.current)}
              >
                <SelectTrigger className="h-7 text-xs font-mono w-36 shrink-0">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent className="max-h-56">
                  {TTS_VOICE_OPTIONS.map((v) => (
                    <SelectItem key={v.id} value={v.id} className="text-xs font-mono">
                      {v.label}
                      <span className="ml-1.5 text-muted-foreground text-[10px]">{v.accent}</span>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <span className="text-[10px] font-mono font-semibold uppercase tracking-widest text-muted-foreground shrink-0 ml-2">
                Speed
              </span>
              <div className="flex items-center gap-1">
                {TTS_SPEED_OPTIONS.map((s) => (
                  <button
                    key={s.value}
                    onClick={() => applyTtsSettings(ttsVoiceRef.current, s.value)}
                    className={`px-2 py-0.5 rounded text-[11px] font-mono transition-colors ${
                      ttsSpeed === s.value
                        ? "bg-primary text-primary-foreground font-semibold"
                        : "bg-muted text-muted-foreground hover:bg-muted/80 hover:text-foreground"
                    }`}
                  >
                    {s.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* Error banner */}
        {hasError && doc.error_message && (
          <div className="mt-3 p-3 rounded-lg border" style={{ background: "var(--rust-soft)", borderColor: "color-mix(in srgb, var(--rust) 28%, transparent)" }}>
            <p className="text-xs font-mono break-all" style={{ color: "var(--rust)" }}>{doc.error_message}</p>
          </div>
        )}

        {/* Extraction warnings (non-fatal issues from the pipeline) */}
        {Array.isArray(doc.warnings) && doc.warnings.length > 0 && (
          <div className="mt-3 p-3 rounded-lg border space-y-1" style={{ background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}>
            <p className="text-[10px] font-mono font-semibold uppercase tracking-wide mb-1.5" style={{ color: "var(--gilt)" }}>
              Extraction warnings ({doc.warnings.length})
            </p>
            {(doc.warnings as any[]).map((w, i) => (
              <p key={i} className="text-xs font-mono break-all" style={{ color: "var(--gilt)" }}>
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
            { label: "Size",      value: (doc as any).size_bytes ? formatBytes((doc as any).size_bytes) : "—" },
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
                      <span className="w-2 h-2 rounded-full shrink-0" style={{
                        background: m.status === "ok" ? "var(--green-2)" :
                        m.status === "empty" ? "var(--gilt)" : "var(--rust)"
                      }} />
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
            <TextSearchableContent text={doc.extracted_text} />
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

      {activeTab === "chunks" && (
        <div className="space-y-3">
          <p className="text-xs font-mono text-muted-foreground">{chunksData?.count ?? 0} text chunk{(chunksData?.count ?? 0) !== 1 ? "s" : ""}</p>
          {chunksLoading ? (
            <div className="space-y-2">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-20 w-full" />)}</div>
          ) : (chunksData?.chunks ?? []).length === 0 ? (
            <div className="text-center py-12 bg-muted/10 border border-dashed rounded-lg">
              <Hash className="w-8 h-8 text-muted-foreground mx-auto mb-3 opacity-50" />
              <p className="text-muted-foreground text-sm">No text chunks available. Re-extract the document to populate them.</p>
            </div>
          ) : (
            (chunksData?.chunks ?? []).map((chunk, i) => (
              <div key={chunk.id ?? i} className="border border-border/50 rounded-lg p-3.5 bg-muted/10 group hover:bg-muted/20 transition-colors">
                <div className="flex items-center gap-2 mb-2">
                  <Badge variant="outline" className="text-[9px] font-mono">chunk {i + 1}</Badge>
                  {chunk.page > 0 && <Badge variant="outline" className="text-[9px] font-mono opacity-60">p. {chunk.page}</Badge>}
                </div>
                <p className="text-[12px] font-mono leading-relaxed text-foreground/80 whitespace-pre-wrap line-clamp-6">{chunk.text}</p>
              </div>
            ))
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
            docId={docId!}
            docWorkId={doc?.work_id}
            docReadiness={readiness}
            aiEnabled={aiExtData?.enabled ?? false}
            knFilter={knFilter}
            setKnFilter={setKnFilter}
            reviewing={reviewing}
            onReview={handleReview}
            onDelete={handleDeleteKnowledge}
            highlightItemId={highlightKnItemId}
          />
        </ErrorBoundary>
      )}

      {activeTab === "related" && (
        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <p className="text-xs font-mono text-muted-foreground uppercase tracking-wider">
              Related documents
            </p>
            <button
              onClick={() => navigate("/topics")}
              className="text-xs text-primary hover:underline font-mono flex items-center gap-1"
            >
              <Network className="w-3 h-3" />
              Browse all topics
            </button>
          </div>

          {relatedLoading ? (
            <div className="space-y-2">
              {[1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16 w-full" />)}
            </div>
          ) : !relatedData?.related?.length ? (
            <div className="text-center py-16 bg-muted/10 border border-dashed rounded-lg space-y-3">
              <Network className="w-9 h-9 text-muted-foreground mx-auto opacity-30" />
              <p className="text-muted-foreground font-serif text-sm">No related documents found yet.</p>
              <p className="text-xs text-muted-foreground/60 max-w-xs mx-auto">
                Related documents appear after the nightshift clustering pass runs.
                Make sure this document has been processed and has extracted text.
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              {relatedData.related.map((rel) => (
                <div
                  key={rel.doc_id}
                  onClick={() => navigate(`/library/${rel.doc_id}`)}
                  className="flex items-start gap-3 p-3 rounded-lg border border-border/50 hover:border-primary/20 hover:bg-muted/20 cursor-pointer transition-colors group"
                >
                  <FileText className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{rel.title || "(untitled)"}</p>
                    <div className="flex flex-wrap items-center gap-1.5 mt-1">
                      {rel.kind && (
                        <Badge variant="outline" className="font-mono text-[10px] uppercase py-0">
                          {rel.kind}
                        </Badge>
                      )}
                      {rel.similarity != null && (
                        <span className="text-[10px] font-mono rounded border px-1.5 py-0.5" style={{ color: "var(--green-2)", background: "var(--green-soft)", borderColor: "color-mix(in srgb, var(--green-2) 20%, transparent)" }}>
                          {(rel.similarity * 100).toFixed(0)}% similar
                        </span>
                      )}
                      {rel.shared_topics.slice(0, 2).map((t) => (
                        <span key={t.id} className="text-[10px] font-mono rounded border px-1.5 py-0.5 truncate max-w-[140px]" style={{ color: "var(--gilt)", background: "var(--gilt-soft)", borderColor: "var(--gilt-line)" }}>
                          {t.name}
                        </span>
                      ))}
                    </div>
                  </div>
                  <ChevronDown className="w-3.5 h-3.5 text-muted-foreground shrink-0 -rotate-90 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
