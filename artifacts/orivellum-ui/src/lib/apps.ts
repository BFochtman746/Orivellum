/**
 * App registry — the GD-industrial Home Screen launches these apps.
 *
 * Each app is a full-screen workspace with its own navigation (rendered by
 * AppFrame). The route → app mapping here is the single source of truth for
 * which shell wraps a given path:
 *   - path matches an app  → AppFrame (immovable header, Home control, app nav)
 *   - "/"                  → the Home Screen itself (no shell)
 */
import type { LucideIcon } from "lucide-react";
import {
  Feather,
  GraduationCap,
  MessageSquare,
  Mic,
  Gauge,
  Mail,
  Library as LibraryIcon,
  NotebookPen,
} from "lucide-react";

export interface AppRoute {
  name: string;
  href: string;
}

export interface AppDef {
  id: string;
  /** Stencil label on the tile and in the frame header. */
  name: string;
  /** One-line role, shown under the tile name. */
  tagline: string;
  icon: LucideIcon;
  /** Route the tile opens. */
  entry: string;
  /** Pages that belong to this app — rendered as the frame's nav strip. */
  routes: AppRoute[];
  /** Path prefixes owned by this app (checked with startsWith on segments). */
  own: string[];
}

export const APPS: AppDef[] = [
  {
    id: "writing",
    name: "Writing",
    tagline: "Books, drafts & finishing",
    icon: Feather,
    entry: "/writing",
    routes: [
      // Hub supersedes the old /works list for browsing; /works stays owned
      // (and reachable from the hub) for the create/import dialogs.
      { name: "Works", href: "/writing" },
      { name: "Write desk", href: "/write" },
      { name: "Books", href: "/books" },
      { name: "Finishing", href: "/finishing" },
    ],
    own: ["/writing", "/write", "/works", "/books", "/finishing"],
  },
  {
    id: "learning",
    name: "Learning",
    tagline: "Study, mastery & topics",
    icon: GraduationCap,
    entry: "/learning",
    routes: [
      // Hub supersedes the old /learn landing page; /learn stays owned so
      // legacy deep links keep rendering inside the Learning frame.
      { name: "Study", href: "/learning" },
      { name: "Projects", href: "/projects" },
      { name: "Review", href: "/learning/review" },
      { name: "Topics", href: "/topics" },
    ],
    own: ["/learning", "/learn", "/projects", "/topics"],
  },
  {
    id: "chat",
    name: "Chat",
    tagline: "Ask the archive",
    icon: MessageSquare,
    entry: "/chat",
    routes: [{ name: "Chat", href: "/chat" }],
    own: ["/chat"],
  },
  {
    id: "studio",
    name: "Studio",
    tagline: "Audio, pressworks & graph",
    icon: Mic,
    entry: "/studio",
    routes: [
      { name: "Studio", href: "/studio" },
      { name: "Pressworks", href: "/forge" },
      { name: "Graph", href: "/graph" },
    ],
    own: ["/studio", "/forge", "/graph"],
  },
  {
    id: "command",
    name: "Command",
    tagline: "System, review & governance",
    icon: Gauge,
    entry: "/command",
    routes: [
      { name: "Command", href: "/command" },
      { name: "System", href: "/system" },
      { name: "Actions", href: "/actions" },
      { name: "Review", href: "/review" },
      { name: "Governance", href: "/governance" },
      { name: "Calibration", href: "/mcos" },
      { name: "Backups", href: "/backups" },
    ],
    own: ["/command", "/system", "/actions", "/review", "/governance", "/mcos", "/backups"],
  },
  {
    id: "mail",
    name: "Mail",
    tagline: "Triage & correspondence",
    icon: Mail,
    entry: "/mail",
    routes: [{ name: "Mail", href: "/mail" }],
    own: ["/mail"],
  },
  {
    id: "library",
    name: "Library",
    tagline: "Documents & intake",
    icon: LibraryIcon,
    entry: "/library",
    routes: [
      { name: "Library", href: "/library" },
      { name: "Intake", href: "/intake" },
    ],
    own: ["/library", "/intake"],
  },
  {
    id: "commonplace",
    name: "Commonplace",
    tagline: "Daily notes & capture",
    icon: NotebookPen,
    entry: "/notes",
    routes: [
      { name: "Notes", href: "/notes" },
    ],
    own: ["/notes"],
  },
];

/** Resolve which app owns a path, or null (unknown routes). */
export function getAppForPath(path: string): AppDef | null {
  const clean = path.split("?")[0];
  for (const app of APPS) {
    for (const prefix of app.own) {
      if (clean === prefix || clean.startsWith(prefix + "/")) return app;
    }
  }
  return null;
}
