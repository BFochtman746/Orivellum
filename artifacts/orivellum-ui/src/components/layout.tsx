import React, { useState, useEffect, useRef, useCallback } from "react";
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
  ALargeSmall, Loader2, CheckCircle2, ExternalLink,
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
    routes: ["/library", "/files", "/studio"],
    items: [
      { name: "Library",  href: "/library", icon: Library },
      { name: "Files",    href: "/files",   icon: FolderOpen },
      { name: "Studio",   href: "/studio",  icon: Mic },
    ],
  },
  {
    id: "understand",
    label: "Understand",
    icon: BookOpen,
    routes: ["/works", "/chat"],
    items: [
      { name: "Works", href: "/works", icon: BookOpen },
      { name: "Chat",  href: "/chat",  icon: MessageSquare },
    ],
  },
  {
    id: "write",
    label: "Write",
    icon: Feather,
    routes: ["/write"],
    items: [
      { name: "Write desk", href: "/write", icon: Feather },
    ],
  },
  {
    id: "review",
    label: "Review",
    icon: Target,
    routes: ["/projects", "/backups", "/system"],
    items: [
      { name: "Projects", href: "/projects", icon: Target },
      { name: "Backups",  href: "/backups",  icon: HardDrive },
      { name: "System",   href: "/system",   icon: Settings },
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
          className={`p-1 rounded transition-colors ${showArchived ? "text-primary" : "text-muted-foreground hover:text-foreground"}`}
        >
          <Archive className="w-3 h-3" />
        </button>
        <button
          onClick={handleCreate}
          disabled={createConv.isPending}
          title="New conversation"
          className="p-1 rounded text-muted-foreground hover:text-foreground transition-colors"
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
        <button onClick={() => onCommitRename(conv.id!)} className="p-0.5 text-emerald-500 hover:text-emerald-400">
          <Check className="w-3 h-3" />
        </button>
        <button onClick={onCancelRename} className="p-0.5 text-muted-foreground hover:text-foreground">
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
      <span className="flex-1 truncate">{conv.title ?? "Untitled"}</span>
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

      {/* Footer: font controls + server status */}
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
      </div>
    </div>
  );
}

// ─── Progress panel ────────────────────────────────────────────────────────────

interface Job { id: string; title?: string | null; source?: string | null; readiness: string; work_title?: string | null; }

function useJobs(open: boolean) {
  return useQuery({
    queryKey: ["system", "jobs"],
    queryFn: async () => {
      const base = `${import.meta.env.BASE_URL}api`.replace(/\/+/g, "/").replace(/\/$/, "");
      const r = await apiFetch(`${base}/system/jobs`);
      if (!r.ok) throw new Error("jobs fetch failed");
      return r.json() as Promise<{ jobs: Job[]; total: number; nightshift: { ran_at: string } | null }>;
    },
    refetchInterval: open ? 3_000 : 15_000,
    staleTime: 2_000,
  });
}

const READINESS_STEPS = ["queued", "imported", "chunked", "extracted", "harvested", "ready"];

function ProgressPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading } = useJobs(open);
  const jobs = data?.jobs ?? [];

  // Group by work
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
            ) : jobs.length === 0 ? (
              <div className="text-center py-12">
                <CheckCircle2 className="w-8 h-8 mx-auto mb-3 text-emerald-500/40" />
                <p className="text-sm text-muted-foreground">All caught up — no jobs running</p>
              </div>
            ) : (
              Object.entries(byWork).map(([wid, { title, jobs: wjobs }]) => (
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
              ))
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

// ─── Mobile hamburger trigger ──────────────────────────────────────────────────

function MobileMenuButton() {
  return (
    <SheetTrigger asChild>
      <button className="lg:hidden p-2 rounded-md text-muted-foreground hover:text-foreground hover:bg-muted/50 transition-colors" aria-label="Open menu">
        <Menu className="w-5 h-5" />
      </button>
    </SheetTrigger>
  );
}

// ─── AppLayout ─────────────────────────────────────────────────────────────────

export function AppLayout({ children }: { children: React.ReactNode }) {
  const [mobileOpen,    setMobileOpen]    = useState(false);
  const [progressOpen,  setProgressOpen]  = useState(false);
  const { data: jobsData } = useJobs(false);
  const activeJobCount = jobsData?.total ?? 0;

  return (
    <SidebarProvider>
      <ProgressPanel open={progressOpen} onClose={() => setProgressOpen(false)} />
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <div className="flex min-h-screen w-full">
          {/* Desktop sidebar */}
          <Sidebar className="hidden lg:flex border-r border-border/50 bg-sidebar flex-col w-56 shrink-0">
            <SidebarHeader className="px-4 py-3 flex flex-row items-center gap-2 border-b border-border/30">
              <div className="bg-primary text-primary-foreground w-7 h-7 rounded-sm flex items-center justify-center font-serif font-bold text-base shrink-0">
                O
              </div>
              <div className="font-serif font-bold text-lg tracking-tight">Orivellum</div>
            </SidebarHeader>
            <SidebarContent className="flex-1 min-h-0 overflow-hidden">
              <SidebarInner onNavigate={() => {}} />
            </SidebarContent>
          </Sidebar>

          {/* Mobile sheet sidebar */}
          <SheetContent side="left" className="p-0 w-64 flex flex-col bg-sidebar">
            <div className="px-4 py-3 flex items-center gap-2 border-b border-border/30">
              <div className="bg-primary text-primary-foreground w-7 h-7 rounded-sm flex items-center justify-center font-serif font-bold text-base shrink-0">
                O
              </div>
              <div className="font-serif font-bold text-lg tracking-tight">Orivellum</div>
            </div>
            <div className="flex-1 min-h-0 overflow-hidden">
              <SidebarInner onNavigate={() => setMobileOpen(false)} />
            </div>
          </SheetContent>

          {/* Main content */}
          <main className="flex-1 overflow-auto bg-background selection:bg-primary/20">
            {/* Mobile top bar */}
            <div className="lg:hidden flex items-center gap-2 px-4 py-3 border-b border-border/30 bg-background/80 backdrop-blur sticky top-0 z-10">
              <MobileMenuButton />
              <div className="font-serif font-bold text-base tracking-tight flex-1">Orivellum</div>
              {/* Progress button — pinned in mobile top bar so it never floats over content */}
              <button
                onClick={() => setProgressOpen(true)}
                className={`flex items-center gap-1 px-2.5 py-1 rounded-full text-xs font-mono border shadow-sm transition-all
                  ${activeJobCount > 0
                    ? "bg-primary text-primary-foreground border-primary animate-pulse"
                    : "bg-muted/60 text-muted-foreground border-border/50"}`}
                title="View background jobs"
              >
                {activeJobCount > 0 ? (
                  <><Loader2 className="w-3 h-3 animate-spin" />{activeJobCount}</>
                ) : (
                  <><Activity className="w-3 h-3" />Progress</>
                )}
              </button>
            </div>
            {/* Progress badge — desktop only, floats top-right of content area */}
            <div className="sticky top-0 z-10 pointer-events-none hidden lg:flex justify-end px-8 pt-6">
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
            <div className="h-full w-full max-w-[1400px] mx-auto px-6 lg:px-8 pb-6 lg:pb-8 lg:-mt-10">
              {children}
            </div>
          </main>
        </div>
      </Sheet>
    </SidebarProvider>
  );
}
