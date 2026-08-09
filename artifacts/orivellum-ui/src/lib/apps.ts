/**
 * App registry — the GD-industrial Home Screen launches these apps.
 *
 * Each app is a full-screen workspace with its own navigation (rendered by
 * AppFrame). The route → app mapping here is the single source of truth for
 * which shell wraps a given path:
 *   - path matches an app  → AppFrame (immovable header, Home control, app nav)
 *   - legacy flag set      → old sidebar AppLayout (transition escape hatch)
 *   - "/"                  → the Home Screen itself (no shell)
 *
 * The follow-on tasks migrate pages INTO these apps; until then the app's
 * `routes` list simply points at the existing pages that belong to it.
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
    entry: "/learn",
    routes: [
      { name: "Learn", href: "/learn" },
      { name: "Projects", href: "/projects" },
      { name: "Topics", href: "/topics" },
    ],
    own: ["/learn", "/projects", "/topics"],
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
    tagline: "Audio, forge & graph",
    icon: Mic,
    entry: "/studio",
    routes: [
      { name: "Studio", href: "/studio" },
      { name: "Forge", href: "/forge" },
      { name: "Graph", href: "/graph" },
    ],
    own: ["/studio", "/forge", "/graph"],
  },
  {
    id: "command",
    name: "Command",
    tagline: "System, review & governance",
    icon: Gauge,
    entry: "/system",
    routes: [
      { name: "System", href: "/system" },
      { name: "Actions", href: "/actions" },
      { name: "Review", href: "/review" },
      { name: "Governance", href: "/governance" },
      { name: "Calibration", href: "/mcos" },
      { name: "Backups", href: "/backups" },
    ],
    own: ["/system", "/actions", "/review", "/governance", "/mcos", "/backups"],
  },
  {
    id: "mail",
    name: "Mail",
    tagline: "Triage & steward",
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
];

/** Resolve which app owns a path, or null (legacy / unknown routes). */
export function getAppForPath(path: string): AppDef | null {
  const clean = path.split("?")[0];
  for (const app of APPS) {
    for (const prefix of app.own) {
      if (clean === prefix || clean.startsWith(prefix + "/")) return app;
    }
  }
  return null;
}

/* ── Legacy shell escape hatch (transition only) ──────────────────────────
   Tapping the "Legacy console" entry on the Home Screen sets this flag so
   every route renders in the old sidebar shell; tapping any app tile (or
   the Home control) clears it.                                            */
const LEGACY_KEY = "orivellum-legacy-shell";

export function isLegacyShell(): boolean {
  try {
    return localStorage.getItem(LEGACY_KEY) === "1";
  } catch {
    return false;
  }
}

export function setLegacyShell(on: boolean): void {
  try {
    if (on) localStorage.setItem(LEGACY_KEY, "1");
    else localStorage.removeItem(LEGACY_KEY);
  } catch {
    /* private mode — non-fatal */
  }
}
