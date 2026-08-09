import { useState, useRef, useEffect, useCallback } from "react";
import { useLocation } from "wouter";
import { ErrorBoundary } from "@/components/error-boundary";
import { toast } from "sonner";
import { apiFetch, buildAuthHeaders } from "@/lib/auth";
import { randomUUID, copyToClipboard } from "@/lib/uuid";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useListConversations,
  useGetConversation,
  useCreateConversation,
  useDeleteConversation,
  useUpdateConversation,
  useGetWork,
  useGetSystemModels,
  getGetSystemModelsQueryKey,
  getListConversationsQueryKey,
  type ModelOption,
  getGetConversationQueryKey,
  getGetWorkQueryKey,
  useGetWorkDocuments,
  getGetWorkDocumentsQueryKey,
  useGetWebSearchStatus,
} from "@workspace/api-client-react";
import { useConnectivity } from "@/lib/useConnectivity";
import { useGdDark } from "@/lib/useGdDark";
import { useQueryClient, useQuery } from "@tanstack/react-query";
import { format } from "date-fns";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  MessageSquare, Plus, Send, Search, Bot, User, Copy, Check,
  Trash2, Wifi, WifiOff, Loader2, Cpu, Pencil, BookOpen, Archive, ArchiveRestore,
  AlertTriangle, FolderOpen, FileText, ChevronRight, ChevronLeft, X as XIcon, Zap, Brain,
  Globe, Paperclip, Download, Layers, HelpCircle, Compass, ChevronDown, ImageIcon, Square,
  Sparkles, History, RefreshCw, ExternalLink, Mail,
} from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/atom-one-dark.css";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

/**
 * Explicit delivery/generation status for a message.
 *
 * sending      — user message sent to fetch; awaiting first byte from server
 * acknowledged — server has started responding (first token received)
 * streaming    — assistant response is actively streaming tokens
 * complete     — assistant response finished (or message loaded from server)
 * failed       — network/server error; user can retry
 */
type MessageStatus = "sending" | "acknowledged" | "streaming" | "complete" | "failed";

interface LocalMessage {
  id: string;

  role: "user" | "assistant";

  text: string;

  created_at: string;

  /** Explicit lifecycle state. Undefined means loaded from server (treat as complete). */
  status?: MessageStatus;

  streaming?: boolean;
  /** Set when the stream was aborted before completion */

  incomplete?: boolean;
  /** Set when this message is a clarifying question from the cognition gate */

  isClarification?: boolean;
  /** Tool intent that produced this message, e.g. "web_search", "weather" */

  intent?: string;

  meta?: Record<string, unknown>;

  /** Base64 image attached to this user message (session-only, not persisted) */
  image_b64?: string;
  image_media_type?: string;

  /** Chain-of-thought text from a reasoning model (e.g. DeepSeek R1 <think> blocks) */
  thinking?: string;
  /** True while thinking tokens are still streaming in */
  thinkingStreaming?: boolean;
}

/** Suffix appended by the backend when a streaming response is cut short by a timeout. */
const TRUNCATION_SUFFIX = "\n\n*(Response was cut short — re-send to continue.)*";

/** Sentinel prefix carried through the token stream when the gate returns "clarify". */
const CLARIFY_PREFIX = "\x02CLARIFY\x02";
const TIMEOUT_SENTINEL = "\x02TIMEOUT\x02";
/** Sentinel prefix carrying the tool intent through the token stream. Format: \x02INTENT\x02web_search\x02 */
const INTENT_PREFIX = "\x02INTENT\x02";
/** Sentinel prefix carrying reasoning/thinking tokens from <think> blocks or reasoning_content. */
const THINKING_PREFIX = "\x02THINKING\x02";

const INTENT_LABELS: Record<string, { icon: string; label: string }> = {
  web_search: { icon: "🌐", label: "Web search" },
  weather:    { icon: "📍", label: "Weather" },
  remember:   { icon: "📌", label: "Remembered" },
  recall:     { icon: "✨", label: "Memory recall" },
  image_gen:  { icon: "🎨", label: "Image gen" },
};

// ─── Activity types ────────────────────────────────────────────────────────────

interface ActivityStep {
  id: string;
  label: string;
  icon: "search" | "read" | "think" | "write";
  startMs: number;
  endMs?: number;
  done: boolean;
}

// ─── Models hook (generated) ──────────────────────────────────────────────────

function useModels() {
  return useGetSystemModels({
    query: { queryKey: getGetSystemModelsQueryKey(), staleTime: 60_000 },
  });
}

// ─── Model label helper ───────────────────────────────────────────────────────

function modelLabel(modelId: string | undefined | null, models: ModelOption[], defaultModel: string | undefined): string {
  if (!modelId) modelId = defaultModel;
  const found = models.find((m) => m.id === modelId);
  if (found) return found.label ?? found.id ?? "Default";
  // Truncate raw ID for display
  return modelId ? modelId.split("-").slice(0, 3).join("-") : "Default";
}

// ─── Model picker ─────────────────────────────────────────────────────────────

interface ModelPickerProps {
  convId: string;
  currentModel: string | null | undefined;
  models: ModelOption[];
  defaultModel: string | undefined;
  onChanged: () => void;
}

