/**
 * Server address management for the Orivellum mobile app.
 *
 * The app can talk to two kinds of servers:
 *   1. The Replit dev environment — `https://${EXPO_PUBLIC_DOMAIN}` baked
 *      into the bundle at build time (the historical default).
 *   2. A self-hosted server (e.g. the user's own PC over Tailscale) —
 *      entered once on the login screen, persisted on-device, e.g.
 *      `http://100.92.116.70:8080`.
 *
 * `apiOrigin()` is the single source of truth for the API origin.  Every
 * hand-rolled fetch in the app builds URLs as `${apiOrigin()}/api/...`.
 * The generated react-query hooks are wired via `setBaseUrl(apiOrigin())`
 * in `_layout.tsx` after `loadServerOrigin()` resolves at startup.
 *
 * IMPORTANT: never capture `apiOrigin()` in a module-level constant — route
 * modules are imported before the stored origin loads.  Call it lazily
 * (inside components, handlers, or arrow-function constants).
 */
import * as SecureStore from 'expo-secure-store';

const STORE_KEY = 'orivellum_server_origin';
// Web detection without importing react-native — keeps this module loadable
// in plain jsdom test environments (SecureStore has no web implementation).
const _isWeb = typeof document !== 'undefined';

/** Build-time default: the Replit dev domain (empty-host URL when unset). */
export const DEFAULT_ORIGIN = `https://${process.env.EXPO_PUBLIC_DOMAIN ?? 'localhost:8000'}`;

let _origin: string = DEFAULT_ORIGIN;

/** The current API origin, e.g. "http://100.92.116.70:8080" — no trailing slash. */
export function apiOrigin(): string {
  return _origin;
}

/** True when the user has configured a custom (self-hosted) server. */
export function isCustomServer(): boolean {
  return _origin !== DEFAULT_ORIGIN;
}

/**
 * Normalize a user-typed server address into an origin string.
 * Accepts "100.92.116.70:8080", "http://host:8080/", "https://host/api" …
 * Returns null when the input cannot be parsed into a valid http(s) URL.
 */
export function normalizeOrigin(raw: string): string | null {
  let text = raw.trim();
  if (!text) return null;
  // Reject explicit non-http(s) schemes rather than mangling them into
  // "http://ftp://host". Only scheme-less input gets http:// prepended.
  if (/^[a-z][a-z0-9+.-]*:\/\//i.test(text)) {
    if (!/^https?:\/\//i.test(text)) return null;
  } else {
    text = `http://${text}`;
  }
  try {
    const url = new URL(text);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
    if (!url.hostname) return null;
    const port = url.port ? `:${url.port}` : '';
    return `${url.protocol}//${url.hostname}${port}`;
  } catch {
    return null;
  }
}

async function _storageGet(): Promise<string | null> {
  if (_isWeb) {
    try { return window.localStorage.getItem(STORE_KEY); } catch { return null; }
  }
  return SecureStore.getItemAsync(STORE_KEY);
}

async function _storageSet(value: string): Promise<void> {
  if (_isWeb) {
    window.localStorage.setItem(STORE_KEY, value);
    return;
  }
  await SecureStore.setItemAsync(STORE_KEY, value);
}

async function _storageDelete(): Promise<void> {
  if (_isWeb) {
    window.localStorage.removeItem(STORE_KEY);
    return;
  }
  await SecureStore.deleteItemAsync(STORE_KEY);
}

/**
 * Load the persisted server origin.  Must run at app startup BEFORE any
 * API request (alongside `loadToken()`).  Returns the effective origin.
 */
export async function loadServerOrigin(): Promise<string> {
  try {
    const stored = await _storageGet();
    _origin = stored || DEFAULT_ORIGIN;
  } catch {
    _origin = DEFAULT_ORIGIN;
  }
  return _origin;
}

/** Persist a validated server origin and make it current. */
export async function saveServerOrigin(origin: string): Promise<void> {
  await _storageSet(origin);
  _origin = origin;
}

/** Forget the stored origin (revert to the build-time default). */
export async function clearServerOrigin(): Promise<void> {
  try {
    await _storageDelete();
  } catch {
    // ignore — may not exist
  }
  _origin = DEFAULT_ORIGIN;
}
