/**
 * Destination registry — the single responsive shell navigates these five
 * persistent destinations: Home, Chat, Works, Library, More.
 *
 * This replaces the old eight-app APPS launcher registry. The route → 
 * destination mapping here is the single source of truth for:
 *   - which tab/rail item lights up for a given path
 *   - which contextual tabs (ContextBar) a path shows
 *   - which legacy accent tint (<html data-app>) a path carries, so the
 *     GD token layer keeps working unchanged until WP2
 *
 * Every pre-existing route stays deep-linkable; nothing here removes a path.
 */
import type { LucideIcon } from "lucide-react";
import {
  Home as HomeIcon,
  MessageSquare,
  Feather,
  Library as LibraryIcon,
  LayoutGrid,
  GraduationCap,
  Mic,
  Gauge,
  Mail as MailIcon,
  Scale,
} from "lucide-react";

export interface DestTab {
  name: string;
  href: string;
}

export interface Destination {
  id: "home" | "chat" | "works" | "library";
  name: string;
  icon: LucideIcon;
  /** Route the tab/rail item opens. */
  entry: string;
  /** Path prefixes owned by this destination ("/" matches exactly). */
  own: string[];
  /** Contextual tabs rendered in the ContextBar while inside. */
  tabs?: DestTab[];
  /** Legacy <html data-app> accent id (token layer unchanged until WP2). */
  accentApp?: string;
}

/** The four routable destinations. "More" is the fifth — see MORE_GROUPS. */
export const DESTINATIONS: Destination[] = [
  {
    id: "home",
    name: "Home",
    icon: HomeIcon,
    entry: "/",
    own: ["/"],
  },
  {
    id: "chat",
    name: "Chat",
    icon: MessageSquare,
    entry: "/chat",
    own: ["/chat"],
    accentApp: "chat",
  },
  {
    id: "works",
    name: "Works",
    icon: Feather,
    entry: "/writing",
    own: [
      "/writing",
      "/write",
      "/works",
      "/projects",
      "/books",
      "/series",
      "/collections",
      "/canon",
      "/architect",
      "/finishing",
    ],
    tabs: [
      { name: "Works", href: "/writing" },
      { name: "Write desk", href: "/write" },
      { name: "Projects", href: "/projects" },
      { name: "Books", href: "/books" },
      { name: "Canon", href: "/canon" },
      { name: "Architect", href: "/architect" },
      { name: "Finishing", href: "/finishing" },
    ],
    accentApp: "writing",
  },
  {
    id: "library",
    name: "Library",
    icon: LibraryIcon,
    entry: "/library",
    own: ["/library", "/intake", "/notes", "/topics"],
    tabs: [
      { name: "Library", href: "/library" },
      { name: "Intake", href: "/intake" },
      { name: "Notes", href: "/notes" },
      { name: "Topics", href: "/topics" },
    ],
    accentApp: "library",
  },
];

export interface MoreGroup {
  name: string;
  icon: LucideIcon;
  /** Path prefixes owned by this group. */
  own: string[];
  /** Destinations listed in the More sheet (and as contextual tabs). */
  items: DestTab[];
  accentApp: string;
}

