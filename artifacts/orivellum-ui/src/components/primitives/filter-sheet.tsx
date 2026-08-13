import type { ReactNode } from "react";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetFooter,
} from "@/components/ui/sheet";
import { Button } from "@/components/ui/button";

/**
 * FilterSheet — bottom sheet for filter/sort controls on mobile. One Apply,
 * one Clear; the sheet never mutates results until Apply.
 */
export function FilterSheet({
  open,
  onOpenChange,
  title = "Filters",
  children,
  onApply,
  onClear,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  children: ReactNode;
  onApply: () => void;
  onClear?: () => void;
}) {
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="bottom" className="max-h-[85dvh] overflow-y-auto pb-[calc(var(--sai-bottom,0px)+1rem)]">
        <SheetHeader>
          <SheetTitle>{title}</SheetTitle>
        </SheetHeader>
        <div className="space-y-4 py-2">{children}</div>
        <SheetFooter className="flex-row gap-2">
          {onClear && (
            <Button variant="outline" className="flex-1 min-h-11" onClick={onClear}>
              Clear
            </Button>
          )}
          <Button
            className="flex-1 min-h-11"
            onClick={() => {
              onApply();
              onOpenChange(false);
            }}
          >
            Apply
          </Button>
        </SheetFooter>
      </SheetContent>
    </Sheet>
  );
}
