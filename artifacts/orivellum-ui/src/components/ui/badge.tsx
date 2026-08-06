import * as React from 'react';
import { cn } from '@/lib/utils';
import { cva, type VariantProps } from 'class-variance-authority';

const badgeVariants = cva(
  // Base: Space Mono, uppercase, small
  'whitespace-nowrap inline-flex items-center rounded-md px-2.5 py-0.5 text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2',
  {
    variants: {
      variant: {
        default:
          'border border-transparent bg-[var(--green-raw)] text-[#F4EEE1] shadow-[var(--shadow-1)] font-medium dark:text-[#12100b]',
        secondary:
          'border border-transparent bg-secondary text-secondary-foreground font-medium',
        destructive:
          'border border-transparent bg-[var(--rust)] text-[#F4EEE1] shadow-[var(--shadow-1)]',
        outline:
          'text-foreground border border-[var(--line-2)]',
        // ── Tier badges (VELLUM document classification system) ──
        // .tier classes from CSS; these add the font
        canon:
          'tier tier-canon font-mono tracking-wider',
        source:
          'tier tier-source font-mono tracking-wider',
        artifact:
          'tier tier-artifact font-mono tracking-wider',
        conv:
          'tier tier-conv font-mono tracking-wider',
        claim:
          'tier tier-claim font-mono tracking-wider',
        // Gilt accent badge (for "now" / current markers)
        gilt:
          'border border-[var(--gilt-line)] bg-[var(--gilt-soft)] text-[var(--gilt)] font-mono text-[9.5px] tracking-widest uppercase',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return (
    <div className={cn(badgeVariants({ variant }), className)} {...props} />
  );
}

export { Badge, badgeVariants };
