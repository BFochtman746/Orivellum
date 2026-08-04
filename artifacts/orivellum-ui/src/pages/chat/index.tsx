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
} from "@workspace/api-client-react";
import { useConnectivity } from "@/lib/useConnectivity";
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
  Globe, Paperclip, Download, Layers, HelpCircle, Compass, ChevronDown, ImageIcon,
} from "lucide-react";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { ScrollArea } from "@/components/ui/scroll-area";
import rehypeHighlight from "rehype-highlight";
import "highlight.js/styles/atom-one-dark.css";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface LocalMessage {
  id: string;

  role: "user" | "assistant";

  text: string;

  created_at: string;

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
/** Sentinel prefix carrying the tool intent through the token stream. Format: \x02INTENT\x02web_search\x02 */
const INTENT_PREFIX = "\x02INTENT\x02";
/** Sentinel prefix carrying reasoning/thinking tokens from <think> blocks or reasoning_content. */
const THINKING_PREFIX = "\x02THINKING\x02";

const INTENT_LABELS: Record<string, { icon: string; label: string }> = {
  web_search: { icon: "🌐", label: "Web search" },
  weather:    { icon: "📍", label: "Weather" },
  remember:   { icon: "📌", label: "Remembered" },
  image_gen:  { icon: "🎨", label: "Image gen" },
};

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

  // Distinguish "Sources" label when there are web results vs. pure knowledge
  const hasWeb = unique.some((s) => s.isWeb);

  return (
    <div className="mt-1.5">
      <button
        onClick={() => setOpen(v => !v)}
        className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground/50 hover:text-muted-foreground/80 transition-colors"
      >
        {hasWeb ? <Globe className="w-2.5 h-2.5" /> : <BookOpen className="w-2.5 h-2.5" />}
        <span>Sources ({unique.length})</span>
        <ChevronDown className={`w-2.5 h-2.5 transition-transform duration-200 ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div className="mt-1.5 flex flex-col gap-1.5 pl-1 border-l border-border/30">
          {groups.map((g, gi) => (
            <div key={gi} className="flex flex-col gap-0.5">
              <span className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground/40 pl-1.5">
                {g.title}
              </span>
              {g.items.map((s, i) => {
                const target = link(s);
                const Icon = s.isWeb ? Globe : FileText;
                return target ? (
                  <a
                    key={i}
                    href={target.href}
                    target={target.external ? "_blank" : undefined}
                    rel={target.external ? "noopener noreferrer" : undefined}
                    className="flex items-center gap-1.5 text-[10px] font-mono text-primary/60 hover:text-primary/90 transition-colors truncate max-w-xs pl-1.5"
                  >
                    <Icon className="w-2.5 h-2.5 shrink-0" />
                    <span className="truncate">{s.title}</span>
                  </a>
                ) : (
                  <span
                    key={i}
                    className="flex items-center gap-1.5 text-[10px] font-mono text-muted-foreground/60 truncate max-w-xs pl-1.5"
                  >
                    <Icon className="w-2.5 h-2.5 shrink-0" />
                    <span className="truncate">{s.title}</span>
                  </span>
                );
              })}
            </div>
          ))}
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
            <span className="block my-3 rounded-lg overflow-hidden border border-white/10 shadow-md">
              {lang && (
                <span className="flex items-center justify-between px-3 py-1.5 bg-zinc-800 border-b border-white/10">
                  <span className="text-[10px] font-mono uppercase tracking-wide text-zinc-400">{lang}</span>
                </span>
              )}
              <code
                className={`block bg-zinc-900 text-zinc-100 px-4 py-3 text-xs font-mono whitespace-pre-wrap leading-relaxed overflow-x-auto ${className ?? ""}`}
              >
                {children}
              </code>
            </span>
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
    yield "AI service is currently unavailable. Your message has been saved.";
    return;
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

// ─── Component ────────────────────────────────────────────────────────────────

export default function Chat() {
  const [, setLocation] = useLocation();
  const searchParams = new URLSearchParams(window.location.search);
  const activeId = searchParams.get("id");

  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [pendingImage, setPendingImage] = useState<{ data: string; type: string } | null>(null);
  const imgInputRef = useRef<HTMLInputElement>(null);
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const localOverride = localMessages.length > 0;

  // Tab-focus resilience refs
  const accumulatorRef = useRef("");
  const thinkingAccRef = useRef("");   // accumulates reasoning tokens during streaming
  const assistantIdRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // Synchronous sending flag (avoids stale closure in RAF loop) + abort controller
  const sendingRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);
  // Track the last message the user sent so the re-send button can restore it
  const lastSentRef = useRef<string>("");

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

  const [deepMode,   setDeepMode]   = useState(false);
  const [scopeAll,   setScopeAll]   = useState(false); // false = "This work", true = "All works"
  const [dragOver,   setDragOver]   = useState(false);
  const [importing,  setImporting]  = useState(false);
  const [newConvModel, setNewConvModel] = useState<string>(() => {
    try { return localStorage.getItem("orivellum:lastModel") ?? ""; } catch { return ""; }
  });
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

  // Abort any in-progress stream when conversation changes or component unmounts
  useEffect(() => {
    return () => {
      if (sendingRef.current && abortRef.current) {
        abortRef.current.abort();
      }
    };
  }, [activeId]);

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

  const handleCreate = (modelOverride?: string) => {
    const chosenModel = modelOverride ?? newConvModel;
    createConv.mutate(
      { data: { title: "New Conversation", ...(chosenModel ? { model: chosenModel } : {}) } },
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

  // ── Core send logic (called by handleSend and the Re-send button) ────────
  const sendText = useCallback(
    async (text: string) => {
      if (!text || !activeId || sendingRef.current) return;

      lastSentRef.current = text;
      // Capture convId now — activeId may change before the stream finishes
      const convId = activeId;
      setSending(true);
      sendingRef.current = true;

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
      const userMsg: LocalMessage = {
        id: randomUUID(), role: "user", text,
        created_at: new Date().toISOString(),
        image_b64: capturedImage?.data,
        image_media_type: capturedImage?.type,
      };
      const assistantId = randomUUID();
      assistantIdRef.current = assistantId;
      accumulatorRef.current = "";
      thinkingAccRef.current = "";
      // Capture the effective model so the attribution label shows during streaming
      const effectiveModel = conv?.model || defaultModel || undefined;

      setLocalMessages([...serverMsgs, userMsg, { id: assistantId, role: "assistant", text: "", created_at: new Date().toISOString(), streaming: true, meta: effectiveModel ? { model: effectiveModel } : undefined }]);

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
      try {
        for await (const token of streamChat(convId, text, controller.signal, deepMode, scopeAll ? "all" : "work", capturedImage?.data, capturedImage?.type)) {
          if (token.startsWith(SOURCES_PREFIX) && token.endsWith(SOURCES_PREFIX) && token.length > SOURCES_PREFIX.length * 2) {
            try {
              streamedSources = JSON.parse(token.slice(SOURCES_PREFIX.length, -SOURCES_PREFIX.length));
            } catch {}
            continue;
          }
          if (token.startsWith(CLARIFY_PREFIX)) {
            // Cognition gate requests clarification — backend persisted with { model, isClarification: true }
            const question = token.slice(CLARIFY_PREFIX.length);
            setLocalMessages((prev) => prev.map((m) =>
              m.id === assistantId
                ? { ...m, text: question, streaming: false, isClarification: true,
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
              m.id === assistantId ? { ...m, text: partialText, streaming: false, incomplete: true } : m
            ));
          } else {
            setLocalMessages((prev) => prev.filter((m) => m.id !== assistantId));
          }
        } else {
          const msg = err?.message ?? String(err);
          const errLabel = (msg.includes("503") || msg.includes("Service Unavailable") || msg.includes("AI"))
            ? "AI service unavailable — check Engine Settings"
            : "Message failed to send";
          // Keep the bubble visible with a failed state instead of silently removing it
          // so the user can see what happened and retry.
          setLocalMessages((prev) => prev.map((m) =>
            m.id === assistantId
              ? { ...m, text: errLabel, streaming: false, failed: true }
              : m
          ));
        }
      } finally {
        if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
        accumulatorRef.current = "";
        thinkingAccRef.current = "";
        assistantIdRef.current = "";
        sendingRef.current = false;
        abortRef.current = null;
        setSending(false);
        // Invalidate using the captured convId, not the potentially-changed activeId
        queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(convId) });
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        // Clear local messages only if still viewing the same conversation
        // (otherwise the activeId-change effect already cleared them)
        // Keep incomplete (truncated) and failed bubbles — both are meaningful states.
        setLocalMessages((prev) => prev.filter((m) => m.incomplete || (m as any).failed));
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
      sendText(text);
    },
    [draft, pendingImage, sendText]
  );

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
        const hasCutShortMeta = !!(m as any).meta?.cut_short;
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

  const filteredConvs = convsResp?.conversations?.filter((c) => {
    return !search || c.title?.toLowerCase().includes(search.toLowerCase()) || c.last_message?.toLowerCase().includes(search.toLowerCase());
  });

  const conv = activeConv?.conversation;

  return (
    <div className="flex-1 min-h-0 flex gap-0 md:gap-6 animate-in fade-in duration-500">
      {/* ── Sidebar — full-width on mobile when no conv selected ─────── */}
      <Card className={`flex flex-col shrink-0 rounded-xl overflow-hidden border-border/50 w-full md:w-72 ${activeId ? "hidden md:flex" : "flex"}`}>
        <div className="p-4 border-b border-border/50 bg-muted/10 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg font-medium">Conversations</h2>
            <div className="flex items-center gap-0.5">
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

        <ScrollArea className="flex-1">
          <div className="p-2 space-y-0.5">
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
              <p className="text-xs text-muted-foreground text-center py-6">{search ? "No conversations match" : "No conversations yet"}</p>
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
                <h2 className="font-serif text-lg font-medium leading-tight truncate">{conv?.title || "Conversation"}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs font-mono text-muted-foreground">{conv?.message_count ?? 0} messages</span>
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

            {/* Messages */}
            <ScrollArea className="flex-1 px-6 py-6">
              <div className="max-w-3xl mx-auto space-y-6">
                {loadingActive && !localOverride ? (
                  <div className="space-y-6">
                    <Skeleton className="h-16 w-2/3 ml-auto rounded-lg" />
                    <Skeleton className="h-24 w-2/3 rounded-lg" />
                  </div>
                ) : displayMessages.length === 0 ? (
                  <div className="text-center py-16 text-muted-foreground">
                    <Bot className="w-10 h-10 mx-auto mb-3 opacity-20" />
                    <p className="text-sm">{aiOnline ? "Send a message to start the conversation." : "AI is offline — start Lemonade or Ollama to enable responses."}</p>
                  </div>
                ) : (
                  <ErrorBoundary label="message list">
                  {displayMessages.map((msg, msgIdx) => (
                    <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
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
                        <div className={`px-4 py-3 rounded-lg text-sm break-words
                          ${(msg as any).failed
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
                                <MarkdownContent text={msg.text} />
                                {msg.streaming && <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-text-bottom" />}
                                {msg.incomplete && (() => {
                                  // Find the user message that triggered this incomplete reply
                                  const prevUser = displayMessages.slice(0, msgIdx).reverse().find(m => m.role === "user");
                                  const resendText = prevUser?.text || lastSentRef.current;
                                  return (
                                    <div className="mt-2 flex items-center justify-between gap-2 border-t border-amber-200/40 pt-2">
                                      <div className="flex items-center gap-1.5 text-xs text-amber-600">
                                        <AlertTriangle className="w-3 h-3 shrink-0" />
                                        <span>Response was cut short.</span>
                                      </div>
                                      <button
                                        onClick={() => resendText && sendText(resendText)}
                                        disabled={!resendText || sending}
                                        className="text-xs font-mono text-amber-700 hover:text-amber-900 underline underline-offset-2 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
                                      >
                                        Re-send
                                      </button>
                                    </div>
                                  );
                                })()}
                                {(msg as any).failed && (() => {
                                  // Failed send — offer to retry with the same user message
                                  const prevUser = displayMessages.slice(0, msgIdx).reverse().find(m => m.role === "user");
                                  const resendText = prevUser?.text || lastSentRef.current;
                                  return (
                                    <div className="mt-2 flex items-center justify-between gap-2 border-t border-destructive/20 pt-2">
                                      <div className="flex items-center gap-1.5 text-xs text-destructive/70">
                                        <AlertTriangle className="w-3 h-3 shrink-0" />
                                        <span>Failed to send.</span>
                                      </div>
                                      <button
                                        onClick={() => {
                                          if (!resendText) return;
                                          // Remove this failed bubble so retries don't accumulate duplicates
                                          setLocalMessages((prev) => prev.filter((m) => m.id !== msg.id));
                                          sendText(resendText);
                                        }}
                                        disabled={!resendText || sending}
                                        className="text-xs font-mono text-destructive/80 hover:text-destructive underline underline-offset-2 shrink-0 disabled:opacity-40 disabled:cursor-not-allowed"
                                      >
                                        Try again
                                      </button>
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
                              className="text-muted-foreground/30 hover:text-muted-foreground/70 transition-colors"
                              title="Copy response"
                            >
                              <Copy className="w-3 h-3" />
                            </button>
                          </div>
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
            </ScrollArea>

            {/* Input */}
            <div className="px-6 py-4 bg-muted/10 border-t border-border/50 shrink-0">
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
                        className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-destructive text-destructive-foreground flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity"
                      >
                        <XIcon className="w-2.5 h-2.5" />
                      </button>
                    </div>
                    <span className="text-xs text-muted-foreground font-mono">Image attached — ask anything about it</span>
                  </div>
                )}
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={dragOver ? "Drop files to import…" : importing ? "Importing…" : aiOnline ? "Ask anything… or drop a file (Enter to send, Shift+Enter for newline)" : "AI offline — messages saved locally"}
                  className="pr-32 resize-none py-3 text-sm"
                  rows={2}
                  disabled={sending || importing}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(e); } }}
                />
                {/* Image attach button */}
                <button
                  type="button"
                  onClick={() => imgInputRef.current?.click()}
                  title="Attach an image"
                  disabled={sending || importing}
                  className={`absolute right-20 top-2 h-8 w-8 rounded flex items-center justify-center transition-colors
                    ${pendingImage ? "text-primary bg-primary/10 border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                >
                  <ImageIcon className="w-4 h-4" />
                </button>
                {/* Deep/Fast toggle */}
                <button
                  type="button"
                  onClick={() => setDeepMode(v => !v)}
                  title={deepMode ? "Deep mode — 3-pass council (click for Fast)" : "Fast mode — single call (click for Deep)"}
                  className={`absolute right-11 top-2 h-8 px-2 rounded flex items-center gap-1 text-xs font-mono transition-colors
                    ${deepMode ? "bg-primary/15 text-primary border border-primary/30" : "text-muted-foreground/50 hover:text-muted-foreground"}`}
                >
                  {deepMode ? <Brain className="w-3.5 h-3.5" /> : <Zap className="w-3.5 h-3.5" />}
                  <span className="hidden sm:inline">{deepMode ? "Deep" : "Fast"}</span>
                </button>
                <Button type="submit" size="icon" disabled={(!draft.trim() && !pendingImage) || sending} className="absolute right-2 top-2 h-8 w-8">
                  {sending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
                </Button>
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground">
            <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
            <h3 className="font-serif text-xl font-medium text-foreground">No Conversation Selected</h3>
            <p className="mt-2 max-w-sm text-sm">Select a conversation from the sidebar or start a new one.</p>

            {models.length > 0 && (
              <div className="mt-6 w-full max-w-xs space-y-2 text-left">
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
    </div>
  );
}
