import React, { useState, useEffect, useRef, useCallback } from "react";
import { BUILD_SHA, BUILD_TIME } from "@/build-info";
import { apiFetch } from "@/lib/auth";
import { Link, useLocation } from "wouter";
import { format, isToday, isYesterday, subDays, isAfter } from "date-fns";
import {
  Sidebar,
  SidebarContent,
  SidebarHeader,
  SidebarFooter,
  SidebarProvider,
  useSidebar,
} from "@/components/ui/sidebar";
import { Collapsible, CollapsibleTrigger, CollapsibleContent } from "@/components/ui/collapsible";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Sheet, SheetContent, SheetTrigger } from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Library, MessageSquare, BookOpen, FolderOpen, Target,
  Settings, HardDrive, Activity, Mic, Wifi, WifiOff,
  ChevronRight, Plus, Search, Archive, RotateCcw,
  Pencil, Check, X, Menu, DownloadCloud, Feather,
  ALargeSmall, Loader2, CheckCircle2, ExternalLink, Gauge, Inbox, Wand2, SlidersHorizontal,
  BookMarked, GraduationCap, Zap, Package, Globe2, Mail,
} from "lucide-react";
import { useConnectivity } from "@/lib/useConnectivity";
import { useQuery } from "@tanstack/react-query";
import {
  useListConversations,
  useUpdateConversation,
  useCreateConversation,
  useListWorks,
  getListConversationsQueryKey,
  getListWorksQueryKey,
} from "@workspace/api-client-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import type { Conversation } from "@workspace/api-client-react";

// ─── Route → phase mapping ────────────────────────────────────────────────────

const PHASES = [
  {
    id: "import",
    label: "Import",
    icon: DownloadCloud,
    routes: ["/library"],
    items: [
      { name: "Library", href: "/library", icon: Library },
    ],
  },
  {
    id: "create",
    label: "Create",
    icon: Wand2,
    routes: ["/studio", "/write", "/finishing", "/forge"],
    items: [
      { name: "Studio",     href: "/studio",    icon: Mic },
      { name: "Write desk", href: "/write",     icon: Feather },
      { name: "Finishing",  href: "/finishing", icon: Package },
      { name: "Forge",      href: "/forge",     icon: Globe2 },
    ],
  },
  {
    id: "understand",
    label: "Understand",
    icon: BookOpen,
    routes: ["/works", "/chat", "/books", "/learn", "/topics"],
    items: [
      { name: "Works",  href: "/works",  icon: BookOpen },
      { name: "Books",  href: "/books",  icon: BookMarked },
      { name: "Topics", href: "/topics", icon: FolderOpen },
      { name: "Learn",  href: "/learn",  icon: GraduationCap },
      { name: "Chat",   href: "/chat",   icon: MessageSquare },
    ],
  },
  {
    id: "act",
    label: "Act",
    icon: Zap,
    routes: ["/actions", "/mail"],
    items: [
      { name: "Actions", href: "/actions", icon: Zap },
      { name: "Mail",    href: "/mail",    icon: Mail },
    ],
  },
  {
    id: "review",
    label: "Review",
    icon: Target,
    routes: ["/projects", "/governance", "/review"],
    items: [
      { name: "Projects",     href: "/projects",   icon: Target },
      { name: "Review Queue", href: "/review",     icon: Inbox },
      { name: "Governance",   href: "/governance", icon: CheckCircle2 },
    ],
  },
  {
    id: "settings",
    label: "Settings",
    icon: SlidersHorizontal,
    routes: ["/system", "/backups", "/mcos"],
    items: [
      { name: "System",      href: "/system",   icon: Activity },
      { name: "Backups",     href: "/backups",  icon: HardDrive },
      { name: "Calibration", href: "/mcos",     icon: Gauge },
    ],
  },
] as const;

// ─── Date grouping ────────────────────────────────────────────────────────────

function dateBucket(dateStr: string | undefined): string {
  if (!dateStr) return "Older";
  const d = new Date(dateStr);
  if (isToday(d))                         return "Today";
  if (isYesterday(d))                     return "Yesterday";
  if (isAfter(d, subDays(new Date(), 7))) return "Previous 7 days";
  return "Older";
}

const BUCKET_ORDER = ["Today", "Yesterday", "Previous 7 days", "Older"];

// ─── Server status ─────────────────────────────────────────────────────────────

