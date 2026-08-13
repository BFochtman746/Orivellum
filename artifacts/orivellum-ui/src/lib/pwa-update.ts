/**
 * PWA update controller (WP5 prompt-based update model).
 *
 * The service worker is registered with `registerType: 'prompt'`: when a new
 * build is deployed the fresh worker installs and then WAITS. Nothing reloads
 * on its own. This module surfaces that waiting state so the UI can offer a
 * safe, explicit "Update now" action — and `applyUpdate()` refuses to run
 * while the app-busy registry holds unsaved work.
 *
 * Build version: `__BUILD_ID__` is defined at build time (vite.config.ts) as
 * "<git short sha> · <date>" and shown on the System page + update prompt.
 */
import { isAppBusy } from './app-busy';

export const BUILD_ID: string =
  typeof __BUILD_ID__ !== 'undefined' ? __BUILD_ID__ : 'dev';

type UpdateState = {
  /** A new version is installed and waiting to take over. */
  ready: boolean;
  /** The app is running from cache with no connectivity (informational). */
  offlineReady: boolean;
};

let state: UpdateState = { ready: false, offlineReady: false };
const listeners = new Set<() => void>();
let doUpdate: ((reload?: boolean) => Promise<void>) | null = null;

function setState(patch: Partial<UpdateState>) {
  state = { ...state, ...patch };
  for (const l of listeners) l();
}

export function getUpdateState(): UpdateState {
  return state;
}

export function subscribeUpdateState(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * Activate the waiting service worker and reload. Returns false (and does
 * nothing) if unsaved work is in flight — callers should disable the action
 * and show `busyLabel()` instead of calling this blind.
 */
export async function applyUpdate(): Promise<boolean> {
  if (!state.ready || !doUpdate) return false;
  if (isAppBusy()) return false;
  await doUpdate(true); // skipWaiting + controllerchange → reload
  return true;
}

/** Register the service worker (production builds only — no-op in dev). */
export function initPwaUpdates(): void {
  if (!import.meta.env.PROD) return;
  if (!('serviceWorker' in navigator)) return;
  // Dynamic import keeps the register shim out of dev bundles entirely.
  import('virtual:pwa-register')
    .then(({ registerSW }) => {
      doUpdate = registerSW({
        immediate: true,
        onNeedRefresh() {
          setState({ ready: true });
        },
        onOfflineReady() {
          setState({ offlineReady: true });
        },
      });
    })
    .catch(() => {
      /* SW registration is best-effort — the app works without it */
    });
}
