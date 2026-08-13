import type { ReactNode } from "react";

/**
 * EmptyState — a designed first-run/empty view: icon, one-line explanation,
 * and the single next action. Never a bare "No items".
 */
export function EmptyState({
  icon,
  title,
  description,
  action,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  /** The one thing the user can do from here (a Button, usually). */
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border px-6 py-10 text-center">
      {icon && <div className="text-muted-foreground [&>svg]:w-8 [&>svg]:h-8">{icon}</div>}
      <p className="text-sm font-medium text-foreground">{title}</p>
      {description && <p className="max-w-sm text-xs text-muted-foreground">{description}</p>}
      {action && <div className="mt-2 [&>*]:min-h-11">{action}</div>}
    </div>
  );
}
