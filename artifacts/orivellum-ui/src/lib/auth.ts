/**
 * Auth helpers for the Orivellum web client.
 *
 * Authentication is handled via an HttpOnly session cookie set by the API
 * when the user logs in through POST /api/auth/login.  The browser sends
 * the cookie automatically on every same-origin request — no bearer token
 * is ever embedded in the client bundle.
 *
 * For mobile / API clients that cannot use cookies, the auth middleware also
 * accepts `Authorization: Bearer <key>` or `X-Api-Key: <key>` headers; those
 * clients supply credentials via `EXPO_PUBLIC_API_KEY` / `mobileFetch`.
 */
import { setAuthTokenGetter } from "@workspace/api-client-react";

// The web client relies on session cookies — no bearer token needed.
// Wire generated hooks with a null getter; the browser cookie handles auth.
setAuthTokenGetter(() => null);

// ── Auth-status helpers ───────────────────────────────────────────────────────

/** Check whether the current session is authenticated. */
export async function checkAuth(): Promise<boolean> {
  try {
    const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
    const resp = await fetch(`${base}/api/auth/me`, { credentials: "same-origin" });
    if (!resp.ok) return false;
    const data = await resp.json();
    return Boolean(data.authenticated);
  } catch {
    return false;
  }
}

/**
 * Submit the API key to the login endpoint.
 * Returns true on success, false if the key is wrong or the server is down.
 */
export async function login(key: string): Promise<boolean> {
  try {
    const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
    const resp = await fetch(`${base}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ key }),
    });
    return resp.ok;
  } catch {
    return false;
  }
}

/** Clear the current session (logout). */
export async function logout(): Promise<void> {
  try {
    const base = (import.meta.env.BASE_URL ?? "/").replace(/\/$/, "");
    await fetch(`${base}/api/auth/logout`, {
      method: "POST",
      credentials: "same-origin",
    });
  } catch {
    // Ignore network errors on logout
  }
}

// ── Fetch helpers ─────────────────────────────────────────────────────────────

/**
 * Thin wrapper around `fetch` for same-origin API calls.
 * Cookies are included automatically by the browser; this wrapper exists so
 * components can call `apiFetch` instead of bare `fetch` for consistency and
 * to make future auth changes easy to apply in one place.
 */
export async function apiFetch(
  input: RequestInfo | URL,
  init?: RequestInit,
): Promise<Response> {
  return fetch(input, { credentials: "same-origin", ...init });
}

/**
 * Build auth header object.  For the web client this is always empty —
 * the session cookie handles auth.  Kept for SSE streaming calls in
 * chat/index.tsx that build headers manually.
 */
export function buildAuthHeaders(): Record<string, string> {
  return {};
}

/**
 * No-op kept for backwards compatibility.
 */
export async function initAuth(): Promise<void> {
  // Auth is established via the login form in App.tsx before the app renders.
}

/** @deprecated Not used in the web client (sessions replace bearer tokens). */
export function getApiToken(): string | null {
  return null;
}
