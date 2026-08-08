/**
 * CommandPalette — Cmd+K / Ctrl+K global palette.
 *
 * Surfaces all navigation destinations, quick actions, and live knowledge
 * search.  Uses cmdk for filtering / keyboard navigation; styled to the
 * VELLUM design system.
 *
 * Keyboard shortcuts
 *   Cmd+K / Ctrl+K  toggle open
 *   Esc             close
 *   ↑ ↓             navigate items (built into cmdk)
 *   Enter           select item
 */

import React, { useCallback, useEffect, useRef, useState } from "react";
import { Command } from "cmdk";
import { useLocation } from "wouter";
import {
  Activity, Library, Workflow, Mic, Feather, Package, Globe2, BookOpen,
  BookMarked, FolderOpen, GraduationCap, MessageSquare, Network, Zap, Mail,
  Target, Inbox, CheckCircle2, HardDrive, Gauge, Search, Plus, RefreshCcw,
  ArrowRight, Sparkles, X,
} from "lucide-react";

// ── Navigation items (mirrors layout.tsx PHASES) ───────────────────────────

const NAV_ITEMS = [
  // Import
  { href: "/library",    label: "Library",       icon: Library,        group: "Import" },
  { href: "/intake",     label: "Intake",         icon: Workflow,       group: "Import" },
  // Create
  { href: "/studio",     label: "Studio",         icon: Mic,            group: "Create" },
  { href: "/write",      label: "Write",          icon: Feather,        group: "Create" },
  { href: "/finishing",  label: "Finishing",      icon: Package,        group: "Create" },
  { href: "/forge",      label: "Forge",          icon: Globe2,         group: "Create" },
  // Understand
  { href: "/works",      label: "Works",          icon: BookOpen,       group: "Understand" },
  { href: "/chat",       label: "Chat",           icon: MessageSquare,  group: "Understand" },
  { href: "/books",      label: "Books",          icon: BookMarked,     group: "Understand" },
  { href: "/learn",      label: "Learn",          icon: GraduationCap,  group: "Understand" },
  { href: "/topics",     label: "Topics",         icon: FolderOpen,     group: "Understand" },
  { href: "/graph",      label: "Graph",          icon: Network,        group: "Understand" },
  // Act
  { href: "/actions",    label: "Actions",        icon: Zap,            group: "Act" },
  { href: "/mail",       label: "Mail",           icon: Mail,           group: "Act" },
  // Review
  { href: "/projects",   label: "Projects",       icon: Target,         group: "Review" },
  { href: "/review",     label: "Review Queue",   icon: Inbox,          group: "Review" },
  { href: "/governance", label: "Governance",     icon: CheckCircle2,   group: "Review" },
  // Settings
  { href: "/system",     label: "System",         icon: Activity,       group: "Settings" },
  { href: "/backups",    label: "Backups",        icon: HardDrive,      group: "Settings" },
  { href: "/mcos",       label: "Calibration",    icon: Gauge,          group: "Settings" },
] as const;

const NAV_GROUPS = ["Import", "Create", "Understand", "Act", "Review", "Settings"] as const;

// ── Quick actions ─────────────────────────────────────────────────────────────

const QUICK_ACTIONS = [
  { id: "new-chat",    label: "New conversation",   icon: Plus,        href: "/chat" },
  { id: "import-doc",  label: "Import a document",  icon: Workflow,    href: "/intake" },
  { id: "reprocess",   label: "System diagnostics", icon: RefreshCcw,  href: "/system" },
] as const;

// ── Knowledge search ──────────────────────────────────────────────────────────

interface KnowledgeHit {
  id: string;
  title: string;
  content: string;
  work_id?: string;
  doc_id?: string;
  kind?: string;
}

function useKnowledgeSearch(query: string) {
  const [hits, setHits] = useState<KnowledgeHit[]>([]);
  const [loading, setLoading] = useState(false);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const debounce = useRef<any>(null);

  useEffect(() => {
    if (!query || query.length < 2) {
      setHits([]);
      return;
    }
    clearTimeout(debounce.current);
    debounce.current = setTimeout(async () => {
      setLoading(true);
      try {
        const r = await fetch(
          `/api/knowledge/search?q=${encodeURIComponent(query)}&limit=5`,
          { credentials: "include" },
        );
        if (r.ok) {
          const data = await r.json();
          setHits(
            (data.items ?? data.results ?? data ?? []).slice(0, 5) as KnowledgeHit[],
          );
        } else {
          setHits([]);
        }
      } catch {
        setHits([]);
      } finally {
        setLoading(false);
      }
    }, 150);
    return () => clearTimeout(debounce.current);
  }, [query]);

  return { hits, loading };
}

