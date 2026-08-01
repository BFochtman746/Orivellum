import { useState, useRef, useEffect, useCallback } from "react";
import { useLocation } from "wouter";
import { useQuery } from "@tanstack/react-query";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  useListConversations,
  useGetConversation,
  useCreateConversation,
  useDeleteConversation,
  useUpdateConversation,
  useGetSystemHealth,
  getListConversationsQueryKey,
  getGetConversationQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
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
  MessageSquare, Plus, Send, Search, Bot, User,
  Trash2, Wifi, WifiOff, Loader2, Cpu,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  streaming?: boolean;
}

interface ModelOption {
  id: string;
  role: string;
  label: string;
  description: string;
}

// ─── Models hook ──────────────────────────────────────────────────────────────

function useModels() {
  return useQuery<{ models: ModelOption[]; default: string }>({
    queryKey: ["system-models"],
    queryFn: () =>
      fetch(`${API_BASE}/system/models`).then((r) => r.json()),
    staleTime: 60_000,
  });
}

// ─── Model label helper ───────────────────────────────────────────────────────

function modelLabel(modelId: string | undefined | null, models: ModelOption[], defaultModel: string): string {
  if (!modelId) modelId = defaultModel;
  const found = models.find((m) => m.id === modelId);
  if (found) return found.label;
  // Truncate raw ID for display
  return modelId ? modelId.split("-").slice(0, 3).join("-") : "Default";
}

// ─── Model picker ─────────────────────────────────────────────────────────────

interface ModelPickerProps {
  convId: string;
  currentModel: string | null | undefined;
  models: ModelOption[];
  defaultModel: string;
  onChanged: () => void;
}

function ModelPicker({ convId, currentModel, models, defaultModel, onChanged }: ModelPickerProps) {
  const updateConv = useUpdateConversation();
  const effective = currentModel || defaultModel;

  const handleChange = (value: string) => {
    updateConv.mutate(
      { convId, data: { model: value } },
      { onSuccess: onChanged }
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
          <SelectItem key={m.id} value={m.id} className="text-xs">
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

// ─── Markdown renderer ────────────────────────────────────────────────────────

function MarkdownContent({ text }: { text: string }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
        code: ({ className, children, ...props }) => {
          const isBlock = className?.startsWith("language-");
          return isBlock ? (
            <code className="block bg-black/10 rounded px-3 py-2 text-xs font-mono whitespace-pre-wrap my-2" {...props}>
              {children}
            </code>
          ) : (
            <code className="bg-black/10 rounded px-1 py-0.5 text-xs font-mono" {...props}>
              {children}
            </code>
          );
        },
        pre: ({ children }) => <>{children}</>,
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
      }}
    >
      {text}
    </ReactMarkdown>
  );
}

// ─── Streaming helper ─────────────────────────────────────────────────────────

