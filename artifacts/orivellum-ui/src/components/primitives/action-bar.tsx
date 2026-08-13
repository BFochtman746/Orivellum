import type { ReactNode } from "react";

/**
 * ActionBar — sticky bottom bar for a screen's primary actions. Sits above
 * the mobile tab bar (--shell-tabbar-h) and the home indicator, on the
 * shell's near-opaque glass so content scrolls beneath it.
 */
export function ActionBar({ children }: { children: ReactNode }) {
  return (
    <div
      className="sticky z-10 -mx-4 mt-4 border-t border-border px-4 py-3"
      style={{
        bottom: "calc(var(--shell-tabbar-h, 0px) + var(--sai-bottom, 0px))",
        background: "var(--gd-glass)",
      }}
    >
      <div className="mx-auto flex max-w-3xl items-center justify-end gap-2 [&>*]:min-h-11">
        {children}
      </div>
    </div>
  );
}