// ── Main component ─────────────────────────────────────────────────────────────

export function CommandPalette() {
  const [open, setOpen] = useState(false);
  const [search, setSearch] = useState("");
  const [, navigate] = useLocation();

  const { hits: knowledgeHits, loading: knowledgeLoading } = useKnowledgeSearch(search);

  // ── Keyboard shortcut ────────────────────────────────────────────────────────
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const close = useCallback(() => {
    setOpen(false);
    setSearch("");
  }, []);

  const go = useCallback(
    (href: string) => {
      navigate(href);
      close();
    },
    [navigate, close],
  );

  // Narrow nav items by search
  const filteredNav = search
    ? NAV_ITEMS.filter((item) =>
        item.label.toLowerCase().includes(search.toLowerCase()),
      )
    : NAV_ITEMS;

  const filteredActions = search
    ? QUICK_ACTIONS.filter((a) =>
        a.label.toLowerCase().includes(search.toLowerCase()),
      )
    : QUICK_ACTIONS;

  const hasResults =
    filteredNav.length > 0 ||
    filteredActions.length > 0 ||
    knowledgeHits.length > 0;

  return (
    <Command.Dialog
      open={open}
      onOpenChange={(v) => { setOpen(v); if (!v) setSearch(""); }}
      label="Command palette"
      shouldFilter={false}
      className="fixed inset-0 z-[9999] flex items-start justify-center pt-[12vh]"
      style={{ background: "rgba(0,0,0,0.55)", backdropFilter: "blur(4px)" }}
      onClick={(e) => { if (e.target === e.currentTarget) close(); }}
    >
      <div
        className="w-full max-w-[640px] mx-4 rounded-2xl overflow-hidden"
        style={{
          background: "var(--paper, #FAF8F5)",
          border: "1px solid rgba(0,0,0,0.08)",
          boxShadow: "0 24px 64px rgba(0,0,0,0.22), 0 4px 16px rgba(0,0,0,0.12)",
        }}
        onClick={(e) => e.stopPropagation()}
      >
        {/* ── Search input ───────────────────────────────────────────────────── */}
        <div
          className="flex items-center gap-3 px-4 py-3 border-b"
          style={{ borderColor: "rgba(0,0,0,0.07)" }}
        >
          <Search className="w-4 h-4 flex-shrink-0 opacity-40" />
          <Command.Input
            value={search}
            onValueChange={setSearch}
            placeholder="Search pages, knowledge, actions…"
            className="flex-1 bg-transparent border-none outline-none text-sm"
            style={{
              color: "var(--ink, #1A1A1A)",
              fontSize: "15px",
              fontFamily: "inherit",
            }}
          />
          {search && (
            <button
              onClick={() => setSearch("")}
              className="opacity-40 hover:opacity-70 transition-opacity"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}
          <kbd
            className="text-[10px] px-1.5 py-0.5 rounded opacity-30 font-mono"
            style={{ background: "rgba(0,0,0,0.07)", border: "1px solid rgba(0,0,0,0.1)" }}
          >
            ESC
          </kbd>
        </div>

        {/* ── Results list ───────────────────────────────────────────────────── */}
        <Command.List
          className="max-h-[400px] overflow-y-auto py-2"
          style={{ scrollbarWidth: "thin" }}
        >
          {/* Empty state */}
          {!hasResults && !knowledgeLoading && (
            <Command.Empty className="py-10 text-center text-sm opacity-40">
              No results for "{search}"
            </Command.Empty>
          )}

          {/* ── Quick actions ─────────────────────────────────────────────── */}
          {filteredActions.length > 0 && (
            <Command.Group
              heading="Actions"
              className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:opacity-40"
            >
              {filteredActions.map((action) => {
                const Icon = action.icon;
                return (
                  <Command.Item
                    key={action.id}
                    value={action.id}
                    onSelect={() => go(action.href)}
                    className="flex items-center gap-3 px-4 py-2.5 cursor-pointer rounded-lg mx-2 text-sm
                               data-[selected=true]:bg-[var(--green,#2D6A4F)] data-[selected=true]:text-white
                               hover:bg-black/5 transition-colors"
                  >
                    <Icon className="w-4 h-4 opacity-70 flex-shrink-0" />
                    <span className="flex-1">{action.label}</span>
                    <ArrowRight className="w-3.5 h-3.5 opacity-30" />
                  </Command.Item>
                );
              })}
            </Command.Group>
          )}

          {/* ── Navigation pages (by phase group) ────────────────────────── */}
          {search
            ? filteredNav.length > 0 && (
                <Command.Group
                  heading="Pages"
                  className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:opacity-40"
                >
                  {filteredNav.map((item) => {
                    const Icon = item.icon;
                    return (
                      <Command.Item
                        key={item.href}
                        value={item.href + item.label}
                        onSelect={() => go(item.href)}
                        className="flex items-center gap-3 px-4 py-2.5 cursor-pointer rounded-lg mx-2 text-sm
                                   data-[selected=true]:bg-[var(--green,#2D6A4F)] data-[selected=true]:text-white
                                   hover:bg-black/5 transition-colors"
                      >
                        <Icon className="w-4 h-4 opacity-70 flex-shrink-0" />
                        <span className="flex-1">{item.label}</span>
                        <span className="text-[11px] opacity-30">{item.group}</span>
                      </Command.Item>
                    );
                  })}
                </Command.Group>
              )
            : NAV_GROUPS.map((group) => {
                const groupItems = NAV_ITEMS.filter((i) => i.group === group);
                return (
                  <Command.Group
                    key={group}
                    heading={group}
                    className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:opacity-40"
                  >
                    {groupItems.map((item) => {
                      const Icon = item.icon;
                      return (
                        <Command.Item
                          key={item.href}
                          value={item.href + item.label + group}
                          onSelect={() => go(item.href)}
                          className="flex items-center gap-3 px-4 py-2 cursor-pointer rounded-lg mx-2 text-sm
                                     data-[selected=true]:bg-[var(--green,#2D6A4F)] data-[selected=true]:text-white
                                     hover:bg-black/5 transition-colors"
                        >
                          <Icon className="w-4 h-4 opacity-60 flex-shrink-0" />
                          <span className="flex-1">{item.label}</span>
                        </Command.Item>
                      );
                    })}
                  </Command.Group>
                );
              })}

          {/* ── Knowledge search results ───────────────────────────────────── */}
          {knowledgeLoading && (
            <div className="flex items-center gap-2 px-4 py-3 text-sm opacity-40">
              <Sparkles className="w-4 h-4 animate-pulse" />
              Searching knowledge…
            </div>
          )}

          {knowledgeHits.length > 0 && (
            <Command.Group
              heading="Knowledge"
              className="[&_[cmdk-group-heading]]:px-4 [&_[cmdk-group-heading]]:py-1.5 [&_[cmdk-group-heading]]:text-[10px] [&_[cmdk-group-heading]]:font-semibold [&_[cmdk-group-heading]]:tracking-widest [&_[cmdk-group-heading]]:uppercase [&_[cmdk-group-heading]]:opacity-40"
            >
              {knowledgeHits.map((hit) => (
                <Command.Item
                  key={hit.id}
                  value={`knowledge-${hit.id}`}
                  onSelect={() => {
                    if (hit.work_id) go(`/works/${hit.work_id}`);
                    else if (hit.doc_id) go(`/library/${hit.doc_id}`);
                    else go("/library");
                  }}
                  className="flex items-start gap-3 px-4 py-2.5 cursor-pointer rounded-lg mx-2
                             data-[selected=true]:bg-[var(--green,#2D6A4F)] data-[selected=true]:text-white
                             hover:bg-black/5 transition-colors"
                >
                  <Sparkles className="w-4 h-4 opacity-60 flex-shrink-0 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium truncate">{hit.title}</div>
                    {hit.content && (
                      <div className="text-xs opacity-50 line-clamp-1 mt-0.5">
                        {hit.content.replace(/\n/g, " ").slice(0, 80)}
                      </div>
                    )}
                  </div>
                </Command.Item>
              ))}
            </Command.Group>
          )}
        </Command.List>

        {/* ── Footer hint ──────────────────────────────────────────────────── */}
        <div
          className="flex items-center justify-between px-4 py-2 border-t text-[11px] opacity-30 font-mono"
          style={{ borderColor: "rgba(0,0,0,0.07)" }}
        >
          <span>↑↓ navigate · Enter select · Esc close</span>
          <span>⌘K</span>
        </div>
      </div>
    </Command.Dialog>
  );
}
