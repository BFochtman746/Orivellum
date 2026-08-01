import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { AlertTriangle } from "lucide-react";

interface Props {
  children: ReactNode;
  /** Custom fallback — receives the error and a reset callback. */
  fallback?: (error: Error, reset: () => void) => ReactNode;
  /** Short label shown in the generic fallback (e.g. "chat panel"). */
  label?: string;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary]", error, info.componentStack);
  }

  reset = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    if (this.props.fallback) return this.props.fallback(error, this.reset);

    const label = this.props.label ?? "this section";
    return (
      <div className="flex flex-col items-center justify-center gap-4 p-8 text-center text-muted-foreground">
        <AlertTriangle className="w-8 h-8 text-amber-500" />
        <div className="space-y-1">
          <p className="font-medium text-foreground text-sm">Something went wrong in {label}</p>
          <p className="text-xs font-mono text-muted-foreground max-w-xs truncate">{error.message}</p>
        </div>
        <Button size="sm" variant="outline" onClick={this.reset}>Try again</Button>
      </div>
    );
  }
}

/** Route-level fallback — full-page error with reload option. */
export function RouteErrorFallback({ error, reset }: { error: Error; reset: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-4 text-center p-8">
      <AlertTriangle className="w-10 h-10 text-amber-500" />
      <div className="space-y-1">
        <h2 className="text-xl font-serif font-semibold">Something went wrong</h2>
        <p className="text-sm text-muted-foreground max-w-md">
          An unexpected error occurred on this page.
        </p>
        <p className="text-xs font-mono text-muted-foreground mt-2 max-w-md truncate">{error.message}</p>
      </div>
      <div className="flex gap-3">
        <Button variant="outline" onClick={reset}>Try again</Button>
        <Button variant="outline" onClick={() => window.location.reload()}>Reload page</Button>
      </div>
    </div>
  );
}
