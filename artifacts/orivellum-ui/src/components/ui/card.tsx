import * as React from 'react';
import { cn } from '@/lib/utils';

const Card = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement> & { tap?: boolean }
>(({ className, tap, ...props }, ref) => (
  <div
    ref={ref}
    className={cn(
      // VELLUM card: warm card bg, warm hairline border, 16px radius, warm shadow
      'rounded-[16px] border bg-card text-card-foreground',
      tap && 'cursor-pointer transition-[box-shadow,border-color,transform] duration-[180ms] ease-out',
      tap && 'hover:border-[var(--gilt-line)] hover:shadow-[var(--shadow-2)] hover:-translate-y-px',
      tap && 'active:scale-[0.99] active:shadow-[var(--shadow-1)]',
      className,
    )}
    style={{ boxShadow: 'var(--shadow-1)', borderColor: 'var(--line)', ...props.style }}
    {...props}
  />
));
Card.displayName = 'Card';

const CardHeader = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex flex-col space-y-1 p-5', className)}
    {...props}
  />
));
CardHeader.displayName = 'CardHeader';

const CardTitle = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    // VELLUM: card titles use Fraunces at opsz 40
    className={cn(
      'font-serif leading-snug tracking-tight text-[18px]',
      className,
    )}
    style={{ fontVariationSettings: '"opsz" 40', ...props.style }}
    {...props}
  />
));
CardTitle.displayName = 'CardTitle';

const CardDescription = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('text-[12.5px] text-muted-foreground leading-relaxed', className)}
    {...props}
  />
));
CardDescription.displayName = 'CardDescription';

const CardContent = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div ref={ref} className={cn('p-5 pt-0', className)} {...props} />
));
CardContent.displayName = 'CardContent';

const CardFooter = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <div
    ref={ref}
    className={cn('flex items-center p-5 pt-0', className)}
    {...props}
  />
));
CardFooter.displayName = 'CardFooter';

export {
  Card,
  CardHeader,
  CardFooter,
  CardTitle,
  CardDescription,
  CardContent,
};
