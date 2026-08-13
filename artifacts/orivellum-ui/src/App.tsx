import React, { Suspense, useEffect, useState } from 'react';
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

// ── Route modules ─────────────────────────────────────────────────────────────
// Home is the only statically-imported screen: it is the initial route and must
// paint without a second network round-trip. Every other destination loads as
// its own bundle via React.lazy, so Home never pays for editor / Studio / mail
// / analysis code (WP5 gate).
import HomeScreen from '@/pages/home/index';

const WorksList = React.lazy(() => import('@/pages/works/index'));
const WorkDetail = React.lazy(() => import('@/pages/works/detail'));
const WorkIntelligence = React.lazy(() => import('@/pages/works/intelligence'));
const GapOraclePage = React.lazy(() => import('@/pages/works/gap-oracle'));
const ContinuityPage = React.lazy(() => import('@/pages/works/continuity'));
const HandoffPage = React.lazy(() => import('@/pages/works/handoff'));
const PacingPage = React.lazy(() => import('@/pages/works/pacing'));
const SeriesList = React.lazy(() => import('@/pages/series/index'));
const SeriesDetail = React.lazy(() => import('@/pages/series/detail'));
const CollectionsPage = React.lazy(() => import('@/pages/collections/index'));
const CollectionDetail = React.lazy(() => import('@/pages/collections/detail'));
const Chat = React.lazy(() => import('@/pages/chat/index'));
const Library = React.lazy(() => import('@/pages/library/index'));
const NotesPage = React.lazy(() => import('@/pages/notes/index'));
const DocumentDetail = React.lazy(() => import('@/pages/library/detail'));
const Projects = React.lazy(() => import('@/pages/projects/index'));
const ProjectDetail = React.lazy(() => import('@/pages/projects/detail'));
const Studio = React.lazy(() => import('@/pages/studio/index'));
const WriteDeskPage = React.lazy(() => import('@/pages/write/index'));
const WritingHub = React.lazy(() => import('@/pages/writing/index'));
const LearningHub = React.lazy(() => import('@/pages/learning/index'));
const LearningSession = React.lazy(() => import('@/pages/learning/session'));
const KnowledgeReview = React.lazy(() => import('@/pages/learning/review'));
const Backups = React.lazy(() => import('@/pages/backups/index'));
const System = React.lazy(() => import('@/pages/system/index'));
const CommandHub = React.lazy(() => import('@/pages/command/index'));
const GovernancePage = React.lazy(() => import('@/pages/governance/index'));
const ReviewPage = React.lazy(() => import('@/pages/review/index'));
const Mcos = React.lazy(() => import('@/pages/mcos/index'));
const AssayPromotion = React.lazy(() => import('@/pages/assay/index'));
const BooksPage = React.lazy(() => import('@/pages/books/index'));
const CanonPage = React.lazy(() => import('@/pages/canon/index'));
const WritingArchitectPage = React.lazy(() => import('@/pages/writing-architect/index'));
const FinishingPage = React.lazy(() => import('@/pages/finishing/index'));
const LearnPage = React.lazy(() => import('@/pages/learn/index'));
const IntakePage = React.lazy(() => import('@/pages/intake/index'));
const TopicsPage = React.lazy(() => import('@/pages/topics/index'));
const ActionsPage = React.lazy(() => import('@/pages/actions/index'));
const OperationsPage = React.lazy(() => import('@/pages/operations/index'));
const GraphPage = React.lazy(() => import('@/pages/graph/index'));
const ForgePage = React.lazy(() => import('@/pages/forge/index'));
const ForgeDetail = React.lazy(() => import('@/pages/forge/detail'));
const WorkbenchPage = React.lazy(() => import('@/pages/workbench/index'));
const WorkbenchDetail = React.lazy(() => import('@/pages/workbench/detail'));
const MailPage = React.lazy(() => import('@/pages/mail/index'));
const MailConnectPage = React.lazy(() => import('@/pages/mail/connect'));
const ComposePage = React.lazy(() => import('@/pages/mail/compose'));
const MailSettingsPage = React.lazy(() => import('@/pages/mail/settings'));
const NotFound = React.lazy(() => import('@/pages/not-found'));
import { ResponsiveShell } from '@/components/shell/responsive-shell';
import { UpdatePrompt } from '@/components/update-prompt';
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


/** Shown while a lazily-loaded route bundle is in flight. Fills the content
 * host with the same surface the destination will paint on, so the swap is a
 * fade-in rather than a layout shift. */
function RouteLoading() {
  return (
    <div
      className="flex items-center justify-center min-h-[40vh]"
      role="status"
      aria-label="Loading page"
    >
      <span className="text-sm text-muted-foreground animate-pulse">Loading…</span>
    </div>
  );
}

function RouteWithBoundary({ component: Page }: { component: React.ComponentType }) {
  return (
    <ErrorBoundary
      fallback={(err, reset) => <RouteErrorFallback error={err} reset={reset} />}
    >
      {/* Destination-level Suspense: each lazily-loaded route suspends here,
          inside the shell, so the tab bar / rail stay interactive while the
          bundle loads. */}
      <Suspense fallback={<RouteLoading />}>
        <Page />
      </Suspense>
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
        <Route>{() => <RouteWithBoundary component={NotFound} />}</Route>
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
          <UpdatePrompt />
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