function ServerStatus() {
  const { ok, aiReachable: aiOk, isError, isFetching, recheckNow } = useConnectivity();

  return (
    <div className="px-3 py-2 border-t border-border/40">
      <div className="flex items-center gap-2">
        <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${ok ? "bg-emerald-500" : "bg-red-500"}`} />
        <span className="text-[10px] font-mono text-muted-foreground flex-1 truncate">
          {isError ? "Server unreachable" : ok ? "Server online" : "Degraded"}
        </span>
        {!aiOk && !isError && <WifiOff className="w-3 h-3 text-amber-500 shrink-0" aria-label="AI offline" />}
        {aiOk  && <Wifi className="w-3 h-3 text-emerald-500 shrink-0" aria-label="AI online" />}
        {isFetching && <span className="w-1 h-1 rounded-full bg-muted-foreground/40 animate-pulse shrink-0" />}
        <button onClick={recheckNow} title="Check now" className="p-0.5 text-muted-foreground/40 hover:text-muted-foreground transition-colors">
          <RotateCcw className="w-2.5 h-2.5" />
        </button>
      </div>
    </div>
  );
}

// ─── Font-size controls ────────────────────────────────────────────────────────

const FONT_KEY   = "oriv-font-size";
const FONT_MIN   = 13;
const FONT_MAX   = 20;
const FONT_DEF   = 15;

function useFontSize() {
  const [size, setSize] = useState<number>(() => {
    const s = localStorage.getItem(FONT_KEY);
    return s ? parseInt(s, 10) : FONT_DEF;
  });

  useEffect(() => {
    document.documentElement.style.fontSize = `${size}px`;
    localStorage.setItem(FONT_KEY, String(size));
  }, [size]);

  return {
    size,
    inc: () => setSize(s => Math.min(s + 1, FONT_MAX)),
    dec: () => setSize(s => Math.max(s - 1, FONT_MIN)),
  };
}

// ─── Conversation history ──────────────────────────────────────────────────────

function ConversationHistory({ activeConvId, onNavigate }: { activeConvId: string | null; onNavigate: () => void }) {
  const [search,       setSearch]       = useState("");
  const [showArchived, setShowArchived] = useState(false);
  const [renamingId,   setRenamingId]   = useState<string | null>(null);
  const [renameValue,  setRenameValue]  = useState("");
  const renameRef = useRef<HTMLInputElement>(null);

  const queryClient  = useQueryClient();
  const updateConv   = useUpdateConversation();
  const createConv   = useCreateConversation();
  const [, setLocation] = useLocation();

  const { data: convsResp, isLoading } = useListConversations(
    { archived: showArchived || undefined },
    { query: { queryKey: getListConversationsQueryKey({ archived: showArchived || undefined }), refetchInterval: 20_000, staleTime: 10_000 } },
  );
  const { data: worksResp } = useListWorks({}, { query: { queryKey: getListWorksQueryKey({}), staleTime: 60_000 } });

  const workTitles = Object.fromEntries(
    (worksResp?.works ?? []).map(w => [w.id, w.title ?? "Work"])
  );

  const convs = (convsResp?.conversations ?? []).filter(c =>
    !search || (c.title ?? "Untitled").toLowerCase().includes(search.toLowerCase())
  );

  // Group by date bucket, then by work_id within each bucket
  const grouped: Record<string, Conversation[]> = {};
  for (const c of convs) {
    const b = dateBucket(c.updated_at);
    (grouped[b] ??= []).push(c);
  }

  const handleCreate = () => {
    createConv.mutate(
      { data: { title: "New conversation" } },
      {
        onSuccess: (res) => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          const id = (res as any)?.conversation?.id ?? (res as any)?.id;
          if (id) { setLocation(`/chat?id=${id}`); onNavigate(); }
        },
        onError: () => toast.error("Could not create conversation"),
      }
    );
  };

  const startRename = (c: Conversation) => {
    setRenamingId(c.id!);
    setRenameValue(c.title ?? "");
    setTimeout(() => renameRef.current?.select(), 30);
  };

  const commitRename = (id: string) => {
    const title = renameValue.trim() || "Untitled";
    updateConv.mutate(
      { convId: id, data: { title } },
      {
        onSuccess: () => queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() }),
        onError:   () => toast.error("Could not rename"),
      }
    );
    setRenamingId(null);
  };

  const toggleArchive = (c: Conversation, archive: boolean) => {
    updateConv.mutate(
      { convId: c.id!, data: { archived: (archive ? 1 : 0) as unknown as boolean } },
      {
        onSuccess: () => {
          queryClient.invalidateQueries({ queryKey: getListConversationsQueryKey() });
          toast.success(archive ? "Archived" : "Restored");
        },
        onError: () => toast.error("Could not update"),
      }
    );
  };

  return (
    <div className="flex flex-col gap-1 min-h-0">
      {/* Header row */}
      <div className="flex items-center gap-1 px-2 pt-1">
        <span className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground flex-1">Conversations</span>
        <button
          onClick={() => setShowArchived(v => !v)}
          title={showArchived ? "Hide archived" : "Show archived"}
          className={`p-2 rounded transition-colors ${showArchived ? "text-primary" : "text-muted-foreground hover:text-foreground active:text-foreground"}`}
        >
          <Archive className="w-3 h-3" />
        </button>
        <button
          onClick={handleCreate}
          disabled={createConv.isPending}
          title="New conversation"
          className="p-2 rounded text-muted-foreground hover:text-foreground active:text-foreground transition-colors"
        >
          <Plus className="w-3 h-3" />
        </button>
      </div>

      {/* Search */}
      <div className="relative px-2">
        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3 h-3 text-muted-foreground pointer-events-none" />
        <Input
          value={search}
          onChange={e => setSearch(e.target.value)}
          placeholder="Search…"
          className="pl-7 h-7 text-xs bg-background/60 border-border/50"
        />
      </div>

      {/* List */}
      <ScrollArea className="flex-1 px-1">
        {isLoading ? (
          <div className="space-y-1 p-1">
            {[1,2,3].map(i => <div key={i} className="h-8 rounded bg-muted/40 animate-pulse" />)}
          </div>
        ) : convs.length === 0 ? (
          <p className="text-[11px] text-muted-foreground px-2 py-3">
            {search ? "No matches" : showArchived ? "No archived conversations" : "No conversations yet"}
          </p>
        ) : (
          BUCKET_ORDER.filter(b => grouped[b]?.length).map(bucket => (
            <div key={bucket} className="mb-2">
              <div className="text-[9px] font-mono uppercase tracking-wider text-muted-foreground/60 px-2 pt-2 pb-0.5">
                {bucket}
              </div>
              {/* Work-scoped groups within bucket */}
              {(() => {
                const items = grouped[bucket];
                const withWork   = items.filter(c => c.work_id);
                const withoutWork = items.filter(c => !c.work_id);

                // Group work items by work_id
                const byWork: Record<string, Conversation[]> = {};
                for (const c of withWork) (byWork[c.work_id!] ??= []).push(c);

                return (
                  <>
                    {Object.entries(byWork).map(([wid, wconvs]) => (
                      <div key={wid} className="mb-1">
                        <div className="text-[9px] font-mono text-primary/60 px-2 py-0.5 flex items-center gap-1">
                          <BookOpen className="w-2.5 h-2.5" />
                          <span className="truncate">{workTitles[wid] ?? "Work"}</span>
                        </div>
                        {wconvs.map(c => (
                          <ConvRow
                            key={c.id}
                            conv={c}
                            isActive={c.id === activeConvId}
                            isRenaming={renamingId === c.id}
                            renameValue={renameValue}
                            renameRef={renamingId === c.id ? renameRef : undefined}
                            onRenameChange={setRenameValue}
                            onStartRename={startRename}
                            onCommitRename={commitRename}
                            onCancelRename={() => setRenamingId(null)}
                            onArchive={toggleArchive}
                            onNavigate={onNavigate}
                            isArchived={!!c.archived}
                          />
                        ))}
                      </div>
                    ))}
                    {withoutWork.map(c => (
                      <ConvRow
                        key={c.id}
                        conv={c}
                        isActive={c.id === activeConvId}
                        isRenaming={renamingId === c.id}
                        renameValue={renameValue}
                        renameRef={renamingId === c.id ? renameRef : undefined}
                        onRenameChange={setRenameValue}
                        onStartRename={startRename}
                        onCommitRename={commitRename}
                        onCancelRename={() => setRenamingId(null)}
                        onArchive={toggleArchive}
                        onNavigate={onNavigate}
                        isArchived={!!c.archived}
                      />
                    ))}
                  </>
                );
              })()}
            </div>
          ))
        )}
      </ScrollArea>
    </div>
  );
}

interface ConvRowProps {
  conv: Conversation;
  isActive: boolean;
  isRenaming: boolean;
  renameValue: string;
  renameRef?: React.RefObject<HTMLInputElement | null>;
  onRenameChange: (v: string) => void;
  onStartRename: (c: Conversation) => void;
  onCommitRename: (id: string) => void;
  onCancelRename: () => void;
  onArchive: (c: Conversation, archive: boolean) => void;
  onNavigate: () => void;
  isArchived: boolean;
}

function ConvRow({
  conv, isActive, isRenaming, renameValue, renameRef,
  onRenameChange, onStartRename, onCommitRename, onCancelRename,
  onArchive, onNavigate, isArchived,
}: ConvRowProps) {
  const [, setLocation] = useLocation();

  const go = () => { setLocation(`/chat?id=${conv.id}`); onNavigate(); };

  if (isRenaming) {
    return (
      <div className="flex items-center gap-1 px-2 py-0.5">
        <input
          ref={renameRef}
          value={renameValue}
          onChange={e => onRenameChange(e.target.value)}
          onKeyDown={e => {
            if (e.key === "Enter")  onCommitRename(conv.id!);
            if (e.key === "Escape") onCancelRename();
          }}
          onBlur={() => onCommitRename(conv.id!)}
          className="flex-1 text-xs bg-background border border-border/60 rounded px-1.5 py-0.5 outline-none focus:ring-1 focus:ring-primary/40"
        />
        <button onClick={() => onCommitRename(conv.id!)} className="p-2 text-emerald-500 hover:text-emerald-400 active:text-emerald-400">
          <Check className="w-3 h-3" />
        </button>
        <button onClick={onCancelRename} className="p-2 text-muted-foreground hover:text-foreground active:text-foreground">
          <X className="w-3 h-3" />
        </button>
      </div>
    );
  }

  return (
    <div
      onDoubleClick={() => onStartRename(conv)}
      onClick={go}
      className={`group flex items-center gap-1 px-2 py-1 rounded-md cursor-pointer transition-colors text-xs
        ${isActive ? "bg-primary/10 text-primary" : "text-foreground/80 hover:bg-muted/50"}
        ${isArchived ? "opacity-50" : ""}`}
    >
      <span className="flex-1 truncate min-w-0">{conv.title ?? "Untitled"}</span>
      {(conv as any).model && (
        <span className="shrink-0 text-[8px] font-mono text-muted-foreground/30 group-hover:hidden leading-none px-1 py-0.5 rounded bg-muted/20 max-w-[60px] truncate">
          {((conv as any).model as string).split("/").pop()?.split(":")[0] ?? ""}
        </span>
      )}
      <button
        onClick={e => { e.stopPropagation(); onStartRename(conv); }}
        className="opacity-0 group-hover:opacity-60 [@media(hover:none)]:opacity-60 hover:!opacity-100 p-0.5 transition-opacity"
        title="Rename"
      >
        <Pencil className="w-2.5 h-2.5" />
      </button>
      <button
        onClick={e => { e.stopPropagation(); onArchive(conv, !isArchived); }}
        className="opacity-0 group-hover:opacity-60 [@media(hover:none)]:opacity-60 hover:!opacity-100 p-0.5 transition-opacity"
        title={isArchived ? "Restore" : "Archive"}
      >
        {isArchived ? <RotateCcw className="w-2.5 h-2.5" /> : <Archive className="w-2.5 h-2.5" />}
      </button>
    </div>
  );
}

// ─── Phase accordion nav ───────────────────────────────────────────────────────

function PhaseNav({ location, onNavigate }: { location: string; onNavigate: () => void }) {
  const [openPhases, setOpenPhases] = useState<Record<string, boolean>>(() => {
    const initial: Record<string, boolean> = {};
    for (const p of PHASES) {
      initial[p.id] = p.routes.some(r => location.startsWith(r));
    }
    return initial;
  });

  // Auto-open phase when route changes
  useEffect(() => {
    setOpenPhases(prev => {
      const next = { ...prev };
      for (const p of PHASES) {
        if (p.routes.some(r => location.startsWith(r))) next[p.id] = true;
      }
      return next;
    });
  }, [location]);

  const toggle = (id: string) =>
    setOpenPhases(prev => ({ ...prev, [id]: !prev[id] }));

  // Review-queue badge: number of items awaiting a human decision.
  const { data: reviewQueue } = useQuery<{ count: number }>({
    queryKey: ["review-queue-count"],
    queryFn: async () => {
      const r = await apiFetch(`${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "") + "/review/queue?limit=1");
      if (!r.ok) throw new Error("review queue count failed");
      return r.json();
    },
    refetchInterval: 60_000,
    staleTime: 55_000,
  });
  const reviewCount = reviewQueue?.count ?? 0;

  // Mail attention badge: high-attention messages waiting in the Act phase.
  const { data: mailSummary } = useQuery<{ connected: boolean; high_attention: number }>({
    queryKey: ["mail-summary"],
    queryFn: async () => {
      const r = await apiFetch(`${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "") + "/mail/summary");
      if (!r.ok) throw new Error("mail summary failed");
      return r.json();
    },
    refetchInterval: 30_000,
    staleTime: 25_000,
  });
  // Suppress the badge while the user is already viewing the Mail workspace —
  // it's redundant when they're looking at the queue.  Invalidate on exit so
  // the badge is fresh the moment they navigate away.
  const rawMailCount = (mailSummary?.connected && mailSummary?.high_attention)
    ? mailSummary.high_attention
    : 0;
  const onMailRoute = location.startsWith("/mail");
  const mailAttentionCount = onMailRoute ? 0 : rawMailCount;

  const qcLayout = useQueryClient();
  const prevOnMailRef = useRef(onMailRoute);
  useEffect(() => {
    if (prevOnMailRef.current && !onMailRoute) {
      // Just left the mail workspace — re-fetch immediately
      qcLayout.invalidateQueries({ queryKey: ["mail-summary"] });
    }
    prevOnMailRef.current = onMailRoute;
  }, [onMailRoute, qcLayout]);

  return (
    <div className="space-y-0.5">
      {PHASES.map(phase => (
        <Collapsible key={phase.id} open={openPhases[phase.id]} onOpenChange={() => toggle(phase.id)}>
          <CollapsibleTrigger className="w-full flex items-center gap-2 px-3 py-1.5 rounded-md text-xs font-medium text-muted-foreground hover:text-foreground hover:bg-muted/40 transition-colors group">
            <phase.icon className="w-3.5 h-3.5 shrink-0" />
            <span className="flex-1 text-left font-mono uppercase tracking-wider text-[10px]">{phase.label}</span>
            <ChevronRight className={`w-3 h-3 transition-transform duration-150 ${openPhases[phase.id] ? "rotate-90" : ""}`} />
          </CollapsibleTrigger>
          <CollapsibleContent>
            <div className="ml-5 border-l border-border/40 pl-2 py-0.5 space-y-0.5">
              {phase.items.map(item => {
                const isActive = location === item.href || location.startsWith(item.href + "/") || location.startsWith(item.href + "?");
                const disabled = (item as any).disabled;
                return (
                  <Link
                    key={item.href}
                    href={disabled ? "#" : item.href}
                    onClick={disabled ? e => e.preventDefault() : onNavigate}
                    className={`flex items-center gap-2 px-2 py-1 rounded-md text-xs transition-colors
                      ${disabled ? "opacity-30 cursor-not-allowed" : "cursor-pointer"}
                      ${isActive ? "bg-primary/10 text-primary font-medium" : "text-foreground/70 hover:bg-muted/50 hover:text-foreground"}`}
                  >
                    <item.icon className="w-3.5 h-3.5 shrink-0" />
                    <span>{item.name}</span>
                    {item.href === "/review" && reviewCount > 0 && (
                      <span
                        className="ml-auto min-w-[18px] px-1 py-px rounded-full bg-primary/15 text-primary text-[10px] font-mono text-center"
                        data-testid="review-queue-badge"
                      >
                        {reviewCount > 99 ? "99+" : reviewCount}
                      </span>
                    )}
                    {item.href === "/mail" && mailAttentionCount > 0 && (
                      <span
                        className="ml-auto min-w-[18px] px-1 py-px rounded-full bg-destructive/15 text-destructive text-[10px] font-mono text-center"
                        data-testid="mail-attention-badge"
                      >
                        {mailAttentionCount > 99 ? "99+" : mailAttentionCount}
                      </span>
                    )}
                    {disabled && <span className="ml-auto text-[9px] font-mono text-muted-foreground/40">soon</span>}
                  </Link>
                );
              })}
            </div>
          </CollapsibleContent>
        </Collapsible>
      ))}
    </div>
  );
}

// ─── Sidebar inner content (shared between desktop + mobile sheet) ─────────────

function SidebarInner({ onNavigate }: { onNavigate: () => void }) {
  const [location] = useLocation();
  const { inc, dec, size } = useFontSize();

  // Extract active conv id from URL (e.g. /chat?id=xxx)
  const activeConvId =
    location.startsWith("/chat")
      ? new URLSearchParams(typeof window !== "undefined" ? window.location.search : "").get("id")
      : null;

  return (
    <div className="flex flex-col h-full">
      {/* Today / Dashboard — always pinned */}
      <div className="px-2 pb-1 pt-1">
        <Link
          href="/"
          onClick={onNavigate}
          className={`flex items-center gap-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors
            ${location === "/" ? "bg-primary/10 text-primary" : "text-foreground/80 hover:bg-muted/50"}`}
        >
          <Activity className="w-4 h-4 shrink-0" />
          <span>Today</span>
        </Link>
      </div>

      {/* Phase accordion */}
      <div className="px-2 pb-2">
        <PhaseNav location={location} onNavigate={onNavigate} />
      </div>

      <div className="border-t border-border/30 mx-2" />

      {/* Conversation history — fills remaining space */}
      <div className="flex-1 min-h-0 pt-1 flex flex-col">
        <ConversationHistory activeConvId={activeConvId} onNavigate={onNavigate} />
      </div>

      {/* Footer: font controls + server status + build stamp */}
      <div className="border-t border-border/40">
        <div className="flex items-center gap-1 px-3 py-1.5">
          <ALargeSmall className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-[10px] font-mono text-muted-foreground flex-1">Text size</span>
          <button
            onClick={dec}
            disabled={size <= FONT_MIN}
            className="w-5 h-5 rounded text-xs font-bold text-muted-foreground hover:text-foreground hover:bg-muted/50 disabled:opacity-30 transition-colors"
            title="Smaller text"
          >A−</button>
          <span className="text-[10px] font-mono text-muted-foreground w-5 text-center">{size}</span>
          <button
            onClick={inc}
            disabled={size >= FONT_MAX}
            className="w-5 h-5 rounded text-sm font-bold text-muted-foreground hover:text-foreground hover:bg-muted/50 disabled:opacity-30 transition-colors"
            title="Larger text"
          >A+</button>
        </div>
        <ServerStatus />
        <div className="px-3 py-1.5 border-t border-border/20">
          <p className="text-[9px] font-mono text-muted-foreground/40 leading-tight" title={`Built ${BUILD_TIME}`}>
            Build {BUILD_SHA} · {BUILD_TIME.slice(0, 16).replace('T', ' ')}
          </p>
        </div>
      </div>
    </div>
  );
}

// ─── Progress panel ────────────────────────────────────────────────────────────

interface Job { id: string; title?: string | null; source?: string | null; readiness: string; work_title?: string | null; completed_at?: string | null; }

function useJobs(open: boolean) {
  return useQuery({
    queryKey: ["system", "jobs"],
    queryFn: async () => {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const r = await apiFetch(`${base}/system/jobs`);
      if (!r.ok) throw new Error("jobs fetch failed");
      return r.json() as Promise<{ jobs: Job[]; total: number; recently_done: Job[]; nightshift: { ran_at: string } | null }>;
    },
    refetchInterval: open ? 3_000 : 15_000,
    staleTime: 2_000,
  });
}

const READINESS_STEPS = ["queued", "imported", "chunked", "extracted", "harvested", "ready"];

function ProgressPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading } = useJobs(open);
  const jobs = data?.jobs ?? [];
  const recentlyDone = data?.recently_done ?? [];

  // Group active jobs by work
  const byWork: Record<string, { title: string; jobs: Job[] }> = {};
  for (const j of jobs) {
    const wid = j.work_title ?? "No Work";
    if (!byWork[wid]) byWork[wid] = { title: wid, jobs: [] };
    byWork[wid].jobs.push(j);
  }

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent side="right" className="w-80 p-0 flex flex-col">
        <div className="px-4 py-3 border-b border-border/40 flex items-center gap-2">
          <Activity className="w-4 h-4 text-primary" />
          <span className="font-serif font-medium text-sm">Background Jobs</span>
          {jobs.length > 0 && (
            <span className="ml-1 px-1.5 py-0.5 rounded-full bg-primary/10 text-primary text-[10px] font-mono">{jobs.length}</span>
          )}
        </div>
        <ScrollArea className="flex-1">
          <div className="p-4 space-y-4">
            {isLoading ? (
              <div className="space-y-2">
                {[1, 2].map(i => <div key={i} className="h-14 rounded-lg bg-muted/40 animate-pulse" />)}
              </div>
            ) : jobs.length === 0 && recentlyDone.length === 0 ? (
              <div className="text-center py-12">
                <CheckCircle2 className="w-8 h-8 mx-auto mb-3 text-emerald-500/40" />
                <p className="text-sm text-muted-foreground">All caught up — no jobs running</p>
              </div>
            ) : (
              <>
                {Object.entries(byWork).map(([wid, { title, jobs: wjobs }]) => (
                  <div key={wid} className="space-y-2">
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">{title}</p>
                    {wjobs.map(j => {
                      const step = READINESS_STEPS.indexOf(j.readiness);
                      const pct  = step < 0 ? 0 : Math.round((step / (READINESS_STEPS.length - 1)) * 100);
                      const name = j.title || (j.source ? j.source.split("/").pop() : j.id);
                      return (
                        <div key={j.id} className="p-3 rounded-lg bg-muted/20 border border-border/40 space-y-2">
                          <div className="flex items-center gap-2">
                            <Loader2 className="w-3 h-3 text-primary animate-spin shrink-0" />
                            <span className="text-xs font-medium truncate flex-1">{name}</span>
                            <span className="text-[10px] font-mono text-muted-foreground shrink-0">{j.readiness}</span>
                            <Link
                              href={`/library/${j.id}`}
                              onClick={onClose}
                              className="shrink-0 text-muted-foreground/50 hover:text-primary transition-colors"
                              title="Open in Library"
                            >
                              <ExternalLink className="w-3 h-3" />
                            </Link>
                          </div>
                          <div className="h-1.5 bg-muted rounded-full overflow-hidden">
                            <div
                              className="h-full bg-primary/60 rounded-full transition-all duration-500"
                              style={{ width: `${pct}%` }}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>
                ))}
                {recentlyDone.length > 0 && (
                  <div className="space-y-2">
                    {jobs.length > 0 && <div className="border-t border-border/30 pt-2" />}
                    <p className="text-[10px] font-mono uppercase tracking-wider text-muted-foreground">Recently completed</p>
                    {recentlyDone.map(j => {
                      const name = j.title || (j.source ? j.source.split("/").pop() : j.id);
                      const isErr = j.readiness === "error" || j.readiness === "no_text";
                      return (
                        <div key={j.id} className="p-3 rounded-lg bg-muted/10 border border-border/30 flex items-center gap-2">
                          {isErr
                            ? <span className="w-3 h-3 rounded-full bg-red-400 shrink-0" />
                            : <CheckCircle2 className="w-3 h-3 text-emerald-500 shrink-0" />}
                          <span className="text-xs text-muted-foreground truncate flex-1">{name}</span>
                          <Link
                            href={`/library/${j.id}`}
                            onClick={onClose}
                            className="shrink-0 text-muted-foreground/40 hover:text-primary transition-colors"
                            title="Open in Library"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </Link>
                        </div>
                      );
                    })}
                  </div>
                )}
              </>
            )}
            {data?.nightshift && (
              <div className="pt-2 border-t border-border/30">
                <p className="text-[10px] font-mono text-muted-foreground">
                  Last nightshift: {new Date(data.nightshift.ran_at).toLocaleString()}
                </p>
              </div>
            )}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

// ─── Route title helper ────────────────────────────────────────────────────────

function useRouteTitle(): string {
  const [location] = useLocation();
  const path = location.split("?")[0];
  if (path === "/") return "Today";
  if (path === "/chat") return "Chat";
  if (path.match(/^\/works\/[^/]+\/intelligence/)) return "Intelligence";
  if (path.match(/^\/works\/[^/]+/)) return "Work";
  if (path === "/works") return "Works";
  if (path.match(/^\/library\/[^/]+/)) return "Document";
  if (path === "/library") return "Library";
  if (path === "/projects") return "Projects";
  if (path.match(/^\/projects\/[^/]+/)) return "Project";
  if (path === "/studio") return "Studio";
  if (path === "/write") return "Write";
  if (path === "/backups") return "Backups";
  if (path === "/system") return "System";
  if (path === "/mcos") return "Calibration";
  if (path === "/governance") return "Governance";
  if (path === "/review") return "Review";
  if (path === "/forge") return "Forge";
  if (path.match(/^\/forge\/[^/]+/)) return "Forge project";
  return "Orivellum";
}

// ─── Nav Rail (tablet tier: 560px–1023px container width) ─────────────────────

const RAIL_ITEMS = [
  { label: "Today",    href: "/",           icon: Activity      },
  { label: "Chat",     href: "/chat",        icon: MessageSquare },
  { label: "Works",    href: "/works",       icon: BookOpen      },
  { label: "Library",  href: "/library",     icon: Library       },
  { label: "Studio",   href: "/studio",      icon: Mic           },
  { label: "Write",    href: "/write",       icon: Feather       },
  { label: "Review",   href: "/review",      icon: Inbox         },
  { label: "System",   href: "/system",      icon: HardDrive     },
] as const;

function NavRail({
  onProgressOpen,
  activeJobCount,
  serverOk,
  aiOk,
  healthFetching,
}: {
  onProgressOpen: () => void;
  activeJobCount: number;
  serverOk: boolean;
  aiOk: boolean;
  healthFetching: boolean;
}) {
  const [location, setLocation] = useLocation();
  // Review-queue badge count
  const { data: reviewQueue } = useQuery<{ count: number }>({
    queryKey: ["review-queue-count"],
    queryFn: async () => {
      const r = await apiFetch(
        `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "") + "/review/queue?limit=1"
      );
      if (!r.ok) throw new Error("review queue count failed");
      return r.json();
    },
    refetchInterval: 60_000,
    staleTime: 55_000,
  });
  const reviewCount = reviewQueue?.count ?? 0;

  return (
    <nav
      aria-label="Primary navigation rail"
      /* Hidden at phone (<560px) and desktop sidebar width (≥1024px) */
      className="hidden @[560px]:flex @[1024px]:hidden flex-col items-center gap-0.5 py-2 px-1 w-16 shrink-0 border-r overflow-y-auto"
      style={{ background: "var(--paper-2)", borderColor: "var(--line)" }}
    >
      {/* Brand sigil */}
      <Link href="/" aria-label="Home">
        <div
          className="w-9 h-9 rounded-[10px] flex items-center justify-center font-serif font-bold text-sm mb-1 shrink-0 text-[#F4EEE1] mt-1"
          style={{ background: "var(--green-raw)" }}
        >
          <span style={{ fontVariationSettings: '"opsz" 40' }}>O</span>
        </div>
      </Link>

      <div className="h-px w-8 my-1 shrink-0" style={{ background: "var(--line)" }} />

      {/* Primary nav items */}
      {RAIL_ITEMS.map(({ label, href, icon: Icon }) => {
        const isActive =
          location === href || (href !== "/" && location.startsWith(href));
        const showBadge = href === "/review" && reviewCount > 0;
        return (
          <button
            key={href}
            onClick={() => setLocation(href)}
            className={[
              "relative flex flex-col items-center justify-center gap-0.5 w-14 min-h-[52px] rounded-xl transition-colors",
              "touch-manipulation",
              isActive
                ? ""
                : "text-muted-foreground hover:bg-muted/50 active:bg-muted/70",
            ].join(" ")}
            style={
              isActive
                ? { background: "var(--green-soft)", color: "var(--green)" }
                : {}
            }
            aria-label={label}
            aria-current={isActive ? "page" : undefined}
            title={label}
          >
            <Icon
              className="w-[18px] h-[18px] shrink-0"
              style={isActive ? { color: "var(--gilt)" } : {}}
            />
            <span
              className="text-[8px] font-mono uppercase tracking-wider leading-tight text-center"
              style={isActive ? { color: "var(--green)" } : {}}
            >
              {label}
            </span>
            {showBadge && (
              <span
                className="absolute top-1 right-1.5 min-w-[14px] h-3.5 px-0.5 rounded-full text-[7px] font-bold flex items-center justify-center leading-none"
                style={{ background: "var(--rust)", color: "#fefcf6" }}
              >
                {reviewCount > 9 ? "9+" : reviewCount}
              </span>
            )}
          </button>
        );
      })}

      {/* Spacer pushes status to bottom */}
      <div className="flex-1" />

      {/* Background jobs */}
      <button
        onClick={onProgressOpen}
        className={[
          "flex flex-col items-center justify-center gap-0.5 w-14 min-h-[44px] rounded-xl transition-colors touch-manipulation",
          activeJobCount > 0 ? "" : "text-muted-foreground hover:bg-muted/50",
        ].join(" ")}
        style={activeJobCount > 0 ? { color: "var(--green)" } : {}}
        aria-label={
          activeJobCount > 0 ? `${activeJobCount} jobs running` : "View background jobs"
        }
        title="Background jobs"
      >
        {activeJobCount > 0 ? (
          <span className="relative inline-flex">
            <Loader2 className="w-4 h-4 animate-spin" />
            <span
              className="absolute -top-1 -right-1 w-3.5 h-3.5 rounded-full text-[7px] font-bold flex items-center justify-center leading-none"
              style={{ background: "var(--rust)", color: "#fefcf6" }}
            >
              {activeJobCount > 9 ? "9+" : activeJobCount}
            </span>
          </span>
        ) : (
          <Activity className="w-4 h-4" />
        )}
        <span className="text-[7.5px] font-mono uppercase tracking-wider">Jobs</span>
      </button>

      {/* Server health dot */}
      <span
        className={[
          "w-2 h-2 rounded-full mb-2 shrink-0 transition-colors",
          healthFetching ? "animate-pulse" : "",
        ].join(" ")}
        style={{
          background: !serverOk
            ? "var(--rust)"
            : !aiOk
            ? "#f59e0b"
            : "var(--green-2)",
        }}
        title={
          !serverOk
            ? "Server unreachable"
            : !aiOk
            ? "Server online — AI degraded"
            : "Server online"
        }
      />
    </nav>
  );
}