function ModelPicker({ convId, currentModel, models, defaultModel, onChanged }: ModelPickerProps) {
  const updateConv = useUpdateConversation();
  const effective = currentModel || defaultModel;

  const handleChange = (value: string) => {
    try { localStorage.setItem("orivellum:lastModel", value); } catch {}
    updateConv.mutate(
      { convId, data: { model: value } },
      { onSuccess: onChanged, onError: () => toast.error("Could not switch model") }
    );
  };

  if (!models.length) return null;

  return (
    <Select value={effective} onValueChange={handleChange} disabled={updateConv.isPending}>
      <SelectTrigger className="h-6 gap-1 border-0 bg-muted/60 hover:bg-muted text-xs font-mono px-2 focus:ring-0 w-auto min-w-[90px]">
        <Cpu className="w-3 h-3 shrink-0 opacity-60" />
        <SelectValue>{modelLabel(effective, models, defaultModel)}</SelectValue>
      </SelectTrigger>
      <SelectContent align="end" className="min-w-[220px]">
        {models.map((m) => (
          <SelectItem key={m.id ?? m.label} value={m.id ?? ""} className="text-xs">
            <div className="flex flex-col gap-0.5">
              <span className="font-medium">{m.label}</span>
              {m.description && (
                <span className="text-muted-foreground text-[10px]">{m.description}</span>
              )}
            </div>
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}

// ─── Sources footer ───────────────────────────────────────────────────────────

interface KnowledgeSource {
  id?: string;
  title?: string;
  kind?: string;
  work_id?: string | null;
  work_title?: string;
  source_doc_id?: string | null;
  // Web search sources carry a URL instead of a doc/work id
  url?: string;
  // Legacy fields (older persisted meta)
  doc_id?: string;
  doc_title?: string;
  // Passage excerpt from the source document (for inline preview)
  passage?: string;
}

/** Normalize a source object across the current + legacy backend shapes.
 *  Web search sources (kind === "web") carry a url field; knowledge sources
 *  carry source_doc_id / work_id.  Both end up in the same SourcesFooter. */
function normalizeSource(s: KnowledgeSource) {
  const isWeb = s.kind === "web";
  const docId = s.source_doc_id ?? s.doc_id ?? null;
  const title = s.title ?? s.doc_title ?? (isWeb ? s.url ?? "Web" : docId ? "Document" : "Knowledge");
  // Group web sources under "Web" so they appear in their own section
  const workTitle = isWeb ? "Web" : (s.work_title ?? s.doc_title ?? "General");
  return {
    id: s.id ?? s.url ?? docId ?? title,
    title,
    kind: s.kind,
    workId: s.work_id ?? null,
    workTitle,
    docId,
    url: s.url ?? null,
    isWeb,
    passage: s.passage ?? null,
  };
}

function SourcesFooter({ sources }: { sources: KnowledgeSource[] }) {
  const [open, setOpen] = useState(false);

  // Normalize then dedupe by stable id
  const normalized = sources.map(normalizeSource);
  const seen = new Set<string>();
  const unique = normalized.filter((s) => {
    const key = s.id ?? s.title;
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  if (unique.length === 0) return null;

  // Group by Work/topic — web sources land in "Web", knowledge in their Work title
  const groups: Array<{ title: string; items: typeof unique }> = [];
  const groupIndex = new Map<string, number>();
  for (const s of unique) {
    const gkey = s.workTitle || "General";
    let idx = groupIndex.get(gkey);
    if (idx === undefined) {
      idx = groups.length;
      groupIndex.set(gkey, idx);
      groups.push({ title: gkey, items: [] });
    }
    groups[idx].items.push(s);
  }

  const link = (s: (typeof unique)[number]): { href: string; external: boolean } | null => {
    if (s.isWeb && s.url) return { href: s.url, external: true };
    if (s.docId) return { href: `${import.meta.env.BASE_URL}library/${s.docId}`.replace(/\/+/g, "/"), external: false };
    if (s.workId) return { href: `${import.meta.env.BASE_URL}works/${s.workId}`.replace(/\/+/g, "/"), external: false };
    return null;
  };

  const hasWeb = unique.some((s) => s.isWeb);

  return (
    <>
      {/* Chip button — meets 44pt touch target via .chat-icon-btn on coarse-pointer */}
      <button
        onClick={() => setOpen(true)}
        className="chat-icon-btn mt-1.5 flex items-center gap-1.5 text-[11px] font-mono text-muted-foreground/50 hover:text-muted-foreground/80 transition-colors"
      >
        {hasWeb ? <Globe className="w-3 h-3" /> : <BookOpen className="w-3 h-3" />}
        <span>Sources ({unique.length})</span>
        <ChevronRight className="w-3 h-3 opacity-60" />
      </button>

      {/* Bottom sheet for source details */}
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="bottom" className="px-0 max-h-[70vh] flex flex-col">
          <SheetHeader className="px-6 pb-3 border-b border-border/40 shrink-0">
            <SheetTitle className="text-sm font-serif flex items-center gap-2">
              {hasWeb ? <Globe className="w-4 h-4 text-primary" /> : <BookOpen className="w-4 h-4 text-primary" />}
              Sources ({unique.length})
            </SheetTitle>
          </SheetHeader>
          <div className="flex-1 min-h-0 overflow-y-auto px-4 py-3 space-y-4">
            {groups.map((g, gi) => (
              <div key={gi} className="space-y-0.5">
                <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground/50 px-2 mb-1">
                  {g.title}
                </p>
                {g.items.map((s, i) => {
                  const target = link(s);
                  const Icon = s.isWeb ? Globe : FileText;
                  return target ? (
                    <a
                      key={i}
                      href={target.href}
                      target={target.external ? "_blank" : undefined}
                      rel={target.external ? "noopener noreferrer" : undefined}
                      onClick={!target.external ? () => setOpen(false) : undefined}
                      className="flex items-start gap-2.5 px-2 py-2.5 rounded-lg hover:bg-muted/50 transition-colors min-h-[44px]"
                    >
                      <Icon className="w-4 h-4 text-primary/60 shrink-0 mt-0.5" />
                      <div className="min-w-0 flex-1">
                        <span className="text-sm block truncate">{s.title}</span>
                        {s.passage && (
                          <span className="text-[11px] text-muted-foreground line-clamp-2 mt-0.5 leading-relaxed">
                            {s.passage}
                          </span>
                        )}
                      </div>
                      <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/40 shrink-0" />
                    </a>
                  ) : (
                    <div key={i} className="flex items-center gap-2.5 px-2 py-2.5 min-h-[44px]">
                      <Icon className="w-4 h-4 text-muted-foreground/40 shrink-0" />
                      <span className="text-sm text-muted-foreground truncate">{s.title}</span>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </SheetContent>
      </Sheet>
    </>
  );
}

// ─── Activity strip ────────────────────────────────────────────────────────────

/** Compact status bar shown above the composer while the AI is generating.
 *  Tapping the chevron opens a detail sheet listing each inferred step. */
function ActivityStrip({
  steps, fading, onExpand,
}: {
  steps: ActivityStep[];
  fading: boolean;
  onExpand: () => void;
}) {
  // Re-render every second to keep elapsed counters live
  const [, setTick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, []);

  const active = steps.find(s => !s.done);
  const label = active?.label ?? steps.at(-1)?.label ?? "Working…";
  const elapsedS = active ? Math.floor((Date.now() - active.startMs) / 1000) : 0;

  return (
    <div
      className={`px-4 shrink-0 border-t border-primary/10 bg-primary/5
        flex items-center gap-3 ${fading ? "activity-strip-fading" : ""}`}
      style={{ minHeight: 44 }}
    >
      <div className="flex items-center gap-2 flex-1 min-w-0">
        <span className="w-2 h-2 rounded-full bg-primary/70 animate-pulse shrink-0" />
        <span className="text-xs font-mono text-primary/80 truncate">{label}</span>
        {elapsedS > 0 && (
          <span className="text-[10px] font-mono text-muted-foreground/50 shrink-0">{elapsedS}s</span>
        )}
      </div>
      <button
        onClick={onExpand}
        title="Show activity detail"
        className="chat-icon-btn rounded text-muted-foreground/60 hover:text-muted-foreground transition-colors shrink-0"
      >
        <ChevronDown className="w-4 h-4" />
      </button>
    </div>
  );
}

// ─── Activity sheet ────────────────────────────────────────────────────────────

const ACTIVITY_STEP_ICONS: Record<ActivityStep["icon"], React.ReactNode> = {
  search: <Search className="w-3.5 h-3.5" />,
  read:   <FileText className="w-3.5 h-3.5" />,
  think:  <Brain className="w-3.5 h-3.5" />,
  write:  <Bot className="w-3.5 h-3.5" />,
};

function ActivitySheet({
  open, onOpenChange, steps,
}: {
  open: boolean;
  onOpenChange: (v: boolean) => void;
  steps: ActivityStep[];
}) {
  // Re-render every second while sheet is open so elapsed times update
  const [, setTick] = useState(0);
  useEffect(() => {
    if (!open) return;
    const id = setInterval(() => setTick(t => t + 1), 1000);
    return () => clearInterval(id);
  }, [open]);

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="px-0">
        <SheetHeader className="px-6 pb-3 border-b border-border/40">
          <SheetTitle className="text-sm font-serif">Activity</SheetTitle>
        </SheetHeader>
        <div className="px-6 py-4 space-y-3">
          {steps.length === 0 ? (
            <p className="text-sm text-muted-foreground text-center py-4">No activity recorded</p>
          ) : (
            steps.map(step => {
              const elapsed = step.done && step.endMs
                ? Math.floor((step.endMs - step.startMs) / 1000)
                : Math.floor((Date.now() - step.startMs) / 1000);
              return (
                <div key={step.id} className="flex items-center gap-3 min-h-[44px]">
                  <div className={`w-7 h-7 rounded-md flex items-center justify-center shrink-0 ${
                    step.done
                      ? "bg-primary/10 text-primary"
                      : "bg-muted/60 text-muted-foreground"
                  }`}>
                    {step.done
                      ? <Check className="w-3.5 h-3.5" />
                      : <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    }
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-sm">{step.label}</p>
                    <p className="text-[10px] font-mono text-muted-foreground/60">
                      {ACTIVITY_STEP_ICONS[step.icon]}
                    </p>
                  </div>
                  <span className="text-[11px] font-mono text-muted-foreground/50 shrink-0">
                    {elapsed > 0 ? `${elapsed}s` : "—"}
                  </span>
                </div>
              );
            })
          )}
        </div>
      </SheetContent>
    </Sheet>
  );
}

// ─── Action confirmation card ─────────────────────────────────────────────────

/**
 * Shown inside an assistant message when the AI detects an action intent.
 * The user clicks "Run" to call the execute endpoint directly — no further
 * confirmation step required since the message itself IS the confirmation.
 */
function ActionConfirmCard({
  actionName,
  actionInputs,
  confirmMessage,
}: {
  actionName: string;
  actionInputs?: Record<string, unknown>;
  confirmMessage?: string;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [result, setResult] = useState<{ output_label?: string; download_url?: string; summary?: string } | null>(null);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  const label = actionName.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

  const handleRun = async () => {
    setStatus("running");
    setErrMsg(null);
    try {
      const r = await apiFetch(`${API_BASE}/api/actions/${actionName}/execute`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(actionInputs ?? {}),
      });
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: "Action failed" }));
        throw new Error(err.detail ?? "Action failed");
      }
      const data = await r.json();
      setResult(data);
      setStatus("done");
    } catch (e: unknown) {
      setErrMsg(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  };

  if (status === "done" && result) {
    return (
      <div className="mt-2 flex items-center gap-2 px-3 py-2 rounded-lg border border-emerald-200/60 bg-emerald-50/40 dark:bg-emerald-950/20 dark:border-emerald-800/30 text-xs">
        <Check className="w-3.5 h-3.5 text-emerald-600 shrink-0" />
        <span className="flex-1 text-emerald-700 dark:text-emerald-300">
          {result.summary ?? result.output_label ?? "Action complete"}
        </span>
        {result.download_url && (
          <a
            href={result.download_url}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 px-2 py-1 rounded bg-emerald-600 text-white hover:bg-emerald-700 transition-colors font-medium shrink-0"
          >
            <Download className="w-3 h-3" />
            Download
          </a>
        )}
      </div>
    );
  }

  return (
    <div className="mt-2 rounded-lg border border-primary/20 bg-primary/5 p-3 space-y-2">
      <div className="flex items-center gap-2">
        <Zap className="w-3.5 h-3.5 text-primary shrink-0" />
        <span className="text-xs font-medium">{label}</span>
      </div>
      {confirmMessage && (
        <p className="text-xs text-muted-foreground leading-relaxed pl-5">
          {confirmMessage}
        </p>
      )}
      {status === "error" && errMsg && (
        <p className="text-xs text-destructive pl-5">{errMsg}</p>
      )}
      <div className="pl-5">
        <Button
          size="sm"
          className="h-7 text-xs gap-1.5"
          disabled={status === "running"}
          onClick={handleRun}
        >
          {status === "running" ? (
            <><Loader2 className="w-3 h-3 animate-spin" />Running…</>
          ) : (
            <><Zap className="w-3 h-3" />Run Action</>
          )}
        </Button>
      </div>
    </div>
  );
}

// ─── Read-more wrapper (progressive disclosure) ────────────────────────────────

/** Collapses long AI responses behind a "Show full response" toggle.
 *  Uses a CSS mask fade on the truncated version for a smooth visual cut-off.
 *  Always renders the full markdown so code blocks, tables, etc. are preserved
 *  in the DOM — only the viewport height is restricted. */
function ReadMore({ text, streaming }: { text: string; streaming?: boolean }) {
  const THRESHOLD = 1200; // chars; short messages show in full
  const [expanded, setExpanded] = useState(false);
  const isLong = text.length > THRESHOLD && !streaming;

  return (
    <div>
      <div className={isLong && !expanded ? "chat-readmore-collapsed" : undefined}>
        <MarkdownContent text={text} />
      </div>
      {isLong && !expanded && (
        <div className="mt-2 pt-1">
          <button
            onClick={() => setExpanded(true)}
            className="chat-icon-btn flex items-center gap-1 text-xs font-mono text-primary/70 hover:text-primary transition-colors"
          >
            <ChevronDown className="w-3.5 h-3.5" />
            Show full response
          </button>
        </div>
      )}
    </div>
  );
}

// ─── Reasoning block ──────────────────────────────────────────────────────────

function ReasoningBlock({ text, streaming }: { text: string; streaming?: boolean }) {
  const [open, setOpen] = useState(!!streaming);

  // Auto-collapse 1.5 s after streaming ends, so the answer takes focus
  useEffect(() => {
    if (!streaming && open) {
      const t = setTimeout(() => setOpen(false), 1500);
      return () => clearTimeout(t);
    }
    return undefined;
  }, [streaming]);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="mb-2.5 rounded-lg border border-violet-200/50 bg-violet-50/40 dark:bg-violet-950/20 dark:border-violet-800/30 overflow-hidden">
      <button
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center gap-2 px-3 py-1.5 text-left hover:bg-violet-100/40 dark:hover:bg-violet-900/20 transition-colors"
      >
        <Brain
          className={`w-3 h-3 text-violet-500/80 shrink-0 ${streaming ? "animate-pulse" : ""}`}
        />
        <span className="text-[11px] font-mono text-violet-600/70 dark:text-violet-400/60 flex-1">
          {streaming ? "Reasoning…" : "Reasoning"}
        </span>
        <ChevronDown
          className={`w-3 h-3 text-violet-400/60 transition-transform duration-200 ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && (
        <div className="px-3 pb-2.5 pt-1.5 border-t border-violet-200/30 dark:border-violet-800/20">
          <p className="text-[12px] font-mono text-violet-700/55 dark:text-violet-300/45 italic leading-relaxed whitespace-pre-wrap">
            {text}
            {streaming && (
              <span className="inline-block w-0.5 h-3 bg-violet-400/60 ml-0.5 animate-pulse align-text-bottom" />
            )}
          </p>
        </div>
      )}
    </div>
  );
}

// ─── Code block with copy button ─────────────────────────────────────────────

function CodeBlock({ lang, className, children }: { lang: string; className?: string; children: React.ReactNode }) {
  const codeRef = useRef<HTMLElement>(null);
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    const text = codeRef.current?.textContent ?? "";
    copyToClipboard(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <span className="block my-3 rounded-lg overflow-hidden border border-white/10 shadow-md">
      <span className="flex items-center justify-between px-3 py-1.5 bg-zinc-800 border-b border-white/10">
        <span className="text-[10px] font-mono uppercase tracking-wide text-zinc-400">{lang || " "}</span>
        <button
          type="button"
          onClick={handleCopy}
          title="Copy code"
          className="flex items-center gap-1 text-[10px] font-mono text-zinc-500 hover:text-zinc-300 transition-colors"
        >
          {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
      </span>
      <code
        ref={codeRef}
        className={`block bg-zinc-900 text-zinc-100 px-4 py-3 text-xs font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto ${className ?? ""}`}
      >
        {children}
      </code>
    </span>
  );
}

// ─── Markdown renderer ────────────────────────────────────────────────────────

function MarkdownContent({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        code: ({ className, children }) => {
          const lang = className?.replace("language-", "").replace(/\s*hljs.*/, "") ?? "";
          const isBlock = className?.startsWith("language-") || className?.startsWith("hljs");
          return isBlock ? (
            <CodeBlock lang={lang} className={className}>
              {children}
            </CodeBlock>
          ) : (
            <code className="bg-zinc-800 text-zinc-200 rounded px-1.5 py-0.5 text-[0.8em] font-mono">
              {children}
            </code>
          );
        },
        pre: ({ children }) => <div className="my-0">{children}</div>,
        ul: ({ children }) => <ul className="list-disc list-inside mb-2 space-y-0.5">{children}</ul>,
        ol: ({ children }) => <ol className="list-decimal list-inside mb-2 space-y-0.5">{children}</ol>,
        li: ({ children }) => <li className="text-sm">{children}</li>,
        h1: ({ children }) => <h1 className="text-base font-semibold mb-1 mt-2">{children}</h1>,
        h2: ({ children }) => <h2 className="text-sm font-semibold mb-1 mt-2">{children}</h2>,
        h3: ({ children }) => <h3 className="text-sm font-medium mb-1 mt-1">{children}</h3>,
        blockquote: ({ children }) => (
          <blockquote className="border-l-2 border-border pl-3 italic text-muted-foreground my-2">{children}</blockquote>
        ),
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2 hover:opacity-70">
            {children}
          </a>
        ),
        strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
        em: ({ children }) => <em className="italic">{children}</em>,
        hr: () => <hr className="border-border my-3" />,
        // Allow data:image/... base64 URLs (generated images) while keeping
        // the default sanitizer for all other URL types.
        img: ({ src, alt }) => {
          const safe =
            typeof src === "string" &&
            (/^data:image\/(png|jpeg|webp|gif);base64,/.test(src) ||
              /^https?:\/\//.test(src) ||
              src.startsWith("/") ||
              src.startsWith("./"));
          if (!safe) return null;
          return (
            <img
              src={src}
              alt={alt ?? ""}
              className="max-w-full rounded-lg border border-border/40 my-2"
            />
          );
        },
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

// ─── Streaming helper ─────────────────────────────────────────────────────────

async function* streamChat(
  convId: string, text: string, signal?: AbortSignal,
  deep = false, scope: "work" | "all" = "work",
  image_b64?: string, image_media_type?: string,
): AsyncGenerator<string> {
  const resp = await fetch(`${API_BASE}/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...buildAuthHeaders() },
    body: JSON.stringify({ text, stream: true, deep, scope, image_b64, image_media_type }),
    credentials: "same-origin",
    keepalive: true,
    signal,
  });

  if (!resp.ok || !resp.body) {
    // Throw so sendText's catch path fires and marks both bubbles as failed,
    // showing the "Not delivered · Retry" control on the user bubble.
    // The user message is already saved on the backend at this point.
    throw new Error(`AI service error: ${resp.status} ${resp.statusText}`);
  }

  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  let intentEmitted = false;
  const SOURCES_PREFIX = "\x02SOURCES\x02";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const lines = buf.split("\n");
    buf = lines.pop() ?? "";
    for (const line of lines) {
      if (!line.startsWith("data: ")) continue;
      const data = line.slice(6).trim();
      if (data === "[DONE]") return;
      try {
        const parsed = JSON.parse(data);
        if (parsed.event === "clarify") {
          yield `${CLARIFY_PREFIX}${parsed.question ?? "Could you clarify what you mean?"}`;
          return;
        }
        // Carry the intent through via a one-time sentinel on the first token
        if (parsed.intent && !intentEmitted) {
          intentEmitted = true;
          yield `${INTENT_PREFIX}${parsed.intent}${INTENT_PREFIX}`;
        }
        // Sources sentinel — emit before any token on this event
        if (parsed.sources) {
          yield `${SOURCES_PREFIX}${JSON.stringify(parsed.sources)}${SOURCES_PREFIX}`;
        }
        // Thinking/reasoning tokens from <think> blocks or reasoning_content
        if (parsed.thinking) yield `${THINKING_PREFIX}${parsed.thinking as string}`;
        // Stream stalled — backend already persisted meta.incomplete + meta.cut_short;
        // yield a sentinel so the caller can mark the bubble incomplete immediately.
        if (parsed.timeout) { yield TIMEOUT_SENTINEL; }
        if (parsed.token) yield parsed.token as string;
      } catch { /* ignore */ }
    }
  }
}

// ─── Work Files Drawer ────────────────────────────────────────────────────────

function WorkFilesDrawer({ workId, workTitle }: { workId: string; workTitle: string }) {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");

  const { data } = useGetWorkDocuments(workId, {
    query: { queryKey: getGetWorkDocumentsQueryKey(workId), enabled: open, staleTime: 30_000 },
  });
  const docs = (data?.documents ?? []).filter(d =>
    !search || (d.title ?? d.source ?? "").toLowerCase().includes(search.toLowerCase())
  );

  const label = (d: { title?: string | null; source?: string | null }) =>
    d.title ?? (d.source ? d.source.split("/").pop() ?? d.source : "Document");

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        title="Work files"
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/60 border border-border/50 transition-colors"
      >
        <FolderOpen className="w-3.5 h-3.5" />
        <span className="hidden sm:inline">Files</span>
      </button>
      <Sheet open={open} onOpenChange={setOpen}>
        <SheetContent side="right" className="w-80 p-0 flex flex-col">
          <SheetHeader className="px-4 py-3 border-b border-border/40">
            <SheetTitle className="text-sm font-serif flex items-center gap-2">
              <BookOpen className="w-4 h-4 text-primary" />
              {workTitle} — Documents
            </SheetTitle>
          </SheetHeader>
          <div className="px-3 py-2 border-b border-border/30">
            <div className="relative">
              <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground pointer-events-none" />
              <input
                value={search}
                onChange={e => setSearch(e.target.value)}
                placeholder="Filter documents…"
                className="w-full pl-7 pr-2 py-1.5 text-xs rounded-md border border-border/50 bg-background outline-none focus:ring-1 focus:ring-primary/40"
              />
            </div>
          </div>
          <ScrollArea className="flex-1">
            {docs.length === 0 ? (
              <p className="text-xs text-muted-foreground text-center py-8">
                {search ? "No matches" : "No documents in this Work yet"}
              </p>
            ) : (
              <div className="p-2 space-y-1">
                {docs.map(d => (
                  <a
                    key={d.id}
                    href={`/library/${d.id}`}
                    onClick={e => { e.preventDefault(); window.location.href = `/library/${d.id}`; }}
                    className="flex items-center gap-2 px-2 py-2 rounded-md hover:bg-muted/50 transition-colors cursor-pointer group"
                  >
                    <FileText className="w-3.5 h-3.5 text-muted-foreground shrink-0" />
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium truncate">{label(d)}</p>
                      <p className="text-[10px] font-mono text-muted-foreground">
                        {d.kind ?? "doc"} · {d.readiness ?? "unknown"}
                      </p>
                    </div>
                    <ChevronRight className="w-3 h-3 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity shrink-0" />
                  </a>
                ))}
              </div>
            )}
          </ScrollArea>
        </SheetContent>
      </Sheet>
    </>
  );
}

// ─── Artifact tracker ─────────────────────────────────────────────────────────

/** Detects markdown download links in assistant messages and shows a pill. */
const FILE_LINK_RE = /\[([^\]]+)\]\(([^)]+\.(?:txt|md|csv|json|pdf|docx|xlsx|zip|py|js|ts|html|xml))\)/gi;
const ALLOWED_SCHEMES = new Set(["http:", "https:", "blob:"]);

/** Returns the URL only if it is safe to navigate/download; rejects javascript:, data:, etc. */
function sanitizeArtifactUrl(raw: string): string | null {
  try {
    // Relative paths (starting with / or .) are safe — no scheme to attack
    if (raw.startsWith("/") || raw.startsWith("./") || raw.startsWith("../")) return raw;
    const parsed = new URL(raw);
    if (!ALLOWED_SCHEMES.has(parsed.protocol)) return null;
    return raw;
  } catch {
    // URL() threw — treat as relative path only if it looks benign (no colon before first slash)
    if (/^[^:]*:/.test(raw)) return null; // has a scheme we couldn't parse → reject
    return raw;
  }
}

function ArtifactTracker({ messages }: { messages: LocalMessage[] }) {
  const [open, setOpen] = useState(false);

  const artifacts: { label: string; url: string; msgId: string }[] = [];
  for (const m of messages) {
    if (m.role !== "assistant" || !m.text) continue;
    let match: RegExpExecArray | null;
    FILE_LINK_RE.lastIndex = 0;
    while ((match = FILE_LINK_RE.exec(m.text)) !== null) {
      const safeUrl = sanitizeArtifactUrl(match[2]);
      if (safeUrl) artifacts.push({ label: match[1], url: safeUrl, msgId: m.id });
    }
  }

  if (artifacts.length === 0) return null;

  return (
    <div className="max-w-3xl mx-auto mb-2 flex items-center gap-2">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-mono bg-primary/10 text-primary border border-primary/20 hover:bg-primary/15 transition-colors"
      >
        <Paperclip className="w-3 h-3" />
        {artifacts.length} file{artifacts.length !== 1 ? "s" : ""} made in this chat
      </button>
      {open && (
        <div className="absolute bottom-24 left-1/2 -translate-x-1/2 z-50 w-72 rounded-xl border border-border bg-popover shadow-lg p-3 space-y-1.5">
          <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground mb-2">Made in this chat</p>
          {artifacts.map((a, i) => (
            <a
              key={i}
              href={a.url}
              download
              className="flex items-center gap-2 px-2 py-1.5 rounded-lg hover:bg-muted/50 transition-colors text-xs"
            >
              <Download className="w-3.5 h-3.5 text-primary shrink-0" />
              <span className="flex-1 truncate font-medium">{a.label}</span>
            </a>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Compass footer ───────────────────────────────────────────────────────────

interface CompassData {
  focus?: string | null;
  last_reasoning?: string | null;
  next_step?: string | null;
  updated_at?: string | null;
}

function useCompass(workId: string | undefined) {
  return useQuery({
    queryKey: ["works", workId, "compass"],
    queryFn: async (): Promise<{ compass: CompassData }> => {
      const r = await apiFetch(`${API_BASE}/works/${workId}/compass`);
      if (!r.ok) return { compass: {} };
      return r.json();
    },
    enabled: !!workId,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** Collapsible Project Compass shown below the last AI message in Work-scoped chats. */
function CompassFooter({ workId }: { workId: string }) {
  const { data } = useCompass(workId);
  const compass = data?.compass;

  if (!compass || (!compass.focus && !compass.last_reasoning && !compass.next_step)) {
    return null;
  }

  return (
    <details className="mt-3 group">
      <summary className="flex items-center gap-1.5 cursor-pointer select-none text-[10px] font-mono text-muted-foreground/50 hover:text-muted-foreground transition-colors list-none">
        <Compass className="w-3 h-3 shrink-0" />
        <span>Project Compass</span>
        <ChevronDown className="w-3 h-3 transition-transform group-open:rotate-180" />
      </summary>
      <div className="mt-2 p-2.5 rounded-lg border border-border/30 bg-muted/20 space-y-1.5 text-xs">
        {compass.focus && (
          <div>
            <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground/60 block">Focus</span>
            <span className="text-foreground/80">{compass.focus}</span>
          </div>
        )}
        {compass.last_reasoning && (
          <div>
            <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground/60 block">Last reasoning</span>
            <span className="text-foreground/70 line-clamp-3">{compass.last_reasoning}</span>
          </div>
        )}
        {compass.next_step && (
          <div>
            <span className="font-mono text-[9px] uppercase tracking-wider text-muted-foreground/60 block">Next step</span>
            <span className="text-foreground/80">{compass.next_step}</span>
          </div>
        )}
      </div>
    </details>
  );
}

// ─── Memory panel ─────────────────────────────────────────────────────────────

type MemoryFact = {
  id: string;
  key: string;
  value: string;
  memory_type?: string | null;
  valid_from?: string | null;
  valid_to?: string | null;
  txn_time?: string | null;
  prev_value?: string | null;
  source_conv_id?: string | null;
  source_evidence_id?: string | null;
  created_at: string;
  // Evidence fields (present when ?include_evidence=1)
  evidence_text?: string | null;
  evidence_source_type?: string | null;
  evidence_source_id?: string | null;
  evidence_conversation_id?: string | null;
  evidence_message_id?: string | null;
};

const MEMORY_TYPE_STYLE: Record<string, { label: string; cls: string }> = {
  episodic:     { label: "episodic",     cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400" },
  semantic:     { label: "semantic",     cls: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400" },
  procedural:   { label: "procedural",   cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400" },
  working:      { label: "working",      cls: "bg-gray-100 text-gray-500 dark:bg-gray-800/50 dark:text-gray-400" },
  zettelkasten: { label: "zettelkasten", cls: "bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400" },
};

// ─── Memory conflict types ────────────────────────────────────────────────────

interface MemoryConflict {
  id: string;
  memory_id_a: string;
  memory_id_b: string;
  detected_at: string;
  resolved: number;
  resolution: string | null;
  resolved_at: string | null;
  key_a: string | null;
  value_a: string | null;
  memory_type_a: string | null;
  key_b: string | null;
  value_b: string | null;
  memory_type_b: string | null;
}

function MemoryPanel({ apiBase }: { apiBase: string }) {
  const [expandedEvidence, setExpandedEvidence] = useState<Set<string>>(new Set());
  const [showConflicts, setShowConflicts] = useState(false);
  const [resolvingId, setResolvingId] = useState<string | null>(null);
  // Inline editing
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editValue, setEditValue] = useState("");
  const [savingId, setSavingId] = useState<string | null>(null);
  // prevValues keyed by fact key (key is stable across edits; id changes after update)
  const [prevValues, setPrevValues] = useState<Record<string, string>>({});

  const { data, isLoading, refetch, isRefetching } = useQuery<{ facts: MemoryFact[]; total: number }>({
    queryKey: ["memory-facts"],
    queryFn: async () => {
      const { buildAuthHeaders } = await import("@/lib/auth");
      const r = await fetch(`${apiBase}/memory?include_evidence=1`, { headers: buildAuthHeaders() });
      if (!r.ok) throw new Error("Failed");
      return r.json();
    },
    staleTime: 30_000,
  });

  const { data: conflictsData, refetch: refetchConflicts } = useQuery<{
    conflicts: MemoryConflict[];
    total: number;
  }>({
    queryKey: ["memory-conflicts"],
    queryFn: async () => {
      const { buildAuthHeaders } = await import("@/lib/auth");
      const r = await fetch(`${apiBase}/memory/conflicts`, { headers: buildAuthHeaders() });
      if (!r.ok) return { conflicts: [], total: 0 };
      return r.json();
    },
    staleTime: 60_000,
  });

  const toggleEvidence = (id: string) =>
    setExpandedEvidence(prev => {
      const next = new Set(prev);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const startEdit = (f: MemoryFact) => {
    setEditingId(f.id);
    setEditValue(f.value);
    // Collapse evidence panel for the fact being edited
    setExpandedEvidence(prev => {
      const next = new Set(prev);
      next.delete(f.id ?? f.key);
      return next;
    });
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditValue("");
  };

  const saveFact = async (factId: string, factKey: string, oldValue: string) => {
    const trimmed = editValue.trim();
    if (!trimmed) { cancelEdit(); return; }
    if (trimmed === oldValue) { cancelEdit(); return; }
    setSavingId(factId);
    try {
      const r = await fetch(`${apiBase}/system/user-memory/${factId}`, {
        method: "PATCH",
        headers: { ...buildAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ value: trimmed }),
      });
      if (!r.ok) throw new Error("Failed");
      // Record the old value keyed by fact key — key is stable after the update
      setPrevValues(pv => ({ ...pv, [factKey]: oldValue }));
      setEditingId(null);
      refetch();
      toast.success("Memory updated");
    } catch {
      toast.error("Could not update memory");
    } finally {
      setSavingId(null);
    }
  };

  const resolveConflict = async (conflictId: string, resolution: string) => {
    setResolvingId(conflictId);
    try {
      const { buildAuthHeaders } = await import("@/lib/auth");
      const r = await fetch(`${apiBase}/memory/conflicts/${conflictId}/resolve`, {
        method: "POST",
        headers: { ...buildAuthHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ resolution }),
      });
      if (!r.ok) throw new Error("Failed");
      refetchConflicts();
      refetch();
    } catch {
      toast.error("Could not resolve conflict");
    } finally {
      setResolvingId(null);
    }
  };

  const facts = data?.facts ?? [];
  const conflicts = conflictsData?.conflicts ?? [];
  const conflictCount = conflicts.length;

  return (
    <div className="border-b border-border/50 bg-violet-500/5">
      <div className="px-4 py-2.5 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          <Sparkles className="w-3 h-3 text-violet-500" />
          <span className="text-xs font-medium text-violet-700 dark:text-violet-300">
            Memory
          </span>
          {facts.length > 0 && (
            <span className="text-[10px] font-mono text-muted-foreground/60 bg-muted/40 rounded px-1">
              {facts.length}
            </span>
          )}
          {conflictCount > 0 && (
            <button
              onClick={() => setShowConflicts(v => !v)}
              title={`${conflictCount} unresolved conflict${conflictCount !== 1 ? "s" : ""} — click to review`}
              className="flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-orange-100 dark:bg-orange-900/30 text-orange-600 dark:text-orange-400 hover:bg-orange-200 dark:hover:bg-orange-900/50 transition-colors"
            >
              <AlertTriangle className="w-2.5 h-2.5" />
              <span className="text-[9px] font-semibold">{conflictCount}</span>
            </button>
          )}
        </div>
        <button
          onClick={() => { refetch(); refetchConflicts(); }}
          disabled={isRefetching}
          title="Refresh"
          className="p-1 rounded text-muted-foreground/50 hover:text-muted-foreground transition-colors disabled:opacity-40"
        >
          <RefreshCw className={`w-3 h-3 ${isRefetching ? "animate-spin" : ""}`} />
        </button>
      </div>

      {/* ── Conflicts panel ── */}
      {showConflicts && conflictCount > 0 && (
        <div className="px-4 pb-3 space-y-2 border-b border-orange-200/40 dark:border-orange-800/30">
          <div className="text-[9px] font-semibold uppercase tracking-wide text-orange-500/80 mb-1.5">
            Conflicting memories ({conflictCount})
          </div>
          {conflicts.map(c => (
            <div
              key={c.id}
              className="rounded border border-orange-200/60 dark:border-orange-800/40 bg-orange-50/40 dark:bg-orange-950/20 p-2 space-y-1.5"
            >
              <div className="flex gap-2 text-[10px]">
                {/* Side A — memory_id_a (newer by convention when set by dedup) */}
                <div className="flex-1 space-y-0.5">
                  <div className="text-[9px] text-muted-foreground/50 font-mono uppercase">Newer</div>
                  <div className="font-mono text-violet-600/80 dark:text-violet-400/80 truncate">{c.key_a ?? "—"}:</div>
                  <div className="text-foreground/70 line-clamp-2">{c.value_a ?? "—"}</div>
                </div>
                <div className="w-px bg-orange-200/60 dark:bg-orange-800/40 self-stretch" />
                {/* Side B — memory_id_b (older by convention) */}
                <div className="flex-1 space-y-0.5">
                  <div className="text-[9px] text-muted-foreground/50 font-mono uppercase">Older</div>
                  <div className="font-mono text-violet-600/80 dark:text-violet-400/80 truncate">{c.key_b ?? "—"}:</div>
                  <div className="text-foreground/70 line-clamp-2">{c.value_b ?? "—"}</div>
                </div>
              </div>
              <div className="flex gap-1 justify-end flex-wrap">
                <button
                  disabled={resolvingId === c.id}
                  onClick={() => resolveConflict(c.id, "keep_a")}
                  title={`Keep: ${c.key_a ?? "this"} = ${(c.value_a ?? "").slice(0, 60)}`}
                  className="text-[9px] px-1.5 py-0.5 rounded border border-border/50 bg-background/60 hover:bg-muted/60 transition-colors disabled:opacity-40"
                >
                  Keep newer
                </button>
                <button
                  disabled={resolvingId === c.id}
                  onClick={() => resolveConflict(c.id, "keep_b")}
                  title={`Keep: ${c.key_b ?? "this"} = ${(c.value_b ?? "").slice(0, 60)}`}
                  className="text-[9px] px-1.5 py-0.5 rounded border border-border/50 bg-background/60 hover:bg-muted/60 transition-colors disabled:opacity-40"
                >
                  Keep older
                </button>
                <button
                  disabled={resolvingId === c.id}
                  onClick={() => resolveConflict(c.id, "dismissed")}
                  className="text-[9px] px-1.5 py-0.5 rounded border border-border/50 bg-background/60 hover:bg-muted/60 transition-colors disabled:opacity-40"
                >
                  Dismiss
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {isLoading ? (
        <div className="px-4 pb-3 space-y-1.5">
          {[1, 2, 3].map(i => <Skeleton key={i} className="h-6 w-full rounded" />)}
        </div>
      ) : facts.length === 0 ? (
        <div className="px-4 pb-3 text-[11px] text-muted-foreground/60 italic">
          No facts yet — they're captured automatically as you chat.
        </div>
      ) : (
        <div className="px-4 pb-3 space-y-2 max-h-64 overflow-y-auto">
          {facts.map((f) => {
            const fid = f.id ?? f.key;
            const typeStyle = MEMORY_TYPE_STYLE[f.memory_type ?? "semantic"] ?? MEMORY_TYPE_STYLE.semantic;
            const hasEvidence = Boolean(f.evidence_text);
            const evidenceOpen = expandedEvidence.has(fid);
            const isEditing = editingId === fid;
            const prevVal = prevValues[f.key];

            return (
              <div key={fid} className="text-[11px] leading-snug">
                {isEditing ? (
                  /* ── Edit mode ────────────────────────────────────────── */
                  <div className="space-y-1.5">
                    <div className="flex items-center gap-1">
                      <span className={`text-[9px] font-mono font-semibold px-1 py-0.5 rounded shrink-0 ${typeStyle.cls}`}>
                        {typeStyle.label}
                      </span>
                      <span className="font-mono text-violet-600/80 dark:text-violet-400/80 shrink-0">
                        {f.key}:
                      </span>
                    </div>
                    <input
                      // eslint-disable-next-line jsx-a11y/no-autofocus
                      autoFocus
                      value={editValue}
                      onChange={e => setEditValue(e.target.value)}
                      onKeyDown={e => {
                        if (e.key === "Enter") saveFact(fid, f.key, f.value);
                        if (e.key === "Escape") cancelEdit();
                      }}
                      className="w-full text-[11px] border border-violet-400/40 rounded px-2 py-1 bg-background focus:outline-none focus:ring-1 focus:ring-violet-400/50"
                    />
                    <div className="flex items-center gap-1.5">
                      <button
                        onClick={() => saveFact(fid, f.key, f.value)}
                        disabled={savingId === fid || !editValue.trim() || editValue.trim() === f.value}
                        className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/80 hover:bg-violet-500 text-white disabled:opacity-40 transition-colors flex items-center gap-0.5"
                      >
                        {savingId === fid
                          ? <Loader2 className="w-2.5 h-2.5 animate-spin" />
                          : <Check className="w-2.5 h-2.5" />}
                        Save
                      </button>
                      <button
                        onClick={cancelEdit}
                        className="text-[9px] px-1.5 py-0.5 rounded border border-border/50 hover:bg-muted/50 transition-colors"
                      >
                        Cancel
                      </button>
                    </div>
                  </div>
                ) : (
                  /* ── View mode ────────────────────────────────────────── */
                  <div className="flex items-start gap-1 flex-wrap group">
                    <span className={`text-[9px] font-mono font-semibold px-1 py-0.5 rounded shrink-0 mt-px ${typeStyle.cls}`}>
                      {typeStyle.label}
                    </span>
                    <span className="font-mono text-violet-600/80 dark:text-violet-400/80 shrink-0">
                      {f.key}:
                    </span>
                    <span className="text-foreground/80 flex-1">{f.value}</span>
                    <button
                      onClick={() => startEdit(f)}
                      title="Edit this memory"
                      className="shrink-0 text-transparent group-hover:text-muted-foreground/40 hover:!text-muted-foreground/70 transition-colors"
                    >
                      <Pencil className="w-2.5 h-2.5" />
                    </button>
                    {hasEvidence && (
                      <button
                        onClick={() => toggleEvidence(fid)}
                        title={evidenceOpen ? "Hide source" : "Show source"}
                        className="shrink-0 text-muted-foreground/40 hover:text-muted-foreground/70 transition-colors"
                      >
                        <ChevronDown className={`w-3 h-3 transition-transform ${evidenceOpen ? "rotate-180" : ""}`} />
                      </button>
                    )}
                  </div>
                )}

                {/* Previously row — shown after a successful edit */}
                {!isEditing && prevVal && (
                  <div className="text-[9px] text-muted-foreground/40 mt-0.5 font-mono truncate">
                    Previously: {prevVal}
                  </div>
                )}

                {!isEditing && f.valid_from && (
                  <div className="text-[9px] text-muted-foreground/40 mt-0.5 font-mono">
                    valid from {f.valid_from.slice(0, 10)}
                    {f.valid_to ? ` → ${f.valid_to.slice(0, 10)}` : ""}
                  </div>
                )}
                {!isEditing && hasEvidence && evidenceOpen && (
                  <div className="mt-1.5 rounded border border-border/40 bg-muted/30 p-2 space-y-1">
                    <div className="text-[9px] font-semibold text-muted-foreground/60 uppercase tracking-wide">
                      Source · {f.evidence_source_type ?? "conversation"}
                    </div>
                    <p className="text-[10px] text-foreground/60 leading-relaxed line-clamp-4 whitespace-pre-wrap break-words">
                      {f.evidence_text}
                    </p>
                    {f.evidence_conversation_id && (
                      <a
                        href={`/chat?id=${f.evidence_conversation_id}`}
                        className="inline-flex items-center gap-0.5 text-[9px] text-violet-500/70 hover:text-violet-500 transition-colors"
                      >
                        <ExternalLink className="w-2.5 h-2.5" />
                        View conversation
                      </a>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function Chat() {
  const [, setLocation] = useLocation();
  const searchParams = new URLSearchParams(window.location.search);
  const activeId = searchParams.get("id");
  // When arriving from a message-search result, this holds the target message id.
  const highlightMsgId = searchParams.get("msg");

  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [showMemory, setShowMemory] = useState(false);
  // Seed composer from ?draft= URL param — set by the dashboard Explore button
  // so the suggestion text arrives pre-filled and ready to send.
  const [draft, setDraft] = useState(() => {
    try {
      return new URLSearchParams(window.location.search).get("draft") ?? "";
    } catch { return ""; }
  });
  const [sending, setSending] = useState(false);
  const [pendingImage, setPendingImage] = useState<{ data: string; type: string } | null>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const localOverride = localMessages.length > 0;

  // Tab-focus resilience refs
  const accumulatorRef = useRef("");
  const thinkingAccRef = useRef("");   // accumulates reasoning tokens during streaming
  const assistantIdRef = useRef("");
  const userMsgIdRef   = useRef("");   // tracks the latest user message id for status updates
  const rafRef = useRef<number | null>(null);
  const messagesEndRef    = useRef<HTMLDivElement>(null);
  const msgsContainerRef  = useRef<HTMLDivElement>(null);  // scroll anchor target
  // Synchronous sending flag (avoids stale closure in RAF loop) + abort controller
  const sendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  // Track the last message the user sent so the re-send button can restore it
  const lastSentRef = useRef<string>("");

  // ── Predictive composer state ───────────────────────────────────────────────
  const [prediction, setPrediction] = useState<{ghost: string; sources: KnowledgeSource[]} | null>(null);
  const predictDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const predictAbortRef = useRef<AbortController | null>(null);

  const { data: convsResp, isLoading: loadingList } = useListConversations(
    { archived: showArchived || undefined },
    { query: { queryKey: getListConversationsQueryKey({ archived: showArchived || undefined }), refetchInterval: 15_000, staleTime: 10_000 } }
  );
  const { data: activeConv, isLoading: loadingActive } = useGetConversation(activeId!, {
    query: { enabled: !!activeId, queryKey: getGetConversationQueryKey(activeId!) },
  });
  const { aiReachable: aiOnline, recheckNow: recheckHealth } = useConnectivity();
  const { data: modelsData } = useModels();
  const models = modelsData?.models ?? [];
  const defaultModel = modelsData?.default ?? "";

  const [deepMode,        setDeepMode]        = useState(false);
  const [scopeAll,        setScopeAll]        = useState(false); // false = "This work", true = "All works"
  const [webSearchOn,     setWebSearchOn]     = useState(false); // per-conversation web-search toggle
  const [mailContextOn,   setMailContextOn]   = useState(false); // per-conversation mail context toggle
  const [dragOver,        setDragOver]        = useState(false);
  const [importing,       setImporting]       = useState(false);

  // Whether Tavily is configured — gates the globe button visibility
  const { data: webSearchStatus } = useGetWebSearchStatus();
  const tavilyConfigured = webSearchStatus?.configured ?? false;

  // Whether Mail Steward is connected — gates the mail context button visibility
  const { data: mailSummary } = useQuery({
    queryKey: ["mail-summary"],
    queryFn: async () => {
      const r = await apiFetch(`${API_BASE}/mail/summary`);
      return r.ok ? (r.json() as Promise<{ connected: boolean }>) : { connected: false };
    },
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
  const mailConnected = mailSummary?.connected ?? false;

  // ── Activity panel state ─────────────────────────────────────────────────
  const [activitySteps,      setActivitySteps]      = useState<ActivityStep[]>([]);
  const [activitySheetOpen,  setActivitySheetOpen]  = useState(false);
  const [activityFading,     setActivityFading]     = useState(false);
  // Tracks which generation the current activity belongs to.
  // The fade-out timeout captures this value; if a new send starts before the
  // timer fires, the IDs won't match and the timeout is a no-op.
  const activityGenRef      = useRef(0);
  const activityFadeTimer   = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [newConvModel, setNewConvModel] = useState<string>(() => {
    try { return localStorage.getItem("orivellum:lastModel") ?? ""; } catch { return ""; }
  });
  const [newConvPersona, setNewConvPersona] = useState<string>("default");
  const createConv = useCreateConversation();
  const deleteConv = useDeleteConversation();
  const updateConvMeta = useUpdateConversation();

  const invalidateActive = useCallback(() => {
    if (activeId) queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
  }, [activeId, queryClient]);

  // Rename state for sidebar
  const [renamingId, setRenamingId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const startRename = (e: React.MouseEvent, id: string, currentTitle: string) => {
    e.stopPropagation();
    setRenamingId(id);
    setRenameValue(currentTitle || "");
  };

  const commitRename = (id: string) => {
    const trimmed = renameValue.trim();
    if (trimmed) {
      updateConvMeta.mutate(
        { convId: id, data: { title: trimmed } },
        {
          onSuccess: () => queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() }),
          onError: () => toast.error("Could not rename conversation"),
        }
      );
    }
    setRenamingId(null);
  };

  // Resolve linked work title for the chat header (use activeConv to avoid ordering issues)
  const convWorkId = activeConv?.conversation?.work_id ?? undefined;
  const { data: linkedWorkResp } = useGetWork(convWorkId ?? "", {
    query: { queryKey: getGetWorkQueryKey(convWorkId ?? ""), enabled: !!convWorkId },
  });
  const linkedWorkTitle = linkedWorkResp?.work?.title ?? undefined;

  useEffect(() => { setLocalMessages([]); setDraft(""); }, [activeId]);
  useEffect(() => { if (activeConv?.messages && !sending) setLocalMessages([]); }, [activeConv?.messages, sending]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [localMessages, activeConv?.messages]);

  // ── Scroll-to-message when arriving from a search result ──────────────────
  // When the ?msg= URL param is present and the conversation messages have
  // loaded, scroll that specific message element into view and briefly
  // highlight it with a yellow flash so the user can spot it in context.
  useEffect(() => {
    if (!highlightMsgId || !activeConv?.messages) return;
    // Small delay so the DOM has rendered before we query it
    const timer = setTimeout(() => {
      const el = document.querySelector(`[data-msg-id="${highlightMsgId}"]`);
      if (el) {
        el.scrollIntoView({ behavior: "smooth", block: "center" });
        el.classList.add("msg-highlight");
        setTimeout(() => el.classList.remove("msg-highlight"), 2500);
      }
    }, 300);
    return () => clearTimeout(timer);
  }, [highlightMsgId, activeConv?.messages]);

  // Abort any in-progress stream when conversation changes or component unmounts
  useEffect(() => {
    return () => {
      if (sendingRef.current && abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, [activeId]);

  // Sync webSearchOn from the active conversation (re-runs whenever conversation changes)
  useEffect(() => {
    const ws = (activeConv?.conversation as any)?.web_search_enabled;
    setWebSearchOn(!!ws);
  }, [activeId, activeConv?.conversation]);

  // Sync mailContextOn from the active conversation
  useEffect(() => {
    const mc = (activeConv?.conversation as any)?.mail_context_enabled;
    setMailContextOn(!!mc);
  }, [activeId, activeConv?.conversation]);

  // ── VisualViewport scroll-anchor preservation ────────────────────────────
  // When the iPhone keyboard opens, --visual-viewport-height shrinks.  Without
  // a scroll correction the visible messages jump upward.  We compensate by
  // shifting scrollTop by the same delta so the user's reading position stays
  // fixed relative to the screen.
  const prevVvhRef = useRef<number>(0);
  useEffect(() => {
    const vv = window.visualViewport;
    if (!vv) return;
    prevVvhRef.current = vv.height;
    const handler = () => {
      const delta = prevVvhRef.current - vv.height;   // positive = keyboard opened
      if (delta > 10 && msgsContainerRef.current) {
        msgsContainerRef.current.scrollTop += delta;
      }
      prevVvhRef.current = vv.height;
    };
    vv.addEventListener("resize", handler, { passive: true });
    return () => vv.removeEventListener("resize", handler);
  }, []);

  // #40 — When AI comes back online: toast + refetch active conversation + conversation list
  const prevAiOnlineRef = useRef<boolean | undefined>(undefined);
  useEffect(() => {
    if (prevAiOnlineRef.current === false && aiOnline === true) {
      toast.success("AI is back online", { duration: 3000 });
      // Refetch the active conversation so any missed/partial state is resolved
      if (activeId) {
        queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
      }
      queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
    }
    prevAiOnlineRef.current = aiOnline;
  }, [aiOnline, activeId, queryClient]);

  // Tab-focus flush — updates both main text and thinking accumulator
  const flushAccumulator = useCallback(() => {
    const text = accumulatorRef.current;
    const thinking = thinkingAccRef.current;
    const id = assistantIdRef.current;
    if (!id || (!text && !thinking)) return;
    setLocalMessages((prev) =>
      prev.map((m) =>
        m.id === id
          ? {
              ...m,
              ...(text ? { text } : {}),
              ...(thinking ? { thinking, thinkingStreaming: true } : {}),
              streaming: true,
            }
          : m
      )
    );
  }, []);

  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible" && sendingRef.current) flushAccumulator(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [flushAccumulator]);

  // ── File drag/drop → auto-import ─────────────────────────────────────────
  const handleDrop = useCallback(async (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (!files.length) return;
    setImporting(true);
    try {
      for (const file of files.slice(0, 3)) {
        // Read file as base64 — the API expects JSON { filename, content_b64, work_id }
        const arrayBuf = await file.arrayBuffer();
        const bytes = new Uint8Array(arrayBuf);
        let binary = "";
        bytes.forEach(b => { binary += String.fromCharCode(b); });
        const content_b64 = btoa(binary);

        const r = await apiFetch(`${API_BASE}/library/import`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            filename: file.name,
            content_b64,
            ...(convWorkId ? { work_id: convWorkId } : {}),
          }),
        });
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          toast.error(`Could not import ${file.name}: ${(err as any)?.detail ?? r.status}`);
          continue;
        }
        setDraft(prev => (prev ? `${prev}\nload: ${file.name}` : `load: ${file.name}`));
        toast.success(`Imported ${file.name}`);
      }
    } catch {
      toast.error("Import failed");
    } finally {
      setImporting(false);
    }
  }, [convWorkId]);

  const handleCreate = (modelOverride?: string, personaOverride?: string) => {
    const chosenModel = modelOverride ?? newConvModel;
    const chosenPersona = personaOverride ?? newConvPersona;
    createConv.mutate(
      { data: { title: "New Conversation", ...(chosenModel ? { model: chosenModel } : {}), ...(chosenPersona && chosenPersona !== "default" ? { persona_id: chosenPersona } : {}) } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
        onError: () => toast.error("Could not create conversation"),
      }
    );
  };

  const handleDelete = (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    deleteConv.mutate({ convId }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        if (activeId === convId) setLocation("/chat");
        toast.success("Conversation deleted");
      },
      onError: () => toast.error("Could not delete conversation"),
    });
  };

  // ── Predictive composer — debounced ghost-text fetch ─────────────────────
  useEffect(() => {
    if (predictDebounceRef.current) clearTimeout(predictDebounceRef.current);
    predictAbortRef.current?.abort();

    if (!draft.trim() || draft.length < 8 || sending || !activeId || !aiOnline) {
      setPrediction(null);
      return;
    }

    predictDebounceRef.current = setTimeout(async () => {
      const ctrl = new AbortController();
      predictAbortRef.current = ctrl;
      try {
        const resp = await apiFetch(`${API_BASE}/conversations/${activeId}/predict`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ draft }),
          signal: ctrl.signal,
        });
        if (ctrl.signal.aborted) return;
        if (!resp.ok) { setPrediction(null); return; }
        const data = await resp.json() as { ghost: string; sources: KnowledgeSource[] };
        if (!ctrl.signal.aborted && data.ghost) {
          setPrediction({ ghost: data.ghost, sources: data.sources ?? [] });
        } else {
          setPrediction(null);
        }
      } catch {
        setPrediction(null);
      }
    }, 800);

    return () => {
      if (predictDebounceRef.current) clearTimeout(predictDebounceRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft, sending, activeId, aiOnline]);

  // ── Core send logic (called by handleSend and the Re-send button) ────────
  const sendText = useCallback(
    async (text: string) => {
      if (!text || !activeId || sendingRef.current) return;

      lastSentRef.current = text;
      // Capture convId now — activeId may change before the stream finishes
      const convId = activeId;
      // Capture work context for activity label (closed over at call time)
      const workIdForActivity = activeConv?.conversation?.work_id ?? undefined;
      setSending(true);
      sendingRef.current = true;

      // ── Initialise activity steps (cancel any prior fade timer first) ────
      if (activityFadeTimer.current !== null) {
        clearTimeout(activityFadeTimer.current);
        activityFadeTimer.current = null;
      }
      activityGenRef.current += 1;
      const thisGen = activityGenRef.current;
      setActivityFading(false);
      setActivitySteps([{
        id: "s1",
        label: workIdForActivity ? "Searching project files" : "Thinking…",
        icon: workIdForActivity ? "search" : "think",
        startMs: Date.now(),
        done: false,
      }]);

      const controller = new AbortController();
      abortRef.current = controller;

      const serverMsgs: LocalMessage[] = (activeConv?.messages ?? []).map((m) => ({
        id: m.id ?? randomUUID(),
        role: m.role as "user" | "assistant",
        text: m.text ?? "",
        created_at: m.created_at ?? new Date().toISOString(),
        meta: (m as any).meta as Record<string, unknown> | undefined,
        isClarification: !!(m as any).meta?.isClarification,
      }));

      const capturedImage = pendingImage;
      setPendingImage(null);
      const userMsgId = randomUUID();
      userMsgIdRef.current = userMsgId;
      const userMsg: LocalMessage = {
        id: userMsgId, role: "user", text,
        created_at: new Date().toISOString(),
        status: "sending",
        image_b64: capturedImage?.data,
        image_media_type: capturedImage?.type,
      };
      const assistantId = randomUUID();
      assistantIdRef.current = assistantId;
      accumulatorRef.current = "";
      thinkingAccRef.current = "";
      // Capture the effective model so the attribution label shows during streaming
      const effectiveModel = conv?.model || defaultModel || undefined;

      setLocalMessages([...serverMsgs, userMsg, { id: assistantId, role: "assistant", text: "", created_at: new Date().toISOString(), status: "streaming", streaming: true, meta: effectiveModel ? { model: effectiveModel } : undefined }]);

      // Use sendingRef (not stale-closure `sending`) so the RAF loop continues in background tabs
      const scheduleFlush = () => {
        rafRef.current = requestAnimationFrame(() => {
          flushAccumulator();
          if (sendingRef.current || accumulatorRef.current) scheduleFlush();
        });
      };
      scheduleFlush();

      let streamedIntent: string | undefined;
      let streamedSources: KnowledgeSource[] | undefined;
      const SOURCES_PREFIX = "\x02SOURCES\x02";
      // On the first token we upgrade the user message from "sending" → "acknowledged"
      // so the "Sending…" indicator disappears as soon as the server starts responding.
      let userAcknowledged = false;
      let firstTextToken = true; // used to advance activity step to "Writing response"
      try {
        for await (const token of streamChat(convId, text, controller.signal, deepMode, scopeAll ? "all" : "work", capturedImage?.data, capturedImage?.type)) {
          if (!userAcknowledged) {
            userAcknowledged = true;
            setLocalMessages((prev) => prev.map((m) =>
              m.id === userMsgId ? { ...m, status: "acknowledged" as const } : m
            ));
            // Activity: step 1 done → advance to "Reading context / Preparing answer"
            setActivitySteps(prev => [
              { ...prev[0], done: true, endMs: Date.now() },
              {
                id: "s2",
                label: workIdForActivity ? "Reading context" : "Preparing answer",
                icon: workIdForActivity ? "read" : "think" as const,
                startMs: Date.now(),
                done: false,
              },
            ]);
          }
          if (token.startsWith(SOURCES_PREFIX) && token.endsWith(SOURCES_PREFIX) && token.length > SOURCES_PREFIX.length * 2) {
            try {
              streamedSources = JSON.parse(token.slice(SOURCES_PREFIX.length, -SOURCES_PREFIX.length));
            } catch {}
            continue;
          }
          if (token === TIMEOUT_SENTINEL) {
            // Stream timed out — mark the assistant bubble as resumable immediately
            // so the user sees the re-send affordance without waiting for [DONE].
            setLocalMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, incomplete: true } : m
            ));
            continue;
          }
          if (token.startsWith(CLARIFY_PREFIX)) {
            // Cognition gate requests clarification — backend persisted with { model, isClarification: true }
            const question = token.slice(CLARIFY_PREFIX.length);
            setLocalMessages((prev) => prev.map((m) =>
              m.id === assistantId
                ? { ...m, text: question, status: "complete" as const, streaming: false, isClarification: true,
                    meta: { ...(m.meta ?? {}), model: effectiveModel, isClarification: true } }
                : m
            ));
            break;
          }
          // Intent sentinel — extract intent, don't add to accumulated text
          if (token.startsWith(INTENT_PREFIX) && token.endsWith(INTENT_PREFIX) && token.length > INTENT_PREFIX.length * 2) {
            streamedIntent = token.slice(INTENT_PREFIX.length, -INTENT_PREFIX.length);
            // Reflect intent badge immediately on the streaming bubble
            setLocalMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, intent: streamedIntent } : m
            ));
            continue;
          }
          if (token.startsWith(THINKING_PREFIX)) {
            thinkingAccRef.current += token.slice(THINKING_PREFIX.length);
          } else {
            accumulatorRef.current += token;
            // Activity: first real text token → advance to "Writing response"
            if (firstTextToken) {
              firstTextToken = false;
              setActivitySteps(prev => [
                ...prev.slice(0, -1).map(s => s.done ? s : { ...s, done: true, endMs: Date.now() }),
                { id: "s3", label: "Writing response", icon: "write" as const, startMs: Date.now(), done: false },
              ]);
            }
          }
        }
        const finalText = accumulatorRef.current;
        const finalThinking = thinkingAccRef.current;
        if (finalText || finalThinking) {
          setLocalMessages((prev) => prev.map((m) =>
            m.id === assistantId
              ? {
                  ...m,
                  ...(finalText ? { text: finalText } : {}),
                  ...(finalThinking ? { thinking: finalThinking } : {}),
                  thinkingStreaming: false,
                  status: "complete" as const,
                  streaming: false,
                  intent: streamedIntent ?? m.intent,
                  meta: { ...(m.meta ?? {}), ...(streamedSources ? { sources: streamedSources } : {}) },
                }
              : m
          ));
        }
      } catch (err: any) {
        if (err?.name === "AbortError") {
          // Intentional cancellation (conversation switch or unmount)
          // Mark with partial text if we received anything; backend saves the rest
          const partialText = accumulatorRef.current;
          if (partialText) {
            setLocalMessages((prev) => prev.map((m) =>
              m.id === assistantId ? { ...m, text: partialText, status: "complete" as const, streaming: false, incomplete: true } : m
            ));
          } else {
            setLocalMessages((prev) => prev.filter((m) => m.id !== assistantId));
          }
        } else {
          const errMsg = err?.message ?? String(err);
          const errLabel = (errMsg.includes("503") || errMsg.includes("Service Unavailable") || errMsg.includes("AI"))
            ? "AI service unavailable — check Engine Settings"
            : "Message failed to send";
          // Mark both the user and assistant messages as failed so the UI can
          // show "Not delivered — Retry" beneath the user bubble and remove the
          // assistant bubble (which just shows the error label for now).
          setLocalMessages((prev) => prev.map((m) => {
            if (m.id === assistantId) return { ...m, text: errLabel, status: "failed" as const, streaming: false };
            if (m.id === userMsgId)   return { ...m, status: "failed" as const };
            return m;
          }));
        }
      } finally {
        if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
        accumulatorRef.current = "";
        thinkingAccRef.current = "";
        assistantIdRef.current = "";
        userMsgIdRef.current = "";
        sendingRef.current = false;
        abortRef.current = null;
        setSending(false);
        // ── Activity: mark all steps done then fade out the strip ──────────
        // Guard with thisGen so a stale timeout from a prior request can never
        // clear activity state that belongs to a newer in-flight request.
        setActivitySteps(prev => prev.map(s => ({ ...s, done: true, endMs: s.endMs ?? Date.now() })));
        setActivityFading(true);
        activityFadeTimer.current = setTimeout(() => {
          if (activityGenRef.current === thisGen) {
            setActivityFading(false);
            setActivitySteps([]);
          }
          activityFadeTimer.current = null;
        }, 600);
        // Invalidate using the captured convId, not the potentially-changed activeId
        queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(convId) });
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        // Clear local messages only if still viewing the same conversation
        // (otherwise the activeId-change effect already cleared them)
        // Keep incomplete (truncated) and failed bubbles — both are meaningful states.
        setLocalMessages((prev) => prev.filter((m) => m.incomplete || m.status === "failed"));
      }
    },
    [activeId, deepMode, scopeAll, pendingImage, activeConv?.messages, flushAccumulator, queryClient, defaultModel]
  );

  const handleSend = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      const text = draft.trim();
      if (!text && !pendingImage) return;
      setDraft("");
      setPrediction(null);
      predictAbortRef.current?.abort();
      sendText(text);
    },
    [draft, pendingImage, sendText]
  );

  // ── Continue a cut-short reply (append mode) ─────────────────────────────
  const handleContinue = useCallback(async (messageId: string) => {
    if (!activeId || sendingRef.current) return;
    const convId = activeId;
    setSending(true);
    sendingRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;

    // Seed local state from server messages so we can mutate the target bubble
    const serverMsgs: LocalMessage[] = (activeConv?.messages ?? []).map((m) => ({
      id: m.id ?? "",
      role: m.role as "user" | "assistant",
      text: m.role === "assistant" && (m as any).meta?.cut_short
        ? ((m.text ?? "").endsWith(TRUNCATION_SUFFIX)
            ? (m.text ?? "").slice(0, -TRUNCATION_SUFFIX.length)
            : (m.text ?? ""))
        : (m.text ?? ""),
      created_at: m.created_at ?? "",
      meta: (m as any).meta as Record<string, unknown> | undefined,
      incomplete: !!(m as any).meta?.cut_short || !!(m as any).meta?.incomplete,
      isClarification: !!(m as any).meta?.isClarification,
      intent: (m as any).meta?.intent as string | undefined,
      thinking: (m as any).meta?.thinking as string | undefined,
    }));

    // Find the base (clean) text for the target message
    const targetBase = serverMsgs.find((m) => m.id === messageId)?.text ?? "";
    setLocalMessages(serverMsgs.map((m) =>
      m.id === messageId ? { ...m, incomplete: false, streaming: true } : m
    ));

    let continueTargetId = messageId;
    let accumulated = "";
    let stillCutShort = false;

    try {
      const resp = await fetch(`${API_BASE}/conversations/${convId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...buildAuthHeaders() },
        body: JSON.stringify({ stream: true }),
        credentials: "same-origin",
        signal: controller.signal,
      });
      if (!resp.ok || !resp.body) throw new Error(`Continue failed: ${resp.status}`);

      const reader = resp.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.startsWith("data: ")) continue;
          const data = line.slice(6).trim();
          if (data === "[DONE]") break;
          try {
            const parsed = JSON.parse(data);
            if (parsed.continue_message_id) continueTargetId = parsed.continue_message_id;
            if (parsed.cut_short || parsed.timeout) stillCutShort = true;
            if (parsed.error === "continuation_failed") {
              // Server produced nothing (transport/model error) — keep the
              // Continue affordance so the user can retry.
              stillCutShort = true;
              toast.error("Continuation failed — please try again");
            }
            if (parsed.token) {
              accumulated += parsed.token;
              const snap = accumulated;
              setLocalMessages((prev) => prev.map((m) =>
                m.id === continueTargetId
                  ? { ...m, text: targetBase + snap, streaming: true }
                  : m
              ));
            }
          } catch { /* ignore */ }
        }
      }
    } catch (err: any) {
      if (err?.name !== "AbortError") toast.error("Could not continue — please try again");
    } finally {
      sendingRef.current = false;
      abortRef.current = null;
      setSending(false);
      setLocalMessages((prev) => prev.map((m) =>
        m.id === continueTargetId
          ? { ...m, streaming: false, incomplete: stillCutShort || undefined }
          : m
      ));
      // Give the final local state a moment to render, then hand off to server data
      setTimeout(() => {
        queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(convId) });
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        setLocalMessages([]);
      }, 600);
    }
  }, [activeId, activeConv?.messages, queryClient]);

  const handleImageSelect = useCallback((file: File) => {
    if (!file.type.startsWith("image/")) return;
    const reader = new FileReader();
    reader.onload = (ev) => {
      const dataUrl = ev.target?.result as string;
      // dataUrl = "data:image/jpeg;base64,<b64>" — strip the prefix
      const comma = dataUrl.indexOf(",");
      const b64 = dataUrl.slice(comma + 1);
      setPendingImage({ data: b64, type: file.type });
    };
    reader.readAsDataURL(file);
  }, []);

  const displayMessages: LocalMessage[] = localOverride
    ? localMessages
    : (activeConv?.messages ?? []).map((m) => {
        const rawText = m.text ?? "";
        const hasCutShortMeta = !!(m as any).meta?.cut_short || !!(m as any).meta?.incomplete;
        const isServerTruncated =
          m.role === "assistant" && (hasCutShortMeta || rawText.endsWith(TRUNCATION_SUFFIX));
        return {
          id: m.id ?? "",
          role: m.role as "user" | "assistant",
          // Strip suffix from legacy messages that used the text-suffix approach
          text: !hasCutShortMeta && rawText.endsWith(TRUNCATION_SUFFIX)
            ? rawText.slice(0, -TRUNCATION_SUFFIX.length)
            : rawText,
          created_at: m.created_at ?? "",
          meta: (m as any).meta as Record<string, unknown> | undefined,
          // Restore amber bubble style for persisted clarification messages
          isClarification: !!(m as any).meta?.isClarification,
          // Surface the tool intent badge from persisted meta
          intent: (m as any).meta?.intent as string | undefined,
          // Restore cut-short flag from meta (new) or suffix detection (legacy)
          incomplete: isServerTruncated || undefined,
          // Restore persisted reasoning / chain-of-thought
          thinking: (m as any).meta?.thinking as string | undefined,
        };
      });

  // ID of the last non-streaming AI message — compass footer renders here
  const lastAiMsgId = [...displayMessages].reverse().find(
    m => m.role === "assistant" && !m.streaming
  )?.id ?? null;

  // ── API-backed message content search ───────────────────────────────────
  // When the user types >= 2 chars, search across message content (not just titles).
  // Debounced 400 ms to avoid spamming the API while typing.
  const [msgSearchResults, setMsgSearchResults] = useState<any[]>([]);
  const [msgSearchLoading, setMsgSearchLoading] = useState(false);
  const _searchAbortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (search.trim().length < 2) {
      setMsgSearchResults([]);
      return;
    }
    const timer = setTimeout(async () => {
      _searchAbortRef.current?.abort();
      const ctrl = new AbortController();
      _searchAbortRef.current = ctrl;
      setMsgSearchLoading(true);
      try {
        const API_BASE = import.meta.env.BASE_URL?.replace(/\/$/, "") || "";
        const r = await fetch(
          `${API_BASE}/api/conversations/search?q=${encodeURIComponent(search.trim())}&limit=30`,
          { headers: buildAuthHeaders(), signal: ctrl.signal }
        );
        if (r.ok) {
          const d = await r.json();
          setMsgSearchResults(d.results ?? []);
        }
      } catch {
        // Aborted or network error — ignore
      } finally {
        setMsgSearchLoading(false);
      }
    }, 400);
    return () => clearTimeout(timer);
  }, [search]);

  const isSearchMode = search.trim().length >= 2;

  const filteredConvs = isSearchMode
    ? undefined  // replaced by msgSearchResults in search mode
    : convsResp?.conversations?.filter((c) => {
        return !search || c.title?.toLowerCase().includes(search.toLowerCase()) || c.last_message?.toLowerCase().includes(search.toLowerCase());
      });

  const conv = activeConv?.conversation;

  // Inside the GD Chat app the whole surface flips to the dark token set so
  // the thread reads as one continuous dark workspace; the legacy console
  // keeps the light parchment look untouched.  The wrapper class below
  // covers the first paint before the hook's effect runs.
  const gdDark = useGdDark();

  return (
    <div className={`flex-1 min-h-0 flex gap-0 md:gap-6 animate-in fade-in duration-500 ${gdDark ? "dark text-foreground" : ""}`}>
      {/* ── Sidebar — full-width on mobile when no conv selected ─────── */}
      <Card className={`flex flex-col shrink-0 rounded-xl overflow-hidden border-border/50 w-full md:w-72 ${activeId ? "hidden md:flex" : "flex"}`}>
        <div className="p-4 border-b border-border/50 bg-muted/10 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg font-medium">Conversations</h2>
            <div className="flex items-center gap-0.5">
              <button
                onClick={() => setShowMemory((v) => !v)}
                title="Memory — facts I've learned about you"
                className={`p-1.5 rounded transition-colors ${showMemory ? "text-violet-600 bg-violet-500/10" : "text-muted-foreground hover:text-foreground"}`}
              >
                <Sparkles className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setShowArchived((v) => !v)}
                title={showArchived ? "Show active" : "Show archived"}
                className={`p-1.5 rounded transition-colors ${showArchived ? "text-primary bg-primary/10" : "text-muted-foreground hover:text-foreground"}`}
              >
                <Archive className="w-3.5 h-3.5" />
              </button>
              <Button size="icon" variant="ghost" onClick={() => handleCreate()} disabled={createConv.isPending || showArchived}>
                <Plus className="w-4 h-4" />
              </Button>
            </div>
          </div>
          <div className="flex items-center gap-1.5 text-xs">
            {aiOnline ? (
              <><Wifi className="w-3 h-3 text-emerald-500" /><span className="text-emerald-600 font-mono">AI connected</span></>
            ) : (
              <><WifiOff className="w-3 h-3 text-muted-foreground" /><span className="text-muted-foreground font-mono">AI offline</span></>
            )}
          </div>
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input placeholder="Search…" value={search} onChange={(e) => setSearch(e.target.value)} className="pl-8 h-8 text-xs bg-background" />
          </div>
        </div>

        {/* ── Memory panel — shown when Sparkles button is active ──────────── */}
        {showMemory && <MemoryPanel apiBase={API_BASE} />}

        <ScrollArea className="flex-1">
          <div className="p-2 space-y-0.5">
            {/* ── Message search results (when search >= 2 chars) ───────── */}
            {isSearchMode ? (
              msgSearchLoading
                ? [1, 2, 3].map((i) => <Skeleton key={i} className="h-16 w-full rounded-md mb-1" />)
                : msgSearchResults.length === 0
                  ? <p className="text-xs text-muted-foreground text-center py-6">No messages match "{search}"</p>
                  : msgSearchResults.map((r: any, i: number) => (
                      <div
                        key={r.id ?? i}
                        onClick={() => {
                          setSearch("");
                          setLocation(`/chat?id=${r.conversation_id}&msg=${r.id}`);
                        }}
                        className="p-3 rounded-md cursor-pointer hover:bg-muted/50 transition-colors"
                      >
                        <div className="font-medium text-sm truncate">{r.conv_title || "Untitled"}</div>
                        <div className="text-xs text-muted-foreground mt-0.5 line-clamp-2 leading-relaxed">{r.snippet}</div>
                        <div className="flex items-center justify-between mt-1">
                          {r.work_title && <span className="text-[10px] text-primary/60 flex items-center gap-0.5"><BookOpen className="w-2.5 h-2.5" />{r.work_title}</span>}
                          <span className="text-[10px] font-mono text-muted-foreground/60 ml-auto">{r.created_at ? format(new Date(r.created_at), "MMM d") : ""}</span>
                        </div>
                      </div>
                    ))
            ) : (
            <>
            {loadingList
              ? [1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full rounded-md mb-1" />)
              : filteredConvs?.map((c) => (
                  <div
                    key={c.id}
                    onClick={() => renamingId !== c.id && setLocation(`/chat?id=${c.id}`)}
                    className={`group p-3 rounded-md cursor-pointer transition-colors flex items-start justify-between gap-2 ${activeId === c.id ? "bg-primary/10 text-primary" : "hover:bg-muted/50"}`}
                  >
                    <div className="min-w-0 flex-1">
                      {renamingId === c.id ? (
                        <input
                          autoFocus
                          value={renameValue}
                          onChange={(e) => setRenameValue(e.target.value)}
                          onBlur={() => commitRename(c.id!)}
                          onKeyDown={(e) => {
                            if (e.key === "Enter") commitRename(c.id!);
                            if (e.key === "Escape") setRenamingId(null);
                          }}
                          onClick={(e) => e.stopPropagation()}
                          className="w-full text-sm font-medium bg-background border border-primary/40 rounded px-1.5 py-0.5 outline-none"
                        />
                      ) : (
                        <div className="font-medium text-sm truncate">{c.title || "Untitled"}</div>
                      )}
                      <div className="flex items-center justify-between gap-1 mt-0.5">
                        <div className="text-xs text-muted-foreground truncate flex-1 flex items-center gap-1">
                          {c.work_id && <BookOpen className="w-2.5 h-2.5 shrink-0 text-primary/50" />}
                          {c.last_message ? c.last_message.slice(0, 45) : "No messages"}
                        </div>
                        {c.updated_at && (
                          <span className="text-[10px] font-mono text-muted-foreground/60 shrink-0">
                            {(() => {
                              const d = new Date(c.updated_at);
                              const now = new Date();
                              const diffMin = Math.floor((now.getTime() - d.getTime()) / 60000);
                              if (diffMin < 1) return "now";
                              if (diffMin < 60) return `${diffMin}m`;
                              const diffH = Math.floor(diffMin / 60);
                              if (diffH < 24) return `${diffH}h`;
                              return format(d, "MMM d");
                            })()}
                          </span>
                        )}
                      </div>
                    </div>
                    <div className="flex items-center gap-0.5 opacity-40 group-hover:opacity-100 transition-opacity shrink-0 mt-0.5">
                      {!c.archived && (
                        <button
                          onClick={(e) => { e.stopPropagation(); updateConvMeta.mutate({ convId: c.id!, data: { archived: true } }, { onSuccess: () => { queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() }); toast.success("Archived"); }, onError: () => toast.error("Could not archive") }); }}
                          title="Archive"
                          className="p-0.5 rounded hover:text-amber-600 text-muted-foreground"
                        >
                          <Archive className="w-3 h-3" />
                        </button>
                      )}
                      {!!c.archived && (
                        <button
                          onClick={(e) => { e.stopPropagation(); updateConvMeta.mutate({ convId: c.id!, data: { archived: false } }, { onSuccess: () => { queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() }); toast.success("Restored"); }, onError: () => toast.error("Could not restore") }); }}
                          title="Restore"
                          className="p-0.5 rounded hover:text-emerald-600 text-muted-foreground"
                        >
                          <ArchiveRestore className="w-3 h-3" />
                        </button>
                      )}
                      {!c.archived && (
                        <button
                          onClick={(e) => startRename(e, c.id!, c.title ?? "")}
                          className="p-0.5 rounded hover:text-foreground text-muted-foreground"
                        >
                          <Pencil className="w-3 h-3" />
                        </button>
                      )}
                      <button onClick={(e) => handleDelete(c.id!, e)} className="p-0.5 rounded hover:text-destructive">
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))}
            {!loadingList && !filteredConvs?.length && (
              <div className="py-8 text-center px-4">
                <p className="text-[11px] font-mono" style={{ color: 'var(--ink-faint)' }}>
                  {search ? "No conversations match" : "No conversations yet"}
                </p>
              </div>
            )}
            </>
            )}
          </div>
        </ScrollArea>
      </Card>

      {/* ── Main chat ──────────────────────────────────────────────────── */}
      <Card className={`flex-1 flex flex-col rounded-xl overflow-hidden border-border/50 min-w-0 ${!activeId ? "hidden md:flex" : "flex"}`}>
        {activeId ? (
          <>
            {/* Header */}
            <div className="px-4 md:px-6 py-3.5 border-b border-border/50 bg-muted/10 flex justify-between items-center shrink-0">
              {/* Back button — mobile only */}
              <button
                onClick={() => setLocation("/chat")}
                className="md:hidden mr-2 p-1 rounded hover:bg-muted/50 text-muted-foreground hover:text-foreground transition-colors shrink-0"
                aria-label="Back to conversations"
              >
                <ChevronLeft className="w-5 h-5" />
              </button>
              <div className="min-w-0 flex-1">
                <h2 className="font-serif text-lg font-medium leading-tight truncate text-balance">{conv?.title || "Conversation"}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs font-mono text-muted-foreground">{displayMessages.length} messages</span>
                  {convWorkId && (
                    <a href={`/works/${convWorkId}`} onClick={(e) => { e.stopPropagation(); setLocation(`/works/${convWorkId}`); e.preventDefault(); }}>
                      <Badge variant="secondary" className="text-[10px] h-4 px-1.5 hover:bg-secondary/80 cursor-pointer transition-colors">
                        {linkedWorkTitle ?? "Work linked"}
                      </Badge>
                    </a>
                  )}
                </div>
              </div>
              {/* Right side: scope toggle + Files drawer + model picker */}
              <div className="flex items-center gap-2">
                {convWorkId && (
                  <button
                    onClick={() => setScopeAll(v => !v)}
                    title={scopeAll ? "Searching all works — click for this work only" : "Searching this work only — click for all works"}
                    className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-medium border transition-colors
                      ${scopeAll
                        ? "bg-primary/10 text-primary border-primary/30"
                        : "text-muted-foreground border-border/50 hover:bg-muted/60 hover:text-foreground"}`}
                  >
                    {scopeAll ? <Globe className="w-3.5 h-3.5" /> : <Layers className="w-3.5 h-3.5" />}
                    <span className="hidden sm:inline">{scopeAll ? "All works" : "This work"}</span>
                  </button>
                )}
                {convWorkId && <WorkFilesDrawer workId={convWorkId} workTitle={linkedWorkTitle ?? "Work"} />}
                {models.length > 0 && activeId && (
                  <ModelPicker
                    convId={activeId}
                    currentModel={conv?.model}
                    models={models}
                    defaultModel={defaultModel}
                    onChanged={invalidateActive}
                  />
                )}
              </div>
            </div>

            {/* Messages — plain overflow-y-auto div instead of Radix ScrollArea.
                overscroll-contain prevents scroll chaining to the parent on
                mobile Safari, keeping the composer anchored at the bottom.
                msgsContainerRef drives the VVH scroll-anchor preservation effect. */}
            <div ref={msgsContainerRef} className="flex-1 min-h-0 overflow-y-auto overscroll-contain px-6 py-6">
              <div className="max-w-3xl mx-auto space-y-6">
                {loadingActive && !localOverride ? (
                  <div className="space-y-6">
                    <Skeleton className="h-16 w-2/3 ml-auto rounded-xl" />
                    <Skeleton className="h-24 w-2/3 rounded-xl" />
                  </div>
                ) : displayMessages.length === 0 ? (
                  <div className="text-center py-16">
                    <Bot className="w-10 h-10 mx-auto mb-3" style={{ opacity: 0.2, color: 'var(--green-raw)' }} />
                    <p className="text-[13px] font-serif italic" style={{ color: 'var(--ink-soft)' }}>
                      {aiOnline ? "Send a message to start the conversation." : "AI is offline — start Lemonade or Ollama to enable responses."}
                    </p>
                  </div>
                ) : (
                  <ErrorBoundary label="message list">
                  {/* ── Earlier context summarized indicator ─────────────────
                      Shown at the top of the message list when the server has
                      condensed older exchanges into a rolling summary.  This
                      tells the user their earlier context is still active even
                      though those messages are no longer visible in full. */}
                  {!!(conv as any)?.context_summary && (
                    <div className="flex items-center gap-3 py-1 select-none" aria-label="Earlier context summarized">
                      <div className="flex-1 h-px bg-border/40" />
                      <div className="flex items-center gap-1.5 px-3 py-1 rounded-full border border-border/50 bg-muted/30 text-[11px] font-mono text-muted-foreground/60">
                        <History className="w-3 h-3 shrink-0" />
                        Earlier context summarized
                      </div>
                      <div className="flex-1 h-px bg-border/40" />
                    </div>
                  )}
                  {displayMessages.map((msg, msgIdx) => (
                    <div key={msg.id} data-msg-id={msg.id} data-role={msg.role} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                      <div className={`w-7 h-7 shrink-0 rounded-sm flex items-center justify-center
                        ${msg.isClarification
                          ? "bg-amber-500/15 text-amber-600"
                          : msg.role === "user"
                            ? "bg-secondary text-secondary-foreground"
                            : "bg-primary text-primary-foreground"}`}>
                        {msg.isClarification ? <HelpCircle className="w-3.5 h-3.5" /> : msg.role === "user" ? <User className="w-3.5 h-3.5" /> : <Bot className="w-3.5 h-3.5" />}
                      </div>
                      <div className={`flex flex-col gap-1 max-w-[78%] ${msg.role === "user" ? "items-end" : "items-start"}`}>
                        <div className="flex items-center gap-2">
                          <span className={`text-[10px] font-mono uppercase tracking-wider ${msg.isClarification ? "text-amber-600/70" : "text-muted-foreground"}`}>
                            {msg.isClarification ? "Needs clarification" : msg.role}
                          </span>
                          {/* Show timestamp in header only for user messages; assistant time appears in model label */}
                          {msg.role === "user" && msg.created_at && (
                            <span className="text-[10px] text-muted-foreground/40 font-mono">{format(new Date(msg.created_at), "HH:mm")}</span>
                          )}
                        </div>
                        {/* Image thumbnail — shown on user messages with attached image */}
                        {msg.role === "user" && msg.image_b64 && (
                          <div className="mb-1">
                            <img
                              src={`data:${msg.image_media_type ?? "image/jpeg"};base64,${msg.image_b64}`}
                              alt="attached"
                              className="max-h-48 rounded-lg border border-secondary object-contain"
                            />
                          </div>
                        )}
                        {/* ── User-message lifecycle indicator ───────────────────
                            Shown below the bubble, right-aligned, using tiny
                            font-mono text so it reads as metadata not content.
                            "sending" shows while awaiting the first server byte;
                            "failed" shows "Not delivered" + a Retry button.    */}
                        {msg.role === "user" && msg.status === "sending" && (
                          <div className="flex items-center gap-1 mt-1 justify-end text-[10px] font-mono text-muted-foreground/50">
                            <Loader2 className="w-2.5 h-2.5 animate-spin" />
                            <span>Sending…</span>
                          </div>
                        )}
                        {msg.role === "user" && msg.status === "failed" && (
                          <div className="flex items-center gap-2 mt-1 justify-end">
                            <div className="flex items-center gap-1 text-[10px] font-mono text-destructive/70">
                              <AlertTriangle className="w-2.5 h-2.5 shrink-0" />
                              <span>Not delivered</span>
                            </div>
                            <button
                              onClick={() => {
                                const resendText = msg.text || lastSentRef.current;
                                if (!resendText || sending) return;
                                // Clear both failed bubbles before retrying so they
                                // don't accumulate on repeated retries.
                                setLocalMessages((prev) => prev.filter(
                                  (m) => m.status !== "failed"
                                ));
                                sendText(resendText);
                              }}
                              disabled={sending}
                              className="text-[10px] font-mono text-destructive/80 hover:text-destructive underline underline-offset-2 disabled:opacity-40 disabled:cursor-not-allowed"
                            >
                              Retry
                            </button>
                          </div>
                        )}
                        <div className={`px-4 py-3 rounded-lg text-base break-words chat-msg-bubble
                          ${msg.status === "failed" && msg.role === "assistant"
                            ? "bg-destructive/5 border border-destructive/30 text-destructive"
                            : msg.isClarification
                              ? "bg-amber-50/50 border border-amber-200/60 text-amber-900 dark:bg-amber-950/20 dark:border-amber-800/40 dark:text-amber-100"
                              : msg.role === "user"
                                ? "bg-secondary/60 border border-secondary whitespace-pre-wrap"
                                : "bg-muted/40 border border-border/40"}`}>
                          {msg.text ? (
                            msg.role === "assistant" ? (
                              <>
                                {msg.thinking && (
                                  <ReasoningBlock
                                    text={msg.thinking}
                                    streaming={!!msg.thinkingStreaming}
                                  />
                                )}
                                <ReadMore text={msg.text} streaming={!!msg.streaming} />
                                {msg.streaming && <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-text-bottom" />}
                                {msg.incomplete && (() => {
                                  return (
                                    <div className="mt-2 flex items-center justify-between gap-2 border-t border-amber-200/40 pt-2">
                                      <div className="flex items-center gap-1.5 text-xs text-amber-600">
                                        <AlertTriangle className="w-3 h-3 shrink-0" />
                                        <span>Response was cut short.</span>
                                      </div>
                                      <button
                                        onClick={() => handleContinue(msg.id)}
                                        disabled={sending}
                                        className="text-xs font-mono text-amber-700 hover:text-amber-900 underline underline-offset-2 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
                                      >
                                        Continue
                                      </button>
                                    </div>
                                  );
                                })()}
                                {msg.status === "failed" && (() => {
                                  // Failed assistant bubble — the user already has
                                  // a "Not delivered · Retry" row under their own
                                  // bubble; this just surfaces the error text.
                                  return (
                                    <div className="mt-2 flex items-center gap-1.5 border-t border-destructive/20 pt-2">
                                      <AlertTriangle className="w-3 h-3 shrink-0 text-destructive/60" />
                                      <span className="text-xs text-destructive/70">Failed — use Retry beneath your message.</span>
                                    </div>
                                  );
                                })()}
                              </>
                            ) : msg.text
                          ) : (
                            <span className="flex items-center gap-1.5 text-muted-foreground">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              <span className="text-xs">Thinking…</span>
                            </span>
                          )}
                        </div>
                        {msg.role === "assistant" && (
                          <>
                          <div className="flex items-center gap-2 px-0.5 flex-wrap">
                            {/* Intent badge */}
                            {msg.intent && INTENT_LABELS[msg.intent] && (
                              <span className="flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-mono bg-primary/8 text-primary/70 border border-primary/15">
                                <span>{INTENT_LABELS[msg.intent].icon}</span>
                                <span>{INTENT_LABELS[msg.intent].label}</span>
                              </span>
                            )}
                            {/* Per-message model attribution: prefer msg.meta.model, fall back to conv.model */}
                            <span className="text-[10px] font-mono text-muted-foreground/50">
                              {modelLabel((msg.meta?.model ?? conv?.model) as string | undefined, models, defaultModel)}
                              {msg.created_at && !msg.streaming && (
                                <> · {format(new Date(msg.created_at), "HH:mm")}</>
                              )}
                            </span>
                            {!!msg.meta?.council && (
                              <span className="text-[10px] font-mono text-primary/50 flex items-center gap-0.5">
                                <Brain className="w-2.5 h-2.5" /> council
                              </span>
                            )}
                            <button
                              onClick={() => {
                                copyToClipboard(msg.text ?? "").then(() => toast.success("Copied"));
                              }}
                              className="chat-icon-btn text-muted-foreground/30 hover:text-muted-foreground/70 transition-colors"
                              title="Copy response"
                            >
                              <Copy className="w-3 h-3" />
                            </button>
                          </div>
                          {/* Action confirmation card — shown when the AI detected an action intent */}
                          {!msg.streaming && msg.meta?.intent === "action" && msg.meta?.needs_confirm && msg.meta?.action_name && (
                            <ActionConfirmCard
                              actionName={msg.meta.action_name as string}
                              actionInputs={msg.meta.action_inputs as Record<string, unknown> | undefined}
                              confirmMessage={msg.meta.action_confirm as string | undefined}
                            />
                          )}
                          {/* Sources section — shown when knowledge context was injected */}
                          {!msg.streaming && msg.meta?.sources && (msg.meta.sources as any[]).length > 0 && (
                            <SourcesFooter sources={msg.meta.sources as any[]} />
                          )}
                          </>
                        )}
                        {/* Compass footer — shown on the last AI message for Work-scoped chats */}
                        {msg.id === lastAiMsgId && convWorkId && !msg.isClarification && (
                          <CompassFooter workId={convWorkId} />
                        )}
                      </div>
                    </div>
                  ))}
                  </ErrorBoundary>
                )}
                <div ref={messagesEndRef} />
              </div>
            </div>

            {/* ── Activity strip — shown while AI is generating ─────────────── */}
            {(sending || activityFading) && activitySteps.length > 0 && (
              <ActivityStrip
                steps={activitySteps}
                fading={activityFading}
                onExpand={() => setActivitySheetOpen(true)}
              />
            )}

            {/* Input — bottom padding accounts for the home indicator safe area so the
                composer never sits below the swipe zone. Task #288 will add full
                keyboard-avoidance behavior on top of this foundation. */}
            <div
              className="px-6 bg-muted/10 border-t border-border/50 shrink-0"
              style={{ paddingTop: '1rem', paddingBottom: 'max(1rem, var(--sai-bottom))' }}
            >
              {/* Made-in-this-chat artifact tracker */}
              <ArtifactTracker messages={displayMessages} />
              {/* Hidden image file input */}
              <input
                ref={imgInputRef}
                type="file"
                accept="image/*"
                className="hidden"
                onChange={(e) => { const f = e.target.files?.[0]; if (f) handleImageSelect(f); e.target.value = ""; }}
              />
              <form
                onSubmit={handleSend}
                className={`max-w-3xl mx-auto relative transition-colors rounded-lg ${dragOver ? "ring-2 ring-primary/40 bg-primary/5" : ""}`}
                onDragOver={e => { e.preventDefault(); setDragOver(true); }}
                onDragLeave={() => setDragOver(false)}
                onDrop={handleDrop}
              >
                {/* Pending image preview strip */}
                {pendingImage && (
                  <div className="flex items-center gap-2 px-3 pt-2 pb-1">
                    <div className="relative group w-16 h-16 shrink-0">
                      <img
                        src={`data:${pendingImage.type};base64,${pendingImage.data}`}
                        alt="pending"
                        className="w-full h-full object-cover rounded border border-border"
                      />
                      <button
                        type="button"
                        onClick={() => setPendingImage(null)}
                        className="touch-target-sm absolute -top-1 -right-1 w-4 h-4 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <XIcon className="w-2.5 h-2.5" />
                      </button>
                    </div>
                    <span className="text-xs text-muted-foreground font-mono">Image attached — ask anything about it</span>
                  </div>
                )}
                {/* ── Textarea row ────────────────────────────────────────────
                    Wrapped in its own relative container so the action-button
                    flex box anchors to THIS row only, not to the whole form
                    (which grows when the pending-image preview strip is shown). */}
                <div className="relative">
                  {/* ── Ghost text overlay ─────────────────────────────────────
                      Positioned absolutely over the textarea; pointer-events:none
                      so all input events still reach the textarea below.
                      Draft is rendered invisible (color:transparent) to position
                      the ghost text at the correct cursor offset. */}
                  {prediction?.ghost && (
                    <div aria-hidden className="predict-ghost-overlay">
                      <span style={{ color: "transparent" }}>{draft}</span>
                      <span className="ghost-text-completion" style={{ color: "hsl(var(--muted-foreground))", opacity: 0.35 }}>
                        {prediction.ghost}
                      </span>
                    </div>
                  )}
                  <Textarea
                    value={draft}
                    onChange={(e) => setDraft(e.target.value)}
                    placeholder={dragOver ? "Drop files to import…" : importing ? "Importing…" : aiOnline ? "Ask anything… or drop a file (Enter to send, Shift+Enter for newline)" : "AI offline — messages saved locally"}
                    className="pr-40 resize-none py-3 text-base"
                    rows={2}
                    disabled={sending || importing}
                    onKeyDown={(e) => {
                      if (e.key === "Tab" && prediction?.ghost) {
                        e.preventDefault();
                        setDraft(d => d + prediction.ghost);
                        setPrediction(null);
                        return;
                      }
                      if (e.key === "Escape" && prediction) {
                        e.preventDefault();
                        setPrediction(null);
                        return;
                      }
                      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(e); }
                    }}
                    onPaste={(e) => {
                      const files = Array.from(e.clipboardData.files).filter(f => f.type.startsWith("image/"));
                      if (files.length > 0) { e.preventDefault(); handleImageSelect(files[0]); }
                    }}
                  />
                {/* ── Composer action buttons ─────────────────────────────────
                    Flex container inside the textarea row so top-1/2 centers
                    within the textarea only, and min-44px expansion on mobile
                    is absorbed by the flex gap rather than causing overlap.   */}
                <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
                  {/* Image attach */}
                  <button
                    type="button"
                    onClick={() => imgInputRef.current?.click()}
                    title="Attach an image"
                    disabled={sending || importing}
                    className={`chat-icon-btn h-8 w-8 rounded flex items-center justify-center transition-colors
                      ${pendingImage ? "text-primary bg-primary/10 border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                  >
                    <ImageIcon className="w-4 h-4" />
                  </button>
                  {/* Mail context toggle — only shown when Mail Steward is connected */}
                  {mailConnected && activeId && (
                    <button
                      type="button"
                      onClick={async () => {
                        const next = !mailContextOn;
                        setMailContextOn(next);
                        try {
                          const resp = await apiFetch(`${API_BASE}/conversations/${activeId}/mail-context`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ enabled: next }),
                          });
                          if (!resp.ok) {
                            setMailContextOn(!next);
                            const err = await resp.json().catch(() => ({}));
                            toast.error((err as any).detail ?? "Could not toggle mail context");
                          } else {
                            queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
                          }
                        } catch {
                          setMailContextOn(!next);
                          toast.error("Could not toggle mail context");
                        }
                      }}
                      title={mailContextOn
                        ? "Mail context on — recent email summaries injected into chat (click to disable)"
                        : "Mail context off — click to inject recent email summaries into chat replies"}
                      className={`chat-icon-btn h-8 w-8 rounded flex items-center justify-center transition-colors
                        ${mailContextOn ? "text-primary bg-primary/10 border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                    >
                      <Mail className="w-4 h-4" />
                    </button>
                  )}
                  {/* Web search toggle — only shown when Tavily is configured */}
                  {tavilyConfigured && activeId && (
                    <button
                      type="button"
                      onClick={async () => {
                        const next = !webSearchOn;
                        setWebSearchOn(next);
                        try {
                          const resp = await apiFetch(`${API_BASE}/conversations/${activeId}/web-search`, {
                            method: "PUT",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({ enabled: next }),
                          });
                          if (!resp.ok) {
                            setWebSearchOn(!next);
                            const err = await resp.json().catch(() => ({}));
                            toast.error((err as any).detail ?? "Could not toggle web search");
                          } else {
                            queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
                          }
                        } catch {
                          setWebSearchOn(!next);
                          toast.error("Could not toggle web search");
                        }
                      }}
                      title={webSearchOn
                        ? "Web search on — answers augmented with live results (click to disable)"
                        : "Web search off — click to augment answers with live web results"}
                      className={`chat-icon-btn h-8 w-8 rounded flex items-center justify-center transition-colors
                        ${webSearchOn ? "text-primary bg-primary/10 border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                    >
                      <Globe className="w-4 h-4" />
                    </button>
                  )}
                  {/* Deep/Fast toggle */}
                  <button
                    type="button"
                    onClick={() => setDeepMode(v => !v)}
                    title={deepMode ? "Deep mode — 3-pass council (click for Fast)" : "Fast mode — single call (click for Deep)"}
                    className={`chat-icon-btn h-8 px-2 rounded flex items-center gap-1 text-xs font-mono transition-colors
                      ${deepMode ? "bg-primary/15 text-primary border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                  >
                    {deepMode ? <Brain className="w-3.5 h-3.5" /> : <Zap className="w-3.5 h-3.5" />}
                    <span className="hidden sm:inline">{deepMode ? "Deep" : "Fast"}</span>
                  </button>
                  {/* Send / Stop */}
                  {sending ? (
                    <Button
                      type="button"
                      size="icon"
                      onClick={() => abortRef.current?.abort()}
                      className="chat-icon-btn h-8 w-8 bg-destructive hover:bg-destructive/90 text-destructive-foreground"
                      title="Stop generating"
                    >
                      <Square className="w-3.5 h-3.5 fill-current" />
                    </Button>
                  ) : (
                    <Button type="submit" size="icon" disabled={!draft.trim() && !pendingImage} className="chat-icon-btn h-8 w-8">
                      <Send className="w-4 h-4" />
                    </Button>
                  )}
                </div>
                </div>{/* end textarea-row relative wrapper */}

                {/* ── Source chips — materialize below textarea when prediction arrives */}
                {prediction && prediction.sources.length > 0 && (
                  <div className="flex items-center gap-1.5 px-0.5 pt-1.5 pb-0.5 overflow-x-auto" style={{ scrollbarWidth: "none" }}>
                    {prediction.sources.map((src, i) => {
                      const norm = normalizeSource(src);
                      const Icon = norm.isWeb ? Globe : BookOpen;
                      const href = norm.docId
                        ? `${import.meta.env.BASE_URL}library/${norm.docId}`.replace(/\/+/g, "/")
                        : norm.workId
                        ? `${import.meta.env.BASE_URL}works/${norm.workId}`.replace(/\/+/g, "/")
                        : null;
                      return (
                        <a
                          key={norm.id ?? i}
                          href={href ?? undefined}
                          onClick={!href ? (e) => e.preventDefault() : undefined}
                          title={norm.passage ?? norm.title}
                          className="predict-chip chat-icon-btn shrink-0 flex items-center gap-1.5 rounded-full bg-muted/60 border border-border/40 px-2.5 py-1 text-[11px] font-mono text-muted-foreground/60 hover:text-muted-foreground hover:bg-muted/80 transition-colors max-w-[200px] no-underline"
                          style={{ animationDelay: `${i * 80}ms` }}
                        >
                          <Icon className="w-3 h-3 shrink-0 text-primary/50" />
                          <span className="truncate">
                            {norm.workTitle && norm.workTitle !== "Web" && norm.workTitle !== "General"
                              ? norm.workTitle
                              : norm.title}
                          </span>
                        </a>
                      );
                    })}
                    <span className="text-[10px] font-mono text-muted-foreground/30 shrink-0 ml-auto hidden sm:block select-none">
                      Tab to accept · Esc to dismiss
                    </span>
                  </div>
                )}
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground">
            <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
            <h3 className="font-serif text-xl font-medium text-foreground">No Conversation Selected</h3>
            <p className="mt-2 max-w-sm text-sm">Select a conversation from the sidebar or start a new one.</p>

            {/* Persona picker */}
            <div className="mt-6 w-full max-w-sm space-y-2 text-left">
              <label className="text-xs font-mono uppercase text-muted-foreground">AI persona</label>
              <div className="flex flex-wrap gap-2">
                {([
                  { id: "default",           label: "Default",            emoji: "🤖" },
                  { id: "story_partner",      label: "Story Partner",      emoji: "✨" },
                  { id: "technical_editor",   label: "Technical Editor",   emoji: "🔬" },
                  { id: "research_assistant", label: "Research Assistant", emoji: "📚" },
                  { id: "devils_advocate",    label: "Devil's Advocate",   emoji: "⚡" },
                ] as const).map(p => (
                  <button
                    key={p.id}
                    onClick={() => setNewConvPersona(p.id)}
                    className={`text-xs px-3 py-1.5 rounded-full border transition-colors ${
                      newConvPersona === p.id
                        ? "bg-primary text-primary-foreground border-primary"
                        : "bg-background border-border hover:border-primary/50 text-foreground"
                    }`}
                  >
                    {p.emoji} {p.label}
                  </button>
                ))}
              </div>
              {newConvPersona !== "default" && (
                <p className="text-[10px] text-muted-foreground mt-1">
                  {{
                    story_partner:      "Sparks imagination, asks 'what if' questions, celebrates ideas first.",
                    technical_editor:   "Flags inconsistencies, suggests clarity improvements, stays concise.",
                    research_assistant: "Cites sources, provides context, asks one clarifying question first.",
                    devils_advocate:    "Challenges assumptions, surfaces counterarguments, strengthens reasoning.",
                  }[newConvPersona as string]}
                </p>
              )}
            </div>

            {models.length > 0 && (
              <div className="mt-4 w-full max-w-sm space-y-2 text-left">
                <label className="text-xs font-mono uppercase text-muted-foreground">Model</label>
                <Select value={newConvModel || defaultModel} onValueChange={setNewConvModel}>
                  <SelectTrigger className="text-sm">
                    <SelectValue placeholder="Default model" />
                  </SelectTrigger>
                  <SelectContent>
                    {models.map((m) => (
                      <SelectItem key={m.id ?? m.label} value={m.id ?? ""} className="text-sm">
                        <span className="font-medium">{m.label}</span>
                        {m.description && (
                          <span className="ml-2 text-xs text-muted-foreground">{m.description}</span>
                        )}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            )}

            {!aiOnline && (
              <div className="mt-4 px-4 py-3 rounded-lg bg-muted/50 border border-border/50 max-w-sm text-left text-xs text-muted-foreground space-y-1">
                <p className="font-mono font-medium text-foreground">AI is offline</p>
                <p>Start Lemonade (port 13305) or Ollama (<code>ollama serve</code>) to enable AI responses.</p>
                <p>Set <code>ORIVELLUM_AI_URL</code> if using a custom endpoint.</p>
              </div>
            )}
            <Button onClick={() => handleCreate()} disabled={createConv.isPending} className="mt-4">
              Start New Conversation
            </Button>
          </div>
        )}
      </Card>

      {/* Activity detail sheet — accessible any time during/after generation */}
      <ActivitySheet
        open={activitySheetOpen}
        onOpenChange={setActivitySheetOpen}
        steps={activitySteps}
      />
    </div>
  );
}
