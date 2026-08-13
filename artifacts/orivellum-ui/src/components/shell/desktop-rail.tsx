/**
 * DesktopRail — the left navigation rail.
 *
 * 768–1199px: compact (icon + tiny label). 1200px+: labeled rows. Hidden
 * below 768px by CSS (the MobileTabBar takes over). Same five items as the
 * tab bar so navigation never moves between breakpoints.
 */
import { Link } from "wouter";
import { DESTINATIONS, MORE_ICON } from "@/lib/destinations";

export function DesktopRail({
  active,
  onMore,
}: {
  active: string | null;
  onMore: () => void;
}) {
  const MoreIcon = MORE_ICON;
  return (
    <nav className="shell-rail" aria-label="Primary">
      <Link href="/" className="shell-rail-brand" aria-label="Orivellum home">
        O<em>V</em>
      </Link>
      {DESTINATIONS.map((dest) => {
        const Icon = dest.icon;
        const isActive = active === dest.id;
        return (
          <Link
            key={dest.id}
            href={dest.entry}
            className="shell-rail-item"
            data-active={isActive}
            aria-current={isActive ? "page" : undefined}
            data-testid={`rail-${dest.id}`}
          >
            <Icon aria-hidden strokeWidth={1.75} />
            <span>{dest.name}</span>
          </Link>
        );
      })}
      <button
        type="button"
        className="shell-rail-item"
        data-active={active === "more"}
        aria-haspopup="dialog"
        onClick={onMore}
        data-testid="rail-more"
      >
        <MoreIcon aria-hidden strokeWidth={1.75} />
        <span>More</span>
      </button>
    </nav>
  );
}