/** Everything behind the fifth tab — grouped by job, not implementation. */
export const MORE_GROUPS: MoreGroup[] = [
  {
    name: "Learn & Review",
    icon: GraduationCap,
    own: ["/learning", "/learn"],
    items: [
      { name: "Study", href: "/learning" },
      { name: "Knowledge review", href: "/learning/review" },
    ],
    accentApp: "learning",
  },
  {
    name: "Studio & Production",
    icon: Mic,
    own: ["/studio", "/forge", "/workbench", "/graph"],
    items: [
      { name: "Studio", href: "/studio" },
      { name: "Pressworks", href: "/forge" },
      { name: "Workbench", href: "/workbench" },
      { name: "Graph", href: "/graph" },
    ],
    accentApp: "studio",
  },
  {
    name: "Mail",
    icon: MailIcon,
    own: ["/mail"],
    items: [
      { name: "Mail", href: "/mail" },
      { name: "Mail settings", href: "/mail/settings" },
    ],
    accentApp: "mail",
  },
  {
    name: "System & Backups",
    icon: Gauge,
    own: ["/command", "/system", "/actions", "/operations", "/backups"],
    items: [
      { name: "Command", href: "/command" },
      { name: "System", href: "/system" },
      { name: "Actions", href: "/actions" },
      { name: "Operations", href: "/operations" },
      { name: "Backups", href: "/backups" },
    ],
    accentApp: "command",
  },
  {
    name: "Governance & Calibration",
    icon: Scale,
    own: ["/review", "/governance", "/mcos", "/assay"],
    items: [
      { name: "Review", href: "/review" },
      { name: "Governance", href: "/governance" },
      { name: "Calibration", href: "/mcos" },
      { name: "Certification", href: "/assay" },
    ],
    accentApp: "command",
  },
];

/** Icon shown for the More tab itself. */
export const MORE_ICON = LayoutGrid;

function matchOwn(path: string, prefix: string): boolean {
  if (prefix === "/") return path === "/";
  return path === prefix || path.startsWith(prefix + "/");
}

function clean(path: string): string {
  return path.split("?")[0];
}

/** Which of the five tab/rail items is active for a path. */
export function activeDestId(
  path: string,
): "home" | "chat" | "works" | "library" | "more" | null {
  const p = clean(path);
  for (const dest of DESTINATIONS) {
    if (dest.own.some((o) => matchOwn(p, o))) return dest.id;
  }
  for (const group of MORE_GROUPS) {
    if (group.own.some((o) => matchOwn(p, o))) return "more";
  }
  return null;
}

export function moreGroupForPath(path: string): MoreGroup | null {
  const p = clean(path);
  for (const group of MORE_GROUPS) {
    if (group.own.some((o) => matchOwn(p, o))) return group;
  }
  return null;
}

/** The heading the shell shows for a path (destination or More-group name). */
export function shellTitleForPath(path: string): string | null {
  const p = clean(path);
  for (const dest of DESTINATIONS) {
    if (dest.own.some((o) => matchOwn(p, o))) return dest.id === "home" ? null : dest.name;
  }
  return moreGroupForPath(p)?.name ?? null;
}

/**
 * Contextual tabs for a path: a destination's tabs (Works, Library) or the
 * containing More group's items. Null when the path has no sibling pages
 * (Home, Chat, unknown routes).
 */
export function contextTabsForPath(path: string): DestTab[] | null {
  const p = clean(path);
  for (const dest of DESTINATIONS) {
    if (dest.own.some((o) => matchOwn(p, o))) {
      return dest.tabs && dest.tabs.length > 1 ? dest.tabs : null;
    }
  }
  const group = moreGroupForPath(p);
  if (group && group.items.length > 1) return group.items;
  return null;
}

/**
 * Which tab in a ContextBar is active — the longest href that
 * prefix-matches the path (so "/learning/review" beats "/learning").
 */
export function activeTabHref(path: string, tabs: DestTab[]): string | null {
  const p = clean(path);
  let best: string | null = null;
  for (const t of tabs) {
    if (matchOwn(p, t.href) && (!best || t.href.length > best.length)) best = t.href;
  }
  // Special case: destination entry pages also own sibling paths (e.g.
  // "/writing" tab while on "/works/:id") — fall back to the entry tab.
  if (!best) {
    const dest = DESTINATIONS.find((d) => d.own.some((o) => matchOwn(p, o)));
    if (dest?.tabs?.some((t) => t.href === dest.entry)) return dest.entry;
  }
  return best;
}

/** Legacy accent tint id for <html data-app> (token layer, unchanged in WP1). */
export function accentAppForPath(path: string): string | null {
  const p = clean(path);
  for (const dest of DESTINATIONS) {
    if (dest.own.some((o) => matchOwn(p, o))) return dest.accentApp ?? null;
  }
  return moreGroupForPath(p)?.accentApp ?? null;
}
