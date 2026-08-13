import * as React from 'react';
import { Slot } from '@radix-ui/react-slot';
import { cn } from '@/lib/utils';
import { cva, type VariantProps } from 'class-variance-authority';

const buttonVariants = cva(
  // Base: Bricolage Grotesque (via font-sans), spring-like active scale
  'inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[13px] text-sm font-medium transition-[transform,box-shadow,border-color,background-color] duration-150 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 [&_svg]:pointer-events-none [&_svg]:size-4 [&_svg]:shrink-0 active:scale-[0.97]',
  {
    variants: {
      variant: {
        // Forest green solid — semantic primary (tokens flip for dark)
        default:
          'bg-primary text-primary-foreground shadow-[var(--gd-shadow)] border border-primary hover:bg-primary/90',
        destructive:
          'bg-destructive text-destructive-foreground shadow-[var(--gd-shadow)] border border-destructive/60 hover:bg-destructive/90',
        // Ghost: transparent with bronze hairline border (VELLUM .btn.ghost)
        outline:
          'bg-transparent text-primary border border-[var(--gd-bronze-soft)] hover:bg-[var(--gd-bronze-soft)] hover:border-[var(--gd-bronze)]',
        secondary:
          'bg-[var(--gd-bronze-soft)] text-foreground border border-[var(--gd-bronze-soft)] hover:bg-[var(--gd-bronze-soft)]',
        ghost:
          'border border-transparent hover:border-border hover:bg-accent',
        link: 'text-primary underline-offset-4 hover:underline',
      },
      size: {
        default: 'min-h-10 px-4 py-2.5',
        sm: 'min-h-8 rounded-[10px] px-3 text-xs',
        lg: 'min-h-11 rounded-[14px] px-8 text-[15px]',
        icon: 'h-9 w-9',
      },
    },
    defaultVariants: {
      variant: 'default',
      size: 'default',
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : 'button';
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  },
);
Button.displayName = 'Button';

export { Button, buttonVariants };
