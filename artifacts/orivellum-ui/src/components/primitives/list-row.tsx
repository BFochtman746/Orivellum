import type { ReactNode } from "react";
import { ChevronRight } from "lucide-react";

/**
 * ListRow — the one way to render a tappable row: leading icon, title,
 * optional subtitle, trailing meta. Always ≥44px tall; navigation rows get
 * a chevron so affordance never relies on color.
 */
export function ListRow({
  icon,
  title,
  subtitle,
  trailing,
  onClick,
  chevron = false,
  disabled = false,
}: {
  icon?: ReactNode;
  title: ReactNode;
  subtitle?: ReactNode;
  trailing?: ReactNode;
  onClick?: () => void;
  /** Show a navigation chevron (rows that push a screen). */
  chevron?: boolean;
  disabled?: boolean;
}) {
  const inner = (
    <>
      {icon && <span className="shrink-0 text-muted-foreground">{icon}</span>}
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium text-foreground">{title}</span>
        {subtitle && (
          <span className="block truncate text-xs text-muted-foreground">{subtitle}</span>
        )}
      </span>
      {trailing && <span className="shrink-0 text-xs text-muted-foreground">{trailing}</span>}
      {chevron && <ChevronRight className="w-4 h-4 shrink-0 text-muted-foreground" aria-hidden />}
    </>
  );
  if (onClick) {
    return (
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        className="flex w-full min-h-11 items-center gap-3 rounded-md border border-border bg-card px-3 py-2 text-left touch-manipulation transition-colors hover:bg-accent active:bg-accent disabled:opacity-50 disabled:pointer-events-none"
      >
        {inner}
      </button>
    );
  }
  return (
    <div className="flex w-full min-h-11 items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
      {inner}
    </div>
  );
}
