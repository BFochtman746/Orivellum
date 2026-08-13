import type { ReactNode } from "react";

/**
 * Panel — a raised, bordered surface for grouped content. The only sanctioned
 * card wrapper for migrated screens (replaces ad-hoc vellum-card/gd-panel
 * variants). Interactive panels get press affordance and a ≥44px target.
 */
export function Panel({
  children,
  onClick,
  className = "",
  padded = true,
}: {
  children: ReactNode;
  /** Makes the whole panel a button with press affordance. */
  onClick?: () => void;
  className?: string;
  padded?: boolean;
}) {
  const base = `rounded-lg border border-card-border bg-card text-card-foreground ${
    padded ? "p-4" : ""
  } ${className}`;
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        className={`${base} block w-full min-h-11 text-left touch-manipulation transition-transform active:scale-[0.99]`}
      >
        {children}
      </button>
    );
  }
  return <div className={base}>{children}</div>;
}