// ─── Mobile navigation bottom sheet ───────────────────────────────────────────

const NAV_ITEMS = [
  { label: "Today",      href: "/",           icon: Activity      },
  { label: "Chat",       href: "/chat",        icon: MessageSquare },
  { label: "Works",      href: "/works",       icon: BookOpen      },
  { label: "Library",    href: "/library",     icon: Library       },
  { label: "Studio",     href: "/studio",      icon: Mic           },
  { label: "Write",      href: "/write",       icon: Feather       },
  { label: "Actions",    href: "/actions",     icon: Zap           },
  { label: "Projects",   href: "/projects",    icon: Target        },
  { label: "Review",     href: "/review",      icon: Inbox         },
  { label: "Governance", href: "/governance",  icon: CheckCircle2  },
  { label: "System",     href: "/system",      icon: HardDrive     },
  { label: "Calibration",href: "/mcos",        icon: Gauge         },
] as const;

function MobileNavSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [location, setLocation] = useLocation();
  const go = (href: string) => { setLocation(href); onClose(); };

  return (
    <Sheet open={open} onOpenChange={v => !v && onClose()}>
      <SheetContent
        side="bottom"
        className="p-0 rounded-t-2xl flex flex-col bg-background border-t border-border/50 [&>button.absolute]:hidden"
        style={{ height: "82svh", paddingBottom: "max(1.5rem, var(--sai-bottom))" }}
      >
        {/* Drag handle visual */}
        <div className="flex justify-center pt-3 pb-1 shrink-0" aria-hidden>
          <div className="w-10 h-1 rounded-full bg-border/70" />
        </div>

        {/* App branding row */}
        <div className="px-5 py-3 flex items-center gap-2.5 shrink-0" style={{ borderBottom: '1px solid var(--line)' }}>
          <div className="w-7 h-7 rounded-[8px] flex items-center justify-center font-serif font-bold text-base shrink-0 text-[#F4EEE1]" style={{ background: 'var(--green-raw)' }}>
            <span style={{ fontVariationSettings: '"opsz" 40' }}>O</span>
          </div>
          <span className="brand-orivellum">
            Ori<span className="brand-accent">vellum</span>
          </span>
        </div>

        {/* 3-column nav grid — thumb-friendly ≥80px tall cells */}
        <ScrollArea className="flex-1 px-3 pt-3">
          <div className="grid grid-cols-3 gap-2 pb-2">
            {NAV_ITEMS.map(({ label, href, icon: Icon }) => {
              const isActive =
                location === href ||
                (href !== "/" && location.startsWith(href));
              return (
                <button
                  key={href}
                  onClick={() => go(href)}
                  className={[
                    "flex flex-col items-center justify-center gap-2 py-4 rounded-xl border",
                    "text-center transition-colors min-h-[80px] active:scale-95",
                    isActive
                      ? "bg-primary/10 border-primary/30 text-primary font-medium"
                      : "bg-muted/30 border-border/40 text-foreground/75 hover:bg-muted/60",
                  ].join(" ")}
                >
                  <Icon className="w-5 h-5 shrink-0" />
                  <span className="text-xs font-medium leading-tight">{label}</span>
                </button>
              );
            })}
          </div>
        </ScrollArea>
      </SheetContent>
    </Sheet>
  );
}

