import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";
import { Button } from "@/components/ui/button";

/**
 * ErrorState — explicit failure view: what failed, in plain words, plus a
 * retry. Failures are never silent and never styled as empty states.
 */
export function ErrorState({
  title = "Something went wrong",
  detail,
  onRetry,
  action,
}: {
  title?: string;
  /** Human-readable failure detail (not a stack trace). */
  detail?: string;
  onRetry?: () => void;
  /** Alternative custom action instead of the default Retry button. */
  action?: ReactNode;
}) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center gap-2 rounded-lg border px-6 py-10 text-center"
      style={{ borderColor: "var(--gd-danger)", background: "var(--gd-danger-soft)" }}
    >
      <AlertTriangle className="w-8 h-8" style={{ color: "var(--gd-danger)" }} aria-hidden />
      <p className="text-sm font-medium text-foreground">{title}</p>
      {detail && <p className="max-w-sm text-xs text-muted-foreground">{detail}</p>}
      <div className="mt-2 [&>*]:min-h-11">
        {action ?? (onRetry && (
          <Button variant="outline" onClick={onRetry}>
            Try again
          </Button>
        ))}
      </div>
    </div>
  );
}
