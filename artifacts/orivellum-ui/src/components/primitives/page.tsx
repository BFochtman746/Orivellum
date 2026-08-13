import type { ReactNode } from "react";

/**
 * Page — outermost content wrapper for a screen rendered inside the shell.
 * Owns horizontal padding, the readable max width, and vertical rhythm so
 * individual screens never re-invent their own margins.
 */
export function Page({
  title,
  eyebrow,
  actions,
  children,
  wide = false,
}: {
  /** Optional page heading rendered in the editorial style. */
  title?: string;
  /** Optional mono eyebrow label above the title. */
  eyebrow?: string;
  /** Optional trailing header actions (buttons, menus). */
  actions?: ReactNode;
  children: ReactNode;
  /** Full-width screens (boards, tables) opt out of the reading measure. */
  wide?: boolean;
}) {
  return (
    <div className={`mx-auto w-full px-4 pb-8 space-y-5 ${wide ? "" : "max-w-3xl"}`}>
      {(title || actions) && (
        <header className="flex items-end justify-between gap-3 pt-4">
          <div className="min-w-0">
            {eyebrow && <span className="eyebrow">{eyebrow}</span>}
            {title && <h1 className="page-h1 truncate">{title}</h1>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      {children}
    </div>
  );
}
