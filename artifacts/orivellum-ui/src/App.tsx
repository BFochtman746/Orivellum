import React, { useEffect, useState } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { Toaster as SonnerToaster } from 'sonner';
import { TooltipProvider } from '@/components/ui/tooltip';
import { Route, Switch, Router as WouterRouter, Redirect } from 'wouter';
import { CommandPalette } from '@/components/command-palette';
import { ErrorBoundary, RouteErrorFallback } from '@/components/error-boundary';
import { checkAuth, login } from '@/lib/auth';
import { useBrowserNotifications } from '@/hooks/use-browser-notifications';
import { useOutboxSync } from '@/hooks/use-outbox';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';

import WorksList from '@/pages/works/index';
import WorkDetail from '@/pages/works/detail';
import WorkIntelligence from '@/pages/works/intelligence';
import GapOraclePage from '@/pages/works/gap-oracle';
import ContinuityPage from '@/pages/works/continuity';
import HandoffPage from '@/pages/works/handoff';
import PacingPage from '@/pages/works/pacing';
import SeriesList from '@/pages/series/index';
import SeriesDetail from '@/pages/series/detail';
import CollectionsPage from '@/pages/collections/index';
import CollectionDetail from '@/pages/collections/detail';
import { useParams, useLocation } from 'wouter';
import Chat from '@/pages/chat/index';
import Library from '@/pages/library/index';
import NotesPage from '@/pages/notes/index';
import DocumentDetail from '@/pages/library/detail';
import Projects from '@/pages/projects/index';
import ProjectDetail from '@/pages/projects/detail';
import Studio from '@/pages/studio/index';
import WriteDeskPage from '@/pages/write/index';
import WritingHub from '@/pages/writing/index';
import LearningHub from '@/pages/learning/index';
import LearningSession from '@/pages/learning/session';
import KnowledgeReview from '@/pages/learning/review';
import Backups from '@/pages/backups/index';
import System from '@/pages/system/index';
import CommandHub from '@/pages/command/index';
import GovernancePage from '@/pages/governance/index';
import ReviewPage from '@/pages/review/index';
import Mcos from '@/pages/mcos/index';
import AssayPromotion from '@/pages/assay/index';
import BooksPage from '@/pages/books/index';
import CanonPage from '@/pages/canon/index';
import WritingArchitectPage from '@/pages/writing-architect/index';
import FinishingPage from '@/pages/finishing/index';
import LearnPage from '@/pages/learn/index';
import IntakePage from '@/pages/intake/index';
import TopicsPage from '@/pages/topics/index';
import ActionsPage from '@/pages/actions/index';
import OperationsPage from '@/pages/operations/index';
import GraphPage from '@/pages/graph/index';
import ForgePage from '@/pages/forge/index';
import ForgeDetail from '@/pages/forge/detail';
import WorkbenchPage from '@/pages/workbench/index';
import WorkbenchDetail from '@/pages/workbench/detail';
import MailPage from '@/pages/mail/index';
import MailConnectPage from '@/pages/mail/connect';
import ComposePage from '@/pages/mail/compose';
import MailSettingsPage from '@/pages/mail/settings';
import NotFound from '@/pages/not-found';
import HomeScreen from '@/pages/home/index';
import { ResponsiveShell } from '@/components/shell/responsive-shell';
import { ReadAloudProvider } from '@/lib/read-aloud';
import { ReadAloudDock } from '@/components/read-aloud-dock';
import { toast } from 'sonner';

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      refetchOnWindowFocus: false,
      // Global staleTime: data is fresh for 30 s, so navigating between pages
      // never shows a loading spinner for data fetched in the last half-minute.
      // Real-time queries (progress panel, connectivity, chat polling) override
      // this with their own shorter staleTime / refetchInterval values.
      staleTime: 30_000,
      // Keep inactive query cache for 5 minutes before garbage-collecting it.
      // This lets the user navigate back to a page without a full refetch for
      // the first 5 minutes, even after the 30 s staleTime window has passed
      // (the data is served from cache while a background refetch runs).
      gcTime: 300_000,
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

/** Mounted once (authenticated tree only): polls the server's notification
 * feed and shows browser alerts / toasts for finished documents & audiobooks. */
function BrowserNotificationsWatcher() {
  useBrowserNotifications();
  return null;
}

/** Mounted once (authenticated tree only): flushes the persistent outbox
 * (queued chat messages / drafts / approvals) on reconnect, foreground and a
 * slow interval — the client half of the iPhone continuity core. */
