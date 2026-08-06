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
        // Forest green solid (VELLUM .btn)
        default:
          'bg-[var(--green-raw)] text-[#F4EEE1] shadow-[var(--shadow-1)] border border-[var(--green-2)] dark:text-[#12100b] hover:bg-[var(--green-2)] hover:shadow-[var(--shadow-2)]',
        destructive:
          'bg-[var(--rust)] text-[#F4EEE1] shadow-[var(--shadow-1)] border border-[rgba(178,67,30,0.6)] hover:bg-[#a33a18]',
        // Ghost: transparent with gilt hairline border (VELLUM .btn.ghost)
        outline:
          'bg-transparent text-[var(--green-raw)] border border-[var(--gilt-line)] hover:bg-[var(--gilt-soft)] hover:border-[var(--gilt)] dark:text-[var(--green-2)]',
        secondary:
          'bg-[var(--gilt-soft)] text-[var(--ink-raw)] border border-[var(--gilt-line)] hover:bg-[var(--gilt-soft)] dark:text-[var(--ink-soft)]',
        ghost:
          'border border-transparent hover:border-[var(--line-2)] hover:bg-[var(--green-soft)]',
        link: 'text-[var(--green-raw)] underline-offset-4 hover:underline dark:text-[var(--green-2)]',
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
