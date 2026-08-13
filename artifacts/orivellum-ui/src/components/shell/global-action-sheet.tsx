/**
 * GlobalActionSheet — the "More" surface.
 *
 * Bottom sheet on phones, side sheet on wider screens (sheets replace side
 * panels on mobile). Lists every less-frequent destination grouped by job:
 * Learn & Review, Studio & Production, Mail, System & Backups,
 * Governance & Calibration. All rows are ≥48px and deep-link to real routes.
 */
import { Link } from "wouter";
import {
  Sheet,
  SheetContent,
  SheetHeader,
  SheetTitle,
  SheetDescription,
} from "@/components/ui/sheet";
import { useIsMobile } from "@/hooks/use-mobile";
import { MORE_GROUPS } from "@/lib/destinations";

export function GlobalActionSheet({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const isMobile = useIsMobile();
  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={isMobile ? "bottom" : "right"}
        className="p-4 overflow-y-auto"
        style={{
          background: "var(--gd-surface)",
          color: "var(--gd-text)",
          borderColor: "var(--gd-line)",
          maxHeight: isMobile ? "80dvh" : undefined,
          paddingBottom: "calc(var(--sai-bottom, 0px) + 16px)",
        }}
        aria-describedby={undefined}
      >
        <SheetHeader className="text-left">
          <SheetTitle
            className="gd-eyebrow"
            style={{ color: "var(--gd-dim)", fontSize: 11 }}
          >
            More
          </SheetTitle>
          <SheetDescription className="sr-only">
            All destinations grouped by job
          </SheetDescription>
        </SheetHeader>
        {MORE_GROUPS.map((group) => {
          const Icon = group.icon;
          return (
            <div key={group.name} className="shell-sheet-group">
              <p className="gd-eyebrow" style={{ padding: "0 12px 6px" }}>
                {group.name}
              </p>
              {group.items.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="shell-sheet-row"
                  onClick={() => onOpenChange(false)}
                  data-testid={`more-${item.href.slice(1).replace(/\//g, "-")}`}
                >
                  <Icon aria-hidden strokeWidth={1.75} />
                  <span>{item.name}</span>
                </Link>
              ))}
            </div>
          );
        })}
      </SheetContent>
    </Sheet>
  );
}
