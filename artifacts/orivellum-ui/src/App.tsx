import React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { AppLayout } from '@/components/layout';
import { ErrorBoundary, RouteErrorFallback } from '@/components/error-boundary';

import Dashboard from '@/pages/dashboard';
import WorksList from '@/pages/works/index';
import WorkDetail from '@/pages/works/detail';
import Chat from '@/pages/chat/index';
import Library from '@/pages/library/index';
import DocumentDetail from '@/pages/library/detail';
import Files from '@/pages/files/index';
import Projects from '@/pages/projects/index';
import ProjectDetail from '@/pages/projects/detail';
import Studio from '@/pages/studio/index';
import Backups from '@/pages/backups/index';
import System from '@/pages/system/index';
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
        <Route path="/chat">{() => <RouteWithBoundary component={Chat} />}</Route>
        <Route path="/library">{() => <RouteWithBoundary component={Library} />}</Route>
        <Route path="/library/:docId">{() => <RouteWithBoundary component={DocumentDetail} />}</Route>
        <Route path="/files">{() => <RouteWithBoundary component={Files} />}</Route>
        <Route path="/projects">{() => <RouteWithBoundary component={Projects} />}</Route>
        <Route path="/projects/:projectId">{() => <RouteWithBoundary component={ProjectDetail} />}</Route>
        <Route path="/studio">{() => <RouteWithBoundary component={Studio} />}</Route>
        <Route path="/backups">{() => <RouteWithBoundary component={Backups} />}</Route>
        <Route path="/system">{() => <RouteWithBoundary component={System} />}</Route>
        <Route component={NotFound} />
      </Switch>
    </AppLayout>
  );
}

function App() {
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
