import { useState, useRef, useEffect, useCallback } from "react";
import { useLocation } from "wouter";
import {
  useListConversations,
  useGetConversation,
  useCreateConversation,
  useDeleteConversation,
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
  MessageSquare, Plus, Send, Search, Bot, User,
  Trash2, Wifi, WifiOff, Loader2,
} from "lucide-react";
import { ScrollArea } from "@/components/ui/scroll-area";

// Base URL for API calls — uses the Vite proxy path
const API_BASE = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");

// ─── Types ────────────────────────────────────────────────────────────────────

interface LocalMessage {
  id: string;
  role: "user" | "assistant";
  text: string;
  created_at: string;
  streaming?: boolean;
}

// ─── Streaming helper ─────────────────────────────────────────────────────────

async function* streamChat(convId: string, text: string): AsyncGenerator<string> {
  const resp = await fetch(`${API_BASE}/conversations/${convId}/messages`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, stream: true }),
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
      } catch {
        // ignore malformed SSE lines
      }
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

  // Local messages overlay for optimistic + streaming display
  const [localMessages, setLocalMessages] = useState<LocalMessage[]>([]);
  const localOverride = localMessages.length > 0;

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const { data: convsResp, isLoading: loadingList } = useListConversations();
  const { data: activeConv, isLoading: loadingActive } = useGetConversation(activeId!, {
    query: { enabled: !!activeId, queryKey: getGetConversationQueryKey(activeId!) },
  });
  const { data: sysHealth } = useGetSystemHealth();
  const aiOnline = sysHealth?.services?.ai?.status === "ok";

  const createConv = useCreateConversation();
  const deleteConv = useDeleteConversation();

  // Sync server messages into local state when conversation loads / refreshes
  useEffect(() => {
    if (activeConv?.messages && !sending) {
      setLocalMessages([]);
    }
  }, [activeConv?.messages, sending]);

  // Reset local messages when conversation changes
  useEffect(() => {
    setLocalMessages([]);
    setDraft("");
  }, [activeId]);

  // Auto-scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [localMessages, activeConv?.messages]);

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
    deleteConv.mutate(
      { convId },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          if (activeId === convId) setLocation("/chat");
        },
      }
    );
  };

  const handleSend = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      if (!draft.trim() || !activeId || sending) return;

      const text = draft.trim();
      setDraft("");
      setSending(true);

      // Build optimistic local state from server messages + new user message
      const serverMsgs: LocalMessage[] = (activeConv?.messages ?? []).map((m) => ({
        id: m.id ?? crypto.randomUUID(),
        role: m.role as "user" | "assistant",
        text: m.text ?? "",
        created_at: m.created_at ?? new Date().toISOString(),
      }));

      const userMsg: LocalMessage = {
        id: crypto.randomUUID(),
        role: "user",
        text,
        created_at: new Date().toISOString(),
      };
      const assistantId = crypto.randomUUID();
      const assistantPlaceholder: LocalMessage = {
        id: assistantId,
        role: "assistant",
        text: "",
        created_at: new Date().toISOString(),
        streaming: true,
      };

      setLocalMessages([...serverMsgs, userMsg, assistantPlaceholder]);

      try {
        let accumulated = "";
        for await (const token of streamChat(activeId, text)) {
          accumulated += token;
          setLocalMessages((prev) =>
            prev.map((m) =>
              m.id === assistantId ? { ...m, text: accumulated, streaming: true } : m
            )
          );
        }
        // Mark streaming done
        setLocalMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId ? { ...m, streaming: false } : m
          )
        );
      } finally {
        setSending(false);
        // Refetch from server to get authoritative IDs and title update
        await queryClient.invalidateQueries({ queryKey: getGetConversationQueryKey(activeId) });
        await queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
        setLocalMessages([]);
      }
    },
    [draft, activeId, sending, activeConv?.messages, queryClient]
  );

  // Messages to render: local overlay when sending, else server messages
  const displayMessages: LocalMessage[] = localOverride
    ? localMessages
    : (activeConv?.messages ?? []).map((m) => ({
        id: m.id ?? "",
        role: m.role as "user" | "assistant",
        text: m.text ?? "",
        created_at: m.created_at ?? "",
      }));

  const filteredConvs = convsResp?.conversations?.filter(
    (c) =>
      c.title?.toLowerCase().includes(search.toLowerCase()) ||
      c.last_message?.toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="h-[calc(100vh-6rem)] flex gap-6 animate-in fade-in duration-500">
      {/* ── Sidebar ──────────────────────────────────────────────────────── */}
      <Card className="w-72 flex flex-col shrink-0 rounded-xl overflow-hidden border-border/50">
        <div className="p-4 border-b border-border/50 bg-muted/10 space-y-3">
          <div className="flex items-center justify-between">
            <h2 className="font-serif text-lg font-medium">Conversations</h2>
            <Button
              size="icon"
              variant="ghost"
              onClick={handleCreate}
              disabled={createConv.isPending}
              data-testid="button-new-conversation"
            >
              <Plus className="w-4 h-4" />
            </Button>
          </div>

          {/* AI status pill */}
          <div className="flex items-center gap-1.5 text-xs">
            {aiOnline ? (
              <>
                <Wifi className="w-3 h-3 text-emerald-500" />
                <span className="text-emerald-600 font-mono">AI connected</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3 h-3 text-muted-foreground" />
                <span className="text-muted-foreground font-mono">AI offline</span>
              </>
            )}
          </div>

          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-muted-foreground" />
            <Input
              placeholder="Search..."
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="pl-8 h-8 text-xs bg-background"
            />
          </div>
        </div>

        <ScrollArea className="flex-1">
          <div className="p-2 space-y-0.5">
            {loadingList
              ? [1, 2, 3].map((i) => <Skeleton key={i} className="h-14 w-full rounded-md mb-1" />)
              : filteredConvs?.map((conv) => (
                  <div
                    key={conv.id}
                    onClick={() => setLocation(`/chat?id=${conv.id}`)}
                    className={`group p-3 rounded-md cursor-pointer transition-colors flex items-start justify-between gap-2 ${
                      activeId === conv.id
                        ? "bg-primary/10 text-primary"
                        : "hover:bg-muted/50"
                    }`}
                    data-testid={`conv-item-${conv.id}`}
                  >
                    <div className="min-w-0 flex-1">
                      <div className="font-medium text-sm truncate">
                        {conv.title || "Untitled"}
                      </div>
                      <div className="text-xs text-muted-foreground truncate mt-0.5">
                        {conv.last_message
                          ? conv.last_message.slice(0, 50)
                          : "No messages"}
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDelete(conv.id!, e)}
                      className="opacity-0 group-hover:opacity-100 transition-opacity p-0.5 rounded hover:text-destructive shrink-0"
                      data-testid={`button-delete-conv-${conv.id}`}
                    >
                      <Trash2 className="w-3 h-3" />
                    </button>
                  </div>
                ))}

            {!loadingList && !filteredConvs?.length && (
              <p className="text-xs text-muted-foreground text-center py-6">
                {search ? "No conversations match" : "No conversations yet"}
              </p>
            )}
          </div>
        </ScrollArea>
      </Card>

      {/* ── Main Chat Area ────────────────────────────────────────────────── */}
      <Card className="flex-1 flex flex-col rounded-xl overflow-hidden border-border/50 min-w-0">
        {activeId ? (
          <>
            {/* Header */}
            <div className="px-6 py-4 border-b border-border/50 bg-muted/10 flex justify-between items-center shrink-0">
              <div>
                <h2 className="font-serif text-lg font-medium leading-tight">
                  {activeConv?.conversation?.title || "Conversation"}
                </h2>
                <div className="flex items-center gap-2 mt-0.5">
                  <span className="text-xs font-mono text-muted-foreground">
                    {activeConv?.conversation?.message_count ?? 0} messages
                  </span>
                  {activeConv?.conversation?.work_id && (
                    <Badge variant="secondary" className="text-[10px] h-4 px-1.5">
                      Work linked
                    </Badge>
                  )}
                </div>
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
                    <p className="text-sm">
                      {aiOnline
                        ? "Send a message to start the conversation."
                        : "AI is offline — start Lemonade or Ollama to enable responses."}
                    </p>
                  </div>
                ) : (
                  displayMessages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`flex gap-3 ${msg.role === "user" ? "flex-row-reverse" : ""}`}
                      data-testid={`message-${msg.role}-${msg.id}`}
                    >
                      {/* Avatar */}
                      <div
                        className={`w-7 h-7 shrink-0 rounded-sm flex items-center justify-center text-[11px] ${
                          msg.role === "user"
                            ? "bg-secondary text-secondary-foreground"
                            : "bg-primary text-primary-foreground"
                        }`}
                      >
                        {msg.role === "user" ? (
                          <User className="w-3.5 h-3.5" />
                        ) : (
                          <Bot className="w-3.5 h-3.5" />
                        )}
                      </div>

                      {/* Bubble */}
                      <div
                        className={`flex flex-col gap-1 max-w-[78%] ${
                          msg.role === "user" ? "items-end" : "items-start"
                        }`}
                      >
                        <div className="flex items-center gap-2">
                          <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">
                            {msg.role}
                          </span>
                          {msg.created_at && (
                            <span className="text-[10px] text-muted-foreground/40 font-mono">
                              {format(new Date(msg.created_at), "HH:mm")}
                            </span>
                          )}
                        </div>

                        <div
                          className={`px-4 py-3 rounded-lg text-sm leading-relaxed whitespace-pre-wrap break-words ${
                            msg.role === "user"
                              ? "bg-secondary/60 border border-secondary"
                              : "bg-muted/40 border border-border/40"
                          }`}
                        >
                          {msg.text || (
                            <span className="flex items-center gap-1.5 text-muted-foreground">
                              <Loader2 className="w-3.5 h-3.5 animate-spin" />
                              <span className="text-xs">Thinking…</span>
                            </span>
                          )}
                          {msg.streaming && msg.text && (
                            <span className="inline-block w-0.5 h-3.5 bg-current ml-0.5 animate-pulse align-text-bottom" />
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
                  ref={textareaRef}
                  value={draft}
                  onChange={(e) => setDraft(e.target.value)}
                  placeholder={
                    aiOnline
                      ? "Ask anything… (Enter to send, Shift+Enter for newline)"
                      : "AI offline — messages saved locally"
                  }
                  className="pr-12 resize-none py-3 text-sm"
                  rows={2}
                  disabled={sending}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      handleSend(e);
                    }
                  }}
                  data-testid="input-message"
                />
                <Button
                  type="submit"
                  size="icon"
                  disabled={!draft.trim() || sending}
                  className="absolute right-2 top-2 h-8 w-8"
                  data-testid="button-send"
                >
                  {sending ? (
                    <Loader2 className="w-4 h-4 animate-spin" />
                  ) : (
                    <Send className="w-4 h-4" />
                  )}
                </Button>
              </form>
            </div>
          </>
        ) : (
          <div className="flex-1 flex flex-col items-center justify-center text-center p-8 text-muted-foreground">
            <MessageSquare className="w-12 h-12 mb-4 opacity-20" />
            <h3 className="font-serif text-xl font-medium text-foreground">
              No Conversation Selected
            </h3>
            <p className="mt-2 max-w-sm text-sm">
              Select a conversation from the sidebar or start a new one.
            </p>
            {!aiOnline && (
              <div className="mt-4 px-4 py-3 rounded-lg bg-muted/50 border border-border/50 max-w-sm text-left text-xs text-muted-foreground space-y-1">
                <p className="font-mono font-medium text-foreground">AI is offline</p>
                <p>Start Lemonade (port 13305) or Ollama (<code>ollama serve</code>) to enable AI responses.</p>
                <p>Set <code>ORIVELLUM_AI_URL</code> if using a custom endpoint.</p>
              </div>
            )}
            <Button
              onClick={handleCreate}
              disabled={createConv.isPending}
              className="mt-6"
              data-testid="button-start-conversation"
            >
              Start New Conversation
            </Button>
          </div>
        )}
      </Card>
    </div>
  );
}
