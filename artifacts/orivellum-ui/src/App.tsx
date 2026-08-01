import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster } from '@/components/ui/toaster';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Route, Switch, Router as WouterRouter } from 'wouter';
import { AppLayout } from '@/components/layout';

import Dashboard from '@/pages/dashboard';
import WorksList from '@/pages/works/index';
import WorkDetail from '@/pages/works/detail';
import Chat from '@/pages/chat/index';
import Library from '@/pages/library/index';
import DocumentDetail from '@/pages/library/detail';
import Files from '@/pages/files/index';
import Projects from '@/pages/projects/index';
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

function Router() {
  return (
    <AppLayout>
      <Switch>
        <Route path="/" component={Dashboard} />
        <Route path="/works" component={WorksList} />
        <Route path="/works/:workId" component={WorkDetail} />
        <Route path="/chat" component={Chat} />
        <Route path="/library" component={Library} />
        <Route path="/library/:docId" component={DocumentDetail} />
        <Route path="/files" component={Files} />
        <Route path="/projects" component={Projects} />
        <Route path="/studio" component={Studio} />
        <Route path="/backups" component={Backups} />
        <Route path="/system" component={System} />
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
