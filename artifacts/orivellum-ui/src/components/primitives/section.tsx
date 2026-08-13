import type { ReactNode } from "react";

/**
 * Section — a titled group of content within a Page. The label is a mono
 * uppercase plate; sections are the unit of vertical rhythm.
 */
export function Section({
  label,
  hint,
  actions,
  children,
}: {
  label: string;
  /** Optional muted description under the label. */
  hint?: string;
  /** Optional trailing action (e.g. a "See all" link). */
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="space-y-2.5">
      <div className="flex items-baseline justify-between gap-3">
        <div className="min-w-0">
          <span className="section-label-mono !m-0">{label}</span>
          {hint && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
        </div>
        {actions && <div className="shrink-0">{actions}</div>}
      </div>
      {children}
    </section>
  );
}
