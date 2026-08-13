/**
 * ContextBar — contextual sibling-page tabs under the shell header.
 *
 * Replaces the old per-app .gd-chip route strips: every target is ≥44px
 * tall and the bar scrolls inside its own container, never forcing the
 * page sideways. Rendered only when the current path has sibling pages
 * (Works sections, Library sections, More groups).
 */
import { Link } from "wouter";
import type { DestTab } from "@/lib/destinations";

export function ContextBar({
  tabs,
  activeHref,
  label,
}: {
  tabs: DestTab[];
  activeHref: string | null;
  label: string;
}) {
  return (
    <nav className="shell-context-bar" aria-label={`${label} sections`}>
      {tabs.map((tab) => {
        const isActive = tab.href === activeHref;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            className="shell-context-tab"
            data-active={isActive}
            aria-current={isActive ? "page" : undefined}
            data-testid={`nav-${tab.href.slice(1).replace(/\//g, "-")}`}
          >
            {tab.name}
          </Link>
        );
      })}
    </nav>
  );
}