function OutboxSyncWatcher() {
  useOutboxSync();
  return null;
}

function Router() {
  // Every route — including Home and unknown paths — renders inside the one
  // ResponsiveShell (WP1 gate: no page lives outside the shell).
  return (
    <ResponsiveShell>
      <RoutedPages />
    </ResponsiveShell>
  );
}

function RoutedPages() {
  return (
      <Switch>
        <Route path="/">{() => <RouteWithBoundary component={HomeScreen} />}</Route>
        <Route path="/works">{() => <RouteWithBoundary component={WorksList} />}</Route>
        <Route path="/works/:workId">{() => <RouteWithBoundary component={WorkDetail} />}</Route>
        <Route path="/works/:workId/intelligence">{() => <RouteWithBoundary component={WorkIntelligence} />}</Route>
        <Route path="/works/:workId/gap-oracle">{() => <RouteWithBoundary component={GapOraclePage} />}</Route>
        <Route path="/works/:workId/continuity">{() => <RouteWithBoundary component={ContinuityPage} />}</Route>
        <Route path="/works/:workId/handoff">{() => <RouteWithBoundary component={HandoffPage} />}</Route>
        <Route path="/works/:workId/pacing">{() => <RouteWithBoundary component={PacingPage} />}</Route>
        <Route path="/series">{() => <RouteWithBoundary component={SeriesList} />}</Route>
        <Route path="/series/:seriesId">{() => <RouteWithBoundary component={SeriesDetail} />}</Route>
        <Route path="/collections">{() => <RouteWithBoundary component={CollectionsPage} />}</Route>
        <Route path="/collections/:collectionId">{() => <RouteWithBoundary component={CollectionDetail} />}</Route>
        <Route path="/chat">{() => <RouteWithBoundary component={Chat} />}</Route>
        <Route path="/notes">{() => <RouteWithBoundary component={NotesPage} />}</Route>
        <Route path="/library">{() => <RouteWithBoundary component={Library} />}</Route>
        <Route path="/library/:docId">{() => <RouteWithBoundary component={DocumentDetail} />}</Route>
        <Route path="/files">{() => <Redirect to="/library" />}</Route>
        <Route path="/projects">{() => <RouteWithBoundary component={Projects} />}</Route>
        <Route path="/projects/:projectId">{() => <RouteWithBoundary component={ProjectDetail} />}</Route>
        <Route path="/studio">{() => <RouteWithBoundary component={Studio} />}</Route>
        <Route path="/write">{() => <RouteWithBoundary component={WriteDeskPage} />}</Route>
        <Route path="/writing">{() => <RouteWithBoundary component={WritingHub} />}</Route>
        <Route path="/learning/session/:workId">{() => <RouteWithBoundary component={LearningSession} />}</Route>
        <Route path="/learning/review">{() => <RouteWithBoundary component={KnowledgeReview} />}</Route>
        <Route path="/learning">{() => <RouteWithBoundary component={LearningHub} />}</Route>
        <Route path="/backups">{() => <RouteWithBoundary component={Backups} />}</Route>
        <Route path="/command">{() => <RouteWithBoundary component={CommandHub} />}</Route>
        <Route path="/system">{() => <RouteWithBoundary component={System} />}</Route>
        <Route path="/mcos">{() => <RouteWithBoundary component={Mcos} />}</Route>
        <Route path="/assay">{() => <RouteWithBoundary component={AssayPromotion} />}</Route>
        <Route path="/governance">{() => <RouteWithBoundary component={GovernancePage} />}</Route>
        <Route path="/review">{() => <RouteWithBoundary component={ReviewPage} />}</Route>
        <Route path="/books">{() => <RouteWithBoundary component={BooksPage} />}</Route>
        <Route path="/canon">{() => <RouteWithBoundary component={CanonPage} />}</Route>
        <Route path="/architect">{() => <RouteWithBoundary component={WritingArchitectPage} />}</Route>
        <Route path="/finishing">{() => <RouteWithBoundary component={FinishingPage} />}</Route>
        <Route path="/learn">{() => <RouteWithBoundary component={LearnPage} />}</Route>
        <Route path="/intake">{() => <RouteWithBoundary component={IntakePage} />}</Route>
        <Route path="/topics">{() => <RouteWithBoundary component={TopicsPage} />}</Route>
        <Route path="/actions">{() => <RouteWithBoundary component={ActionsPage} />}</Route>
        <Route path="/operations">{() => <RouteWithBoundary component={OperationsPage} />}</Route>
        <Route path="/graph">{() => <RouteWithBoundary component={GraphPage} />}</Route>
        <Route path="/forge">{() => <RouteWithBoundary component={ForgePage} />}</Route>
        <Route path="/forge/:projectId">{() => <RouteWithBoundary component={ForgeDetail} />}</Route>
        <Route path="/workbench">{() => <RouteWithBoundary component={WorkbenchPage} />}</Route>
        <Route path="/workbench/:projectId">{() => <RouteWithBoundary component={WorkbenchDetail} />}</Route>
        <Route path="/mail">{() => <RouteWithBoundary component={MailPage} />}</Route>
        <Route path="/mail/connect">{() => <RouteWithBoundary component={MailConnectPage} />}</Route>
        <Route path="/mail/compose/:actionRequestId">{() => <RouteWithBoundary component={ComposePage} />}</Route>
        <Route path="/mail/settings">{() => <RouteWithBoundary component={MailSettingsPage} />}</Route>
        <Route component={NotFound} />
      </Switch>
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
    <div className="min-h-screen flex items-center justify-center bg-background relative overflow-hidden">
      {/* Radial gradient ground — token-driven paper wash */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ background: 'radial-gradient(130% 100% at 50% 0%, var(--gd-canvas) 0%, var(--gd-surface) 55%, var(--gd-surface-raised) 100%)' }}
        aria-hidden
      />

      <div className="w-full max-w-[360px] space-y-8 px-6 py-10 relative z-10">
        {/* Brand */}
        <div className="text-center space-y-3">
          <div className="flex items-center justify-center gap-2.5 mb-1">
            <div className="w-9 h-9 rounded-[10px] flex items-center justify-center font-serif font-bold text-lg bg-primary text-primary-foreground">
              <span style={{ fontVariationSettings: '"opsz" 40' }}>O</span>
            </div>
            <span className="brand-orivellum text-[22px]">
              Ori<span className="brand-accent">vellum</span>
            </span>
          </div>
          {/* Gilt rule */}
          <div className="gilt-rule mx-auto max-w-[180px]" />
          <p className="eyebrow" style={{ color: 'var(--gd-dim)' }}>
            Sovereign knowledge
          </p>
        </div>

        {/* Card */}
        <div className="rounded-[18px] p-7 space-y-5 relative overflow-hidden bg-card border border-card-border" style={{ boxShadow: 'var(--gd-shadow)' }}>
          {/* Lens flare */}
          <div
            className="absolute top-0 left-0 right-0 h-px pointer-events-none"
            style={{ background: 'linear-gradient(90deg, transparent, var(--gd-bronze-soft) 40%, var(--gd-bronze-soft) 60%, transparent)' }}
          />
          <div className="space-y-1">
            <p className="text-[15px] font-medium text-foreground">
              Enter your access key
            </p>
            <p className="text-[12.5px] text-muted-foreground">
              Nothing leaves your machine.
            </p>
          </div>
          <form onSubmit={handleSubmit} className="space-y-3.5">
            <Input
              type="password"
              placeholder="API key"
              value={key}
              onChange={(e) => setKey(e.target.value)}
              autoFocus
              disabled={loading}
              className="rounded-[12px] border-border bg-muted/30 placeholder:text-muted-foreground/60"
            />
            {error && (
              <p className="text-[12.5px]" style={{ color: 'var(--gd-danger)' }}>{error}</p>
            )}
            <Button type="submit" className="w-full rounded-[12px] min-h-11" disabled={loading || !key.trim()}>
              {loading ? 'Checking…' : 'Continue →'}
            </Button>
          </form>
        </div>

        <p className="text-[11px] text-center font-mono text-muted-foreground" style={{ letterSpacing: '0.04em' }}>
          key in startup logs · <code>data/api_key.txt</code>
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
          <ReadAloudProvider onFail={(msg) => toast.error(msg, { duration: 8000 })}>
            <Router />
            <ReadAloudDock />
          </ReadAloudProvider>
          <CommandPalette />
          <BrowserNotificationsWatcher />
          <OutboxSyncWatcher />
        </WouterRouter>
        {/* Sonner is the app's single toast system — every page's toast()
            renders through this one Toaster. Keep it mounted or all
            notifications silently disappear. */}
        <SonnerToaster position="top-center" richColors closeButton />
      </TooltipProvider>
    </QueryClientProvider>
  );
}

export default App;
