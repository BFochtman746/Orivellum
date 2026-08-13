import { Skeleton } from "@/components/ui/skeleton";

/**
 * LoadingState — designed skeleton rows while content loads. Announces
 * politely to screen readers; shape approximates the content it replaces.
 */
export function LoadingState({
  rows = 3,
  label = "Loading",
}: {
  /** Number of skeleton rows to render. */
  rows?: number;
  /** Screen-reader announcement. */
  label?: string;
}) {
  return (
    <div role="status" aria-live="polite" aria-label={label} className="space-y-2">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex min-h-11 items-center gap-3 rounded-md border border-border bg-card px-3 py-2">
          <Skeleton className="h-8 w-8 rounded-md shrink-0" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-3.5 w-2/5" />
            <Skeleton className="h-3 w-3/5" />
          </div>
        </div>
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}
