/**
 * MobileTabBar — fixed, safe-area-aware bottom tab bar (320–767px).
 *
 * Five items: Home, Chat, Works, Library, More. Each target is ≥48px tall.
 * "More" opens the GlobalActionSheet instead of navigating. Hidden ≥768px
 * by CSS (the DesktopRail takes over).
 */
import { Link } from "wouter";
import { DESTINATIONS, MORE_ICON } from "@/lib/destinations";

export function MobileTabBar({
  active,
  onMore,
}: {
  active: string | null;
  onMore: () => void;
}) {
  const MoreIcon = MORE_ICON;
  return (
    <nav className="shell-tabbar" aria-label="Primary">
      {DESTINATIONS.map((dest) => {
        const Icon = dest.icon;
        const isActive = active === dest.id;
        return (
          <Link
            key={dest.id}
            href={dest.entry}
            className="shell-tab"
            data-active={isActive}
            aria-current={isActive ? "page" : undefined}
            data-testid={`tab-${dest.id}`}
          >
            <Icon aria-hidden strokeWidth={1.75} />
            <span>{dest.name}</span>
          </Link>
        );
      })}
      <button
        type="button"
        className="shell-tab"
        data-active={active === "more"}
        aria-haspopup="dialog"
        onClick={onMore}
        data-testid="tab-more"
      >
        <MoreIcon aria-hidden strokeWidth={1.75} />
        <span>More</span>
      </button>
    </nav>
  );
}
