import React, { useEffect, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { AppLayout } from '@/components/layout';
import { ErrorBoundary, RouteErrorFallback } from '@/components/error-boundary';
import { checkAuth, login } from '@/lib/auth';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import Dashboard from '@/pages/dashboard';
import WorksList from '@/pages/works/index';
import WorkDetail from '@/pages/works/detail';
import WorkIntelligence from '@/pages/works/intelligence';
import { useParams, useLocation } from 'wouter';
import Chat from '@/pages/chat/index';
import Library from '@/pages/library/index';
import DocumentDetail from '@/pages/library/detail';
import Files from '@/pages/files/index';
import Projects from '@/pages/projects/index';
import ProjectDetail from '@/pages/projects/detail';
import Studio from '@/pages/studio/index';
import WriteDeskPage from '@/pages/write/index';
import Backups from '@/pages/backups/index';
import System from '@/pages/system/index';
import GovernancePage from '@/pages/governance/index';
import ReviewPage from '@/pages/review/index';
import Mcos from '@/pages/mcos/index';
import NotFound from '@/pages/not-found';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});


function RouteWithBoundary({ component: Page }: { component: React.ComponentType }) {
  return (
    <ErrorBoundary
      fallback={(err, reset) => <RouteErrorFallback error={err} reset={reset} />}
    >
      <Page />
    </ErrorBoundary>
  );
}

function Router() {
  return (
    <AppLayout>
      <Switch>
        <Route path="/">{() => <RouteWithBoundary component={Dashboard} />}</Route>
        <Route path="/works">{() => <RouteWithBoundary component={WorksList} />}</Route>
        <Route path="/works/:workId">{() => <RouteWithBoundary component={WorkDetail} />}</Route>
        <Route path="/works/:workId/intelligence">{() => <RouteWithBoundary component={WorkIntelligence} />}</Route>
        <Route path="/chat">{() => <RouteWithBoundary component={Chat} />}</Route>
        <Route path="/library">{() => <RouteWithBoundary component={Library} />}</Route>
        <Route path="/library/:docId">{() => <RouteWithBoundary component={DocumentDetail} />}</Route>
        <Route path="/files">{() => <RouteWithBoundary component={Files} />}</Route>
        <Route path="/projects">{() => <RouteWithBoundary component={Projects} />}</Route>
        <Route path="/projects/:projectId">{() => <RouteWithBoundary component={ProjectDetail} />}</Route>
        <Route path="/studio">{() => <RouteWithBoundary component={Studio} />}</Route>
        <Route path="/write">{() => <RouteWithBoundary component={WriteDeskPage} />}</Route>
        <Route path="/backups">{() => <RouteWithBoundary component={Backups} />}</Route>
        <Route path="/system">{() => <RouteWithBoundary component={System} />}</Route>
        <Route path="/mcos">{() => <RouteWithBoundary component={Mcos} />}</Route>
        <Route path="/governance">{() => <RouteWithBoundary component={GovernancePage} />}</Route>
        <Route path="/review">{() => <RouteWithBoundary component={ReviewPage} />}</Route>
        <Route component={NotFound} />
      </Switch>
    </AppLayout>
  );
}

// ── Login form ────────────────────────────────────────────────────────────────

function LoginForm({ onSuccess }: { onSuccess: () => void }) {
  const [key, setKey] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);
    const ok = await login(key.trim());
    setLoading(false);
    if (ok) {
      onSuccess();
    } else {
      setError('Incorrect key. Check your startup logs or data/api_key.txt.');
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-background">
      <div className="w-full max-w-sm space-y-6 p-8">
        <div className="space-y-1 text-center">
          <div className="flex items-center justify-center gap-2 mb-4">
            <span className="text-2xl font-bold tracking-tight">Orivellum</span>
          </div>
          <p className="text-sm text-muted-foreground">
            Enter your API key to continue
          </p>
        </div>

        <form onSubmit={handleSubmit} className="space-y-4">
          <Input
            type="password"
            placeholder="API key"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            autoFocus
            disabled={loading}
          />
          {error && (
            <p className="text-sm text-destructive">{error}</p>
          )}
          <Button type="submit" className="w-full" disabled={loading || !key.trim()}>
            {loading ? 'Checking…' : 'Continue'}
          </Button>
        </form>

        <p className="text-xs text-muted-foreground text-center">
          Find your key in the API server startup logs or{' '}
          <code className="font-mono">data/api_key.txt</code>
        </p>
      </div>
    </div>
  );
}

// ── App root ──────────────────────────────────────────────────────────────────

type AuthState = 'checking' | 'authenticated' | 'unauthenticated';

function App() {
  const [authState, setAuthState] = useState<AuthState>('checking');

  useEffect(() => {
    checkAuth().then((ok) =>
      setAuthState(ok ? 'authenticated' : 'unauthenticated'),
    );
  }, []);

  if (authState === 'checking') {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <span className="text-sm text-muted-foreground">Connecting…</span>
      </div>
    );
  }

  if (authState === 'unauthenticated') {
    return (
      <LoginForm onSuccess={() => setAuthState('authenticated')} />
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <TooltipProvider>
        <WouterRouter base={import.meta.env.BASE_URL.replace(/\/$/, '')}>
          <Router />
        </WouterRouter>
        <Toaster />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
