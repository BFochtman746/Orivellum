import type { ReactNode } from "react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";

/**
 * ConfirmAction — the one sanctioned confirmation flow for destructive or
 * irreversible actions. States the consequence in plain words; the
 * destructive control is the screen's single danger element.
 */
export function ConfirmAction({
  trigger,
  title,
  consequence,
  confirmLabel = "Confirm",
  destructive = false,
  onConfirm,
  open,
  onOpenChange,
}: {
  /** The element that opens the dialog (rendered via asChild). */
  trigger?: ReactNode;
  title: string;
  /** Plain-words statement of what happens and whether it can be undone. */
  consequence: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  /** Controlled mode (omit trigger and drive open state yourself). */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}) {
  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      {trigger && <AlertDialogTrigger asChild>{trigger}</AlertDialogTrigger>}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{consequence}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel className="min-h-11">Cancel</AlertDialogCancel>
          <AlertDialogAction
            className={`min-h-11 ${
              destructive ? "bg-destructive text-destructive-foreground hover:bg-destructive/90" : ""
            }`}
            onClick={onConfirm}
          >
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