// ─── Mobile header ─────────────────────────────────────────────────────────────

function MobileHeader({
  onMenuOpen,
  onProgressOpen,
  activeJobCount,
  serverOk,
  aiOk,
  healthFetching,
}: {
  onMenuOpen: () => void;
  onProgressOpen: () => void;
  activeJobCount: number;
  serverOk: boolean;
  aiOk: boolean;
  healthFetching: boolean;
}) {
  const title = useRouteTitle();
  return (
    <div
      className="flex @[560px]:hidden items-center px-2 z-10 shrink-0 glass-vellum"
      style={{ paddingTop: "max(0.75rem, var(--sai-top))", paddingBottom: "0.75rem", borderBottom: '1px solid var(--line)' }}
    >
      {/* App-menu button — ≥44×44pt touch target per HIG */}
      <button
        onClick={onMenuOpen}
        className="flex items-center justify-center w-11 h-11 rounded-lg text-foreground/80 hover:bg-muted/50 transition-colors shrink-0"
        aria-label="Open navigation menu"
      >
        <Menu className="w-5 h-5" />
      </button>

      {/* Centered route title + health dot */}
      <div className="flex-1 flex items-center justify-center gap-1.5 min-w-0 px-2">
        <span className="font-serif font-semibold text-base tracking-tight truncate">{title}</span>
        <span
          title={
            !serverOk ? "Server unreachable"
            : !aiOk   ? "Server online — AI degraded"
            :            "Server online"
          }
          className={`w-2 h-2 rounded-full shrink-0 transition-colors ${
            healthFetching ? "bg-amber-400 animate-pulse"
            : !serverOk   ? "bg-red-500"
            : !aiOk       ? "bg-amber-400"
            :                "bg-emerald-500"
          }`}
        />
      </div>

      {/* Right slot: background jobs indicator — ≥44×44pt */}
      <button
        onClick={onProgressOpen}
        className={`flex items-center justify-center w-11 h-11 rounded-lg transition-colors shrink-0 ${
          activeJobCount > 0
            ? "text-primary"
            : "text-muted-foreground hover:bg-muted/50"
        }`}
        aria-label={activeJobCount > 0 ? `${activeJobCount} jobs running` : "View background jobs"}
        title="View background jobs"
      >
        {activeJobCount > 0 ? (
          <span className="relative inline-flex">
            <Loader2 className="w-5 h-5 animate-spin" />
            <span className="absolute -top-1.5 -right-1.5 min-w-[14px] h-3.5 px-0.5 rounded-full bg-primary text-primary-foreground text-[8px] font-bold flex items-center justify-center leading-none">
              {activeJobCount > 9 ? "9+" : activeJobCount}
            </span>
          </span>
        ) : (
          <Activity className="w-5 h-5" />
        )}
      </button>
    </div>
  );
}