async function* streamChat(convId: string, text: string): AsyncGenerator<string> {
  const resp = await fetch(`${API_BASE}/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, stream: true }),
    keepalive: true,
  });

  if (!resp.ok || !resp.body) {
    yield "AI service is currently unavailable. Your message has been saved.";
    return;
  }

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
      if (data === "[DONE]") return;
      try {
        const parsed = JSON.parse(data);
        if (parsed.token) yield parsed.token as string;
      } catch { /* ignore */ }
    }
  }
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function Chat() {
  const [, setLocation] = useLocation();
  const searchParams = new URLSearchParams(window.location.search);
  const activeId = searchParams.get("id");

  const queryClient = useQueryClient();
  const [search, setSearch] = useState("");
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const localOverride = localMessages.length > 0;

  // Tab-focus resilience refs
  const accumulatorRef = useRef("");
  const assistantIdRef = useRef("");
  const rafRef = useRef<number | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  const { data: convsResp, isLoading: loadingList } = useListConversations();
  const { data: activeConv, isLoading: loadingActive } = useGetConversation(activeId!, {
    query: { enabled: !!activeId, queryKey: getGetConversationQueryKey(activeId!) },
  });
  const { data: sysHealth } = useGetSystemHealth();
  const { data: modelsData } = useModels();
  const aiOnline = sysHealth?.services?.ai?.status === "ok";
  const models = modelsData?.models ?? [];
  const defaultModel = modelsData?.default ?? "";

  const createConv = useCreateConversation();
  const deleteConv = useDeleteConversation();

  useEffect(() => { setLocalMessages([]); setDraft(""); }, [activeId]);
  useEffect(() => { if (activeConv?.messages && !sending) setLocalMessages([]); }, [activeConv?.messages, sending]);
  useEffect(() => { messagesEndRef.current?.scrollIntoView({ behavior: "smooth" }); }, [localMessages, activeConv?.messages]);

  // Tab-focus flush
  const flushAccumulator = useCallback(() => {
    const text = accumulatorRef.current;
    const id = assistantIdRef.current;
    if (!id || !text) return;
    setLocalMessages((prev) =>
      prev.map((m) => (m.id === id ? { ...m, text, streaming: true } : m))
    );
  }, []);

  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible" && sending) flushAccumulator(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [sending, flushAccumulator]);

  const handleCreate = () => {
    createConv.mutate(
      { data: { title: "New Conversation" } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (res?.conversation?.id) setLocation(`/chat?id=${res.conversation.id}`);
        },
      }
    );
  };

  const handleDelete = (convId: string, e: React.MouseEvent) => {
    e.stopPropagation();
    deleteConv.mutate({ convId }, {
      onSuccess: () => {
        queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        if (activeId === convId) setLocation("/chat");
      },
    });
  };

  const invalidateActive = useCallback(() => {
    if (!activeId) return;
    queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
    queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
  }, [activeId, queryClient]);

  const handleSend = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!draft.trim() || !activeId || sending) return;

      const text = draft.trim();
      setDraft("");
      setSending(true);

      const serverMsgs: LocalMessage[] = (activeConv?.messages ?? []).map((m) => ({
        id: m.id ?? crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        text: m.text ?? "",
        created_at: m.created_at ?? new Date().toISOString(),
      }));

      const userMsg: LocalMessage = { id: crypto.randomUUID(), role: "user", text, created_at: new Date().toISOString() };
      const assistantId = crypto.randomUUID();
      assistantIdRef.current = assistantId;
      accumulatorRef.current = "";

      setLocalMessages([...serverMsgs, userMsg, { id: assistantId, role: "assistant", text: "", created_at: new Date().toISOString(), streaming: true }]);

      const scheduleFlush = () => {
        rafRef.current = requestAnimationFrame(() => { flushAccumulator(); if (sending || accumulatorRef.current) scheduleFlush(); });
      };
      scheduleFlush();

      try {
        for await (const token of streamChat(activeId, text)) {
          accumulatorRef.current += token;
        }
        const finalText = accumulatorRef.current;
        setLocalMessages((prev) => prev.map((m) => m.id === assistantId ? { ...m, text: finalText, streaming: false } : m));
      } finally {
        if (rafRef.current !== null) { cancelAnimationFrame(rafRef.current); rafRef.current = null; }
        accumulatorRef.current = "";
        assistantIdRef.current = "";
        setSending(false);
        await invalidateActive();
        setLocalMessages([]);
      }
    },
    [draft, activeId, sending, activeConv?.messages, flushAccumulator, invalidateActive]
  );

  const displayMessages: LocalMessage[] = localOverride
    ? localMessages
    : (activeConv?.messages ?? []).map((m) => ({
        id: m.id ?? "", role: m.role as "user" | "assistant",
        text: m.text ?? "", created_at: m.created_at ?? "",
      }));

  const filteredConvs = convsResp?.conversations?.filter(
    (c) => c.title?.toLowerCase().includes(search.toLowerCase()) || c.last_message?.toLowerCase().includes(search.toLowerCase())
  );

  const conv = activeConv?.conversation;

  return (
    <div className="h-[calc(100vh-6rem)] flex gap-6 animate-in fade-in duration-500">
      {/* ── Sidebar ────────────────────────────────────────────────────── */}
      <Card className="w-72 flex flex-col shrink-0 rounded-xl overflow-hidden border-border/50">
        <div className="p-4 border-b border-border/50 bg-muted/10 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg font-medium">Conversations</h2>
            <Button size="icon" variant="ghost" onClick={handleCreate} disabled={createConv.isPending}>
              <Plus className="w-4 h-4" />
            </Button>
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
                    onClick={() => setLocation(`/chat?id=${c.id}`)}
                    className={`group p-3 rounded-md cursor-pointer transition-colors flex items-start justify-between gap-2 ${activeId === c.id ? "bg-primary/10 text-primary" : "hover:bg-muted/50"}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">{c.title || "Untitled"}</div>
                      <div className="text-xs text-muted-foreground truncate mt-0.5">
                        {c.last_message ? c.last_message.slice(0, 50) : "No messages"}
                      </div>
                    </div>
                    <button onClick={(e) => handleDelete(c.id!, e)} className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:text-destructive shrink-0 mt-0.5">
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))}
            {!loadingList && !filteredConvs?.length && (
              <p className="text-xs text-muted-foreground text-center py-6">{search ? "No conversations match" : "No conversations yet"}</p>
            )}
          </div>
        </ScrollArea>
      </Card>

      {/* ── Main chat ──────────────────────────────────────────────────── */}
      <Card className="flex-1 flex flex-col rounded-xl overflow-hidden border-border/50 min-w-0">
        {activeId ? (
          <>
            {/* Header */}
            <div className="px-6 py-3.5 border-b border-border/50 bg-muted/10 flex justify-between items-center shrink-0">
              <div>
                <h2 className="font-serif text-lg font-medium leading-tight">{conv?.title || "Conversation"}</h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs font-mono text-muted-foreground">{conv?.message_count ?? 0} messages</span>
                  {conv?.work_id && <Badge variant="secondary" className="text-[10px] h-4 px-1.5">Work linked</Badge>}
                </div>
              </div>
              {/* Model picker in header */}
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
                  displayMessages.map((msg) => (
                    <div key={msg.id} className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}>
                      <div className={`w-7 h-7 shrink-0 rounded-sm flex items-center justify-center ${msg.role === "user" ? "bg-secondary text-secondary-foreground" : "bg-primary text-primary-foreground"}`}>
                        {msg.role === "user" ? <User className="w-3.5 h-3.5" /> : <Bot className="w-3.5 h-3.5" />}
                      </div>
                      <div className={`flex flex-col gap-1 max-w-[78%] ${msg.role === "user" ? "items-end" : "items-start"}`}>
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{msg.role}</span>
                          {msg.created_at && (
                            <span className="text-[10px] text-muted-foreground/40 font-mono">{format(new Date(msg.created_at), "HH:mm")}</span>
                          )}
                        </div>
                        <div className={`px-4 py-3 rounded-lg text-sm break-words ${msg.role === "user" ? "bg-secondary/60 border border-secondary whitespace-pre-wrap" : "bg-muted/40 border border-border/40"}`}>
                          {msg.text ? (
                            msg.role === "assistant" ? (
                              <>
                                <MarkdownContent text={msg.text} />
                                {msg.streaming && <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-text-bottom" />}
                              </>
                            ) : msg.text
                          ) : (
                            <span className="flex items-center gap-1.5 text-muted-foreground">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              <span className="text-xs">Thinking…</span>
                            </span>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
                <div ref={messagesEndRef} />
              </div>
            </ScrollArea>

            {/* Input */}
            <div className="px-6 py-4 bg-muted/10 border-t border-border/50 shrink-0">
              <form onSubmit={handleSend} className="max-w-3xl mx-auto relative">
                <Textarea
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={aiOnline ? "Ask anything… (Enter to send, Shift+Enter for newline)" : "AI offline — messages saved locally"}
                  className="pr-12 resize-none py-3 text-sm"
                  rows={2}
                  disabled={sending}
                  onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleSend(e); } }}
                />
                <Button type="submit" size="icon" disabled={!draft.trim() || sending} className="absolute right-2 top-2 h-8 w-8">
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
            {!aiOnline && (
              <div className="mt-4 px-4 py-3 rounded-lg bg-muted/50 border border-border/50 max-w-sm text-left text-xs text-muted-foreground space-y-1">
                <p className="font-mono font-medium text-foreground">AI is offline</p>
                <p>Start Lemonade (port 13305) or Ollama (<code>ollama serve</code>) to enable AI responses.</p>
                <p>Set <code>ORIVELLUM_AI_URL</code> if using a custom endpoint.</p>
              </div>
            )}
            <Button onClick={handleCreate} disabled={createConv.isPending} className="mt-6">Start New Conversation</Button>
          </div>
        )}
      </Card>
    </div>
  );
}
