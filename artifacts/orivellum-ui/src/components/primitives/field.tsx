import type { ReactNode } from "react";

/**
 * Field — label + control + help/error wrapper for every form input.
 * Errors are dual-coded: color plus explicit text tied to the control
 * via aria-describedby (callers pass `id` matching their control).
 */
export function Field({
  id,
  label,
  required = false,
  help,
  error,
  children,
}: {
  /** id of the wrapped control (used for label htmlFor + describedby). */
  id: string;
  label: string;
  required?: boolean;
  /** Muted guidance under the control. */
  help?: string;
  /** Validation message — presence switches the field to its error state. */
  error?: string;
  children: ReactNode;
}) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="block text-sm font-medium text-foreground">
        {label}
        {required && <span className="text-destructive ml-0.5" aria-hidden>*</span>}
      </label>
      {children}
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs font-medium text-destructive">
          {error}
        </p>
      ) : help ? (
        <p id={`${id}-help`} className="text-xs text-muted-foreground">
          {help}
        </p>
      ) : null}
    </div>
  );
}