// ─── AppLayout ─────────────────────────────────────────────────────────────────

export function AppLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen,   setMobileOpen]   = useState(false);
  const [progressOpen, setProgressOpen] = useState(false);
  const [location] = useLocation();
  const { data: jobsData } = useJobs(false);
  const activeJobCount = jobsData?.total ?? 0;
  const { ok: serverOk, aiReachable: aiOk, isFetching: healthFetching } = useConnectivity();

  // Auto-dismiss the progress panel 2 s after all jobs finish
  const prevJobCount = useRef(0);
  useEffect(() => {
    let t: ReturnType<typeof setTimeout> | undefined;
    if (prevJobCount.current > 0 && activeJobCount === 0 && progressOpen) {
      t = setTimeout(() => setProgressOpen(false), 2000);
    }
    prevJobCount.current = activeJobCount;
    return () => { if (t) clearTimeout(t); };
  }, [activeJobCount, progressOpen]);

  // Slide-in animation on path change (mobile only; CSS guards for reduced-motion)
  const contentRef = useRef<HTMLDivElement>(null);
  const prevPath = useRef(location.split("?")[0]);
  useEffect(() => {
    const path = location.split("?")[0];
    if (prevPath.current === path) return;
    prevPath.current = path;
    const el = contentRef.current;
    if (!el) return;
    el.classList.remove("mobile-page-slide-in");
    void el.offsetWidth; // force reflow to restart the animation
    el.classList.add("mobile-page-slide-in");
    const t = setTimeout(() => el.classList.remove("mobile-page-slide-in"), 250);
    return () => clearTimeout(t);
  }, [location]);

  return (
    <SidebarProvider>
      <ProgressPanel open={progressOpen} onClose={() => setProgressOpen(false)} />
      {/* Mobile bottom-sheet nav — rendered outside the main layout grid */}
      <MobileNavSheet open={mobileOpen} onClose={() => setMobileOpen(false)} />

      {/* Height is driven by --visual-viewport-height (set by the inline VisualViewport
          controller in index.html) so the shell stays inside the visible viewport on
          iPhone Safari regardless of address bar state.

          @container gives descendants container-query breakpoints keyed off the
          app's own inline-size, not the viewport — so Split View / Stage Manager
          at any fraction reflows correctly:
            < 560px  → phone layout (MobileHeader + bottom sheet)
            ≥ 560px  → compact two-pane: NavRail + single content column
            ≥ 1024px → full desktop: ShadCN Sidebar + content column           */}
      <div
        className="@container flex w-full overflow-hidden"
        style={{ height: "var(--visual-viewport-height, 100dvh)" }}
      >
        {/* Desktop sidebar — visible at container ≥ 1024px (replaces lg:flex) */}
        <Sidebar className="hidden @[1024px]:flex border-r border-border/50 bg-sidebar flex-col w-56 shrink-0">
          <SidebarHeader className="px-5 py-3.5 flex flex-row items-center gap-3 border-b shrink-0" style={{ borderColor: 'var(--line)' }}>
            {/* Forest-green sigil */}
            <div className="w-7 h-7 rounded-[8px] flex items-center justify-center font-serif font-bold text-base shrink-0 text-[#F4EEE1]" style={{ background: 'var(--green-raw)' }}>
              <span style={{ fontVariationSettings: '"opsz" 40' }}>O</span>
            </div>
            {/* VELLUM brand — Fraunces with gilt accent on "vellum" */}
            <div className="brand-orivellum">
              Ori<span className="brand-accent">vellum</span>
            </div>
          </SidebarHeader>
          <SidebarContent className="flex-1 min-h-0 overflow-hidden">
            <SidebarInner onNavigate={() => {}} />
          </SidebarContent>
        </Sidebar>

        {/* Nav Rail — tablet tier only (560px–1023px container width) */}
        <NavRail
          onProgressOpen={() => setProgressOpen(true)}
          activeJobCount={activeJobCount}
          serverOk={serverOk}
          aiOk={aiOk}
          healthFetching={healthFetching}
        />

        {/* Main content column */}
        <main className="flex-1 overflow-hidden bg-background selection:bg-primary/20 flex flex-col min-w-0">
          {/* Mobile compact header — visible only at container < 560px */}
          <MobileHeader
            onMenuOpen={() => setMobileOpen(true)}
            onProgressOpen={() => setProgressOpen(true)}
            activeJobCount={activeJobCount}
            serverOk={serverOk}
            aiOk={aiOk}
            healthFetching={healthFetching}
          />

          {/* Desktop progress badge — fixed top-right, never overlaps content */}
          <div className="fixed top-4 right-6 z-20 pointer-events-none hidden @[1024px]:flex">
            <button
              onClick={() => setProgressOpen(true)}
              className={`pointer-events-auto flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-mono border shadow-sm transition-all
                ${activeJobCount > 0
                  ? "bg-primary text-primary-foreground border-primary animate-pulse"
                  : "bg-background/80 backdrop-blur text-muted-foreground border-border/50 hover:border-border hover:text-foreground"}`}
              title="View background jobs"
            >
              {activeJobCount > 0 ? (
                <><Loader2 className="w-3 h-3 animate-spin" />{activeJobCount} running</>
              ) : (
                <><Activity className="w-3 h-3" />Progress</>
              )}
            </button>
          </div>

          {/* Content area: flex-1 + min-h-0 let full-height pages (chat, write desk)
              fill the available space exactly; overflow-auto gives normal pages a
              scrollbar. This div is the ONLY vertically scrolling surface for
              non-chat pages — html/body have overflow:hidden.
              Padding scales with container tiers so text stays 60–75ch at any width. */}
          <div
            ref={contentRef}
            className="flex-1 min-h-0 overflow-auto w-full max-w-[1400px] mx-auto px-4 @[560px]:px-6 @[1024px]:px-8 py-4 @[560px]:py-6 @[1024px]:py-8 flex flex-col"
          >
            {children}
          </div>
        </main>
      </div>
    </SidebarProvider>
  );
}
